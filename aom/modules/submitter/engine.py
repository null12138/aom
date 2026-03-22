from __future__ import annotations

import json
import sqlite3
import threading
import time
import asyncio
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, Iterable, Iterator, List, Optional, Tuple, Callable
import logging

from ...core.fingerprint import factor_fingerprint
from ..library.engine import (
    connect as lib_connect,
    init_db as lib_init_db,
    _upsert_factor as lib_upsert_factor,
    _insert_submission as lib_insert_submission,
    load_fingerprints as lib_load_fingerprints,
)

logger = logging.getLogger("SubmitterEngine")
STATE_VERSION = "0.1"

class SubmitterError(RuntimeError): pass

@dataclass
class SubmissionResult:
    submission_id: str
    status: str
    result: Dict[str, Any]

class SubmissionAdapter:
    def submit(self, item: Dict[str, Any], stop_event: Optional[threading.Event] = None, on_heartbeat: Optional[Callable[[int], None]] = None) -> SubmissionResult: raise NotImplementedError
    def submit_multiple(self, items: List[Dict[str, Any]], stop_event: Optional[threading.Event] = None, on_heartbeat: Optional[Callable[[int], None]] = None) -> List[SubmissionResult]: raise NotImplementedError

class BackfillAdapter:
    def backfill(self, item: Dict[str, Any]) -> Dict[str, Any]: raise NotImplementedError

# --- Helper Functions ---
def _now() -> str: return datetime.now().isoformat(timespec="seconds")

_state_lock = threading.Lock()

def _update_stats(state: Dict[str, Any]) -> None:
    with _state_lock:
        stats = state.setdefault("stats", {})
        for k in ["queue", "in_flight", "completed", "failed"]: 
            stats[k] = len(state.get(k, []))

# --- Factor & State Management ---

def load_factors(path: Path) -> List[Dict[str, Any]]:
    data = json.loads(path.read_text(encoding="utf-8"))
    if isinstance(data, dict) and data.get("type") == "bundle":
        common = data.get("common_settings", {})
        return [{"factor_id": f.get("id"), "expression": f.get("expr"), "settings": common, "tags": f.get("tags", []), "priority": f.get("priority", 100)} for f in data.get("factors", [])]
    if not isinstance(data, list): raise SubmitterError("Invalid factors file format")
    return data

def iter_factors(path: Path, start_index: int = 0) -> Iterator[Tuple[int, Dict[str, Any]]]:
    with path.open("r", encoding="utf-8") as f: 
        data = json.load(f)
    if isinstance(data, dict) and data.get("type") == "bundle":
        common = data.get("common_settings", {})
        for idx, f in enumerate(data.get("factors", [])):
            if idx < start_index: continue
            yield idx, {"factor_id": f.get("id"), "expression": f.get("expr"), "settings": common, "tags": f.get("tags", []), "priority": f.get("priority", 100)}
    else:
        for idx, item in enumerate(data):
            if idx < start_index: continue
            yield idx, item

def init_state(factors: List[Dict[str, Any]], run_id: str, config: Dict[str, Any], dedup: bool = True, existing_fingerprints: Optional[set[str]] = None) -> Dict[str, Any]:
    queue = []
    skipped = 0
    fingerprints = existing_fingerprints or set()
    for item in factors:
        fp = factor_fingerprint(item["expression"], item["settings"])
        if dedup and fp in fingerprints:
            skipped += 1
            continue
        fingerprints.add(fp)
        item["fingerprint"] = fp
        item["status"] = "queued"
        queue.append(item)
    return {"run_id": run_id, "schema_version": STATE_VERSION, "created_at": _now(), "config": config, "queue": queue, "in_flight": [], "completed": [], "failed": [], "stats": {"skipped_duplicates": skipped}}

def init_state_stream(source_file: Path, run_id: str, config: Dict[str, Any], start_index: int = 0, dedup: bool = True) -> Dict[str, Any]:
    return {"run_id": run_id, "schema_version": STATE_VERSION, "created_at": _now(), "config": config, "mode": "stream", "cursor": start_index, "in_flight": [], "completed": [], "failed": [], "stats": {"skipped_duplicates": 0}}

def save_state(path: Path, state: Dict[str, Any]) -> None:
    path.write_text(json.dumps(state, indent=2, ensure_ascii=False), encoding="utf-8")

def load_state(path: Path) -> Dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))

def validate_factor_payload(item: Dict[str, Any]) -> None:
    if "expression" not in item: raise SubmitterError("Missing 'expression' in factor")
    if "settings" not in item: raise SubmitterError("Missing 'settings' in factor")

# --- Processing Logic ---

def _process_single_item(item: Dict[str, Any], adapter: SubmissionAdapter, state: Dict[str, Any], lib_conn: Optional[sqlite3.Connection], on_progress: Optional[Callable[[Dict[str, Any]], None]], stop_event: Optional[threading.Event] = None) -> None:
    if stop_event and stop_event.is_set(): return
    item["updated_at"] = _now()
    def hb(seconds): 
        if on_progress: on_progress({"status": "WAITING", "expression": item["expression"], "wait_time": seconds})
    try:
        item["status"] = "submitted"
        result = adapter.submit(item, stop_event=stop_event, on_heartbeat=hb)
        with _state_lock:
            item.update({"submission_id": result.submission_id, "status": result.status, "result": result.result})
            if result.status == "completed": state["completed"].append(item)
            else: state["in_flight"].append(item)
    except Exception as e:
        with _state_lock:
            item["status"] = "failed"
            item["last_error"] = str(e)
            state["failed"].append(item)
    if lib_conn:
        try:
            fp = item.get("fingerprint") or factor_fingerprint(item["expression"], item["settings"])
            lib_upsert_factor(lib_conn, fp, item["expression"], item["settings"], item["status"], item.get("result"), item["updated_at"])
            lib_conn.commit()
        except: pass
    if on_progress:
        try: on_progress(item)
        except: pass

def _process_batch(batch: List[Dict[str, Any]], adapter: SubmissionAdapter, state: Dict[str, Any], db_path: Optional[Path], on_progress: Optional[Callable[[Dict[str, Any]], None]], stop_event: Optional[threading.Event] = None) -> int:
    """处理一个批次，极大优化锁的范围且解决多线程 SQLite 问题"""
    if stop_event and stop_event.is_set(): return 0
    
    def hb(seconds):
        if on_progress: on_progress({"status": "WAITING_BATCH", "count": len(batch), "wait_time": seconds})
    
    try:
        if on_progress: on_progress({"status": "SUBMITTING", "expression": f"正在向 API 提交 {len(batch)} 个回测任务..."})
        
        results = adapter.submit_multiple(batch, stop_event=stop_event, on_heartbeat=hb)
        
        # 1. 第一步：仅更新内存状态，锁的范围最小化
        with _state_lock:
            for item, res in zip(batch, results):
                item.update({"submission_id": res.submission_id, "status": res.status, "result": res.result, "updated_at": _now()})
                
                # 双重保险：同时使用 logger 和 print 确保回传显示
                if res.status == "completed":
                    state["completed"].append(item)
                    sim_id = res.submission_id
                    web_link = adapter.get_simulation_url(sim_id)
                    msg = f"\033[1;32m[SUCCESS]\033[0m {item['expression'][:40]}... -> {web_link}"
                    logger.info(msg)
                    print(msg) 
                elif res.status == "failed":
                    item["last_error"] = res.result.get("error", "Unknown error")
                    state["failed"].append(item)
                    msg = f"\033[1;31m[FAILED]\033[0m {item['expression'][:40]}... Error: {item['last_error']}"
                    logger.error(msg)
                    print(msg)
                else: 
                    state["in_flight"].append(item)
        
        # 2. 第二步：在独立线程内建立数据库连接，解决跨线程访问报错
        if db_path:
            try:
                # 每次批次处理开启独立连接，完成即关闭
                local_conn = lib_connect(db_path)
                for item in batch:
                    try:
                        fp = item.get("fingerprint") or factor_fingerprint(item["expression"], item["settings"])
                        lib_upsert_factor(local_conn, fp, item["expression"], item["settings"], item["status"], item.get("result"), item["updated_at"])
                    except Exception as e:
                        logger.error(f"SQLite 写入单条失败: {e}")
                local_conn.commit()
                local_conn.close()
            except Exception as e:
                logger.error(f"SQLite 批次操作失败: {e}")

        # 3. 第三步：UI 进度更新，不持锁
        if on_progress:
            for item in batch:
                try: on_progress(item)
                except: pass
            
        return len(batch)
    except Exception as e:
        logger.error(f"Batch processing failed: {e}")
        # 错误处理也需要加锁
        with _state_lock:
            for item in batch:
                if item.get("status") not in ("completed", "submitted"):
                    item["status"] = "failed"
                    item["last_error"] = str(e)
                    state["failed"].append(item)
                if on_progress:
                    try: on_progress(item)
                    except: pass
        return len(batch)

# --- Concurrent Async Engine ---

def run_submitter_concurrent(state: Dict[str, Any], adapter: SubmissionAdapter, concurrency: int = 2, batch_size: int = 3, db_path: Optional[Path] = None, source_file: Optional[Path] = None, stop_event: Optional[threading.Event] = None, on_progress: Optional[Callable[[Dict[str, Any]], None]] = None) -> Tuple[Dict[str, Any], int]:
    """高性能并发/多重回测统一引擎"""
    processed = 0
    
    if on_progress: on_progress({"status": "STARTING", "expression": "正在初始化引擎..."})
    
    # 预初始化数据库表结构，但不持有长连接
    if db_path:
        tmp_conn = lib_connect(db_path)
        lib_init_db(tmp_conn)
        tmp_conn.close()
    
    if on_progress: on_progress({"status": "LOADING", "expression": "正在加载因子..."})
    
    if state.get("mode") == "stream" and source_file:
        factors_iter = iter_factors(source_file, start_index=state.get("cursor", 0))
    else:
        def _q_iter():
            q = state.get("queue", [])
            for i, it in enumerate(list(q)): yield i, it
        factors_iter = _q_iter()

    all_factors = []
    for idx, item in factors_iter:
        all_factors.append((idx, item))
    
    if not all_factors:
        if lib_conn: lib_conn.close()
        return state, 0

    batches = [all_factors[i:i + batch_size] for i in range(0, len(all_factors), batch_size)]
    
    async def _run_async_engine():
        nonlocal processed
        if hasattr(adapter, 'ensure_login'):
            if on_progress: on_progress({"status": "AUTH", "expression": "正在验证 API 身份..."})
            await asyncio.to_thread(adapter.ensure_login)

        sem = asyncio.Semaphore(concurrency)
        
        async def _process_batch_task(batch_items_with_idx):
            nonlocal processed
            async with sem:
                if stop_event and stop_event.is_set(): return
                
                batch_items = [item for idx, item in batch_items_with_idx]
                try:
                    def _sync_work():
                        if stop_event and stop_event.is_set(): return 0
                        return _process_batch(batch_items, adapter, state, db_path, on_progress, stop_event)
                    
                    count = await asyncio.to_thread(_sync_work)
                    processed += count
                    
                    with _state_lock:
                        if state.get("mode") == "stream":
                            last_idx = batch_items_with_idx[-1][0]
                            state["cursor"] = max(state.get("cursor", 0), last_idx + 1)
                except Exception as e:
                    logger.error(f"Batch execution error: {e}")

        tasks = [_process_batch_task(b) for b in batches]
        await asyncio.gather(*tasks)

    try:
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        loop.run_until_complete(_run_async_engine())
        loop.close()
    except Exception as e:
        logger.error(f"Async engine runtime error: {e}")
        if on_progress: on_progress({"status": "ERROR", "expression": f"引擎运行出错: {e}"})
    
    _update_stats(state)
    return state, processed

# --- Legacy Functions (Restored) ---

def run_submitter(state: Dict[str, Any], adapter: SubmissionAdapter, db_path: Optional[Path] = None, max_items: Optional[int] = None, stop_event: Optional[threading.Event] = None, on_progress: Optional[Callable[[Dict[str, Any]], None]] = None) -> Tuple[Dict[str, Any], int]:
    queue = state.get("queue", [])
    processed = 0
    lib_conn = lib_connect(db_path) if db_path else None
    if lib_conn: lib_init_db(lib_conn)
    try:
        while queue:
            if stop_event and stop_event.is_set(): break
            item = queue.pop(0)
            _process_single_item(item, adapter, state, lib_conn, on_progress, stop_event)
            processed += 1
            if max_items and processed >= max_items: break
    finally:
        if lib_conn: lib_conn.close()
    _update_stats(state)
    return state, processed

def run_submitter_stream(state: Dict[str, Any], adapter: SubmissionAdapter, source_file: Path, start_index: int = 0, db_path: Optional[Path] = None, max_items: Optional[int] = None, stop_event: Optional[threading.Event] = None, on_progress: Optional[Callable[[Dict[str, Any]], None]] = None) -> Tuple[Dict[str, Any], int]:
    processed = 0
    lib_conn = lib_connect(db_path) if db_path else None
    if lib_conn: lib_init_db(lib_conn)
    try:
        for idx, item in iter_factors(source_file, start_index=start_index):
            if stop_event and stop_event.is_set(): break
            _process_single_item(item, adapter, state, lib_conn, on_progress, stop_event)
            state["cursor"] = idx + 1
            processed += 1
            if max_items and processed >= max_items: break
    finally:
        if lib_conn: lib_conn.close()
    _update_stats(state)
    return state, processed

def run_submitter_multiple(state: Dict[str, Any], adapter: SubmissionAdapter, source_file: Optional[Path] = None, batch_size: int = 10, db_path: Optional[Path] = None, on_progress: Optional[Callable[[Dict[str, Any]], None]] = None, stop_event: Optional[threading.Event] = None) -> Tuple[Dict[str, Any], int]:
    return run_submitter_concurrent(state, adapter, concurrency=1, batch_size=batch_size, db_path=db_path, source_file=source_file, stop_event=stop_event, on_progress=on_progress)

# --- Adapter Implementations ---

class BrainApiAdapter(SubmissionAdapter):
    def __init__(self, username, password, api_base, max_wait=1800, settings_override=None, use_proxy=False):
        from ...api.brain import BrainClient
        self.client = BrainClient(username, password, api_base, use_proxy=use_proxy)
        self.username = username
        self.password = password
        self.max_wait = max_wait
        self.settings_override = settings_override or {}
        self._is_logged_in = False
        self._auth_lock = threading.Lock()

    def ensure_login(self):
        with self._auth_lock:
            if not self._is_logged_in:
                self.client.login()
                self._is_logged_in = True

    def get_simulation_url(self, simulation_id: str) -> str:
        return self.client.get_simulation_url(simulation_id)

    def submit(self, item, stop_event=None, on_heartbeat=None):
        self.ensure_login()
        payload = build_brain_payload(item, self.settings_override)
        out = self.client.simulate(payload, max_wait=self.max_wait, stop_event=stop_event, on_heartbeat=on_heartbeat)
        return SubmissionResult(out.simulation_id, "completed", {"alpha_id": out.alpha_id, "alpha": out.result})

    def submit_multiple(self, items, stop_event=None, on_heartbeat=None):
        self.ensure_login()
        if not items: return []
        payloads = [build_brain_payload(it, self.settings_override) for it in items]
        try:
            outcomes = self.client.simulate_multiple(payloads, max_wait=self.max_wait, stop_event=stop_event, on_heartbeat=on_heartbeat)
            return [SubmissionResult(o.simulation_id, "completed", {"alpha_id": o.alpha_id, "alpha": o.result}) for o in outcomes]
        except Exception as e:
            if stop_event and stop_event.is_set(): raise e
            logger.warning(f"Multi-sim 整体提交失败，转为逐条重试: {e}")
            res = []
            for it in items:
                if stop_event and stop_event.is_set(): break
                try:
                    # 逐条尝试，成功则返回 completed，失败则记录错误原因
                    out = self.submit(it, stop_event=stop_event, on_heartbeat=on_heartbeat)
                    res.append(out)
                except Exception as individual_error:
                    logger.error(f"因子提交失败 (已跳过): {it.get('expression')[:30]}... 错误: {individual_error}")
                    res.append(SubmissionResult("", "failed", {"error": str(individual_error)}))
            return res

class BrainBackfillAdapter(BackfillAdapter):
    def __init__(self, username, password, api_base, use_proxy=False):
        from ...api.brain import BrainClient
        self.client = BrainClient(username, password, api_base, use_proxy=use_proxy)
        self.client.login()
    def backfill(self, item):
        if not item.get("submission_id"): return {}
        try: return self.client.get_simulation(item["submission_id"])
        except: return {}

def backfill_state(state, adapter, force=False):
    in_flight = state.get("in_flight", [])
    if not in_flight: return 0
    updated = 0
    remaining = []
    for item in in_flight:
        try:
            res = adapter.backfill(item)
            if res and res.get("status") == "COMPLETE":
                item["status"] = "completed"
                item["result"] = {"alpha_id": res.get("alpha"), "alpha": res}
                state["completed"].append(item)
                updated += 1
            else: remaining.append(item)
        except: remaining.append(item)
    state["in_flight"] = remaining
    return updated

def build_brain_settings(s):
    def get_val(keys, default):
        for k in keys:
            if k in s:
                v = s[k]
                # Defensive check for any non-standard objects that might have leaked
                if v is None or isinstance(v, (str, int, float, bool)):
                    return v
                # Fallback: if it's a "NoSelection" object or similar, return default
                if "NoSelection" in str(v):
                    return default
                return v
        return default
    return {"instrumentType": get_val(["instrumentType", "instrument_type"], "EQUITY"), "region": get_val(["region"], "USA"), "universe": get_val(["universe"], "TOP3000"), "delay": int(get_val(["delay"], 1) or 1), "decay": int(get_val(["decay"], 0) or 0), "neutralization": get_val(["neutralization"], "INDUSTRY"), "truncation": float(get_val(["truncation"], 0.08) or 0.08), "pasteurization": get_val(["pasteurization"], "ON"), "unitHandling": get_val(["unitHandling", "unit_handling"], "VERIFY"), "nanHandling": get_val(["nanHandling", "nan_handling"], "OFF"), "language": "FASTEXPR", "visualization": False}

def build_brain_payload(item, override=None):
    s = dict(item.get("settings", {}))
    if override: s.update(override)
    return {"type": "REGULAR", "settings": build_brain_settings(s), "regular": item["expression"]}
