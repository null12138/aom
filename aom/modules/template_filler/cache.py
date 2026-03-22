from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any, Dict, Iterable, List, Tuple

from .engine import TemplateError, load_template_file, validate_template


class CacheError(ValueError):
    pass


def load_datafields(path: Path) -> List[Dict[str, Any]]:
    data = json.loads(path.read_text(encoding="utf-8"))
    if isinstance(data, dict) and "results" in data:
        items = data["results"]
    elif isinstance(data, list):
        items = data
    else:
        raise CacheError("datafields cache must be a list or {results: []}")

    if not isinstance(items, list) or not all(isinstance(item, dict) for item in items):
        raise CacheError("datafields cache must contain list of objects")
    return items


def extract_field_names(items: Iterable[Dict[str, Any]]) -> List[Tuple[str, Dict[str, Any]]]:
    result: List[Tuple[str, Dict[str, Any]]] = []
    for item in items:
        name = _extract_name(item)
        if name:
            result.append((name, item))
    return result


def build_fill_rules(
    placeholders: List[str],
    items: Iterable[Tuple[str, Dict[str, Any]]],
    rules: Dict[str, Any],
    default_limit: int,
) -> Dict[str, List[str]]:
    fill_rules: Dict[str, List[str]] = {}
    for placeholder in placeholders:
        rule = rules.get(placeholder, {})
        if isinstance(rule, list) and all(isinstance(v, str) for v in rule):
            fill_rules[placeholder] = rule
            continue
        if not isinstance(rule, dict):
            rule = {}
        values = rule.get("values")
        if isinstance(values, list) and all(isinstance(v, str) for v in values):
            fill_rules[placeholder] = values
            continue

        matched = _filter_items(items, rule)
        limit = int(rule.get("limit", default_limit)) if default_limit else int(rule.get("limit", 0))
        if limit > 0:
            matched = matched[:limit]
        fill_rules[placeholder] = matched
    return fill_rules


def apply_cache_fill(
    template_path: Path,
    datafields_path: Path,
    rules: Dict[str, Any],
    default_limit: int,
    out_path: Path | None = None,
) -> Path:
    template = load_template_file(template_path)
    placeholders = _extract_placeholders(template.template)
    if not placeholders:
        raise TemplateError("template must contain placeholders to auto-fill")

    datafields = load_datafields(datafields_path)
    items = extract_field_names(datafields)
    fill_rules = build_fill_rules(placeholders, items, rules, default_limit)

    template.fill_rules = fill_rules
    errors = validate_template(template)
    if errors:
        raise TemplateError("; ".join(errors))

    output = out_path or template_path
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(template.to_dict(), ensure_ascii=False, indent=2), encoding="utf-8")
    return output


def _extract_name(item: Dict[str, Any]) -> str | None:
    for key in ("id", "name", "field", "short_name", "full_name", "alpha_name"):
        value = _get_nested(item, key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return None


def _get_nested(item: Dict[str, Any], key: str) -> Any:
    if key in item:
        return item.get(key)
    if "." not in key:
        return None
    node: Any = item
    for part in key.split("."):
        if not isinstance(node, dict):
            return None
        node = node.get(part)
    return node


def _extract_placeholders(template: str) -> List[str]:
    return sorted(set(re.findall(r"<([A-Za-z0-9_]+)\s*/>", template)))


def _filter_items(items: Iterable[Tuple[str, Dict[str, Any]]], rule: Dict[str, Any]) -> List[str]:
    contains = rule.get("contains")
    exclude = rule.get("exclude")
    dataset = rule.get("dataset")
    regex = rule.get("regex")

    contains_list = _normalize_str_list(contains)
    exclude_list = _normalize_str_list(exclude)

    pattern = re.compile(regex) if isinstance(regex, str) and regex else None

    matched: List[str] = []
    for name, item in items:
        if dataset and not _match_dataset(item, str(dataset)):
            continue
        if contains_list and not any(token in name for token in contains_list):
            continue
        if exclude_list and any(token in name for token in exclude_list):
            continue
        if pattern and not pattern.search(name):
            continue
        matched.append(name)

    return matched


def _normalize_str_list(value: Any) -> List[str]:
    if isinstance(value, str) and value.strip():
        return [value.strip()]
    if isinstance(value, list):
        return [str(v).strip() for v in value if str(v).strip()]
    return []


def _match_dataset(item: Dict[str, Any], dataset: str) -> bool:
    dataset_id = _get_nested(item, "dataset.id")
    dataset_name = _get_nested(item, "dataset.name")
    dataset_flat = item.get("dataset") if isinstance(item.get("dataset"), str) else None
    for val in (dataset_id, dataset_name, dataset_flat, item.get("dataset_id"), item.get("datasetId")):
        if isinstance(val, str) and val == dataset:
            return True
    return False
