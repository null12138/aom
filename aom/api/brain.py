from __future__ import annotations

import time
import threading
import logging
from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Callable, TYPE_CHECKING
from concurrent.futures import ThreadPoolExecutor

import requests

DEFAULT_API_BASE = "https://api.worldquantbrain.com"
logger = logging.getLogger("BrainClient")


def _as_bool(value: Any, default: bool = False) -> bool:
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

class BrainApiError(RuntimeError):
    def __init__(self, message, status_code=None):
        super().__init__(message)
        self.status_code = status_code

class BrainAuthError(BrainApiError): pass

@dataclass
class SimulationOutcome:
    simulation_id: str
    alpha_id: str
    result: Dict[str, Any]

class BrainClient:
    def __init__(
        self,
        username: str,
        password: str,
        api_base: str = DEFAULT_API_BASE,
        timeout: int = 30,
        use_proxy: bool = False,
    ):
        self.username = username
        self.password = password
        self.api_base = api_base or DEFAULT_API_BASE
        self.session = requests.Session()
        self.use_proxy = _as_bool(use_proxy, False)
        # Default: do not read HTTP(S)_PROXY/ALL_PROXY from environment.
        self.session.trust_env = self.use_proxy
        self.timeout = timeout
        self._login_lock = threading.Lock()

    def _request(self, method: str, url: str, **kwargs) -> requests.Response:
        if "timeout" not in kwargs:
            kwargs["timeout"] = self.timeout
            
        resp = self.session.request(method, url, **kwargs)
        
        # If 401, try to re-login once (avoid recursion during login)
        if resp.status_code == 401 and "/authentication" not in url:
            logger.info("Session expired (401), re-authenticating...")
            self.login()
            resp = self.session.request(method, url, **kwargs)
            
        return resp

    @staticmethod
    def _simulation_id_from_location(location: str) -> str:
        raw = str(location or "").strip()
        if not raw:
            return ""
        if "/" not in raw:
            return raw
        return raw.rstrip("/").split("/")[-1]

    def login(self) -> None:
        with self._login_lock:
            resp = self.session.post(f"{self.api_base}/authentication", auth=(self.username, self.password), timeout=self.timeout)
            if resp.status_code == 201: return
            if resp.status_code == 401 and resp.headers.get("WWW-Authenticate") == "persona":
                raise BrainAuthError("persona authentication required", status_code=401)
            raise BrainAuthError(f"authentication failed: {resp.status_code} {resp.text}", status_code=resp.status_code)

    def start_simulation(self, payload: Dict[str, Any]) -> requests.Response:
        resp = self._request("POST", f"{self.api_base}/simulations", json=payload)
        if resp.status_code // 100 != 2:
            raise BrainApiError(f"simulation start failed: {resp.status_code} {resp.text}", status_code=resp.status_code)
        return resp

    def poll_simulation(
        self, 
        location: str, 
        max_wait: int = 1800, 
        stop_event: Optional[threading.Event] = None,
        on_heartbeat: Optional[Callable[[int], None]] = None
    ) -> Dict[str, Any]:
        """增强版轮询：更灵敏的响应和心跳"""
        start = time.time()
        url = location if location.startswith("http") else f"{self.api_base}{location}"
        simulation_id = self._simulation_id_from_location(url)
        
        # 立即触发首次心跳
        if on_heartbeat: on_heartbeat(0)
        
        retry_count = 0
        while True:
            if stop_event and stop_event.is_set():
                raise BrainApiError("任务已被用户手动中断")
            
            elapsed = int(time.time() - start)
            if elapsed > max_wait:
                status_text = "UNKNOWN"
                detail_msg = ""
                try:
                    latest = self._request("GET", url)
                    if latest.status_code // 100 == 2:
                        latest_data = latest.json()
                        status_text = str(latest_data.get("status") or "UNKNOWN")
                        detail_msg = str(latest_data.get("message") or "").strip()
                except Exception:
                    pass
                extra = f"simulation_id={simulation_id or '-'} status={status_text}"
                if detail_msg:
                    extra += f" message={detail_msg}"
                raise BrainApiError(f"仿真超时 ({max_wait}s) | {extra}")

            try:
                resp = self._request("GET", url)
                if resp.status_code == 429: # Rate limit
                    wait_time = int(resp.headers.get("Retry-After", 10))
                    time.sleep(wait_time)
                    continue
                    
                if resp.status_code // 100 != 2:
                    raise BrainApiError(
                        f"poll failed: {resp.status_code} {resp.text} | simulation_id={simulation_id or '-'}",
                        status_code=resp.status_code,
                    )

                data = resp.json()
                status = data.get("status")
                
                if status == "COMPLETE": return data
                if status in ("ERROR", "CANCELLED", "CANCELED"):
                    msg = data.get("message", status)
                    raise BrainApiError(f"Brain 节点任务终止: {msg} | simulation_id={simulation_id or '-'}")

                # 触发心跳更新 UI
                if on_heartbeat:
                    on_heartbeat(elapsed)

            except requests.RequestException as e:
                logger.warning(f"Network error during polling: {e}")
                time.sleep(5)
                continue

            # 动态调整休眠时间：初期快，后期慢
            sleep_total = 2 if retry_count < 5 else 10
            retry_count += 1
            
            for _ in range(sleep_total):
                if stop_event and stop_event.is_set(): break
                time.sleep(1)
        
    def get_simulation_url(self, simulation_id: str) -> str:
        """生成 WQ Brain 官网的模拟详情直达链接"""
        if not simulation_id: return "https://www.worldquantbrain.com/alpha-design/simulations"
        return f"https://www.worldquantbrain.com/alpha-design/simulations?id={simulation_id}"

    def simulate(self, payload: Dict[str, Any], max_wait: int = 1800, stop_event: Optional[threading.Event] = None, on_heartbeat: Optional[Callable[[int], None]] = None) -> SimulationOutcome:
        resp = self.start_simulation(payload)
        loc = resp.headers.get("Location") or resp.headers.get("location")
        if not loc: raise BrainApiError("missing location header")
        
        sim_id = loc.split("/")[-1]
        logger.info(f"回测已启动: {self.get_simulation_url(sim_id)}")

        progress = self.poll_simulation(loc, max_wait=max_wait, stop_event=stop_event, on_heartbeat=on_heartbeat)
        alpha_id = progress.get("alpha")
        if not alpha_id:
            raise BrainApiError(f"no alpha id returned: {progress} | simulation_id={sim_id}")
        
        result = self.get_alpha(str(alpha_id))
        return SimulationOutcome(str(progress.get("id", sim_id)), str(alpha_id), result)

    def simulate_multiple(self, payload: Any, max_wait: int = 1800, stop_event: Optional[threading.Event] = None, on_heartbeat: Optional[Callable[[int], None]] = None) -> List[SimulationOutcome]:
        """支持并发获取结果的多重回测"""
        resp = self.start_simulation(payload)
        loc = resp.headers.get("Location") or resp.headers.get("location")
        if not loc: raise BrainApiError("missing location header")
        
        parent_sim_id = loc.split("/")[-1]
        logger.info(f"多重回测已启动 (Bundle): {self.get_simulation_url(parent_sim_id)}")

        progress = self.poll_simulation(loc, max_wait=max_wait, stop_event=stop_event, on_heartbeat=on_heartbeat)
        
        # 提取子任务结果
        alpha_ids = []
        children = progress.get("children", [])
        
        # 优先从 parent 直接获取 alpha 列表 (如果存在)
        parent_alphas = progress.get("alpha", [])
        if parent_alphas:
            if isinstance(parent_alphas, list): alpha_ids = parent_alphas
            else: alpha_ids = [parent_alphas]
        
        # 如果 parent 没带或者没带全，且有 children，则并发获取 children 的 alpha ID
        if not alpha_ids and children:
            if on_heartbeat: on_heartbeat(-1) # 特殊标记：正在拉取子任务 ID
            with ThreadPoolExecutor(max_workers=min(len(children), 20)) as executor:
                child_results = list(executor.map(self.get_simulation, children))
                for child_data in child_results:
                    aid = child_data.get("alpha")
                    if aid: alpha_ids.append(aid)

        if not alpha_ids:
            # 最后的保命检查：如果确实没拿到 ID，报错让上层重试或降级
            raise BrainApiError(
                f"Multiple simulation produced no alpha IDs. Status: {progress.get('status')} | simulation_id={parent_sim_id}"
            )

        # 并发获取所有 Alpha 的详细指标，极大缩短心跳停止后的卡顿
        if on_heartbeat: on_heartbeat(-2) # 特殊标记：正在拉取指标详情
        
        results = []
        with ThreadPoolExecutor(max_workers=min(len(alpha_ids), 20)) as executor:
            outcomes = list(executor.map(lambda aid: SimulationOutcome(str(progress.get("id", "")), str(aid), self.get_alpha(str(aid))), alpha_ids))
            results.extend(outcomes)
            
        return results

    def get_alpha(self, alpha_id: str) -> Dict[str, Any]:
        resp = self._request("GET", f"{self.api_base}/alphas/{alpha_id}")
        return resp.json()

    def get_submission_check(self, alpha_id: str) -> Dict[str, Any]:
        endpoints = [
            ("GET", f"{self.api_base}/alphas/{alpha_id}/check", None),
            ("GET", f"{self.api_base}/alphas/{alpha_id}/checks", None),
            ("GET", f"{self.api_base}/alphas/{alpha_id}/submission-check", None),
            ("POST", f"{self.api_base}/alphas/{alpha_id}/check", {}),
        ]
        last_error: Optional[str] = None
        for method, url, payload in endpoints:
            try:
                kwargs: Dict[str, Any] = {}
                if payload is not None:
                    kwargs["json"] = payload
                resp = self._request(method, url, **kwargs)
                if resp.status_code // 100 == 2:
                    data = resp.json()
                    return data if isinstance(data, dict) else {"raw": data}
                if resp.status_code in (404, 405):
                    last_error = f"{resp.status_code} {resp.text}"
                    continue
                raise BrainApiError(
                    f"submission check failed: {resp.status_code} {resp.text}",
                    status_code=resp.status_code,
                )
            except Exception as exc:
                last_error = str(exc)
        raise BrainApiError(f"submission check endpoint unavailable for alpha={alpha_id}: {last_error}")

    def get_simulation(self, sid: str) -> Dict[str, Any]:
        resp = self._request("GET", f"{self.api_base}/simulations/{sid}")
        return resp.json()

    def get_operators(self) -> Dict[str, Any]:
        resp = self._request("GET", f"{self.api_base}/operators")
        return resp.json()

    def get_settings_options(self) -> Dict[str, Any]:
        resp = self._request("OPTIONS", f"{self.api_base}/simulations")
        return resp.json().get("actions", {}).get("POST", {}).get("settings", {}).get("children", {})

    def get_datafields(self, region="USA", universe="TOP3000", search="", limit=10, **kwargs) -> Dict[str, Any]:
        # Handle extra kwargs that might be passed (like instrument_type, dataset_id, data_type, delay)
        params = {
            "region": region,
            "universe": universe,
            "search": search,
            "limit": limit
        }
        params.update(kwargs)
        # requests will handle dict in params= correctly
        resp = self._request("GET", f"{self.api_base}/data-fields", params=params)
        return resp.json()
