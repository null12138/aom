from __future__ import annotations
import json
import logging
from typing import Any, Dict, List
from .brain_api import load_brain_config
from ..modules.brain.engine import AlphaGenerator
from ..modules.brain.field_meta_cache import LocalFieldMetaCache, build_field_meta_context_key
from ..modules.brain.knowledge import get_all_patterns
from ..api.brain import BrainClient

logger = logging.getLogger("AI_API")

# 内存缓存，避免每次生成都去爬一次 API
OPERATORS_CACHE: List[Dict[str, Any]] = []


def _extract_context_settings(context: Dict[str, Any]) -> Dict[str, Any]:
    if not isinstance(context, dict):
        context = {}
    instrument = str(context.get("instrumentType") or context.get("instrument") or "EQUITY")
    region = str(context.get("region") or "USA")
    try:
        delay = int(context.get("delay", 1))
    except (TypeError, ValueError):
        delay = 1
    universe = str(context.get("universe") or "TOP3000")
    return {
        "instrumentType": instrument,
        "region": region,
        "delay": max(0, delay),
        "universe": universe,
    }


def _pick_best_field_meta(target_id: str, batch: Any) -> Dict[str, str]:
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


def _enrich_fields_with_api(
    fields: List[Dict[str, Any]],
    brain_cfg: Dict[str, Any],
    context: Dict[str, Any],
    cache_file: str | None = None,
) -> List[Dict[str, Any]]:
    if not isinstance(fields, list) or not fields:
        return []
    settings = _extract_context_settings(context)
    context_key = build_field_meta_context_key(context)
    local_cache = LocalFieldMetaCache(cache_file)
    client = BrainClient(brain_cfg["username"], brain_cfg["password"], brain_cfg["api_base"])
    client.login()

    out: List[Dict[str, Any]] = []
    meta_cache: Dict[str, Dict[str, str]] = {}
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
                    resp = client._request(
                        "GET",
                        f"{client.api_base}/data-fields",
                        params={
                            "instrumentType": settings["instrumentType"],
                            "region": settings["region"],
                            "delay": str(settings["delay"]),
                            "universe": settings["universe"],
                            "search": fid,
                            "limit": "50",
                            "offset": "0",
                        },
                    )
                    if resp.status_code // 100 != 2:
                        raise RuntimeError(f"data-fields query failed: {resp.status_code} {resp.text}")
                    payload = resp.json()
                    meta = _pick_best_field_meta(fid, payload.get("results"))
                    if meta:
                        local_cache.set(context_key, fid, meta)
                        cache_writes += 1
                except Exception:
                    meta = {}
            meta_cache[fid] = meta
        if meta:
            if meta.get("description"):
                node["description"] = meta["description"]
            if meta.get("type"):
                node["type"] = meta["type"]
            if meta.get("dataset_id"):
                node["dataset_id"] = meta["dataset_id"]
            if meta.get("dataset_name"):
                node["dataset_name"] = meta["dataset_name"]
        node["id"] = fid
        node.setdefault("description", "")
        node.setdefault("type", "")
        node.setdefault("dataset_id", "")
        node.setdefault("dataset_name", "")
        out.append(node)
    if cache_writes > 0:
        local_cache.flush()
    return out


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

async def get_real_operators() -> List[Dict[str, Any]]:
    global OPERATORS_CACHE
    if OPERATORS_CACHE:
        return OPERATORS_CACHE
    
    try:
        cfg = load_brain_config()
        client = BrainClient(cfg["username"], cfg["password"], cfg["api_base"])
        client.login()
        ops = _normalize_operators_payload(client.get_operators())
        if ops:
            OPERATORS_CACHE = ops
            return ops
    except Exception as e:
        logger.error(f"Failed to fetch real operators from API: {e}")
    return []

def ai_generate_alphas(payload: Dict[str, Any]) -> List[Dict[str, Any]]:
    brain_cfg = load_brain_config()
    
    fields = payload.get("fields") or []
    if not fields:
        raise ValueError("No data fields provided for generation")

    report_text = payload.get("report_text")
    count = int(payload.get("count", 5))
    context = payload.get("context") or {}
    
    include_patterns = bool(payload.get("include_patterns", True))
    patterns = get_all_patterns() if include_patterns else None

    # 获取实时操作符列表 (同步环境直接调用，暂不处理异步，server 是 ThreadingHTTPServer)
    import asyncio
    try:
        # 这里为了简单直接阻塞获取
        real_ops = asyncio.run(get_real_operators())
    except:
        real_ops = []

    try:
        fields = _enrich_fields_with_api(
            fields=fields,
            brain_cfg=brain_cfg,
            context=context,
            cache_file=str(payload.get("field_meta_cache_file") or "metadata/field_meta_cache.json"),
        )
    except Exception as exc:
        logger.warning("Failed to enrich fields via API, fallback to incoming fields: %s", exc)

    generator = AlphaGenerator(brain_cfg)
    return generator.generate_alphas(fields, report_text, patterns, context, real_ops, count)

def ai_get_knowledge() -> List[Dict[str, Any]]:
    return get_all_patterns()

def ai_process_report(payload: Dict[str, Any]) -> str:
    text = payload.get("text", "")
    if not text:
        raise ValueError("No report text provided")
    return text[:20000] 
