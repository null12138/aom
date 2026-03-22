from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict

from ..modules.library.engine import (
    connect as lib_connect,
    init_db as lib_init_db,
)
from ..modules.template_filler.engine import (
    build_template_skeleton,
)
from .models import FILE_KIND_CONFIG

ROOT_DIR = Path(__file__).resolve().parents[2]
WEB_DIR = ROOT_DIR / "webui"
UPLOAD_DIR = ROOT_DIR / "runs" / "uploads"
logger = logging.getLogger("AOMFileOps")


def resolve_path(raw_path: str) -> Path:
    path = Path(raw_path)
    if not path.is_absolute():
        path = (ROOT_DIR / path).resolve()
    else:
        path = path.resolve()
    if ROOT_DIR not in path.parents and path != ROOT_DIR:
        raise ValueError("path outside project root is not allowed")
    return path


def get_file_kind(kind: str | None) -> Dict[str, Any]:
    if not kind or kind not in FILE_KIND_CONFIG:
        raise ValueError("invalid file kind")
    return FILE_KIND_CONFIG[kind]


def match_ext(name: str, exts: list[str]) -> bool:
    if not exts:
        return True
    lower = name.lower()
    return any(lower.endswith(ext) for ext in exts)


def normalize_filename(kind: str, name: str | None) -> str:
    if not name:
        raise ValueError("file name required")
    name = str(name).strip()
    if not name or "/" in name or "\\" in name:
        raise ValueError("invalid file name")
    info = get_file_kind(kind)
    exts = info.get("ext") or []
    if exts and not match_ext(name, exts):
        name = f"{name}{exts[0]}"
    return name


def normalize_folder(name: str | None) -> str:
    if not name:
        return ""
    name = str(name).strip()
    if not name or "/" in name or "\\" in name or name in {".", ".."}:
        raise ValueError("invalid folder name")
    return name


def normalize_upload_filename(name: str) -> str:
    filename = Path(str(name)).name
    if not filename or filename in {".", ".."}:
        filename = "upload.json"
    if not filename.lower().endswith(".json"):
        filename = f"{filename}.json"
    return filename


def resolve_kind_base(kind: str) -> Path:
    info = get_file_kind(kind)
    return resolve_path(info["dir"])


def resolve_kind_folder(kind: str, folder: str | None) -> Path:
    base = resolve_kind_base(kind)
    folder_name = normalize_folder(folder)
    if not folder_name:
        return base
    return base / folder_name


def resolve_kind_path(kind: str, name: str, folder: str | None = None) -> Path:
    base = resolve_kind_folder(kind, folder)
    return base / name


def list_files(kind: str | None, folder: str | None = None) -> Dict[str, Any]:
    info = get_file_kind(kind)
    base = resolve_kind_folder(kind, folder)
    base.mkdir(parents=True, exist_ok=True)
    exts = info.get("ext") or []
    default_name = info.get("default")
    # 对模板库做稳健兜底：默认文件不存在时自动创建，避免误选普通模板 JSON 触发解析错误
    if kind == "template_library" and default_name:
        default_path = base / str(default_name)
        if not default_path.exists():
            default_path.write_text("[]", encoding="utf-8")
    files = sorted([p.name for p in base.iterdir() if p.is_file() and match_ext(p.name, exts)])
    dirs = sorted([p.name for p in base.iterdir() if p.is_dir()])
    if default_name not in files and files:
        default_name = files[0]
    return {"files": files, "dirs": dirs, "default": default_name or "", "folder": normalize_folder(folder)}


def create_file(kind: str | None, name: str | None, folder: str | None = None) -> Dict[str, Any]:
    filename = normalize_filename(kind, name)
    path = resolve_kind_path(kind, filename, folder)
    if path.exists():
        raise ValueError("file already exists")
    path.parent.mkdir(parents=True, exist_ok=True)

    if kind == "template":
        doc = build_template_skeleton(template_id=path.stem, template="divide(<x/>, add(1, <y/>))")
        path.write_text(json.dumps(doc, ensure_ascii=False, indent=2), encoding="utf-8")
    elif kind == "template_library":
        path.write_text("[]", encoding="utf-8")
    elif kind == "factors":
        path.write_text("[]", encoding="utf-8")
    elif kind == "state":
        doc = {
            "schema_version": "0.1",
            "run_id": path.stem,
            "created_at": datetime.now(timezone.utc).isoformat(),
            "config": {"mode": "brain"},
            "queue": [],
            "in_flight": [],
            "completed": [],
            "failed": [],
            "stats": {},
        }
        path.write_text(json.dumps(doc, ensure_ascii=False, indent=2), encoding="utf-8")
    elif kind == "settings":
        path.write_text("{}", encoding="utf-8")
    elif kind == "datafields":
        path.write_text(json.dumps({"results": []}, ensure_ascii=False, indent=2), encoding="utf-8")
    elif kind == "operators":
        path.write_text("[]", encoding="utf-8")
    elif kind == "factor_library":
        conn = lib_connect(path)
        lib_init_db(conn)
        conn.close()
    else:
        path.write_text("{}", encoding="utf-8")

    return {"file": filename}


def rename_file(kind: str | None, name: str | None, new_name: str | None, folder: str | None = None) -> Dict[str, Any]:
    old_name = normalize_filename(kind, name)
    new_name = normalize_filename(kind, new_name)
    old_path = resolve_kind_path(kind, old_name, folder)
    new_path = resolve_kind_path(kind, new_name, folder)
    if not old_path.exists():
        raise ValueError("file not found")
    if new_path.exists():
        raise ValueError("target file already exists")
    old_path.rename(new_path)
    return {"file": new_name}


def delete_file(kind: str | None, name: str | None, folder: str | None = None) -> Dict[str, Any]:
    get_file_kind(kind)
    filename = normalize_filename(kind, name)
    path = resolve_kind_path(kind, filename, folder)
    if not path.exists():
        raise ValueError("file not found")
    path.unlink()
    return {"deleted": filename}


def create_folder(kind: str | None, name: str | None, folder: str | None = None) -> Dict[str, Any]:
    get_file_kind(kind)
    parent = resolve_kind_folder(kind, folder)
    folder_name = normalize_folder(name)
    path = parent / folder_name
    if path.exists():
        raise ValueError("folder already exists")
    path.mkdir(parents=True, exist_ok=True)
    return {"folder": folder_name}


def preview_file(kind: str | None, name: str | None, folder: str | None = None) -> Dict[str, Any]:
    get_file_kind(kind)
    filename = normalize_filename(kind, name)
    path = resolve_kind_path(kind, filename, folder)
    if not path.exists():
        raise ValueError("file not found")
    if path.suffix.lower() == ".db":
        return {"name": filename, "content": "数据库文件不支持预览", "truncated": False}
    try:
        data = path.read_text(encoding="utf-8", errors="replace")
    except Exception:
        return {"name": filename, "content": "无法读取文件内容", "truncated": False}
    limit = 20000
    truncated = len(data) > limit
    content = data[:limit]
    return {"name": filename, "content": content, "truncated": truncated}


def load_template_library(path: Path) -> list[Dict[str, Any]]:
    if not path.exists():
        return []
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception as exc:
        logger.warning("Invalid template library JSON at %s: %s; fallback to empty list", path, exc)
        return []
    if isinstance(data, dict) and "items" in data:
        data = data["items"]
    if not isinstance(data, list):
        logger.warning("Template library is not a list at %s; fallback to empty list", path)
        return []
    items: list[Dict[str, Any]] = []
    for item in data:
        if isinstance(item, dict):
            items.append(item)
    return items


def save_template_library(path: Path, items: list[Dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(items, ensure_ascii=False, indent=2), encoding="utf-8")


def template_key(item: Dict[str, Any]) -> str:
    return str(item.get("template_id") or item.get("name") or item.get("id") or "")


def find_template_item(items: list[Dict[str, Any]], key: str) -> Dict[str, Any] | None:
    for item in items:
        if template_key(item) == key:
            return item
    return None


def normalize_template_item(item: Dict[str, Any]) -> Dict[str, Any]:
    key = template_key(item)
    if not key:
        raise ValueError("template item must include template_id or name")
    normalized = {
        "template_id": item.get("template_id") or key,
        "name": item.get("name") or item.get("template_id") or key,
        "template": item.get("template") or "",
        "fill_rules": item.get("fill_rules") or {},
        "metadata": item.get("metadata") or {},
        "updated_at": datetime.now(timezone.utc).isoformat(),
    }
    return normalized


def upsert_template_item(items: list[Dict[str, Any]], item: Dict[str, Any]) -> list[Dict[str, Any]]:
    key = template_key(item)
    updated: list[Dict[str, Any]] = []
    replaced = False
    for existing in items:
        if template_key(existing) == key:
            updated.append(item)
            replaced = True
        else:
            updated.append(existing)
    if not replaced:
        updated.append(item)
    return updated


def dataset_cache_path() -> Path:
    return ROOT_DIR / "metadata" / "datasets_cache.json"


def load_dataset_cache(cache_key: str) -> list[Dict[str, Any]] | None:
    path = dataset_cache_path()
    if not path.exists():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return None
    if not isinstance(data, dict):
        return None
    items = data.get("items", {})
    if not isinstance(items, dict):
        return None
    cached = items.get(cache_key)
    if not isinstance(cached, dict):
        return None
    results = cached.get("results")
    if not isinstance(results, list):
        return None
    return results


def save_dataset_cache(cache_key: str, results: list[Dict[str, Any]]) -> None:
    path = dataset_cache_path()
    items: Dict[str, Any] = {}
    if path.exists():
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            if isinstance(data, dict) and isinstance(data.get("items"), dict):
                items = data["items"]
        except json.JSONDecodeError:
            items = {}
    items[cache_key] = {"updated_at": datetime.now(timezone.utc).isoformat(), "results": results}
    payload = {"version": 1, "items": items}
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def datafields_cache_path() -> Path:
    return ROOT_DIR / "metadata" / "datafields_cache.json"


def build_datafields_cache_key(
    instrument: str,
    region: str,
    delay: int,
    universe: str,
    dataset_ids: list[str],
    limit: int,
) -> str:
    payload = {
        "instrument": instrument,
        "region": region,
        "delay": delay,
        "universe": universe,
        "dataset_ids": sorted([str(x) for x in dataset_ids if str(x).strip()]),
        "limit": int(limit),
    }
    return json.dumps(payload, ensure_ascii=False, sort_keys=True)


def load_datafields_cache(cache_key: str) -> list[Dict[str, Any]] | None:
    path = datafields_cache_path()
    if not path.exists():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return None
    if not isinstance(data, dict):
        return None
    items = data.get("items", {})
    if not isinstance(items, dict):
        return None
    cached = items.get(cache_key)
    if not isinstance(cached, dict):
        return None
    results = cached.get("results")
    if not isinstance(results, list):
        return None
    return results


def save_datafields_cache(cache_key: str, results: list[Dict[str, Any]]) -> None:
    path = datafields_cache_path()
    items: Dict[str, Any] = {}
    if path.exists():
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            if isinstance(data, dict) and isinstance(data.get("items"), dict):
                items = data["items"]
        except json.JSONDecodeError:
            items = {}
    items[cache_key] = {"updated_at": datetime.now(timezone.utc).isoformat(), "results": results}
    payload = {"version": 1, "items": items}
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
