from __future__ import annotations

import json
import logging
import re
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional
from urllib.parse import parse_qsl, quote, urlencode, urlparse, urlunparse

import requests

from ...api.brain import BrainClient
from ..brain.engine import AlphaGenerator
from ..brain.field_meta_cache import LocalFieldMetaCache, build_field_meta_context_key
from ..brain.knowledge import get_all_patterns
from ..submitter.engine import build_brain_payload

logger = logging.getLogger("DreamAlpha")


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _to_int(value: Any, default: int) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _to_float(value: Any, default: float) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _to_bool(value: Any, default: bool = False) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        lower = value.strip().lower()
        if lower in {"1", "true", "yes", "on"}:
            return True
        if lower in {"0", "false", "no", "off"}:
            return False
    if value is None:
        return default
    return bool(value)


def _safe_read_json(path: Path, fallback: Any) -> Any:
    if not path.exists():
        return fallback
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return fallback


def _write_json_atomic(path: Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = path.with_suffix(path.suffix + ".tmp")
    tmp_path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp_path.replace(path)


def _build_notify_url(base_url: str, msg: str) -> str:
    if not base_url:
        return ""
    if "{msg}" in base_url:
        return base_url.replace("{msg}", quote(msg, safe=""))
    parsed = urlparse(base_url)
    query = dict(parse_qsl(parsed.query, keep_blank_values=True))
    query["msg"] = msg
    return urlunparse(parsed._replace(query=urlencode(query)))


def _dig(node: Any, path: List[str], default: Any = None) -> Any:
    cur = node
    for key in path:
        if not isinstance(cur, dict):
            return default
        if key not in cur:
            return default
        cur = cur[key]
    return cur


def _extract_metrics(result_wrapper: Dict[str, Any]) -> Dict[str, float]:
    alpha_node = result_wrapper.get("alpha") if isinstance(result_wrapper, dict) else {}
    if not isinstance(alpha_node, dict):
        alpha_node = {}

    sharpe_candidates = [
        _dig(alpha_node, ["is", "sharpe"]),
        _dig(alpha_node, ["sharpe"]),
        _dig(alpha_node, ["metrics", "sharpe"]),
        _dig(alpha_node, ["statistics", "sharpe"]),
        _dig(alpha_node, ["isSharpe"]),
        _dig(result_wrapper, ["sharpe"]),
    ]
    fitness_candidates = [
        _dig(alpha_node, ["is", "fitness"]),
        _dig(alpha_node, ["fitness"]),
        _dig(alpha_node, ["metrics", "fitness"]),
        _dig(alpha_node, ["statistics", "fitness"]),
        _dig(alpha_node, ["isFitness"]),
        _dig(result_wrapper, ["fitness"]),
    ]

    sharpe = 0.0
    fitness = 0.0
    for value in sharpe_candidates:
        if value is None:
            continue
        try:
            sharpe = float(value)
            break
        except (TypeError, ValueError):
            continue
    for value in fitness_candidates:
        if value is None:
            continue
        try:
            fitness = float(value)
            break
        except (TypeError, ValueError):
            continue
    return {"sharpe": sharpe, "fitness": fitness}


def _find_matching_paren(text: str, open_idx: int) -> int:
    if open_idx < 0 or open_idx >= len(text) or text[open_idx] != "(":
        return -1
    depth = 0
    for idx in range(open_idx, len(text)):
        ch = text[idx]
        if ch == "(":
            depth += 1
        elif ch == ")":
            depth -= 1
            if depth == 0:
                return idx
    return -1


def _split_top_level_args(inner: str) -> List[str]:
    args: List[str] = []
    depth = 0
    start = 0
    for idx, ch in enumerate(inner):
        if ch == "(":
            depth += 1
        elif ch == ")":
            depth -= 1
        elif ch == "," and depth == 0:
            args.append(inner[start:idx].strip())
            start = idx + 1
    last = inner[start:].strip()
    if last:
        args.append(last)
    return args


def _replace_binary_aliases(expr: str) -> str:
    alias_op = {
        "sub": "-",
        "add": "+",
        "mul": "*",
        "div": "/",
    }
    text = expr
    changed = True
    # multiple rounds to resolve nested aliases progressively
    for _ in range(40):
        if not changed:
            break
        changed = False
        for alias, op in alias_op.items():
            pattern = f"{alias}("
            scan_from = 0
            while True:
                idx = text.find(pattern, scan_from)
                if idx < 0:
                    break
                # do not match suffix names like ts_sub(...)
                prev_ok = idx == 0 or (not (text[idx - 1].isalnum() or text[idx - 1] == "_"))
                if not prev_ok:
                    scan_from = idx + len(pattern)
                    continue
                open_idx = idx + len(alias)
                close_idx = _find_matching_paren(text, open_idx)
                if close_idx < 0:
                    scan_from = idx + len(pattern)
                    continue
                inner = text[open_idx + 1:close_idx]
                args = _split_top_level_args(inner)
                if len(args) != 2:
                    scan_from = close_idx + 1
                    continue
                repl = f"({args[0]} {op} {args[1]})"
                text = text[:idx] + repl + text[close_idx + 1:]
                scan_from = idx + len(repl)
                changed = True
    return text


def _normalize_expression(expr: str) -> str:
    text = str(expr or "").strip()
    # trim trailing separators produced by natural-language list formatting
    text = re.sub(r"[;；\s]+$", "", text)
    text = _replace_binary_aliases(text)
    return text.strip()


def _contains_field_token(expression: str, field_id: str) -> bool:
    expr = str(expression or "")
    fid = str(field_id or "").strip()
    if not expr or not fid:
        return False
    pattern = r"(?<![A-Za-z0-9_])" + re.escape(fid) + r"(?![A-Za-z0-9_])"
    return re.search(pattern, expr, flags=re.IGNORECASE) is not None


def _extract_expression_fields(expression: str, field_ids: List[str]) -> List[str]:
    out: List[str] = []
    seen = set()
    # long tokens first to reduce partial token ambiguity
    for fid in sorted([str(x) for x in field_ids if str(x).strip()], key=len, reverse=True):
        if fid in seen:
            continue
        if _contains_field_token(expression, fid):
            seen.add(fid)
            out.append(fid)
    return out


def _expression_structure_signature(expression: str, field_ids: List[str]) -> str:
    text = str(expression or "").lower()
    if not text:
        return ""
    for fid in sorted([str(x).lower() for x in field_ids if str(x).strip()], key=len, reverse=True):
        pattern = r"(?<![A-Za-z0-9_])" + re.escape(fid) + r"(?![A-Za-z0-9_])"
        text = re.sub(pattern, "<f>", text)
    text = re.sub(r"\b\d+(\.\d+)?\b", "<n>", text)
    text = re.sub(r"\s+", "", text)
    return text


def _count_operator_calls(expression: str) -> int:
    text = str(expression or "")
    if not text:
        return 0
    # FastExpr operators are function-like tokens followed by "(".
    return len(re.findall(r"(?<![A-Za-z0-9_])[A-Za-z_][A-Za-z0-9_]*\s*\(", text))


def _normalize_operators_payload(raw: Any) -> List[Dict[str, Any]]:
    candidates: List[Any] = []
    if isinstance(raw, list):
        candidates = raw
    elif isinstance(raw, dict):
        for key in ("results", "items", "operators", "data"):
            value = raw.get(key)
            if isinstance(value, list):
                candidates = value
                break
        if not candidates:
            for value in raw.values():
                if isinstance(value, list):
                    candidates.extend(value)

    out: List[Dict[str, Any]] = []
    seen = set()
    for item in candidates:
        if not isinstance(item, dict):
            continue
        name = item.get("name") or item.get("id") or item.get("key") or item.get("operator")
        if not name:
            continue
        key = str(name).strip()
        if not key or key in seen:
            continue
        seen.add(key)
        out.append(
            {
                "name": key,
                "description": str(item.get("description") or item.get("desc") or ""),
                "definition": str(item.get("definition") or item.get("sign") or item.get("signature") or ""),
            }
        )
    return out


class DreamAlphaDaemon:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._thread: Optional[threading.Thread] = None
        self._stop_event: Optional[threading.Event] = None
        self._state: Dict[str, Any] = self._default_state()
        self._cfg: Dict[str, Any] = {}
        self._last_error_notify_ts = 0.0
        self._last_notify_transport_warn_ts = 0.0

    def _default_state(self) -> Dict[str, Any]:
        return {
            "schema_version": "0.1",
            "running": False,
            "stopping": False,
            "started_at": "",
            "stopped_at": "",
            "last_cycle_at": "",
            "last_error": "",
            "stats": {
                "cycles": 0,
                "raw_generated": 0,
                "generated": 0,
                "simulated": 0,
                "accepted": 0,
                "high_templates": 0,
                "errors": 0,
                "duplicates_skipped": 0,
                "single_dataset_skipped": 0,
                "structure_skipped": 0,
                "operator_limit_skipped": 0,
            },
            "cursor": {
                "cycle": 0,
                "candidate": 0,
                "accepted": 0,
                "high_template": 0,
                "error": 0,
                "updated_at": "",
            },
            "recent_events": [],
            "seen_expressions": [],
            "seen_signatures": [],
            "config": {},
        }

    def _cursor_file(self) -> Path:
        return Path(self._cfg.get("cursor_file", "runs/dream_alpha_cursor.json"))

    def _seed_file(self) -> Path:
        return Path(self._cfg.get("seed_file", "runs/dream_alpha_seed_library.json"))

    def _high_template_file(self) -> Path:
        return Path(self._cfg.get("high_template_file", "runs/dream_alpha_high_templates.jsonl"))

    def _notify_url(self) -> str:
        return str(self._cfg.get("notify_url") or "")

    def _error_notify_cooldown(self) -> int:
        return max(0, _to_int(self._cfg.get("error_notify_cooldown_sec"), 180))

    def _snapshot_locked(self) -> Dict[str, Any]:
        payload = {
            **self._state,
            "stats": dict(self._state.get("stats", {})),
            "cursor": dict(self._state.get("cursor", {})),
            "recent_events": list(self._state.get("recent_events", [])),
            "seen_expressions_count": len(self._state.get("seen_expressions", [])),
            "seen_signatures_count": len(self._state.get("seen_signatures", [])),
        }
        payload.pop("seen_expressions", None)
        payload.pop("seen_signatures", None)
        payload["thread_alive"] = bool(self._thread and self._thread.is_alive())
        return payload

    def _load_cursor(self) -> Dict[str, Any]:
        cursor = {
            "cycle": 0,
            "candidate": 0,
            "accepted": 0,
            "high_template": 0,
            "error": 0,
            "updated_at": "",
        }
        raw = _safe_read_json(self._cursor_file(), {})
        if not isinstance(raw, dict):
            return cursor
        node = raw.get("cursor") if isinstance(raw.get("cursor"), dict) else raw
        if not isinstance(node, dict):
            return cursor
        cursor["cycle"] = max(0, _to_int(node.get("cycle"), 0))
        cursor["candidate"] = max(0, _to_int(node.get("candidate"), 0))
        cursor["accepted"] = max(0, _to_int(node.get("accepted"), 0))
        cursor["high_template"] = max(0, _to_int(node.get("high_template"), 0))
        cursor["error"] = max(0, _to_int(node.get("error"), 0))
        cursor["updated_at"] = str(node.get("updated_at") or "")
        return cursor

    def status(self) -> Dict[str, Any]:
        with self._lock:
            return self._snapshot_locked()

    def start(self, cfg: Dict[str, Any]) -> Dict[str, Any]:
        with self._lock:
            if self._thread and self._thread.is_alive():
                current = self._snapshot_locked()
                current["already_running"] = True
                return current

            merged_cfg = self._normalize_cfg(cfg)
            self._cfg = merged_cfg

            initial = self._default_state()
            loaded_cursor = self._load_cursor()
            initial["cursor"] = loaded_cursor
            initial_stats = initial.get("stats", {})
            initial_stats["cycles"] = int(loaded_cursor.get("cycle", 0))
            initial_stats["simulated"] = int(loaded_cursor.get("candidate", 0))
            initial_stats["accepted"] = int(loaded_cursor.get("accepted", 0))
            initial_stats["high_templates"] = int(loaded_cursor.get("high_template", 0))
            initial_stats["errors"] = int(loaded_cursor.get("error", 0))
            initial["stats"] = initial_stats
            initial["running"] = True
            initial["stopping"] = False
            initial["started_at"] = _utc_now()
            initial["stopped_at"] = ""
            initial["last_error"] = ""
            initial["config"] = self._public_cfg(merged_cfg)
            if not isinstance(initial.get("seen_expressions"), list):
                initial["seen_expressions"] = []
            if not isinstance(initial.get("seen_signatures"), list):
                initial["seen_signatures"] = []
            if not isinstance(initial.get("recent_events"), list):
                initial["recent_events"] = []
            if not isinstance(initial.get("stats"), dict):
                initial["stats"] = self._default_state()["stats"]
            self._state = initial

            self._stop_event = threading.Event()
            self._persist_state_locked()

            self._thread = threading.Thread(
                target=self._run_loop,
                name="DreamAlphaDaemon",
                daemon=True,
            )
            self._thread.start()

            return self._snapshot_locked()

    def stop(self, wait_timeout_sec: int = 5) -> Dict[str, Any]:
        thread: Optional[threading.Thread] = None
        with self._lock:
            if not (self._thread and self._thread.is_alive()):
                self._state["running"] = False
                self._state["stopping"] = False
                self._state["stopped_at"] = _utc_now()
                self._persist_state_locked()
                return self._snapshot_locked()
            self._state["stopping"] = True
            self._persist_state_locked()
            if self._stop_event:
                self._stop_event.set()
            thread = self._thread

        if thread:
            thread.join(timeout=max(1, wait_timeout_sec))

        with self._lock:
            still_alive = bool(self._thread and self._thread.is_alive())
            self._state["running"] = still_alive
            self._state["stopping"] = still_alive
            if not still_alive:
                self._state["stopped_at"] = _utc_now()
                self._thread = None
            self._persist_state_locked()
            return self._snapshot_locked()

    def _normalize_cfg(self, cfg: Dict[str, Any]) -> Dict[str, Any]:
        out = dict(cfg or {})
        brain_cfg = out.get("brain") if isinstance(out.get("brain"), dict) else {}
        out["brain"] = brain_cfg
        out["generation_count"] = max(1, _to_int(out.get("generation_count"), 5))
        out["interval_sec"] = max(5, _to_int(out.get("interval_sec"), 30))
        out["max_wait_sec"] = max(60, _to_int(out.get("max_wait_sec"), 1800))
        out["max_seed_in_prompt"] = max(1, _to_int(out.get("max_seed_in_prompt"), 20))
        out["auth_refresh_interval_sec"] = max(60, _to_int(out.get("auth_refresh_interval_sec"), 900))
        out["operators_refresh_interval_sec"] = max(60, _to_int(out.get("operators_refresh_interval_sec"), 1800))
        out["generation_attempts"] = max(1, min(6, _to_int(out.get("generation_attempts"), 3)))
        out["mutation_multiplier"] = max(1, min(8, _to_int(out.get("mutation_multiplier"), 3)))
        out["simulation_concurrency"] = max(1, min(32, _to_int(out.get("simulation_concurrency"), 5)))
        out["max_operator_calls"] = max(1, min(64, _to_int(out.get("max_operator_calls"), 8)))
        out["error_notify_cooldown_sec"] = max(0, _to_int(out.get("error_notify_cooldown_sec"), 180))
        out["sharpe_abs_threshold"] = _to_float(out.get("sharpe_abs_threshold"), 1.0)
        out["fitness_threshold"] = _to_float(out.get("fitness_threshold"), 1.0)
        out["template_sharpe_threshold"] = _to_float(out.get("template_sharpe_threshold"), 1.58)
        out["include_patterns"] = _to_bool(out.get("include_patterns"), True)
        out["single_dataset_only"] = _to_bool(out.get("single_dataset_only"), True)
        out["seed_expressions"] = self._normalize_seed_exprs(out.get("seed_expressions"))
        out["fields"] = self._normalize_fields(out.get("fields"))
        out["context"] = out.get("context") if isinstance(out.get("context"), dict) else {}
        out["report_text"] = str(out.get("report_text") or "")
        out["notify_url"] = str(out.get("notify_url") or "").strip()
        out["cursor_file"] = str(out.get("cursor_file") or "runs/dream_alpha_cursor.json")
        out["seed_file"] = str(out.get("seed_file") or "runs/dream_alpha_seed_library.json")
        out["high_template_file"] = str(out.get("high_template_file") or "runs/dream_alpha_high_templates.jsonl")
        out["field_meta_cache_file"] = str(out.get("field_meta_cache_file") or "metadata/field_meta_cache.json")
        use_proxy_raw = out.get("use_proxy")
        if use_proxy_raw is None:
            use_proxy_raw = brain_cfg.get("use_proxy")
        out["use_proxy"] = _to_bool(use_proxy_raw, False)
        return out

    def _normalize_seed_exprs(self, raw: Any) -> List[str]:
        if raw is None:
            return []
        items: List[str] = []
        if isinstance(raw, str):
            for line in raw.splitlines():
                expr = line.strip()
                if expr:
                    items.append(expr)
        elif isinstance(raw, list):
            for item in raw:
                if isinstance(item, dict):
                    expr = _normalize_expression(str(item.get("expression") or ""))
                else:
                    expr = _normalize_expression(str(item or ""))
                if expr:
                    items.append(expr)
        return list(dict.fromkeys(items))

    def _normalize_fields(self, raw: Any) -> List[Dict[str, Any]]:
        fields: List[Dict[str, Any]] = []
        if not isinstance(raw, list):
            return fields
        for item in raw:
            if isinstance(item, dict):
                fid = str(item.get("id") or item.get("name") or "").strip()
                if not fid:
                    continue
                fields.append(
                    {
                        "id": fid,
                        "description": str(item.get("description") or ""),
                        "type": str(item.get("type") or ""),
                        "dataset_id": str(item.get("dataset_id") or item.get("datasetId") or ""),
                        "dataset_name": str(item.get("dataset_name") or item.get("datasetName") or ""),
                    }
                )
            else:
                fid = str(item or "").strip()
                if fid:
                    fields.append({"id": fid, "description": "", "type": "", "dataset_id": "", "dataset_name": ""})
        return fields

    def _public_cfg(self, cfg: Dict[str, Any]) -> Dict[str, Any]:
        return {
            "generation_count": cfg["generation_count"],
            "interval_sec": cfg["interval_sec"],
            "max_wait_sec": cfg["max_wait_sec"],
            "sharpe_abs_threshold": cfg["sharpe_abs_threshold"],
            "fitness_threshold": cfg["fitness_threshold"],
            "template_sharpe_threshold": cfg["template_sharpe_threshold"],
            "include_patterns": cfg["include_patterns"],
            "max_seed_in_prompt": cfg["max_seed_in_prompt"],
            "auth_refresh_interval_sec": cfg["auth_refresh_interval_sec"],
            "operators_refresh_interval_sec": cfg["operators_refresh_interval_sec"],
            "generation_attempts": cfg["generation_attempts"],
            "mutation_multiplier": cfg["mutation_multiplier"],
            "simulation_concurrency": cfg["simulation_concurrency"],
            "max_operator_calls": cfg["max_operator_calls"],
            "single_dataset_only": cfg["single_dataset_only"],
            "seed_file": cfg["seed_file"],
            "cursor_file": cfg["cursor_file"],
            "high_template_file": cfg["high_template_file"],
            "field_meta_cache_file": cfg["field_meta_cache_file"],
            "use_proxy": cfg["use_proxy"],
            "fields_count": len(cfg.get("fields") or []),
            "context": cfg.get("context") or {},
            "notify_url_set": bool(cfg.get("notify_url")),
        }

    def _persist_state_locked(self) -> None:
        try:
            cursor = self._state.setdefault("cursor", {})
            cursor["updated_at"] = _utc_now()
            payload = {
                "schema_version": "0.1",
                "updated_at": cursor["updated_at"],
                "running": bool(self._state.get("running")),
                "cursor": {
                    "cycle": max(0, _to_int(cursor.get("cycle"), 0)),
                    "candidate": max(0, _to_int(cursor.get("candidate"), 0)),
                    "accepted": max(0, _to_int(cursor.get("accepted"), 0)),
                    "high_template": max(0, _to_int(cursor.get("high_template"), 0)),
                    "error": max(0, _to_int(cursor.get("error"), 0)),
                    "updated_at": cursor["updated_at"],
                },
            }
            _write_json_atomic(self._cursor_file(), payload)
        except Exception as exc:
            logger.error("Failed to persist dream alpha cursor: %s", exc)

    def _append_event_locked(self, event: Dict[str, Any], max_events: int = 120) -> None:
        events = self._state.setdefault("recent_events", [])
        events.append(event)
        if len(events) > max_events:
            del events[:-max_events]

    def _inc_stat_locked(self, key: str, delta: int = 1) -> None:
        stats = self._state.setdefault("stats", {})
        stats[key] = int(stats.get(key, 0)) + delta

    def _inc_cursor_locked(self, key: str, delta: int = 1) -> None:
        cursor = self._state.setdefault("cursor", {})
        cursor[key] = int(cursor.get(key, 0)) + int(delta)
        cursor["updated_at"] = _utc_now()

    def _notify(self, title: str, body: str, force: bool = False) -> None:
        url = self._notify_url()
        if not url:
            return
        now_ts = time.time()
        if not force and title.startswith("ERROR"):
            cooldown = self._error_notify_cooldown()
            if cooldown > 0 and now_ts - self._last_error_notify_ts < cooldown:
                return
            self._last_error_notify_ts = now_ts

        msg = f"[DreamAlpha] {title}\n{body}"
        final_url = _build_notify_url(url, msg)
        if not final_url:
            return

        errors: List[str] = []
        # Default: direct/no-proxy. If explicitly enabled, try env-proxy first.
        use_proxy = _to_bool(self._cfg.get("use_proxy"), False)
        modes = ("env", "direct", "direct") if use_proxy else ("direct", "direct")
        for idx, mode in enumerate(modes):
            if idx > 0:
                time.sleep(0.6 * idx)
            try:
                with requests.Session() as sess:
                    sess.trust_env = (mode == "env")
                    resp = sess.get(final_url, timeout=(5, 10))
                if resp.status_code // 100 == 2:
                    return
                errors.append(f"{mode}: http {resp.status_code}")
            except Exception as exc:
                errors.append(f"{mode}: {exc}")

        # throttle transport warning logs to avoid noisy flooding
        if now_ts - self._last_notify_transport_warn_ts >= 60:
            self._last_notify_transport_warn_ts = now_ts
            logger.warning("Notification push failed after retries: %s", " | ".join(errors))

    def _normalize_seed_file(self, seed_file: Path) -> Dict[str, Any]:
        raw = _safe_read_json(seed_file, {})
        normalized = {
            "schema_version": "0.1",
            "updated_at": _utc_now(),
            "items": [],
        }
        items: List[Any]
        if isinstance(raw, dict):
            raw_items = raw.get("items")
            if isinstance(raw_items, list):
                items = raw_items
            elif isinstance(raw.get("seeds"), list):
                items = raw.get("seeds")
            else:
                items = []
        elif isinstance(raw, list):
            items = raw
        else:
            items = []

        seen = set()
        for item in items:
            if isinstance(item, dict):
                expr = _normalize_expression(str(item.get("expression") or ""))
                if not expr or expr in seen:
                    continue
                seen.add(expr)
                normalized["items"].append(
                    {
                        "expression": expr,
                        "name": str(item.get("name") or ""),
                        "logic": str(item.get("logic") or ""),
                        "sharpe": _to_float(item.get("sharpe"), 0.0),
                        "fitness": _to_float(item.get("fitness"), 0.0),
                        "created_at": str(item.get("created_at") or _utc_now()),
                        "source": str(item.get("source") or "seed_file"),
                        "alpha_id": str(item.get("alpha_id") or ""),
                        "simulation_id": str(item.get("simulation_id") or ""),
                    }
                )
            else:
                expr = _normalize_expression(str(item or ""))
                if expr and expr not in seen:
                    seen.add(expr)
                    normalized["items"].append(
                        {
                            "expression": expr,
                            "name": "",
                            "logic": "",
                            "sharpe": 0.0,
                            "fitness": 0.0,
                            "created_at": _utc_now(),
                            "source": "seed_file",
                            "alpha_id": "",
                            "simulation_id": "",
                        }
                    )
        _write_json_atomic(seed_file, normalized)
        return normalized

    def _seed_prompt_lines(self, seed_items: List[Dict[str, Any]], max_count: int) -> List[str]:
        enriched = []
        for item in seed_items:
            sharpe = _to_float(item.get("sharpe"), 0.0)
            fitness = _to_float(item.get("fitness"), 0.0)
            score = abs(sharpe) + max(fitness, 0.0)
            enriched.append((score, item))
        enriched.sort(key=lambda pair: pair[0], reverse=True)

        lines = []
        for _, item in enriched[:max_count]:
            expr = str(item.get("expression") or "").strip()
            if not expr:
                continue
            sharpe = _to_float(item.get("sharpe"), 0.0)
            fitness = _to_float(item.get("fitness"), 0.0)
            lines.append(f"- expr: {expr} | sharpe={sharpe:.3f} | fitness={fitness:.3f}")
        return lines

    def _append_high_template(self, event: Dict[str, Any]) -> None:
        path = self._high_template_file()
        path.parent.mkdir(parents=True, exist_ok=True)
        line = json.dumps(event, ensure_ascii=False)
        with path.open("a", encoding="utf-8") as f:
            f.write(line + "\n")

    def _build_settings_from_context(self, context: Dict[str, Any]) -> Dict[str, Any]:
        instrument = context.get("instrumentType") or context.get("instrument") or "EQUITY"
        region = context.get("region") or "USA"
        delay = _to_int(context.get("delay"), 1)
        universe = context.get("universe") or "TOP3000"
        settings = {
            "instrumentType": str(instrument),
            "region": str(region),
            "delay": int(delay),
            "universe": str(universe),
        }
        optional_keys = [
            "decay",
            "neutralization",
            "truncation",
            "pasteurization",
            "unitHandling",
            "nanHandling",
            "maxTrade",
            "maxPosition",
        ]
        for key in optional_keys:
            if key in context:
                settings[key] = context[key]
        return settings

    def _fetch_real_operators(self, client: BrainClient) -> List[Dict[str, Any]]:
        last_error = None
        for attempt in range(1, 4):
            try:
                raw = client.get_operators()
                # Try to crawl all pages if API returns paged results
                if isinstance(raw, dict) and isinstance(raw.get("results"), list):
                    results = list(raw.get("results") or [])
                    count = _to_int(raw.get("count"), len(results))
                    if count > len(results) and count < 10000 and len(results) > 0:
                        limit = max(len(results), 200)
                        offset = len(results)
                        while offset < count:
                            resp = client._request(
                                "GET",
                                f"{client.api_base}/operators",
                                params={"limit": limit, "offset": offset},
                            )
                            if resp.status_code // 100 != 2:
                                break
                            page = resp.json()
                            batch = page.get("results") if isinstance(page, dict) else None
                            if not isinstance(batch, list) or not batch:
                                break
                            results.extend(batch)
                            offset += len(batch)
                        raw = {"results": results}

                ops = _normalize_operators_payload(raw)
                if ops:
                    return ops
                last_error = RuntimeError("operators payload empty after normalization")
            except Exception as exc:
                last_error = exc
            if attempt < 3:
                time.sleep(1.5 * attempt)
        if last_error:
            raise RuntimeError(f"fetch operators failed: {last_error}")
        return []

    def _extract_context_settings(self, context: Dict[str, Any]) -> Dict[str, Any]:
        if not isinstance(context, dict):
            context = {}
        instrument = str(context.get("instrumentType") or context.get("instrument") or "EQUITY")
        region = str(context.get("region") or "USA")
        delay = max(0, _to_int(context.get("delay"), 1))
        universe = str(context.get("universe") or "TOP3000")
        return {
            "instrumentType": instrument,
            "region": region,
            "delay": delay,
            "universe": universe,
        }

    def _pick_best_field_meta(self, target_id: str, batch: Any) -> Dict[str, str]:
        if not isinstance(batch, list):
            return {}
        target = str(target_id or "").strip().lower()
        if not target:
            return {}
        fuzzy: Dict[str, str] = {}
        for item in batch:
            if not isinstance(item, dict):
                continue
            fid = str(item.get("id") or item.get("name") or item.get("field") or "").strip()
            if not fid:
                continue
            description = str(item.get("description") or item.get("desc") or item.get("full_name") or "").strip()
            ftype = str(item.get("type") or item.get("data_type") or "").strip()
            dataset_node = item.get("dataset")
            dataset_id = str(item.get("datasetId") or item.get("dataset_id") or "").strip()
            dataset_name = str(item.get("datasetName") or item.get("dataset_name") or "").strip()
            if isinstance(dataset_node, dict):
                if not dataset_id:
                    dataset_id = str(dataset_node.get("id") or dataset_node.get("datasetId") or dataset_node.get("dataset_id") or "").strip()
                if not dataset_name:
                    dataset_name = str(dataset_node.get("name") or dataset_node.get("datasetName") or dataset_node.get("dataset_name") or "").strip()
            out = {
                "id": fid,
                "description": description,
                "type": ftype,
                "dataset_id": dataset_id,
                "dataset_name": dataset_name,
            }
            if fid.lower() == target:
                return out
            fid_lower = fid.lower()
            if not fuzzy and (target in fid_lower or fid_lower in target):
                fuzzy = out
        return fuzzy

    def _fetch_field_meta(self, client: BrainClient, field_id: str, context: Dict[str, Any]) -> Dict[str, str]:
        settings = self._extract_context_settings(context)
        last_error: Optional[Exception] = None
        for attempt in range(1, 4):
            try:
                resp = client._request(
                    "GET",
                    f"{client.api_base}/data-fields",
                    params={
                        "instrumentType": settings["instrumentType"],
                        "region": settings["region"],
                        "delay": str(settings["delay"]),
                        "universe": settings["universe"],
                        "search": field_id,
                        "limit": "50",
                        "offset": "0",
                    },
                )
                if resp.status_code == 429:
                    if attempt < 3:
                        time.sleep(1.2 * attempt)
                        continue
                    raise RuntimeError("data-fields rate limited (429)")
                if resp.status_code // 100 != 2:
                    raise RuntimeError(f"data-fields query failed: {resp.status_code} {resp.text}")
                payload = resp.json()
                return self._pick_best_field_meta(field_id, payload.get("results"))
            except Exception as exc:
                last_error = exc
                if attempt < 3:
                    time.sleep(1.2 * attempt)
        if last_error:
            raise RuntimeError(f"fetch field meta failed for {field_id}: {last_error}")
        return {}

    def _enrich_fields_from_api(
        self,
        client: BrainClient,
        fields: List[Dict[str, Any]],
        context: Dict[str, Any],
        meta_cache: Dict[str, Dict[str, str]],
        local_cache: LocalFieldMetaCache,
        context_key: str,
    ) -> Dict[str, Any]:
        if not isinstance(fields, list) or not fields:
            return {"fields": [], "resolved": 0, "updated": 0, "failed": 0}
        out: List[Dict[str, Any]] = []
        resolved = 0
        updated = 0
        failed = 0
        cache_writes = 0
        for raw in fields:
            node = dict(raw) if isinstance(raw, dict) else {"id": str(raw or "")}
            fid = str(node.get("id") or node.get("name") or "").strip()
            if not fid:
                continue
            meta = meta_cache.get(fid)
            if meta is None:
                cached_meta = local_cache.get(context_key, fid)
                if cached_meta:
                    meta = cached_meta
                else:
                    try:
                        meta = self._fetch_field_meta(client, fid, context)
                        if meta:
                            local_cache.set(context_key, fid, meta)
                            cache_writes += 1
                    except Exception:
                        failed += 1
                        meta = {}
                meta_cache[fid] = meta
                # keep api pressure modest when field count is large
                time.sleep(0.05)
            if meta:
                resolved += 1
                next_desc = str(meta.get("description") or "").strip()
                next_type = str(meta.get("type") or "").strip()
                next_dataset_id = str(meta.get("dataset_id") or "").strip()
                next_dataset_name = str(meta.get("dataset_name") or "").strip()
                if next_desc and node.get("description") != next_desc:
                    node["description"] = next_desc
                    updated += 1
                if next_type and node.get("type") != next_type:
                    node["type"] = next_type
                    updated += 1
                if next_dataset_id and node.get("dataset_id") != next_dataset_id:
                    node["dataset_id"] = next_dataset_id
                    updated += 1
                if next_dataset_name and node.get("dataset_name") != next_dataset_name:
                    node["dataset_name"] = next_dataset_name
                    updated += 1
            node["id"] = fid
            node.setdefault("description", "")
            node.setdefault("type", "")
            node.setdefault("dataset_id", "")
            node.setdefault("dataset_name", "")
            out.append(node)
        if cache_writes > 0:
            local_cache.flush()
        return {"fields": out, "resolved": resolved, "updated": updated, "failed": failed, "cache_writes": cache_writes}

    def _run_loop(self) -> None:
        cfg = dict(self._cfg)
        seed_file = self._seed_file()
        stop_event = self._stop_event or threading.Event()
        failure_streak = 0
        auth_failure_streak = 0

        try:
            if not cfg.get("fields"):
                raise ValueError("DreamAlpha requires data fields (fields cannot be empty).")

            brain = cfg.get("brain") or {}
            if not isinstance(brain, dict):
                raise ValueError("brain config missing")
            username = str(brain.get("username") or "")
            password = str(brain.get("password") or "")
            api_base = str(brain.get("api_base") or "https://api.worldquantbrain.com")
            if not username or not password:
                raise ValueError("brain credentials missing")

            seed_payload = self._normalize_seed_file(seed_file)
            seed_items = seed_payload.get("items", [])

            for expr in cfg.get("seed_expressions") or []:
                if any(str(it.get("expression")) == expr for it in seed_items):
                    continue
                seed_items.append(
                    {
                        "expression": expr,
                        "name": "",
                        "logic": "bootstrap seed",
                        "sharpe": 0.0,
                        "fitness": 0.0,
                        "created_at": _utc_now(),
                        "source": "user_bootstrap",
                        "alpha_id": "",
                        "simulation_id": "",
                    }
                )
            seed_payload["items"] = seed_items
            seed_payload["updated_at"] = _utc_now()
            _write_json_atomic(seed_file, seed_payload)

            generator = AlphaGenerator(brain)
            patterns = get_all_patterns() if _to_bool(cfg.get("include_patterns"), True) else None
            operators: Optional[List[Dict[str, Any]]] = None
            operators_last_fetch_ts = 0.0
            auth_last_ts = 0.0
            auth_ok = False
            fields_for_generation: List[Dict[str, Any]] = [dict(it) for it in (cfg.get("fields") or [])]
            fields_meta_cache: Dict[str, Dict[str, str]] = {}
            fields_local_cache = LocalFieldMetaCache(cfg.get("field_meta_cache_file"))
            fields_context_key = build_field_meta_context_key(cfg.get("context") or {})
            fields_last_refresh_ts = 0.0
            fields_refresh_interval_sec = 6 * 3600
            auth_refresh_interval_sec = int(cfg.get("auth_refresh_interval_sec", 900))
            operators_refresh_interval_sec = int(cfg.get("operators_refresh_interval_sec", 1800))

            client = BrainClient(
                username=username,
                password=password,
                api_base=api_base,
                use_proxy=cfg.get("use_proxy", False),
            )

            self._notify(
                "START",
                (
                    f"DreamAlpha loop started\n"
                    f"fields={len(cfg.get('fields') or [])}, gen_count={cfg.get('generation_count')}, "
                    f"sim_concurrency={cfg.get('simulation_concurrency')}, interval={cfg.get('interval_sec')}s"
                ),
                force=True,
            )

            while not stop_event.is_set():
                now_ts = time.time()

                need_auth = (not auth_ok) or ((now_ts - auth_last_ts) >= auth_refresh_interval_sec)
                if need_auth:
                    auth_err: Optional[Exception] = None
                    for attempt in range(1, 4):
                        if stop_event.is_set():
                            break
                        try:
                            client.login()
                            auth_ok = True
                            auth_last_ts = time.time()
                            auth_failure_streak = 0
                            with self._lock:
                                self._state["last_error"] = ""
                                self._append_event_locked(
                                    {
                                        "at": _utc_now(),
                                        "type": "auth",
                                        "stage": "refresh",
                                        "attempt": attempt,
                                    }
                                )
                                self._persist_state_locked()
                            break
                        except Exception as exc:
                            auth_err = exc
                            auth_ok = False
                            if attempt < 3:
                                for _ in range(min(12, 2 ** attempt)):
                                    if stop_event.is_set():
                                        break
                                    time.sleep(1)

                    if not auth_ok and auth_err is not None:
                        auth_failure_streak += 1
                        with self._lock:
                            self._inc_stat_locked("errors", 1)
                            self._inc_cursor_locked("error", 1)
                            self._state["last_error"] = f"auth failed: {auth_err}"
                            self._append_event_locked(
                                {
                                    "at": _utc_now(),
                                    "type": "error",
                                    "stage": "auth",
                                    "message": str(auth_err),
                                }
                            )
                            self._persist_state_locked()
                        self._notify("ERROR auth", str(auth_err))
                        backoff = min(300, max(5, cfg["interval_sec"] * (2 ** min(auth_failure_streak, 6))))
                        for _ in range(backoff):
                            if stop_event.is_set():
                                break
                            time.sleep(1)
                        continue

                if (now_ts - fields_last_refresh_ts) >= fields_refresh_interval_sec:
                    try:
                        enriched = self._enrich_fields_from_api(
                            client=client,
                            fields=fields_for_generation,
                            context=cfg.get("context") or {},
                            meta_cache=fields_meta_cache,
                            local_cache=fields_local_cache,
                            context_key=fields_context_key,
                        )
                        next_fields = enriched.get("fields") if isinstance(enriched, dict) else None
                        if isinstance(next_fields, list) and next_fields:
                            fields_for_generation = next_fields
                        fields_last_refresh_ts = now_ts
                        with self._lock:
                            self._append_event_locked(
                                {
                                    "at": _utc_now(),
                                    "type": "fields",
                                    "stage": "api_refresh",
                                    "resolved": _to_int(enriched.get("resolved"), 0) if isinstance(enriched, dict) else 0,
                                    "updated": _to_int(enriched.get("updated"), 0) if isinstance(enriched, dict) else 0,
                                    "failed": _to_int(enriched.get("failed"), 0) if isinstance(enriched, dict) else 0,
                                    "cache_writes": _to_int(enriched.get("cache_writes"), 0) if isinstance(enriched, dict) else 0,
                                }
                            )
                            self._persist_state_locked()
                    except Exception as exc:
                        fields_last_refresh_ts = now_ts
                        with self._lock:
                            self._append_event_locked(
                                {
                                    "at": _utc_now(),
                                    "type": "warn",
                                    "stage": "fields_refresh",
                                    "message": str(exc),
                                }
                            )
                            self._persist_state_locked()
                        self._notify("ERROR fields_refresh", str(exc))

                if (now_ts - operators_last_fetch_ts) >= operators_refresh_interval_sec:
                    try:
                        refreshed_ops = self._fetch_real_operators(client)
                        if refreshed_ops:
                            operators = refreshed_ops
                            operators_last_fetch_ts = now_ts
                            with self._lock:
                                self._append_event_locked(
                                    {
                                        "at": _utc_now(),
                                        "type": "operators",
                                        "count": len(refreshed_ops),
                                        "stage": "refresh",
                                    }
                                )
                                self._persist_state_locked()
                    except Exception as exc:
                        operators_last_fetch_ts = now_ts
                        with self._lock:
                            self._append_event_locked(
                                {
                                    "at": _utc_now(),
                                    "type": "warn",
                                    "stage": "operators_refresh",
                                    "message": str(exc),
                                }
                            )
                            self._persist_state_locked()
                        self._notify("ERROR operators_refresh", str(exc))
                        # Keep previous operators and continue working.

                cycle_started_at = _utc_now()
                with self._lock:
                    self._state["last_cycle_at"] = cycle_started_at
                    self._inc_stat_locked("cycles", 1)
                    self._inc_cursor_locked("cycle", 1)
                    self._persist_state_locked()

                known_exprs = set(str(x) for x in self._state.get("seen_expressions", []))
                known_exprs.update(str(item.get("expression") or "") for item in seed_items)
                known_signatures = set(str(x) for x in self._state.get("seen_signatures", []))

                field_ids_for_detection = [
                    str(item.get("id") or "").strip()
                    for item in fields_for_generation
                    if isinstance(item, dict) and str(item.get("id") or "").strip()
                ]
                field_to_dataset: Dict[str, str] = {}
                for item in fields_for_generation:
                    if not isinstance(item, dict):
                        continue
                    fid = str(item.get("id") or "").strip()
                    if not fid:
                        continue
                    dataset_key = str(item.get("dataset_id") or item.get("dataset_name") or "").strip()
                    if dataset_key:
                        field_to_dataset[fid] = dataset_key

                for item in seed_items:
                    sig = _expression_structure_signature(str(item.get("expression") or ""), field_ids_for_detection)
                    if sig:
                        known_signatures.add(sig)

                seed_lines = self._seed_prompt_lines(seed_items, cfg.get("max_seed_in_prompt", 20))
                report_text = cfg.get("report_text") or ""
                hint_lines = [
                    "Seed Library Signals:",
                    *seed_lines,
                    "Constraint: generate novel expressions different from prior seeds.",
                    "Mutation goal: maximize structural diversity (operators/lookbacks/constants).",
                ]
                if _to_bool(cfg.get("single_dataset_only"), True):
                    hint_lines.append("Hard constraint: each expression can only use fields from ONE dataset.")
                combined_report = (report_text + "\n\n" + "\n".join(hint_lines)).strip()

                generation_context = dict(cfg.get("context") or {})
                generation_context["mutation_mode"] = "max"
                generation_context["single_dataset_only"] = _to_bool(cfg.get("single_dataset_only"), True)
                generation_context["max_operator_calls"] = int(cfg.get("max_operator_calls", 8))

                target_count = int(cfg.get("generation_count", 5))
                generation_attempts = int(cfg.get("generation_attempts", 3))
                mutation_multiplier = int(cfg.get("mutation_multiplier", 3))
                request_count = max(target_count, target_count * mutation_multiplier)
                raw_generated_total = 0
                single_dataset_skipped = 0
                structure_skipped = 0
                operator_limit_skipped = 0
                generated: List[Dict[str, Any]] = []
                candidate_exprs = set()
                candidate_sigs = set()
                generate_error: Optional[Exception] = None

                for attempt in range(generation_attempts):
                    if stop_event.is_set():
                        break
                    try:
                        generated_raw = generator.generate_alphas(
                            fields=fields_for_generation,
                            report_text=combined_report,
                            patterns=patterns,
                            context=generation_context,
                            operators=operators,
                            count=request_count,
                        )
                        batch = generated_raw if isinstance(generated_raw, list) else []
                        raw_generated_total += len(batch)
                    except Exception as exc:
                        generate_error = exc
                        if attempt + 1 < generation_attempts:
                            time.sleep(min(3, attempt + 1))
                        continue

                    for candidate in batch:
                        if not isinstance(candidate, dict):
                            continue
                        expr = _normalize_expression(str(candidate.get("expression") or ""))
                        if not expr:
                            continue
                        op_calls = _count_operator_calls(expr)
                        if op_calls > int(cfg.get("max_operator_calls", 8)):
                            operator_limit_skipped += 1
                            continue
                        used_fields = _extract_expression_fields(expr, field_ids_for_detection)
                        if not used_fields:
                            single_dataset_skipped += 1
                            continue

                        if _to_bool(cfg.get("single_dataset_only"), True):
                            datasets = set(
                                str(field_to_dataset.get(fid) or "").strip()
                                for fid in used_fields
                                if str(field_to_dataset.get(fid) or "").strip()
                            )
                            unresolved = [fid for fid in used_fields if not str(field_to_dataset.get(fid) or "").strip()]
                            if len(datasets) > 1 or (len(used_fields) > 1 and unresolved):
                                single_dataset_skipped += 1
                                continue

                        sig = _expression_structure_signature(expr, field_ids_for_detection)
                        if sig and (sig in known_signatures or sig in candidate_sigs):
                            structure_skipped += 1
                            continue
                        if expr in known_exprs or expr in candidate_exprs:
                            continue

                        normalized_candidate = dict(candidate)
                        normalized_candidate["expression"] = expr
                        normalized_candidate["_structure_sig"] = sig
                        normalized_candidate["_used_fields"] = used_fields
                        normalized_candidate["_operator_calls"] = op_calls
                        generated.append(normalized_candidate)
                        candidate_exprs.add(expr)
                        if sig:
                            candidate_sigs.add(sig)
                        if len(generated) >= target_count:
                            break
                    if len(generated) >= target_count:
                        break

                if not generated and generate_error is not None:
                    failure_streak += 1
                    with self._lock:
                        self._state["last_error"] = f"generate failed: {generate_error}"
                        self._inc_stat_locked("errors", 1)
                        self._inc_cursor_locked("error", 1)
                        self._append_event_locked(
                            {
                                "at": _utc_now(),
                                "type": "error",
                                "stage": "generate",
                                "message": str(generate_error),
                            }
                        )
                        self._persist_state_locked()
                    self._notify("ERROR generate", str(generate_error))
                    backoff = min(300, max(5, cfg["interval_sec"] * (2 ** min(failure_streak, 6))))
                    for _ in range(backoff):
                        if stop_event.is_set():
                            break
                        time.sleep(1)
                    continue

                failure_streak = 0
                with self._lock:
                    self._inc_stat_locked("raw_generated", raw_generated_total)
                    self._inc_stat_locked("generated", len(generated))
                    self._inc_stat_locked("single_dataset_skipped", single_dataset_skipped)
                    self._inc_stat_locked("structure_skipped", structure_skipped)
                    self._inc_stat_locked("operator_limit_skipped", operator_limit_skipped)
                    if not generated:
                        self._append_event_locked(
                            {
                                "at": _utc_now(),
                                "type": "warn",
                                "stage": "generate_filter",
                                "message": "no candidate passed mutation/single-dataset/operator constraints",
                            }
                        )
                    self._persist_state_locked()

                settings = self._build_settings_from_context(cfg.get("context") or {})
                max_wait_sec = int(cfg.get("max_wait_sec", 1800))
                sim_concurrency = int(cfg.get("simulation_concurrency", 5))
                dispatch_tasks: List[Dict[str, Any]] = []

                for idx, candidate in enumerate(generated):
                    if stop_event.is_set():
                        break
                    if not isinstance(candidate, dict):
                        continue
                    raw_expr = str(candidate.get("expression") or "")
                    expr = _normalize_expression(raw_expr)
                    if not expr:
                        continue
                    if raw_expr.strip() != expr:
                        with self._lock:
                            self._append_event_locked(
                                {
                                    "at": _utc_now(),
                                    "type": "normalize",
                                    "from": raw_expr[:200],
                                    "to": expr[:200],
                                }
                            )
                            self._persist_state_locked()
                    expr_sig = str(candidate.get("_structure_sig") or _expression_structure_signature(expr, field_ids_for_detection))
                    if expr in known_exprs:
                        with self._lock:
                            self._inc_stat_locked("duplicates_skipped", 1)
                            self._append_event_locked(
                                {
                                    "at": _utc_now(),
                                    "type": "skip",
                                    "stage": "dedup",
                                    "expression": expr[:200],
                                }
                            )
                            self._persist_state_locked()
                        continue
                    if expr_sig and expr_sig in known_signatures:
                        with self._lock:
                            self._inc_stat_locked("structure_skipped", 1)
                            self._append_event_locked(
                                {
                                    "at": _utc_now(),
                                    "type": "skip",
                                    "stage": "structure",
                                    "signature": expr_sig[:200],
                                }
                            )
                            self._persist_state_locked()
                        continue

                    known_exprs.add(expr)
                    if expr_sig:
                        known_signatures.add(expr_sig)
                    with self._lock:
                        seen = self._state.setdefault("seen_expressions", [])
                        seen.append(expr)
                        if len(seen) > 20000:
                            del seen[:-20000]
                        if expr_sig:
                            sigs = self._state.setdefault("seen_signatures", [])
                            sigs.append(expr_sig)
                            if len(sigs) > 30000:
                                del sigs[:-30000]
                        self._persist_state_locked()

                    payload_item = {"expression": expr, "settings": settings}
                    sim_payload = build_brain_payload(payload_item)
                    with self._lock:
                        self._inc_cursor_locked("candidate", 1)
                        self._persist_state_locked()

                    dispatch_tasks.append(
                        {
                            "idx": idx,
                            "candidate": candidate,
                            "expression": expr,
                            "operator_calls": int(candidate.get("_operator_calls") or _count_operator_calls(expr)),
                            "sim_payload": sim_payload,
                        }
                    )

                if dispatch_tasks:
                    max_workers = max(1, min(sim_concurrency, len(dispatch_tasks)))

                    def _simulate_task(task: Dict[str, Any]) -> Dict[str, Any]:
                        expr = str(task.get("expression") or "")
                        worker_client = BrainClient(
                            username=username,
                            password=password,
                            api_base=api_base,
                            use_proxy=cfg.get("use_proxy", False),
                        )
                        outcome = worker_client.simulate(
                            task.get("sim_payload") or {},
                            max_wait=max_wait_sec,
                            stop_event=stop_event,
                        )
                        wrapped = {
                            "alpha_id": outcome.alpha_id,
                            "alpha": outcome.result,
                            "simulation_id": outcome.simulation_id,
                        }
                        metrics = _extract_metrics(wrapped)
                        sharpe = metrics["sharpe"]
                        fitness = metrics["fitness"]
                        accepted = abs(sharpe) > float(cfg["sharpe_abs_threshold"]) and fitness > float(cfg["fitness_threshold"])
                        high_template = sharpe > float(cfg["template_sharpe_threshold"]) and fitness > float(cfg["fitness_threshold"])
                        return {
                            "ok": True,
                            "idx": int(task.get("idx", 0)),
                            "candidate": task.get("candidate") if isinstance(task.get("candidate"), dict) else {},
                            "expression": expr,
                            "operator_calls": int(task.get("operator_calls", 0)),
                            "alpha_id": str(outcome.alpha_id),
                            "simulation_id": str(outcome.simulation_id),
                            "sharpe": sharpe,
                            "fitness": fitness,
                            "accepted": accepted,
                            "high_template": high_template,
                        }

                    with ThreadPoolExecutor(max_workers=max_workers) as executor:
                        future_map = {executor.submit(_simulate_task, task): task for task in dispatch_tasks}
                        for fut in as_completed(future_map):
                            task = future_map[fut]
                            expr = str(task.get("expression") or "")
                            candidate = task.get("candidate") if isinstance(task.get("candidate"), dict) else {}
                            try:
                                result = fut.result()
                            except Exception as exc:
                                with self._lock:
                                    self._inc_stat_locked("errors", 1)
                                    self._inc_cursor_locked("error", 1)
                                    self._state["last_error"] = f"simulate failed: {exc}"
                                    self._append_event_locked(
                                        {
                                            "at": _utc_now(),
                                            "type": "error",
                                            "stage": "simulate",
                                            "expression": expr[:200],
                                            "message": str(exc),
                                        }
                                    )
                                    self._persist_state_locked()
                                self._notify("ERROR simulate", f"{exc}\nexpr={expr[:400]}")
                                continue

                            if not isinstance(result, dict) or not result.get("ok"):
                                with self._lock:
                                    self._inc_stat_locked("errors", 1)
                                    self._inc_cursor_locked("error", 1)
                                    self._state["last_error"] = "simulate failed: unknown result"
                                    self._append_event_locked(
                                        {
                                            "at": _utc_now(),
                                            "type": "error",
                                            "stage": "simulate",
                                            "expression": expr[:200],
                                            "message": "unknown simulation result",
                                        }
                                    )
                                    self._persist_state_locked()
                                self._notify("ERROR simulate", f"unknown simulation result\nexpr={expr[:400]}")
                                continue

                            sharpe = float(result.get("sharpe", 0.0))
                            fitness = float(result.get("fitness", 0.0))
                            accepted = bool(result.get("accepted"))
                            high_template = bool(result.get("high_template"))
                            alpha_id = str(result.get("alpha_id") or "")
                            simulation_id = str(result.get("simulation_id") or "")
                            operator_calls = int(result.get("operator_calls", 0))

                            event = {
                                "at": _utc_now(),
                                "type": "result",
                                "idx": int(result.get("idx", 0)),
                                "name": str(candidate.get("name") or ""),
                                "expression": expr,
                                "logic": str(candidate.get("logic") or ""),
                                "alpha_id": alpha_id,
                                "simulation_id": simulation_id,
                                "sharpe": sharpe,
                                "fitness": fitness,
                                "operator_calls": operator_calls,
                                "accepted": accepted,
                                "high_template": high_template,
                            }

                            with self._lock:
                                self._inc_stat_locked("simulated", 1)
                                self._append_event_locked(event)
                                if accepted:
                                    self._inc_stat_locked("accepted", 1)
                                    self._inc_cursor_locked("accepted", 1)
                                if high_template:
                                    self._inc_stat_locked("high_templates", 1)
                                    self._inc_cursor_locked("high_template", 1)
                                self._state["last_error"] = ""
                                self._persist_state_locked()

                            if accepted:
                                if not any(str(it.get("expression") or "") == expr for it in seed_items):
                                    seed_items.append(
                                        {
                                            "expression": expr,
                                            "name": str(candidate.get("name") or ""),
                                            "logic": str(candidate.get("logic") or ""),
                                            "sharpe": sharpe,
                                            "fitness": fitness,
                                            "created_at": _utc_now(),
                                            "source": "dream_alpha_loop",
                                            "alpha_id": alpha_id,
                                            "simulation_id": simulation_id,
                                        }
                                    )
                                    seed_payload["items"] = seed_items
                                    seed_payload["updated_at"] = _utc_now()
                                    _write_json_atomic(seed_file, seed_payload)

                            if high_template:
                                template_event = {
                                    "at": _utc_now(),
                                    "expression": expr,
                                    "name": str(candidate.get("name") or ""),
                                    "logic": str(candidate.get("logic") or ""),
                                    "sharpe": sharpe,
                                    "fitness": fitness,
                                    "alpha_id": alpha_id,
                                    "simulation_id": simulation_id,
                                }
                                self._append_high_template(template_event)
                                self._notify(
                                    "HIGH_TEMPLATE",
                                    (
                                        f"sharpe={sharpe:.3f}, fitness={fitness:.3f}\n"
                                        f"alpha={alpha_id}\nexpr={expr[:600]}"
                                    ),
                                    force=True,
                                )

                for _ in range(int(cfg["interval_sec"])):
                    if stop_event.is_set():
                        break
                    time.sleep(1)

        except Exception as exc:
            logger.error("DreamAlpha loop fatal error: %s", exc)
            with self._lock:
                self._inc_stat_locked("errors", 1)
                self._inc_cursor_locked("error", 1)
                self._state["last_error"] = str(exc)
                self._append_event_locked(
                    {
                        "at": _utc_now(),
                        "type": "error",
                        "stage": "fatal",
                        "message": str(exc),
                    }
                )
                self._persist_state_locked()
            self._notify("ERROR fatal", str(exc), force=True)
        finally:
            with self._lock:
                self._state["running"] = False
                self._state["stopping"] = False
                self._state["stopped_at"] = _utc_now()
                self._persist_state_locked()
                self._thread = None
            self._notify("STOP", f"DreamAlpha loop stopped at {self._state.get('stopped_at')}", force=True)
