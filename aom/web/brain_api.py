from __future__ import annotations

import json
import time
from typing import Any, Dict

from ..config import ConfigError, load_config
from ..api.brain import BrainClient, BrainAuthError


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


def load_brain_config() -> Dict[str, Any]:
    cfg, _ = load_config()
    brain = cfg.get("brain", {})
    if not isinstance(brain, dict):
        raise ConfigError("brain config must be a table")
    
    # 基础 Brain 认证
    username = brain.get("username")
    password = brain.get("password")
    if not username or not password:
        raise ConfigError("brain username/password missing in config")

    use_proxy = _as_bool(brain.get("use_proxy"), False)
    llm_use_proxy = _as_bool(brain.get("llm_use_proxy"), use_proxy)
    
    # 返回所有 brain 块下的配置（包含 openai_api_key, gemini_api_key 等）
    return {
        "api_base": "https://api.worldquantbrain.com",
        **brain,
        "username": str(username),
        "password": str(password),
        "use_proxy": use_proxy,
        "llm_use_proxy": llm_use_proxy,
    }


def extract_choice_values(node: Any) -> list[str]:
    if not isinstance(node, dict):
        return []
    choices = node.get("choices") or node.get("values") or []
    values: list[str] = []
    for item in choices:
        if isinstance(item, dict):
            val = item.get("value")
            if val is None:
                val = item.get("id") or item.get("name") or item.get("key")
            if val is None:
                continue
            values.append(str(val))
        else:
            values.append(str(item))
    return values


def fetch_datafield_types(
    username: str,
    password: str,
    api_base: str,
    instrument_type: str,
    region: str,
    delay: int,
    universe: str,
    use_proxy: bool = False,
) -> list[str]:
    client = BrainClient(username=username, password=password, api_base=api_base, use_proxy=use_proxy)
    try:
        client.login()
    except BrainAuthError as exc:
        raise ValueError(str(exc)) from exc
    params = {
        "instrumentType": instrument_type,
        "region": region,
        "delay": str(delay),
        "universe": universe,
        "limit": "200",
        "offset": "0",
    }
    try:
        resp = client.session.get(f"{client.api_base}/data-fields", params=params, timeout=client.timeout)
    except Exception as exc:
        raise ValueError(f"datafields types fetch failed: {exc}") from exc
    if resp.status_code // 100 != 2:
        raise ValueError(f"datafields types fetch failed: {resp.status_code} {resp.text}")
    payload = resp.json()
    results = payload.get("results", [])
    types: list[str] = []
    for item in results:
        if not isinstance(item, dict):
            continue
        value = item.get("type")
        if isinstance(value, str) and value not in types:
            types.append(value)
    return types


def fetch_datafields_preview(
    username: str,
    password: str,
    api_base: str,
    instrument_type: str,
    region: str,
    delay: int,
    universe: str,
    dataset_id: str,
    max_count: int = 500,
    use_proxy: bool = False,
) -> list[Dict[str, Any]]:
    client = BrainClient(username=username, password=password, api_base=api_base, use_proxy=use_proxy)
    try:
        client.login()
    except BrainAuthError as exc:
        raise ValueError(str(exc)) from exc

    def _request_with_backoff(params: Dict[str, Any]) -> Any:
        backoff = 1.0
        last_resp = None
        for attempt in range(3):
            resp = client.session.get(f"{client.api_base}/data-fields", params=params, timeout=client.timeout)
            last_resp = resp
            if resp.status_code == 429 or "limit" in resp.text.lower():
                if attempt < 2:
                    time.sleep(backoff)
                    backoff *= 2
                    continue
            return resp
        return last_resp

    results: list[Dict[str, Any]] = []
    page_size = 50 if max_count <= 0 else min(50, max_count)
    params = {
        "instrumentType": instrument_type,
        "region": region,
        "delay": str(delay),
        "universe": universe,
        "limit": str(page_size),
        "offset": "0",
    }
    if dataset_id:
        params["dataset.id"] = dataset_id
    total = None
    offset = 0
    while True:
        params["offset"] = str(offset)
        resp = _request_with_backoff(params)
        if resp.status_code // 100 != 2:
            raise ValueError(f"datafields fetch failed: {resp.status_code} {resp.text}")
        payload = resp.json()
        if total is None:
            total = int(payload.get("count", 0))
        batch = payload.get("results", [])
        if isinstance(batch, list):
            results.extend(batch)
        if max_count > 0 and len(results) >= max_count:
            break
        offset += page_size
        if total is not None and offset >= total:
            break
        if not batch:
            break

    trimmed = results[:max_count] if max_count > 0 else results
    simplified = []
    for field in trimmed:
        if not isinstance(field, dict):
            continue
        simplified.append(
            {
                "id": field.get("id") or field.get("name") or field.get("field"),
                "description": field.get("description") or field.get("desc") or "",
                "type": field.get("type") or "",
                "coverage": field.get("coverage", 0),
                "userCount": field.get("userCount", 0),
                "alphaCount": field.get("alphaCount", 0),
            }
        )
    return simplified


def fetch_datafields_preview_multi(
    username: str,
    password: str,
    api_base: str,
    instrument_type: str,
    region: str,
    delay: int,
    universe: str,
    dataset_ids: list[str],
    max_count: int = 500,
    use_proxy: bool = False,
) -> list[Dict[str, Any]]:
    seen: set[str] = set()
    merged: list[Dict[str, Any]] = []
    for dataset_id in dataset_ids:
        if max_count > 0:
            remaining = max_count - len(merged)
            if remaining <= 0:
                break
        else:
            remaining = 0
        results = fetch_datafields_preview(
            username=username,
            password=password,
            api_base=api_base,
            instrument_type=instrument_type,
            region=region,
            delay=delay,
            universe=universe,
            dataset_id=dataset_id,
            max_count=remaining,
            use_proxy=use_proxy,
        )
        for item in results:
            key = str(item.get("id") or "")
            if not key or key in seen:
                continue
            seen.add(key)
            merged.append(item)
            if max_count > 0 and len(merged) >= max_count:
                return merged[:max_count]
        if dataset_id != dataset_ids[-1]:
            time.sleep(0.2)
    return merged[:max_count] if max_count > 0 else merged


def fetch_datasets(
    username: str,
    password: str,
    api_base: str,
    instrument_type: str,
    region: str,
    delay: int,
    universe: str,
    use_proxy: bool = False,
) -> list[Dict[str, Any]]:
    client = BrainClient(username=username, password=password, api_base=api_base, use_proxy=use_proxy)
    try:
        client.login()
    except BrainAuthError as exc:
        raise ValueError(str(exc)) from exc
    params = {
        "instrumentType": instrument_type,
        "region": region,
        "delay": str(delay),
        "universe": universe,
    }
    datasets: list[Dict[str, Any]] = []
    for theme in ("false", "true"):
        params["theme"] = theme
        resp = client.session.get(f"{client.api_base}/data-sets", params=params, timeout=client.timeout)
        if resp.status_code // 100 != 2:
            raise ValueError(f"datasets fetch failed: {resp.status_code} {resp.text}")
        payload = resp.json()
        batch = payload.get("results", [])
        if isinstance(batch, list):
            datasets.extend(batch)
    return datasets
