from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict, List, Tuple

REQUIRED_KEYS = ["factor_id", "expression", "settings", "priority", "source_template_id", "tags"]


class FactorError(ValueError):
    pass


def load_factors(path: Path) -> List[Dict[str, Any]]:
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, list):
        raise FactorError("factor file must be a JSON list")
    if not all(isinstance(item, dict) for item in data):
        raise FactorError("factor list must contain JSON objects")
    return data


def validate_factors(factors: List[Dict[str, Any]]) -> List[str]:
    errors: List[str] = []
    for idx, item in enumerate(factors, start=1):
        missing = [k for k in REQUIRED_KEYS if k not in item]
        if missing:
            errors.append(f"#{idx} missing keys: {', '.join(missing)}")
        if "settings" in item and not isinstance(item["settings"], dict):
            errors.append(f"#{idx} settings must be object")
    return errors


def find_factor(factors: List[Dict[str, Any]], factor_id: str) -> Tuple[int, Dict[str, Any]]:
    for idx, item in enumerate(factors):
        if item.get("factor_id") == factor_id:
            return idx, item
    raise FactorError(f"factor_id not found: {factor_id}")


def write_factors(path: Path, factors: List[Dict[str, Any]]) -> None:
    path.write_text(json.dumps(factors, ensure_ascii=False, indent=2), encoding="utf-8")


def set_nested_value(target: Dict[str, Any], key: str, value: Any) -> None:
    parts = key.split(".")
    node: Dict[str, Any] = target
    for part in parts[:-1]:
        if part not in node or not isinstance(node[part], dict):
            node[part] = {}
        node = node[part]
    node[parts[-1]] = value
