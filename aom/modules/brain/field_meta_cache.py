from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def default_field_meta_cache_path() -> Path:
    # project_root / metadata / field_meta_cache.json
    return Path(__file__).resolve().parents[3] / "metadata" / "field_meta_cache.json"


def build_field_meta_context_key(context: Dict[str, Any]) -> str:
    node = context if isinstance(context, dict) else {}
    instrument = str(node.get("instrumentType") or node.get("instrument") or "EQUITY").upper()
    region = str(node.get("region") or "USA").upper()
    try:
        delay = int(node.get("delay", 1))
    except (TypeError, ValueError):
        delay = 1
    universe = str(node.get("universe") or "TOP3000").upper()
    return f"{instrument}|{region}|{max(0, delay)}|{universe}"


class LocalFieldMetaCache:
    def __init__(self, cache_file: str | Path | None = None) -> None:
        self.path = Path(cache_file) if cache_file else default_field_meta_cache_path()
        self._loaded = False
        self._dirty = False
        self._doc: Dict[str, Any] = {"version": 1, "updated_at": "", "items": {}}

    def _ensure_loaded(self) -> None:
        if self._loaded:
            return
        self._loaded = True
        if not self.path.exists():
            return
        try:
            raw = json.loads(self.path.read_text(encoding="utf-8"))
        except Exception:
            return
        if not isinstance(raw, dict):
            return
        items = raw.get("items")
        if not isinstance(items, dict):
            return
        self._doc = {
            "version": int(raw.get("version", 1) or 1),
            "updated_at": str(raw.get("updated_at") or ""),
            "items": items,
        }

    def get(self, context_key: str, field_id: str) -> Dict[str, str]:
        self._ensure_loaded()
        key = str(context_key or "").strip()
        fid = str(field_id or "").strip()
        if not key or not fid:
            return {}
        items = self._doc.get("items")
        if not isinstance(items, dict):
            return {}
        ctx_node = items.get(key)
        if not isinstance(ctx_node, dict):
            return {}
        fields = ctx_node.get("fields")
        if not isinstance(fields, dict):
            return {}
        node = fields.get(fid.lower())
        if not isinstance(node, dict):
            return {}
        return {
            "id": str(node.get("id") or fid),
            "description": str(node.get("description") or ""),
            "type": str(node.get("type") or ""),
            "dataset_id": str(node.get("dataset_id") or ""),
            "dataset_name": str(node.get("dataset_name") or ""),
        }

    def set(self, context_key: str, field_id: str, meta: Dict[str, Any]) -> None:
        self._ensure_loaded()
        key = str(context_key or "").strip()
        fid = str(field_id or "").strip()
        if not key or not fid:
            return

        m = meta if isinstance(meta, dict) else {}
        description = str(m.get("description") or "").strip()
        ftype = str(m.get("type") or "").strip()
        dataset_id = str(m.get("dataset_id") or "").strip()
        dataset_name = str(m.get("dataset_name") or "").strip()
        resolved_id = str(m.get("id") or fid).strip() or fid

        items = self._doc.setdefault("items", {})
        if not isinstance(items, dict):
            items = {}
            self._doc["items"] = items
        ctx_node = items.get(key)
        if not isinstance(ctx_node, dict):
            ctx_node = {"updated_at": "", "fields": {}}
            items[key] = ctx_node
        fields = ctx_node.get("fields")
        if not isinstance(fields, dict):
            fields = {}
            ctx_node["fields"] = fields

        field_key = fid.lower()
        old = fields.get(field_key)
        next_node = {
            "id": resolved_id,
            "description": description,
            "type": ftype,
            "dataset_id": dataset_id,
            "dataset_name": dataset_name,
            "updated_at": _utc_now(),
        }
        if (
            isinstance(old, dict)
            and old.get("id") == next_node["id"]
            and old.get("description") == next_node["description"]
            and old.get("type") == next_node["type"]
            and old.get("dataset_id") == next_node["dataset_id"]
            and old.get("dataset_name") == next_node["dataset_name"]
        ):
            return
        fields[field_key] = next_node
        ctx_node["updated_at"] = next_node["updated_at"]
        self._dirty = True

    def flush(self) -> None:
        self._ensure_loaded()
        if not self._dirty:
            return
        self._doc["updated_at"] = _utc_now()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp_path = self.path.with_suffix(self.path.suffix + ".tmp")
        tmp_path.write_text(json.dumps(self._doc, ensure_ascii=False, indent=2), encoding="utf-8")
        tmp_path.replace(self.path)
        self._dirty = False
