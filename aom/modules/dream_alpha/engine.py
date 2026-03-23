from __future__ import annotations

import hashlib
import json
import logging
import random
import re
import threading
import time
from concurrent.futures import ThreadPoolExecutor, TimeoutError as FuturesTimeoutError, as_completed
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Set, Tuple
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


def _first_float(candidates: List[Any], default: float = 0.0) -> float:
    for value in candidates:
        if value is None:
            continue
        try:
            return float(value)
        except (TypeError, ValueError):
            continue
    return default


def _extract_turnover(result_wrapper: Dict[str, Any]) -> float:
    alpha_node = result_wrapper.get("alpha") if isinstance(result_wrapper, dict) else {}
    if not isinstance(alpha_node, dict):
        alpha_node = {}
    return _first_float(
        [
            _dig(alpha_node, ["is", "turnover"]),
            _dig(alpha_node, ["turnover"]),
            _dig(alpha_node, ["metrics", "turnover"]),
            _dig(alpha_node, ["statistics", "turnover"]),
            _dig(result_wrapper, ["turnover"]),
        ],
        default=0.0,
    )


def _extract_max_weight(result_wrapper: Dict[str, Any]) -> float:
    alpha_node = result_wrapper.get("alpha") if isinstance(result_wrapper, dict) else {}
    if not isinstance(alpha_node, dict):
        alpha_node = {}
    return _first_float(
        [
            _dig(alpha_node, ["is", "maxWeight"]),
            _dig(alpha_node, ["is", "max_weight"]),
            _dig(alpha_node, ["maxWeight"]),
            _dig(alpha_node, ["max_weight"]),
            _dig(alpha_node, ["metrics", "maxWeight"]),
            _dig(alpha_node, ["metrics", "max_weight"]),
            _dig(alpha_node, ["statistics", "maxWeight"]),
            _dig(alpha_node, ["statistics", "max_weight"]),
            _dig(result_wrapper, ["maxWeight"]),
            _dig(result_wrapper, ["max_weight"]),
        ],
        default=0.0,
    )


def _extract_operator_count(result_wrapper: Dict[str, Any]) -> int:
    alpha_node = result_wrapper.get("alpha") if isinstance(result_wrapper, dict) else {}
    if not isinstance(alpha_node, dict):
        alpha_node = {}
    candidates = [
        _dig(alpha_node, ["settings", "operatorCount"]),
        _dig(alpha_node, ["operatorCount"]),
        _dig(alpha_node, ["operator_count"]),
        _dig(alpha_node, ["is", "operatorCount"]),
        _dig(alpha_node, ["is", "operator_count"]),
        _dig(result_wrapper, ["operatorCount"]),
        _dig(result_wrapper, ["operator_count"]),
    ]
    for value in candidates:
        if value is None:
            continue
        try:
            return int(value)
        except (TypeError, ValueError):
            continue
    return -1


def _collect_fail_items(node: Any, out: List[str]) -> None:
    if isinstance(node, dict):
        name = str(node.get("name") or node.get("test") or node.get("id") or "").strip()
        status = str(node.get("status") or node.get("result") or node.get("grade") or "").strip().upper()
        passed_flag = node.get("passed")
        if status in {"FAIL", "FAILED", "ERROR", "WARN", "WARNING"}:
            out.append(name or status)
        elif isinstance(passed_flag, bool) and not passed_flag:
            out.append(name or "CHECK_FAILED")
        for value in node.values():
            _collect_fail_items(value, out)
    elif isinstance(node, list):
        for item in node:
            _collect_fail_items(item, out)


def _extract_fail_list(result_wrapper: Dict[str, Any]) -> List[str]:
    alpha_node = result_wrapper.get("alpha") if isinstance(result_wrapper, dict) else {}
    targets: List[Any] = []
    if isinstance(alpha_node, dict):
        targets.extend(
            [
                alpha_node.get("checks"),
                _dig(alpha_node, ["is", "checks"]),
                alpha_node.get("tests"),
                _dig(alpha_node, ["is", "tests"]),
                alpha_node.get("status"),
            ]
        )
    if isinstance(result_wrapper, dict):
        targets.extend([result_wrapper.get("checks"), result_wrapper.get("tests"), result_wrapper.get("status")])
    out: List[str] = []
    for node in targets:
        _collect_fail_items(node, out)
    dedup = []
    seen: Set[str] = set()
    for item in out:
        key = str(item or "").strip()
        if not key:
            continue
        if key in seen:
            continue
        seen.add(key)
        dedup.append(key)
    return dedup


def _extract_sub_universe_pass(result_wrapper: Dict[str, Any]) -> bool:
    fails = [str(x).upper() for x in _extract_fail_list(result_wrapper)]
    return not any(("SUB" in item and "UNIVERSE" in item) or ("LADDER" in item and "SHARPE" in item) for item in fails)


def _extract_prod_corr(submission_check: Any) -> float:
    if not isinstance(submission_check, (dict, list)):
        return 1.0
    candidates: List[Any] = []
    if isinstance(submission_check, dict):
        candidates.extend(
            [
                submission_check.get("prodCorrelation"),
                submission_check.get("prod_correlation"),
                _dig(submission_check, ["prod", "correlation"]),
                _dig(submission_check, ["correlation", "prod"]),
                _dig(submission_check, ["checks", "prodCorrelation"]),
            ]
        )
    if isinstance(submission_check, list):
        for item in submission_check:
            if isinstance(item, dict):
                candidates.extend(
                    [
                        item.get("prodCorrelation"),
                        item.get("value"),
                        item.get("correlation"),
                    ]
                )
    return _first_float(candidates, default=1.0)


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
    text = _apply_forced_named_optional_fixes(text)
    return text.strip()


_FORCED_NAMED_OPTIONAL_ARG_POSITIONS: Dict[str, Dict[int, str]] = {
    # Brain parser requires named attribute for the 3rd argument.
    "kth_element": {2: "k"},
}


def _apply_forced_named_optional_fixes(expression: str) -> str:
    text = str(expression or "")
    if not text:
        return text
    # Apply from inner-most call to outer-most call to keep indexes stable.
    for _ in range(3):
        calls = _extract_function_calls(text)
        changed_any = False
        for call in sorted(calls, key=lambda node: _to_int(node.get("open_idx"), -1), reverse=True):
            op_name = str(call.get("name_lower") or "").strip()
            named_positions = _FORCED_NAMED_OPTIONAL_ARG_POSITIONS.get(op_name)
            if not named_positions:
                continue
            args = list(call.get("args") or [])
            changed = False
            for pos, key in named_positions.items():
                if pos >= len(args):
                    continue
                token = str(args[pos] or "").strip()
                if not token:
                    continue
                is_named, _ = _parse_named_argument(token)
                if is_named:
                    continue
                args[pos] = f"{key}={token}"
                changed = True
            if not changed:
                continue
            open_idx = _to_int(call.get("open_idx"), -1)
            close_idx = _to_int(call.get("close_idx"), -1)
            if open_idx < 0 or close_idx <= open_idx:
                continue
            inner = ", ".join(str(arg) for arg in args)
            text = text[:open_idx + 1] + inner + text[close_idx:]
            changed_any = True
        if not changed_any:
            break
    return text


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


def _extract_identifier_tokens(expression: str) -> Set[str]:
    text = str(expression or "")
    if not text:
        return set()
    tokens = set(re.findall(r"(?<![A-Za-z0-9_])([A-Za-z_][A-Za-z0-9_]*)", text))
    function_names = {
        str(match.group(1)).strip()
        for match in re.finditer(r"(?<![A-Za-z0-9_])([A-Za-z_][A-Za-z0-9_]*)\s*\(", text)
    }
    return {token for token in tokens if token not in function_names}


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
    functions = {
        str(match.group(1)).strip().lower()
        for match in re.finditer(r"(?<![A-Za-z0-9_])([A-Za-z_][A-Za-z0-9_]*)\s*\(", text)
    }
    symbols = {sym for sym in ("+", "-", "*", "/") if sym in text}
    # If function aliases are used, do not double count symbolic operators.
    if "add" in functions:
        symbols.discard("+")
    if "sub" in functions or "subtract" in functions:
        symbols.discard("-")
    if "mul" in functions or "multiply" in functions:
        symbols.discard("*")
    if "div" in functions or "divide" in functions:
        symbols.discard("/")
    return len(functions) + len(symbols)


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


THEME_OPERATORS: Dict[str, Set[str]] = {
    "A": {"trade_when", "keep", "if_else", "nan_mask"},
    "B": {
        "days_from_last_change",
        "filter",
        "group_backfill",
        "hump",
        "hump_decay",
        "jump_decay",
        "kth_element",
        "last_diff_value",
        "ts_backfill",
    },
    "C": {
        "clamp",
        "left_tail",
        "nan_out",
        "pasteurize",
        "purify",
        "replace",
        "right_tail",
        "tail",
        "truncate",
        "winsorize",
    },
    "D": {
        "group_multi_regression",
        "group_vector_neut",
        "group_vector_proj",
        "multi_regression",
        "regression_neut",
        "regression_proj",
        "ts_poly_regression",
        "ts_regression",
        "ts_theilsen",
        "ts_vector_neut",
        "ts_vector_proj",
        "vector_neut",
        "vector_proj",
    },
    "E": {
        "ts_co_kurtosis",
        "ts_co_skewness",
        "ts_corr",
        "ts_covariance",
        "ts_partial_corr",
        "ts_triple_corr",
    },
    "F": {
        "inst_pnl",
        "inst_tvr",
        "one_side",
        "rank_by_side",
        "scale",
        "scale_down",
        "ts_delta_limit",
        "ts_target_tvr_decay",
        "ts_target_tvr_delta_limit",
        "ts_target_tvr_hump",
    },
}

FREQUENT_OPERATORS: Set[str] = {
    "ts_sum",
    "ts_mean",
    "rank",
    "zscore",
    "winsorize",
    "ts_std_dev",
    "scale",
    "round",
    "trade_when",
}

OPERATOR_THEME_INDEX: Dict[str, str] = {
    operator: theme
    for theme, operators in THEME_OPERATORS.items()
    for operator in operators
}

THEME_QUOTA_MIN = 4
DEFAULT_GENERATION_COUNT = 5
FREQUENT_OP_LIMIT = 2
DEFAULT_NOTIFY_URL = "https://tgpusher.opener.eu.org/"

SIMPLE_PREFERRED_OPERATORS: Set[str] = {
    "rank",
    "zscore",
    "winsorize",
    "ts_rank",
    "ts_delta",
    "ts_mean",
    "ts_std_dev",
    "ts_zscore",
    "ts_backfill",
    "group_rank",
    "group_zscore",
    "group_neutralize",
    "trade_when",
}

SIMPLE_DISCOURAGED_OPERATORS: Set[str] = {
    "group_multi_regression",
    "group_vector_neut",
    "group_vector_proj",
    "multi_regression",
    "regression_neut",
    "regression_proj",
    "ts_poly_regression",
    "ts_regression",
    "ts_theilsen",
    "ts_vector_neut",
    "ts_vector_proj",
    "vector_neut",
    "vector_proj",
    "ts_partial_corr",
    "ts_triple_corr",
    "ts_co_kurtosis",
    "ts_co_skewness",
}

LOW_INFO_FIELD_TOKENS: Tuple[str, ...] = (
    "id",
    "code",
    "name",
    "currency",
    "ticker",
    "sedol",
    "isin",
    "cusip",
    "flag",
    "datatime",
    "datetime",
    "timestamp",
    "remark",
    "unit",
)


def _extract_function_calls(expression: str) -> List[Dict[str, Any]]:
    text = str(expression or "")
    if not text:
        return []
    out: List[Dict[str, Any]] = []
    for match in re.finditer(r"(?<![A-Za-z0-9_])([A-Za-z_][A-Za-z0-9_]*)\s*\(", text):
        name = str(match.group(1) or "").strip()
        if not name:
            continue
        open_idx = match.end() - 1
        close_idx = _find_matching_paren(text, open_idx)
        if close_idx < 0:
            continue
        inner = text[open_idx + 1:close_idx]
        out.append(
            {
                "name": name,
                "name_lower": name.lower(),
                "open_idx": open_idx,
                "close_idx": close_idx,
                "args_inner": inner,
                "args": _split_top_level_args(inner),
            }
        )
    return out


def _parse_operator_signature(definition: str) -> Dict[str, Any]:
    text = str(definition or "").strip()
    match = re.search(r"\((.*)\)", text)
    if not match:
        return {"param_names": [], "required_count": 0, "optional_names": set(), "allow_varargs": False}
    inner = match.group(1)
    raw_args = _split_top_level_args(inner)
    param_names: List[str] = []
    optional_names: Set[str] = set()
    required_count = 0
    allow_varargs = False
    for raw in raw_args:
        token = str(raw or "").strip()
        if not token:
            continue
        if token in {"...", "…"}:
            allow_varargs = True
            continue
        if token.startswith("*"):
            allow_varargs = True
            token = token.lstrip("*").strip()
            if not token:
                continue
        optional = ("=" in token) or token.startswith("[") or token.endswith("]")
        name_token = token.split("=", 1)[0].strip()
        name_token = name_token.strip("[] ").split(":", 1)[0].strip()
        if not re.match(r"^[A-Za-z_][A-Za-z0-9_]*$", name_token):
            continue
        param_names.append(name_token)
        if optional:
            optional_names.add(name_token)
        else:
            required_count += 1
    return {
        "param_names": param_names,
        "required_count": required_count,
        "optional_names": optional_names,
        "allow_varargs": allow_varargs,
    }


def _build_operator_index(operators: List[Dict[str, Any]]) -> Dict[str, Dict[str, Any]]:
    out: Dict[str, Dict[str, Any]] = {}
    for item in operators or []:
        if not isinstance(item, dict):
            continue
        name = str(item.get("name") or "").strip()
        if not name:
            continue
        definition = str(item.get("definition") or "")
        out[name.lower()] = {
            "name": name,
            "definition": definition,
            "signature": _parse_operator_signature(definition),
        }
    return out


def _parse_named_argument(arg: str) -> Tuple[bool, str]:
    token = str(arg or "").strip()
    match = re.match(r"^([A-Za-z_][A-Za-z0-9_]*)\s*=\s*(.+)$", token)
    if not match:
        return (False, "")
    key = str(match.group(1) or "").strip()
    return (True, key)


def _validate_expression_locally(
    expression: str,
    operator_index: Dict[str, Dict[str, Any]],
    max_operator_calls: int,
) -> Dict[str, Any]:
    expr = _normalize_expression(expression)
    errors: List[str] = []
    if not expr:
        errors.append("empty expression")
        return {"is_valid": False, "errors": errors, "operator_calls": 0, "operators": []}

    # Quick bracket sanity check before deeper inspection.
    depth = 0
    for idx, ch in enumerate(expr):
        if ch == "(":
            depth += 1
        elif ch == ")":
            depth -= 1
            if depth < 0:
                errors.append(f"unmatched ')' at index {idx}")
                break
    if depth != 0:
        errors.append("unbalanced parentheses")

    calls = _extract_function_calls(expr)
    op_names: List[str] = []
    for call in calls:
        op_name = str(call.get("name_lower") or "")
        if not op_name:
            continue
        op_names.append(op_name)
        op_meta = operator_index.get(op_name)
        if not op_meta:
            errors.append(f"INVALID_OP: operator '{op_name}' not found")
            continue

        signature = op_meta.get("signature") if isinstance(op_meta.get("signature"), dict) else {}
        param_names = list(signature.get("param_names") or [])
        required_count = int(signature.get("required_count") or 0)
        optional_names = set(signature.get("optional_names") or set())
        allow_varargs = bool(signature.get("allow_varargs"))
        args = call.get("args") if isinstance(call.get("args"), list) else []

        positional_count = 0
        seen_named: Set[str] = set()
        for arg_idx, arg in enumerate(args):
            token = str(arg or "").strip()
            if not token:
                errors.append(f"{op_name}: empty argument at position {arg_idx + 1}")
                continue
            is_named, key = _parse_named_argument(token)
            if is_named:
                if key in seen_named:
                    errors.append(f"{op_name}: duplicated keyword '{key}'")
                seen_named.add(key)
                if param_names and key not in param_names:
                    errors.append(f"{op_name}: unknown keyword '{key}'")
                continue
            positional_count += 1

        if positional_count < required_count:
            errors.append(
                f"{op_name}: missing required args (got positional={positional_count}, required={required_count})"
            )

        if (not allow_varargs) and param_names and len(args) > len(param_names):
            errors.append(
                f"{op_name}: too many args (got={len(args)}, max={len(param_names)})"
            )

        if optional_names and positional_count > required_count:
            errors.append(
                f"{op_name}: optional args must use named form key=value (positional optional found)"
            )

        forced_named_positions = _FORCED_NAMED_OPTIONAL_ARG_POSITIONS.get(op_name)
        if forced_named_positions:
            for pos, key in forced_named_positions.items():
                if pos >= len(args):
                    continue
                token = str(args[pos] or "").strip()
                if not token:
                    continue
                is_named, parsed_key = _parse_named_argument(token)
                if not is_named:
                    errors.append(f"{op_name}: argument#{pos + 1} must use named form '{key}=...'")
                elif parsed_key != key:
                    errors.append(f"{op_name}: argument#{pos + 1} must be '{key}=...'")

    operator_calls = _count_operator_calls(expr)
    if operator_calls > max_operator_calls:
        errors.append(
            f"operator budget exceeded: estimated={operator_calls}, limit={max_operator_calls}"
        )

    return {
        "is_valid": len(errors) == 0,
        "errors": errors,
        "operator_calls": operator_calls,
        "operators": sorted(set(op_names)),
    }


def _analyze_expression(expression: str) -> Dict[str, Any]:
    expr = _normalize_expression(expression)
    calls = _extract_function_calls(expr)
    counter: Dict[str, int] = {}
    for call in calls:
        name = str(call.get("name_lower") or "").strip()
        if not name:
            continue
        counter[name] = int(counter.get(name, 0)) + 1
    operators = set(counter.keys())
    themes = {OPERATOR_THEME_INDEX[name] for name in operators if name in OPERATOR_THEME_INDEX}
    frequent_used = {name for name in operators if name in FREQUENT_OPERATORS}
    return {
        "expression": expr,
        "operator_counter": counter,
        "operators": operators,
        "themes": themes,
        "frequent_used": frequent_used,
        "theme_ops_count": sum(counter.get(op, 0) for op in counter if op in OPERATOR_THEME_INDEX),
        "operator_calls": _count_operator_calls(expr),
    }


def _candidate_quality_score(candidate: Dict[str, Any], max_operator_calls: int, max_expression_fields: int) -> float:
    expr = _normalize_expression(str(candidate.get("expression") or ""))
    profile = _analyze_expression(expr)
    op_calls = int(candidate.get("_operator_calls") or profile.get("operator_calls") or 0)
    used_fields = list(candidate.get("_used_fields") or [])
    field_count = len(used_fields)
    ops = set(profile.get("operators") or set())
    preferred_hits = sum(1 for op in ops if op in SIMPLE_PREFERRED_OPERATORS)
    discouraged_hits = sum(1 for op in ops if op in SIMPLE_DISCOURAGED_OPERATORS)
    # Encourage compact, robust expressions around 3~6 operators with moderate field breadth.
    op_fit = max(0.0, 2.5 - 0.5 * abs(op_calls - 4))
    field_fit = max(0.0, 2.0 - 0.4 * abs(field_count - 2))
    hard_penalty = 0.0
    if op_calls > max_operator_calls:
        hard_penalty += 5.0
    if field_count > max_expression_fields:
        hard_penalty += 5.0
    if field_count <= 0:
        hard_penalty += 3.0
    return op_fit + field_fit + 0.25 * preferred_hits - 0.8 * discouraged_hits - hard_penalty


def _candidate_mutation_score(candidate: Dict[str, Any]) -> float:
    expr = _normalize_expression(str(candidate.get("expression") or ""))
    profile = _analyze_expression(expr)
    ops = set(profile.get("operators") or set())
    themes = set(profile.get("themes") or set())
    # Favor structural diversity for mutation slots.
    return float(len(ops)) + 1.5 * float(len(themes)) + random.uniform(0.0, 0.3)


def _split_candidates_with_mutation(
    candidates: List[Dict[str, Any]],
    target_count: int,
    max_operator_calls: int,
    max_expression_fields: int,
    mutation_ratio: float = 0.4,
) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]]]:
    if target_count <= 0:
        return ([], list(candidates))
    if len(candidates) <= target_count:
        return (list(candidates), [])

    scored: List[Tuple[float, float, int, Dict[str, Any]]] = []
    for idx, item in enumerate(candidates):
        scored.append(
            (
                _candidate_quality_score(item, max_operator_calls=max_operator_calls, max_expression_fields=max_expression_fields),
                _candidate_mutation_score(item),
                idx,
                item,
            )
        )
    scored.sort(key=lambda row: row[0], reverse=True)

    mutation_slots = int(round(target_count * mutation_ratio))
    mutation_slots = max(1, min(target_count - 1, mutation_slots)) if target_count > 1 else 0
    exploit_slots = target_count - mutation_slots

    selected_rows: List[Tuple[float, float, int, Dict[str, Any]]] = scored[:exploit_slots]
    selected_idx = {row[2] for row in selected_rows}

    mutation_pool = [row for row in scored[exploit_slots:] if row[2] not in selected_idx]
    mutation_pool.sort(key=lambda row: row[1], reverse=True)
    for row in mutation_pool:
        if len(selected_rows) >= target_count:
            break
        selected_rows.append(row)
        selected_idx.add(row[2])

    if len(selected_rows) < target_count:
        for row in scored:
            if len(selected_rows) >= target_count:
                break
            if row[2] in selected_idx:
                continue
            selected_rows.append(row)
            selected_idx.add(row[2])

    selected = [row[3] for row in selected_rows[:target_count]]
    backlog = [row[3] for row in scored if row[2] not in selected_idx]
    return (selected, backlog)


def _merge_candidate_backlog(
    existing: List[Dict[str, Any]],
    incoming: List[Dict[str, Any]],
    limit: int,
) -> List[Dict[str, Any]]:
    merged: List[Dict[str, Any]] = []
    seen_expr: Set[str] = set()
    for item in list(existing or []) + list(incoming or []):
        if not isinstance(item, dict):
            continue
        expr = _normalize_expression(str(item.get("expression") or ""))
        if not expr or expr in seen_expr:
            continue
        seen_expr.add(expr)
        normalized = dict(item)
        normalized["expression"] = expr
        merged.append(normalized)
        if len(merged) >= max(0, limit):
            break
    return merged


def _pair_key_from_themes(themes: Set[str]) -> str:
    if not themes:
        return ""
    return "|".join(sorted(themes))


def _check_batch_quotas(expressions: List[str], stage: str, batch_size: int) -> Dict[str, Any]:
    profiles = [_analyze_expression(expr) for expr in expressions]
    errors: List[str] = []
    if len(expressions) != batch_size:
        errors.append(f"batch size must be {batch_size}, got {len(expressions)}")

    theme_coverage: Set[str] = set()
    frequent_counter: Dict[str, int] = {}
    for idx, profile in enumerate(profiles):
        themes = set(profile.get("themes") or set())
        theme_coverage.update(themes)
        op_counter = profile.get("operator_counter") if isinstance(profile.get("operator_counter"), dict) else {}
        frequent_used = [op for op in op_counter.keys() if op in FREQUENT_OPERATORS]
        for op in frequent_used:
            frequent_counter[op] = int(frequent_counter.get(op, 0)) + int(op_counter.get(op, 0))
        if len(frequent_used) >= 2 and int(profile.get("theme_ops_count", 0)) < 1:
            errors.append(
                f"slot#{idx + 1}: uses >=2 frequent operators but has no A-F theme operator"
            )

    min_theme_coverage = max(2, min(THEME_QUOTA_MIN, batch_size // 2 + 1))
    if len(theme_coverage) < min_theme_coverage:
        errors.append(f"theme coverage too low: {len(theme_coverage)} < {min_theme_coverage}")

    for op, used in sorted(frequent_counter.items()):
        if int(used) > FREQUENT_OP_LIMIT:
            errors.append(f"frequent operator quota exceeded: {op} used {used} > {FREQUENT_OP_LIMIT}")

    # For larger batches keep tail slots explore-heavy to avoid homogenization.
    if batch_size >= 8:
        pair_seen: Set[str] = set()
        for slot in range(max(0, batch_size - 3), batch_size):
            if slot >= len(profiles):
                continue
            profile = profiles[slot]
            themes = set(profile.get("themes") or set())
            if len(themes) < 2:
                errors.append(f"slot#{slot + 1}: explore candidate must include >=2 A-F themes")
                continue
            pair_key = _pair_key_from_themes(themes)
            if pair_key in pair_seen:
                errors.append(f"slot#{slot + 1}: explore theme pair duplicated with another explore slot")
            pair_seen.add(pair_key)

    return {
        "ok": len(errors) == 0,
        "errors": errors,
        "profiles": profiles,
        "theme_coverage": sorted(theme_coverage),
        "frequent_counter": frequent_counter,
        "stage": stage,
    }


def _strip_number_tokens(expression: str) -> str:
    text = str(expression or "").lower()
    text = re.sub(r"(?<![A-Za-z0-9_])[-+]?\d+(\.\d+)?", "<n>", text)
    text = re.sub(r"\s+", "", text)
    return text


def _field_is_low_information(field_id: str) -> bool:
    token = str(field_id or "").strip().lower()
    if not token:
        return True
    return any(marker in token for marker in LOW_INFO_FIELD_TOKENS)


def _is_weak_self_normalization(expression: str, used_fields: List[str]) -> bool:
    text = re.sub(r"\s+", "", str(expression or "").lower())
    if not text:
        return True
    for fid in used_fields:
        key = str(fid or "").strip().lower()
        if not key:
            continue
        if f"{key}/group_sum({key}," in text:
            return True
        if f"({key}-ts_mean({key}," in text and f")/ts_mean({key}," in text:
            return True
    return False


def _violates_simple_operator_policy(expression: str) -> bool:
    calls = _extract_function_calls(expression)
    if not calls:
        return True
    op_names = {str(call.get("name_lower") or "").strip() for call in calls}
    op_names.discard("")
    if not op_names:
        return True
    if any(op in SIMPLE_DISCOURAGED_OPERATORS for op in op_names):
        return True
    if not any(op in SIMPLE_PREFERRED_OPERATORS for op in op_names):
        return True
    return False


def _is_pure_parameter_tweak(candidate_expression: str, baseline_expression: str) -> bool:
    cand = _strip_number_tokens(_normalize_expression(candidate_expression))
    base = _strip_number_tokens(_normalize_expression(baseline_expression))
    return bool(cand and base and cand == base)


def _extract_choice_values(node: Any) -> List[str]:
    if not isinstance(node, dict):
        return []
    choices = node.get("choices") or node.get("values") or []
    out: List[str] = []
    for item in choices:
        if isinstance(item, dict):
            value = item.get("value")
            if value is None:
                value = item.get("id") or item.get("name") or item.get("key")
            if value is None:
                continue
            out.append(str(value))
        else:
            out.append(str(item))
    return out


def _extract_choices_with_children(node: Any) -> List[Dict[str, Any]]:
    if not isinstance(node, dict):
        return []
    choices = node.get("choices") or node.get("values") or []
    out: List[Dict[str, Any]] = []
    for item in choices:
        if isinstance(item, dict):
            value = item.get("value")
            if value is None:
                value = item.get("id") or item.get("name") or item.get("key")
            if value is None:
                continue
            out.append(
                {
                    "value": str(value),
                    "children": item.get("children") if isinstance(item.get("children"), dict) else {},
                }
            )
        else:
            out.append({"value": str(item), "children": {}})
    return out


def _find_setting_node(node: Any, key: str, depth: int = 0) -> Dict[str, Any]:
    if depth > 10:
        return {}
    if isinstance(node, dict):
        direct = node.get(key)
        if isinstance(direct, dict):
            return direct
        children = node.get("children")
        if isinstance(children, dict):
            found = _find_setting_node(children, key, depth + 1)
            if found:
                return found
        for value in node.values():
            found = _find_setting_node(value, key, depth + 1)
            if found:
                return found
    elif isinstance(node, list):
        for item in node:
            found = _find_setting_node(item, key, depth + 1)
            if found:
                return found
    return {}


def _is_placeholder_choices(choices: List[str]) -> bool:
    if not choices:
        return False
    keys = {"instrumenttype", "region", "delay", "universe", "neutralization", "language"}
    lower = {str(item).strip().lower() for item in choices if str(item).strip()}
    return bool(lower) and lower.issubset(keys)


def _pick_choice_children(setting_node: Dict[str, Any], selected_value: str) -> Dict[str, Any]:
    for item in _extract_choices_with_children(setting_node):
        if str(item.get("value") or "") == selected_value:
            children = item.get("children")
            if isinstance(children, dict):
                return children
    return {}


def _validate_setting_value(
    selected_value: str,
    setting_node: Dict[str, Any],
    setting_name: str,
) -> Tuple[bool, str]:
    choices = _extract_choice_values(setting_node)
    if not choices or _is_placeholder_choices(choices):
        return (True, "")
    if str(selected_value) in set(choices):
        return (True, "")
    return (False, f"invalid {setting_name}={selected_value}, valid={choices}")


def _validate_context_settings_with_options(context: Dict[str, Any], options: Dict[str, Any]) -> Tuple[bool, str]:
    if not isinstance(context, dict):
        context = {}
    if not isinstance(options, dict):
        return (True, "")

    instrument = str(context.get("instrumentType") or context.get("instrument") or "EQUITY")
    region = str(context.get("region") or "USA")
    delay = str(_to_int(context.get("delay"), 1))
    universe = str(context.get("universe") or "TOP3000")

    instrument_node = _find_setting_node(options, "instrumentType")
    ok, err = _validate_setting_value(instrument, instrument_node, "instrumentType")
    if not ok:
        return (False, err)

    instrument_children = _pick_choice_children(instrument_node, instrument)
    level_region_root: Any = instrument_children if instrument_children else options
    region_node = _find_setting_node(level_region_root, "region")
    ok, err = _validate_setting_value(region, region_node, "region")
    if not ok:
        return (False, err)

    region_children = _pick_choice_children(region_node, region)
    level_delay_root: Any = region_children if region_children else level_region_root
    delay_node = _find_setting_node(level_delay_root, "delay")
    ok, err = _validate_setting_value(delay, delay_node, "delay")
    if not ok:
        return (False, err)

    delay_children = _pick_choice_children(delay_node, delay)
    level_universe_root: Any = delay_children if delay_children else level_delay_root
    universe_node = _find_setting_node(level_universe_root, "universe")
    ok, err = _validate_setting_value(universe, universe_node, "universe")
    if not ok:
        return (False, err)

    return (True, "")


class DreamAlphaDaemon:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._thread: Optional[threading.Thread] = None
        self._stop_event: Optional[threading.Event] = None
        self._state: Dict[str, Any] = self._default_state()
        self._cfg: Dict[str, Any] = {}
        self._last_error_notify_ts = 0.0
        self._last_notify_transport_warn_ts = 0.0
        self._last_no_success_notify_ts = 0.0

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
                "round_batches": 0,
                "quota_rebuilds": 0,
                "local_invalid": 0,
                "backlog_reused": 0,
                "submission_checks": 0,
                "prod_corr_blocked": 0,
                "cand_neg": 0,
                "shortflip_generated": 0,
                "field_limit_skipped": 0,
                "repair_requested": 0,
                "repair_success": 0,
                "prefetch_ready": 0,
                "prefetch_errors": 0,
                "pg_seed_saved": 0,
                "pg_seed_errors": 0,
                "no_success_cycles": 0,
                "no_success_notified": 0,
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
            "optimizer": {
                "stage": "A",
                "baseline_alpha_id": "",
                "baseline_expression": "",
                "core_fields": [],
                "core_datasets": [],
                "last_round_file": "",
            },
            "config": {},
        }

    def _cursor_file(self) -> Path:
        return Path(self._cfg.get("cursor_file", "runs/dream_alpha_cursor.json"))

    def _seed_file(self) -> Path:
        return Path(self._cfg.get("seed_file", "runs/dream_alpha_seed_library.json"))

    def _high_template_file(self) -> Path:
        return Path(self._cfg.get("high_template_file", "runs/dream_alpha_high_templates.jsonl"))

    def _operators_file(self) -> Path:
        return Path(self._cfg.get("operators_file", "metadata/operators.json"))

    def _results_file(self) -> Path:
        configured = str(self._cfg.get("results_file") or "").strip()
        if configured:
            return Path(configured)
        return Path("runs/dream_alpha_optimization_results.txt")

    def _notify_url(self) -> str:
        return str(self._cfg.get("notify_url") or DEFAULT_NOTIFY_URL)

    def _error_notify_cooldown(self) -> int:
        return max(0, _to_int(self._cfg.get("error_notify_cooldown_sec"), 180))

    def _no_success_notify_every(self) -> int:
        return max(1, _to_int(self._cfg.get("no_success_notify_every"), 2))

    def _no_success_notify_cooldown(self) -> int:
        return max(0, _to_int(self._cfg.get("no_success_notify_cooldown_sec"), 180))

    def _format_recent_events_for_notify_locked(self, limit: int = 12) -> str:
        events = self._state.get("recent_events")
        if not isinstance(events, list) or not events:
            return "(no recent events)"
        lines: List[str] = []
        for raw in events[-max(1, limit):]:
            if not isinstance(raw, dict):
                continue
            at = str(raw.get("at") or "")[-14:]
            event_type = str(raw.get("type") or "-")
            stage = str(raw.get("stage") or "-")
            message = str(raw.get("message") or "").strip()
            if not message:
                reason = str(raw.get("reason") or "").strip()
                if reason:
                    message = f"reason={reason}"
                elif isinstance(raw.get("errors"), list) and raw.get("errors"):
                    message = "; ".join(str(x) for x in list(raw.get("errors") or [])[:2])
                elif event_type == "generate_summary":
                    message = (
                        f"raw={_to_int(raw.get('raw_generated'), 0)} "
                        f"kept={_to_int(raw.get('generated'), 0)} "
                        f"invalid={_to_int(raw.get('local_invalid_skipped'), 0)}"
                    )
                elif event_type == "result":
                    message = (
                        f"slot={_to_int(raw.get('slot'), 0)} "
                        f"sharpe={_to_float(raw.get('sharpe'), 0.0):.3f} "
                        f"fitness={_to_float(raw.get('fitness'), 0.0):.3f}"
                    )
                elif event_type == "warn" and stage == "generate_short":
                    message = f"got={_to_int(raw.get('got'), 0)} need={_to_int(raw.get('need'), 0)}"
                elif event_type == "warn" and stage == "preflight":
                    failures = raw.get("failures")
                    if isinstance(failures, list) and failures:
                        one = failures[0]
                        if isinstance(one, dict):
                            message = str(one.get("error") or one.get("expression") or "")
            compact = re.sub(r"\s+", " ", message).strip()[:180]
            line = f"{at} {event_type}/{stage}"
            if compact:
                line += f" | {compact}"
            lines.append(line)
        return "\n".join(lines) if lines else "(no recent events)"

    def _maybe_notify_no_success(self, cycle: int, reason: str, streak: int, detail: str = "") -> None:
        every = self._no_success_notify_every()
        if streak < every or (streak % every) != 0:
            return
        cooldown = self._no_success_notify_cooldown()
        now_ts = time.time()
        if cooldown > 0 and (now_ts - self._last_no_success_notify_ts) < cooldown:
            return
        self._last_no_success_notify_ts = now_ts
        with self._lock:
            digest = self._format_recent_events_for_notify_locked(limit=14)
            self._inc_stat_locked("no_success_notified", 1)
            self._persist_state_locked()
        header = f"cycle={cycle} streak={streak} reason={reason}"
        if detail:
            header += f"\ndetail={str(detail)[:240]}"
        body = f"{header}\nRecent logs:\n{digest}"
        self._notify("NO_SUCCESS", body, force=True)

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
            if str(merged_cfg.get("start_mode") or "inherit") == "fresh":
                loaded_cursor = dict(initial.get("cursor") or {})
            else:
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
        out["generation_count"] = max(1, min(32, _to_int(out.get("generation_count"), DEFAULT_GENERATION_COUNT)))
        out["interval_sec"] = max(5, _to_int(out.get("interval_sec"), 30))
        out["max_wait_sec"] = max(60, _to_int(out.get("max_wait_sec"), 1800))
        start_mode = str(out.get("start_mode") or "inherit").strip().lower()
        if start_mode not in {"inherit", "fresh"}:
            start_mode = "inherit"
        out["start_mode"] = start_mode
        out["max_seed_in_prompt"] = max(1, _to_int(out.get("max_seed_in_prompt"), 20))
        out["auth_refresh_interval_sec"] = max(60, _to_int(out.get("auth_refresh_interval_sec"), 900))
        out["operators_refresh_interval_sec"] = max(60, _to_int(out.get("operators_refresh_interval_sec"), 1800))
        out["generation_attempts"] = max(1, min(6, _to_int(out.get("generation_attempts"), 3)))
        out["mutation_multiplier"] = max(1, min(8, _to_int(out.get("mutation_multiplier"), 3)))
        out["simulation_concurrency"] = max(1, min(32, _to_int(out.get("simulation_concurrency"), 5)))
        out["max_operator_calls"] = max(1, min(8, _to_int(out.get("max_operator_calls"), 8)))
        out["max_expression_fields"] = max(1, min(8, _to_int(out.get("max_expression_fields"), 8)))
        out["enable_llm_repair"] = _to_bool(out.get("enable_llm_repair"), True)
        out["repair_attempts"] = max(0, min(2, _to_int(out.get("repair_attempts"), 1)))
        out["mutation_keep_ratio"] = max(0.1, min(0.9, _to_float(out.get("mutation_keep_ratio"), 0.4)))
        out["error_notify_cooldown_sec"] = max(0, _to_int(out.get("error_notify_cooldown_sec"), 180))
        out["no_success_notify_every"] = max(1, _to_int(out.get("no_success_notify_every"), 2))
        out["no_success_notify_cooldown_sec"] = max(0, _to_int(out.get("no_success_notify_cooldown_sec"), 180))
        out["strict_quota_enabled"] = _to_bool(out.get("strict_quota_enabled"), False)
        out["sharpe_abs_threshold"] = _to_float(out.get("sharpe_abs_threshold"), 1.0)
        out["fitness_threshold"] = _to_float(out.get("fitness_threshold"), 1.0)
        out["template_sharpe_threshold"] = _to_float(out.get("template_sharpe_threshold"), 1.58)
        out["prefer_simple_operators"] = _to_bool(out.get("prefer_simple_operators"), True)
        out["learning_seed_sharpe_min"] = _to_float(out.get("learning_seed_sharpe_min"), 0.20)
        out["learning_seed_fitness_min"] = _to_float(out.get("learning_seed_fitness_min"), 0.05)
        out["include_patterns"] = _to_bool(out.get("include_patterns"), True)
        out["single_dataset_only"] = _to_bool(out.get("single_dataset_only"), True)
        out["seed_expressions"] = self._normalize_seed_exprs(out.get("seed_expressions"))
        out["fields"] = self._normalize_fields(out.get("fields"))
        out["context"] = out.get("context") if isinstance(out.get("context"), dict) else {}
        out["report_text"] = str(out.get("report_text") or "")
        notify_url = str(out.get("notify_url") or "").strip()
        out["notify_url"] = notify_url or DEFAULT_NOTIFY_URL
        out["push_all_console_notifications"] = _to_bool(out.get("push_all_console_notifications"), True)
        out["baseline_alpha_id"] = str(out.get("baseline_alpha_id") or "").strip()
        out["operators_file"] = str(out.get("operators_file") or "metadata/operators.json")
        out["results_file"] = str(out.get("results_file") or "").strip()
        out["force_stage"] = str(out.get("force_stage") or "").strip().upper()
        out["pg_seed_dsn"] = str(out.get("pg_seed_dsn") or "").strip()
        out["pg_seed_table"] = str(out.get("pg_seed_table") or "dream_alpha_good_seeds").strip()
        out["pg_seed_min_fitness"] = _to_float(out.get("pg_seed_min_fitness"), 0.9)
        out["pg_seed_min_turnover"] = _to_float(out.get("pg_seed_min_turnover"), 5.0)
        out["pg_seed_max_turnover"] = _to_float(out.get("pg_seed_max_turnover"), 200.0)
        out["pg_seed_timeout_sec"] = max(1, _to_int(out.get("pg_seed_timeout_sec"), 10))
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
            "start_mode": cfg["start_mode"],
            "interval_sec": cfg["interval_sec"],
            "max_wait_sec": cfg["max_wait_sec"],
            "sharpe_abs_threshold": cfg["sharpe_abs_threshold"],
            "fitness_threshold": cfg["fitness_threshold"],
            "template_sharpe_threshold": cfg["template_sharpe_threshold"],
            "prefer_simple_operators": cfg["prefer_simple_operators"],
            "include_patterns": cfg["include_patterns"],
            "max_seed_in_prompt": cfg["max_seed_in_prompt"],
            "auth_refresh_interval_sec": cfg["auth_refresh_interval_sec"],
            "operators_refresh_interval_sec": cfg["operators_refresh_interval_sec"],
            "generation_attempts": cfg["generation_attempts"],
            "mutation_multiplier": cfg["mutation_multiplier"],
            "simulation_concurrency": cfg["simulation_concurrency"],
            "max_operator_calls": cfg["max_operator_calls"],
            "max_expression_fields": cfg["max_expression_fields"],
            "enable_llm_repair": cfg["enable_llm_repair"],
            "repair_attempts": cfg["repair_attempts"],
            "mutation_keep_ratio": cfg["mutation_keep_ratio"],
            "strict_quota_enabled": cfg["strict_quota_enabled"],
            "no_success_notify_every": cfg["no_success_notify_every"],
            "no_success_notify_cooldown_sec": cfg["no_success_notify_cooldown_sec"],
            "single_dataset_only": cfg["single_dataset_only"],
            "baseline_alpha_id": cfg["baseline_alpha_id"],
            "operators_file": cfg["operators_file"],
            "results_file": str(self._results_file()),
            "force_stage": cfg["force_stage"],
            "pg_seed_store_enabled": bool(cfg.get("pg_seed_dsn")),
            "pg_seed_dsn_set": bool(cfg.get("pg_seed_dsn")),
            "pg_seed_table": cfg["pg_seed_table"],
            "pg_seed_min_fitness": cfg["pg_seed_min_fitness"],
            "pg_seed_min_turnover": cfg["pg_seed_min_turnover"],
            "pg_seed_max_turnover": cfg["pg_seed_max_turnover"],
            "learning_seed_sharpe_min": cfg["learning_seed_sharpe_min"],
            "learning_seed_fitness_min": cfg["learning_seed_fitness_min"],
            "seed_file": cfg["seed_file"],
            "cursor_file": cfg["cursor_file"],
            "high_template_file": cfg["high_template_file"],
            "field_meta_cache_file": cfg["field_meta_cache_file"],
            "use_proxy": cfg["use_proxy"],
            "fields_count": len(cfg.get("fields") or []),
            "context": cfg.get("context") or {},
            "notify_url_set": bool(cfg.get("notify_url")),
            "push_all_console_notifications": cfg.get("push_all_console_notifications", True),
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
        if _to_bool(self._cfg.get("push_all_console_notifications"), True):
            line = self._event_brief(event)
            if line:
                self._notify_async("CONSOLE", line)

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
                err_type = type(exc).__name__
                err_msg = str(exc)
                if len(err_msg) > 160:
                    err_msg = err_msg[:157] + "..."
                errors.append(f"{mode}: {err_type} {err_msg}")

        # throttle transport warning logs to avoid noisy flooding
        if now_ts - self._last_notify_transport_warn_ts >= 60:
            self._last_notify_transport_warn_ts = now_ts
            logger.warning("Notification push failed after retries: %s", " | ".join(errors))

    def _notify_async(self, title: str, body: str, force: bool = False) -> None:
        if not self._notify_url():
            return
        def _run() -> None:
            try:
                self._notify(title, body, force=force)
            except Exception:
                pass
        threading.Thread(target=_run, daemon=True).start()

    def _event_brief(self, event: Dict[str, Any]) -> str:
        if not isinstance(event, dict):
            return ""
        event_type = str(event.get("type") or "-")
        stage = str(event.get("stage") or "-")
        parts = [f"{event_type}/{stage}"]
        slot = event.get("slot")
        if slot is not None:
            parts.append(f"slot={_to_int(slot, 0)}")
        if "sharpe" in event:
            parts.append(f"sharpe={_to_float(event.get('sharpe'), 0.0):.3f}")
        if "fitness" in event:
            parts.append(f"fitness={_to_float(event.get('fitness'), 0.0):.3f}")
        if event.get("message"):
            msg = str(event.get("message") or "")
            if len(msg) > 220:
                msg = msg[:217] + "..."
            parts.append(msg)
        elif event.get("errors"):
            errs = event.get("errors")
            if isinstance(errs, list) and errs:
                msg = str(errs[0])
                if len(msg) > 220:
                    msg = msg[:217] + "..."
                parts.append(msg)
        line = " | ".join(parts).strip()
        if len(line) > 320:
            line = line[:317] + "..."
        return line

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
            if "{" in expr or "}" in expr or "same_dataset" in expr.lower():
                continue
            if _violates_simple_operator_policy(expr):
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

    def _append_round_result_file(
        self,
        baseline_alpha_id: str,
        round_index: int,
        context: Dict[str, Any],
        stage: str,
        items: List[Dict[str, Any]],
        next_actions: List[str],
    ) -> str:
        ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        ctx = context if isinstance(context, dict) else {}
        region = str(ctx.get("region") or "USA")
        delay = _to_int(ctx.get("delay"), 1)
        universe = str(ctx.get("universe") or "TOP3000")
        lines = [
            (
                f"[{ts}] baseline={baseline_alpha_id or '-'} round={round_index} "
                f"region={region} delay={delay} universe={universe} stage={stage}"
            )
        ]
        for item in items:
            slot = _to_int(item.get("slot"), 0)
            alpha_id = str(item.get("alpha_id") or "-")
            sharpe = _to_float(item.get("sharpe"), 0.0)
            fitness = _to_float(item.get("fitness"), 0.0)
            turnover = _to_float(item.get("turnover"), 0.0)
            max_weight = _to_float(item.get("max_weight"), 0.0)
            operator_count = _to_int(item.get("platform_operator_count"), -1)
            prod_corr = item.get("prod_corr")
            fail_items = item.get("fails") if isinstance(item.get("fails"), list) else []
            tags = item.get("tags") if isinstance(item.get("tags"), list) else []
            fail_text = ",".join(str(x) for x in fail_items) if fail_items else "none"
            tag_text = ",".join(str(x) for x in tags) if tags else "-"
            prod_text = f"{_to_float(prod_corr, 0.0):.4f}" if prod_corr is not None else "-"
            lines.append(
                (
                    f"slot={slot} alpha_id={alpha_id} Sharpe={sharpe:.4f} Fitness={fitness:.4f} "
                    f"Turnover={turnover:.4f} MaxWeight={max_weight:.4f} operatorCount={operator_count} "
                    f"FAIL={fail_text} PROD={prod_text} tags={tag_text}"
                )
            )
        lines.append(f"next_actions={'; '.join(next_actions) if next_actions else '-'}")
        content = "\n".join(lines) + "\n"
        path = self._results_file()
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8") as f:
            f.write(content)
        return str(path)

    def _load_local_operators_file(self) -> List[Dict[str, Any]]:
        path = self._operators_file()
        if not path.exists():
            return []
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            return []
        return _normalize_operators_payload(raw)

    def _resolve_stage(self, best_sharpe: float, best_fitness: float) -> str:
        forced = str(self._cfg.get("force_stage") or "").strip().upper()
        if forced in {"A", "B"}:
            return forced
        if best_sharpe > 1.40 and best_fitness > 0.90:
            return "B"
        return "A"

    def _safe_pg_table_identifier(self) -> str:
        raw = str(self._cfg.get("pg_seed_table") or "dream_alpha_good_seeds").strip()
        if not raw:
            raw = "dream_alpha_good_seeds"
        parts = raw.split(".")
        safe_parts: List[str] = []
        for part in parts:
            key = str(part or "").strip()
            if not re.match(r"^[A-Za-z_][A-Za-z0-9_]*$", key):
                return '"dream_alpha_good_seeds"'
            safe_parts.append(f'"{key}"')
        return ".".join(safe_parts)

    def _is_pg_good_seed(self, fitness: float, turnover: float) -> bool:
        min_fit = float(self._cfg.get("pg_seed_min_fitness", 0.9))
        min_tvr = float(self._cfg.get("pg_seed_min_turnover", 5.0))
        max_tvr = float(self._cfg.get("pg_seed_max_turnover", 200.0))
        if max_tvr < min_tvr:
            min_tvr, max_tvr = max_tvr, min_tvr
        return (fitness > min_fit) and (turnover >= min_tvr) and (turnover <= max_tvr)

    def _persist_good_seeds_to_postgres(self, records: List[Dict[str, Any]]) -> Dict[str, Any]:
        dsn = str(self._cfg.get("pg_seed_dsn") or "").strip()
        if not dsn:
            return {"enabled": False, "saved": 0, "error": ""}
        if not records:
            return {"enabled": True, "saved": 0, "error": ""}

        try:
            import psycopg  # type: ignore
        except Exception as exc:
            return {"enabled": True, "saved": 0, "error": f"psycopg import failed: {exc}"}

        table_ident = self._safe_pg_table_identifier()
        timeout_sec = int(self._cfg.get("pg_seed_timeout_sec", 10))
        create_sql = f"""
CREATE TABLE IF NOT EXISTS {table_ident} (
  unique_key TEXT PRIMARY KEY,
  expression_hash TEXT NOT NULL,
  expression TEXT NOT NULL,
  alpha_id TEXT,
  simulation_id TEXT,
  baseline_alpha_id TEXT,
  stage TEXT,
  name TEXT,
  logic TEXT,
  sharpe DOUBLE PRECISION,
  fitness DOUBLE PRECISION NOT NULL,
  turnover DOUBLE PRECISION NOT NULL,
  max_weight DOUBLE PRECISION,
  operator_count INTEGER,
  tags_json TEXT,
  source TEXT,
  raw_payload_json TEXT,
  created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
"""
        upsert_sql = f"""
INSERT INTO {table_ident} (
  unique_key, expression_hash, expression, alpha_id, simulation_id, baseline_alpha_id, stage,
  name, logic, sharpe, fitness, turnover, max_weight, operator_count, tags_json, source, raw_payload_json,
  created_at, updated_at
) VALUES (
  %s, %s, %s, %s, %s, %s, %s,
  %s, %s, %s, %s, %s, %s, %s, %s, %s, %s,
  NOW(), NOW()
)
ON CONFLICT (unique_key) DO UPDATE SET
  expression_hash = EXCLUDED.expression_hash,
  expression = EXCLUDED.expression,
  alpha_id = EXCLUDED.alpha_id,
  simulation_id = EXCLUDED.simulation_id,
  baseline_alpha_id = EXCLUDED.baseline_alpha_id,
  stage = EXCLUDED.stage,
  name = EXCLUDED.name,
  logic = EXCLUDED.logic,
  sharpe = EXCLUDED.sharpe,
  fitness = EXCLUDED.fitness,
  turnover = EXCLUDED.turnover,
  max_weight = EXCLUDED.max_weight,
  operator_count = EXCLUDED.operator_count,
  tags_json = EXCLUDED.tags_json,
  source = EXCLUDED.source,
  raw_payload_json = EXCLUDED.raw_payload_json,
  updated_at = NOW();
"""

        saved = 0
        try:
            with psycopg.connect(dsn, connect_timeout=timeout_sec) as conn:
                with conn.cursor() as cur:
                    cur.execute(create_sql)
                    for record in records:
                        expr = _normalize_expression(str(record.get("expression") or ""))
                        if not expr:
                            continue
                        alpha_id = str(record.get("alpha_id") or "")
                        unique_base = f"{expr}|{alpha_id}"
                        unique_key = hashlib.sha256(unique_base.encode("utf-8")).hexdigest()
                        expr_hash = hashlib.sha256(expr.encode("utf-8")).hexdigest()
                        cur.execute(
                            upsert_sql,
                            (
                                unique_key,
                                expr_hash,
                                expr,
                                alpha_id or None,
                                str(record.get("simulation_id") or "") or None,
                                str(record.get("baseline_alpha_id") or "") or None,
                                str(record.get("stage") or "") or None,
                                str(record.get("name") or "") or None,
                                str(record.get("logic") or "") or None,
                                _to_float(record.get("sharpe"), 0.0),
                                _to_float(record.get("fitness"), 0.0),
                                _to_float(record.get("turnover"), 0.0),
                                _to_float(record.get("max_weight"), 0.0),
                                _to_int(record.get("operator_count"), -1),
                                json.dumps(record.get("tags") or [], ensure_ascii=False),
                                str(record.get("source") or "dream_alpha_loop"),
                                json.dumps(record, ensure_ascii=False),
                            ),
                        )
                        saved += 1
                conn.commit()
            return {"enabled": True, "saved": saved, "error": "", "table": table_ident}
        except Exception as exc:
            return {"enabled": True, "saved": saved, "error": str(exc), "table": table_ident}

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

    def _extract_baseline_expression(self, alpha_details: Dict[str, Any]) -> str:
        if not isinstance(alpha_details, dict):
            return ""
        candidates = [
            alpha_details.get("regular"),
            alpha_details.get("expression"),
            _dig(alpha_details, ["regular", "code"]),
            _dig(alpha_details, ["alpha", "regular"]),
            _dig(alpha_details, ["alpha", "expression"]),
            _dig(alpha_details, ["formula"]),
        ]
        for value in candidates:
            expr = _normalize_expression(str(value or ""))
            if expr:
                return expr
        return ""

    def _freeze_baseline_fields(
        self,
        baseline_expression: str,
        fields_for_generation: List[Dict[str, Any]],
    ) -> Dict[str, Any]:
        field_ids = [
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
            dataset = str(item.get("dataset_id") or item.get("dataset_name") or "").strip()
            if dataset:
                field_to_dataset[fid] = dataset

        core_fields = _extract_expression_fields(baseline_expression, field_ids)
        if not core_fields:
            # Fallback: if baseline expression does not overlap selected fields, use the first selected field.
            core_fields = field_ids[:1]

        core_datasets = sorted(
            {
                str(field_to_dataset.get(fid) or "").strip()
                for fid in core_fields
                if str(field_to_dataset.get(fid) or "").strip()
            }
        )

        allowed_fields: List[str] = []
        if core_datasets:
            allowed_fields = [
                fid
                for fid in field_ids
                if str(field_to_dataset.get(fid) or "").strip() in set(core_datasets)
            ]
        if not allowed_fields:
            allowed_fields = list(core_fields)
        return {
            "core_fields": list(dict.fromkeys(core_fields)),
            "core_datasets": core_datasets,
            "allowed_fields": list(dict.fromkeys(allowed_fields)),
        }

    def _candidate_respects_core_lock(
        self,
        expression: str,
        all_field_ids: List[str],
        core_fields: List[str],
        allowed_fields: List[str],
    ) -> bool:
        used_fields = _extract_expression_fields(expression, all_field_ids)
        if not used_fields:
            return False
        core_intersection = [fid for fid in used_fields if fid in set(core_fields)]
        if not core_intersection:
            return False
        if allowed_fields:
            for fid in used_fields:
                if fid not in set(allowed_fields):
                    return False
        return True

    def _build_strict_report_text(
        self,
        base_report_text: str,
        stage: str,
        baseline_expression: str,
        core_fields: List[str],
        shortflip_queue: List[str],
        force_count: int,
        prefer_simple_operators: bool,
        batch_size: int,
    ) -> str:
        lines = [str(base_report_text or "").strip(), ""]
        if prefer_simple_operators:
            lines.extend(
                [
                    "WQ_BRAIN_ALPHA_OPTIMIZATION_V1 SIMPLE-OP MODE:",
                    f"- Batch size target: {batch_size} per round.",
                    "- Baseline lock is DISABLED. Do not anchor to one fixed alpha id.",
                    "- Operator budget target <= 8 per expression, preferred range is 3~6.",
                    "- Field budget target <= 8 per expression.",
                    "- Use simple and robust operators first. Preferred set:",
                    "  rank zscore winsorize ts_rank ts_delta ts_mean ts_std_dev ts_zscore ts_backfill group_rank group_zscore group_neutralize trade_when",
                    "- Avoid heavy/random operators (especially regression/vector families) unless absolutely necessary.",
                    "- Avoid weak structures like field/group_sum(field, group) and pure self-normalization clones.",
                    "- Named parameters required for optional arguments.",
                    "- When parser errors happen, repair expression before discard.",
                    "- Batch policy: generate a surplus candidate pool; keep best subset + mutation subset each round.",
                    f"- Current stage: Stage {stage}.",
                    "- Structural novelty > parameter-only tweaks.",
                    "",
                    f"Reference expression (soft hint only): {baseline_expression if baseline_expression else '-'}",
                    f"Reference fields (soft hint only): {', '.join(core_fields) if core_fields else '-'}",
                    f"Require at least {max(0, force_count)} short-flip candidates from queue in this round.",
                ]
            )
        else:
            lines.extend(
                [
                    "WQ_BRAIN_ALPHA_OPTIMIZATION_V1 STRICT MODE:",
                    f"- Batch size target: {batch_size} per round.",
                    "- Baseline lock is DISABLED. Do not anchor to a fixed alpha id or one core field set.",
                    "- Operator budget target <= 8 per expression.",
                    "- Field budget target <= 8 per expression.",
                    "- Named parameters required for optional arguments.",
                    "- Batch policy: generate a surplus candidate pool; keep best subset + mutation subset each round.",
                    f"- Current stage: Stage {stage}.",
                    "- Theme coverage per batch: at least 4 distinct themes from A-F.",
                    "- Common operator quota: ts_sum/ts_mean/rank/zscore/winsorize/ts_std_dev/scale/round/trade_when <=2 each batch.",
                    "- For large batches, keep tail slots explore-heavy and avoid duplicate theme pairs.",
                    "- Preserve validity first: prefer robust transforms (winsorize/zscore/rank/ts_backfill) for noisy fields.",
                    "- Structural novelty > parameter-only tweaks.",
                    "",
                    "Theme A: trade_when keep if_else nan_mask",
                    "Theme B: days_from_last_change filter group_backfill hump hump_decay jump_decay kth_element last_diff_value ts_backfill",
                    "Theme C: clamp left_tail nan_out pasteurize purify replace right_tail tail truncate winsorize",
                    "Theme D: group_multi_regression group_vector_neut group_vector_proj multi_regression regression_neut regression_proj ts_poly_regression ts_regression ts_theilsen ts_vector_neut ts_vector_proj vector_neut vector_proj",
                    "Theme E: ts_co_kurtosis ts_co_skewness ts_corr ts_covariance ts_partial_corr ts_triple_corr",
                    "Theme F: inst_pnl inst_tvr one_side rank_by_side scale scale_down ts_delta_limit ts_target_tvr_decay ts_target_tvr_delta_limit ts_target_tvr_hump",
                    "",
                    f"Reference expression (soft hint only): {baseline_expression if baseline_expression else '-'}",
                    f"Reference fields (soft hint only): {', '.join(core_fields) if core_fields else '-'}",
                    f"Require at least {max(0, force_count)} short-flip candidates from queue in this round.",
                ]
            )
        if stage == "A":
            lines.extend(
                [
                    "- Stage A: pure parameter tweaks are FORBIDDEN when a meaningful reference expression exists.",
                    "- Stage A quota suggestion: structural>=3, same-dataset-combo>=3, PV semantic>=2.",
                ]
            )
        else:
            if prefer_simple_operators:
                lines.append("- Stage B: fine-tune simple operators and horizons first, avoid unnecessary operator expansion.")
            else:
                lines.append("- Stage B: slots #1-#5 may fine-tune, slots #6-#8 must stay explore-heavy.")

        if shortflip_queue:
            lines.append(f"- Short-flip queue seeds: {len(shortflip_queue)}")
            for expr in shortflip_queue[:6]:
                lines.append(f"  * flip_source: {expr}")

        return "\n".join([line for line in lines if line is not None]).strip()

    def _prefetch_candidate_pool(
        self,
        brain: Dict[str, Any],
        fields: List[Dict[str, Any]],
        report_text: str,
        patterns: Optional[List[Dict[str, Any]]],
        context: Dict[str, Any],
        operators: List[Dict[str, Any]],
        count: int,
        attempts: int,
        stop_event: threading.Event,
    ) -> Dict[str, Any]:
        target_count = max(1, _to_int(count, 1))
        max_attempts = max(1, min(6, _to_int(attempts, 1)))
        generated: List[Dict[str, Any]] = []
        seen_expr: Set[str] = set()
        raw_generated = 0
        last_error = ""

        generator = AlphaGenerator(brain)
        prompt_context = dict(context or {})
        prompt_context["prefetch_mode"] = True

        for attempt_idx in range(max_attempts):
            if stop_event.is_set():
                break
            try:
                batch_raw = generator.generate_alphas(
                    fields=[dict(item) for item in (fields or [])],
                    report_text=report_text,
                    patterns=patterns,
                    context=prompt_context,
                    operators=operators or None,
                    count=target_count,
                )
            except Exception as exc:
                last_error = str(exc)
                if attempt_idx + 1 < max_attempts and not stop_event.is_set():
                    time.sleep(min(2, attempt_idx + 1))
                continue

            batch = batch_raw if isinstance(batch_raw, list) else []
            raw_generated += len(batch)
            for candidate in batch:
                if not isinstance(candidate, dict):
                    continue
                expr = _normalize_expression(str(candidate.get("expression") or ""))
                if not expr or expr in seen_expr:
                    continue
                seen_expr.add(expr)
                item = dict(candidate)
                item["expression"] = expr
                item["_prefetch"] = True
                generated.append(item)
                if len(generated) >= target_count:
                    return {
                        "candidates": generated,
                        "raw_generated": raw_generated,
                        "error": "",
                    }

        return {
            "candidates": generated,
            "raw_generated": raw_generated,
            "error": last_error,
        }

    def _meets_performance_gate(
        self,
        sharpe: float,
        fitness: float,
        turnover: float,
        max_weight: float,
        fails: List[str],
        operator_count: int,
    ) -> bool:
        if operator_count > int(self._cfg.get("max_operator_calls", 8)):
            return False
        if sharpe <= 1.58 or fitness <= 1.0:
            return False
        if turnover < 0.01 or turnover > 0.40:
            return False
        if max_weight >= 0.10:
            return False
        if fails:
            return False
        return True

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

            start_mode = str(cfg.get("start_mode") or "inherit").strip().lower()
            if start_mode == "fresh":
                seed_payload = {
                    "schema_version": "0.1",
                    "updated_at": _utc_now(),
                    "items": [],
                }
                _write_json_atomic(seed_file, seed_payload)
            else:
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
            operators: List[Dict[str, Any]] = []
            operator_index: Dict[str, Dict[str, Any]] = {}
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
            settings_checked = False

            requested_baseline_alpha_id = str(cfg.get("baseline_alpha_id") or "").strip()
            baseline_alpha_id = ""
            baseline_expression = ""
            core_fields: List[str] = []
            core_datasets: List[str] = []
            allowed_fields: List[str] = []
            shortflip_queue: List[str] = []
            candidate_backlog: List[Dict[str, Any]] = []
            prefetch_executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="dream-prefetch")
            prefetch_future: Optional[Any] = None
            round_index = 0
            no_success_streak = 0
            current_cycle = 0

            best_sharpe = 0.0
            best_fitness = 0.0
            if seed_items:
                ranked_seed = sorted(
                    seed_items,
                    key=lambda item: (
                        _to_float(item.get("sharpe"), -999.0),
                        _to_float(item.get("fitness"), -999.0),
                    ),
                    reverse=True,
                )
                if ranked_seed:
                    best_seed = ranked_seed[0]
                    best_sharpe = _to_float(best_seed.get("sharpe"), 0.0)
                    best_fitness = _to_float(best_seed.get("fitness"), 0.0)
                    baseline_expression = _normalize_expression(str(best_seed.get("expression") or ""))
            baseline_initialized = False

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

            def mark_no_success(reason: str, detail: str = "") -> None:
                nonlocal no_success_streak, current_cycle
                no_success_streak += 1
                detail_text = re.sub(r"\s+", " ", str(detail or "")).strip()[:240]
                with self._lock:
                    self._inc_stat_locked("no_success_cycles", 1)
                    optimizer = self._state.setdefault("optimizer", {})
                    optimizer["no_success_streak"] = no_success_streak
                    optimizer["last_no_success_reason"] = reason
                    self._append_event_locked(
                        {
                            "at": _utc_now(),
                            "type": "warn",
                            "stage": "no_success",
                            "cycle": current_cycle,
                            "reason": reason,
                            "detail": detail_text,
                            "streak": no_success_streak,
                        }
                    )
                    self._persist_state_locked()
                self._maybe_notify_no_success(
                    cycle=current_cycle,
                    reason=reason,
                    streak=no_success_streak,
                    detail=detail_text,
                )

            def mark_progress_success(marker: str = "") -> None:
                nonlocal no_success_streak
                if no_success_streak <= 0:
                    return
                no_success_streak = 0
                with self._lock:
                    optimizer = self._state.setdefault("optimizer", {})
                    optimizer["no_success_streak"] = 0
                    if marker:
                        optimizer["last_success_marker"] = str(marker)[:120]
                    self._persist_state_locked()

            while not stop_event.is_set():
                now_ts = time.time()
                prefetch_backlog_limit = max(
                    max(1, _to_int(cfg.get("generation_count"), DEFAULT_GENERATION_COUNT)) * 8,
                    40,
                )
                if prefetch_future is not None and prefetch_future.done():
                    prefetch_payload: Dict[str, Any] = {}
                    prefetch_error = ""
                    try:
                        maybe_payload = prefetch_future.result()
                        if isinstance(maybe_payload, dict):
                            prefetch_payload = maybe_payload
                    except Exception as exc:
                        prefetch_error = str(exc)
                    prefetch_future = None

                    incoming_prefetch = prefetch_payload.get("candidates") if isinstance(prefetch_payload, dict) else []
                    incoming_count = 0
                    if isinstance(incoming_prefetch, list) and incoming_prefetch:
                        incoming_count = len(incoming_prefetch)
                        candidate_backlog = _merge_candidate_backlog(
                            existing=candidate_backlog,
                            incoming=incoming_prefetch,
                            limit=prefetch_backlog_limit,
                        )
                    raw_prefetch = _to_int(
                        prefetch_payload.get("raw_generated"),
                        incoming_count,
                    ) if isinstance(prefetch_payload, dict) else incoming_count
                    if isinstance(prefetch_payload, dict) and prefetch_payload.get("error"):
                        prefetch_error = str(prefetch_payload.get("error") or "")

                    with self._lock:
                        if incoming_count > 0:
                            self._inc_stat_locked("prefetch_ready", incoming_count)
                        if prefetch_error:
                            self._inc_stat_locked("prefetch_errors", 1)
                        event = {
                            "at": _utc_now(),
                            "type": "prefetch",
                            "stage": "done",
                            "raw_generated": raw_prefetch,
                            "prefetched": incoming_count,
                            "backlog_size": len(candidate_backlog),
                        }
                        if prefetch_error:
                            event["message"] = prefetch_error[:280]
                        self._append_event_locked(event)
                        self._persist_state_locked()

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

                if not settings_checked:
                    try:
                        setting_options = client.get_settings_options()
                        ok_settings, setting_error = _validate_context_settings_with_options(
                            cfg.get("context") or {},
                            setting_options,
                        )
                        if not ok_settings:
                            raise RuntimeError(setting_error)
                        settings_checked = True
                        with self._lock:
                            self._append_event_locked(
                                {
                                    "at": _utc_now(),
                                    "type": "settings",
                                    "stage": "validate",
                                    "ok": True,
                                }
                            )
                            self._persist_state_locked()
                    except Exception as exc:
                        with self._lock:
                            self._inc_stat_locked("errors", 1)
                            self._inc_cursor_locked("error", 1)
                            self._state["last_error"] = f"settings validate failed: {exc}"
                            self._append_event_locked(
                                {
                                    "at": _utc_now(),
                                    "type": "error",
                                    "stage": "settings",
                                    "message": str(exc),
                                }
                            )
                            self._persist_state_locked()
                        self._notify("ERROR settings", str(exc))
                        time.sleep(min(15, cfg["interval_sec"]))
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
                        local_ops = self._load_local_operators_file()
                        merged: Dict[str, Dict[str, Any]] = {}
                        for op in local_ops:
                            if not isinstance(op, dict):
                                continue
                            name = str(op.get("name") or "").strip().lower()
                            if not name:
                                continue
                            merged[name] = dict(op)
                        for op in refreshed_ops:
                            if not isinstance(op, dict):
                                continue
                            name = str(op.get("name") or "").strip().lower()
                            if not name:
                                continue
                            merged[name] = dict(op)
                        if merged:
                            operators = list(merged.values())
                            operator_index = _build_operator_index(operators)
                        operators_last_fetch_ts = now_ts
                        with self._lock:
                            self._append_event_locked(
                                {
                                    "at": _utc_now(),
                                    "type": "operators",
                                    "count": len(operators),
                                    "api_count": len(refreshed_ops),
                                    "local_count": len(local_ops),
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

                if not operator_index:
                    local_ops = self._load_local_operators_file()
                    if local_ops:
                        operators = local_ops
                        operator_index = _build_operator_index(operators)
                if not operator_index:
                    with self._lock:
                        self._inc_stat_locked("errors", 1)
                        self._inc_cursor_locked("error", 1)
                        self._state["last_error"] = "operators unavailable (need metadata/operators.json or API operators)"
                        self._append_event_locked(
                            {
                                "at": _utc_now(),
                                "type": "error",
                                "stage": "operators_required",
                                "message": "operator index empty",
                            }
                        )
                        self._persist_state_locked()
                    self._notify("ERROR operators_required", "operator index empty")
                    time.sleep(min(20, cfg["interval_sec"]))
                    continue

                if not baseline_initialized:
                    if requested_baseline_alpha_id:
                        with self._lock:
                            self._append_event_locked(
                                {
                                    "at": _utc_now(),
                                    "type": "warn",
                                    "stage": "baseline_disabled",
                                    "alpha_id": requested_baseline_alpha_id,
                                    "message": "baseline setting ignored; lock disabled",
                                }
                            )
                            self._persist_state_locked()

                    if not baseline_expression and seed_items:
                        sorted_seed = sorted(
                            seed_items,
                            key=lambda item: (
                                _to_float(item.get("sharpe"), -999.0),
                                _to_float(item.get("fitness"), -999.0),
                            ),
                            reverse=True,
                        )
                        if sorted_seed:
                            baseline_expression = _normalize_expression(str(sorted_seed[0].get("expression") or ""))

                    baseline_initialized = True
                    with self._lock:
                        optimizer = self._state.setdefault("optimizer", {})
                        optimizer["baseline_alpha_id"] = ""
                        optimizer["baseline_expression"] = baseline_expression
                        optimizer["core_fields"] = []
                        optimizer["core_datasets"] = []
                        optimizer["stage"] = self._resolve_stage(best_sharpe, best_fitness)
                        self._append_event_locked(
                            {
                                "at": _utc_now(),
                                "type": "optimizer",
                                "stage": "baseline_disabled",
                                "message": "baseline lock disabled; using free structural exploration",
                                "reference_expression": baseline_expression[:220],
                            }
                        )
                        self._persist_state_locked()

                cycle_started_at = _utc_now()
                with self._lock:
                    self._state["last_cycle_at"] = cycle_started_at
                    self._inc_stat_locked("cycles", 1)
                    self._inc_cursor_locked("cycle", 1)
                    current_cycle = _to_int(_dig(self._state, ["cursor", "cycle"]), 0)
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

                stage = self._resolve_stage(best_sharpe, best_fitness)
                prefer_simple_operators = _to_bool(cfg.get("prefer_simple_operators"), True)
                target_count = max(1, _to_int(cfg.get("generation_count"), DEFAULT_GENERATION_COUNT))
                force_shortflip_count = min(2, target_count) if shortflip_queue else 0
                seed_lines = self._seed_prompt_lines(seed_items, cfg.get("max_seed_in_prompt", 20))
                strict_report_text = self._build_strict_report_text(
                    base_report_text=cfg.get("report_text") or "",
                    stage=stage,
                    baseline_expression=baseline_expression,
                    core_fields=core_fields,
                    shortflip_queue=shortflip_queue,
                    force_count=force_shortflip_count,
                    prefer_simple_operators=prefer_simple_operators,
                    batch_size=target_count,
                )
                hint_lines = ["Seed Library Signals:", *seed_lines]
                combined_report = (strict_report_text + "\n\n" + "\n".join(hint_lines)).strip()

                generation_context = dict(cfg.get("context") or {})
                generation_context["mutation_mode"] = "balanced" if prefer_simple_operators else "max"
                generation_context["single_dataset_only"] = _to_bool(cfg.get("single_dataset_only"), True)
                generation_context["max_operator_calls"] = int(cfg.get("max_operator_calls", 8))
                generation_context["max_expression_fields"] = int(cfg.get("max_expression_fields", 8))
                generation_context["stage"] = stage
                generation_context["strict_batch_size"] = target_count
                generation_context["prefer_simple_operators"] = prefer_simple_operators
                if baseline_expression:
                    generation_context["reference_expression"] = baseline_expression

                generation_attempts = max(int(cfg.get("generation_attempts", 3)), 3)
                mutation_multiplier = int(cfg.get("mutation_multiplier", 3))
                request_count = max(target_count, target_count * mutation_multiplier)
                pool_target = request_count
                raw_generated_total = 0
                single_dataset_skipped = 0
                structure_skipped = 0
                operator_limit_skipped = 0
                field_limit_skipped = 0
                core_lock_skipped = 0
                stage_tune_skipped = 0
                local_invalid_skipped = 0
                low_info_skipped = 0
                weak_structure_skipped = 0
                simple_policy_skipped = 0
                repair_requested = 0
                repair_success = 0
                generated: List[Dict[str, Any]] = []
                candidate_exprs = set()
                candidate_sigs = set()
                generate_error: Optional[Exception] = None
                shortflip_generated_count = 0
                max_expression_fields = max(1, _to_int(cfg.get("max_expression_fields"), 8))
                enable_llm_repair = _to_bool(cfg.get("enable_llm_repair"), True)
                repair_attempts = max(0, _to_int(cfg.get("repair_attempts"), 1))
                backlog_reused = 0
                backlog_limit = max(target_count * 8, 40)

                if candidate_backlog:
                    remaining_backlog: List[Dict[str, Any]] = []
                    for item in candidate_backlog:
                        if len(generated) >= pool_target:
                            remaining_backlog.append(item)
                            continue
                        if not isinstance(item, dict):
                            continue
                        expr = _normalize_expression(str(item.get("expression") or ""))
                        if not expr or expr in known_exprs or expr in candidate_exprs:
                            continue
                        sig = _expression_structure_signature(expr, field_ids_for_detection)
                        if sig and (sig in known_signatures or sig in candidate_sigs):
                            continue
                        reused = dict(item)
                        reused["expression"] = expr
                        if not isinstance(reused.get("_used_fields"), list):
                            reused["_used_fields"] = _extract_expression_fields(expr, field_ids_for_detection)
                        if not isinstance(reused.get("_opcheck"), dict):
                            reused["_opcheck"] = _validate_expression_locally(
                                expr,
                                operator_index,
                                int(cfg.get("max_operator_calls", 8)),
                            )
                        reused["_structure_sig"] = sig
                        reused["_operator_calls"] = int(
                            reused.get("_operator_calls")
                            or _to_int(_dig(reused, ["_opcheck", "operator_calls"]), 0)
                            or _count_operator_calls(expr)
                        )
                        generated.append(reused)
                        candidate_exprs.add(expr)
                        if sig:
                            candidate_sigs.add(sig)
                        backlog_reused += 1
                    candidate_backlog = remaining_backlog

                if shortflip_queue:
                    queue_copy = list(shortflip_queue)
                    while queue_copy and len(generated) < min(pool_target, force_shortflip_count):
                        src_expr = _normalize_expression(queue_copy.pop(0))
                        if not src_expr:
                            continue
                        flip_variants = [
                            _normalize_expression(f"multiply(-1, {src_expr})"),
                            _normalize_expression(f"negate({src_expr})"),
                        ]
                        for flip_expr in flip_variants:
                            if len(generated) >= min(pool_target, force_shortflip_count):
                                break
                            if not flip_expr or flip_expr in known_exprs or flip_expr in candidate_exprs:
                                continue
                            if stage == "A" and baseline_expression and _is_pure_parameter_tweak(flip_expr, baseline_expression):
                                stage_tune_skipped += 1
                                continue
                            shortflip_fields = _extract_expression_fields(flip_expr, field_ids_for_detection)
                            if not shortflip_fields:
                                single_dataset_skipped += 1
                                continue
                            if len(shortflip_fields) > max_expression_fields:
                                field_limit_skipped += 1
                                continue
                            validation = _validate_expression_locally(
                                flip_expr,
                                operator_index,
                                int(cfg.get("max_operator_calls", 8)),
                            )
                            if not validation.get("is_valid"):
                                local_invalid_skipped += 1
                                continue
                            sig = _expression_structure_signature(flip_expr, field_ids_for_detection)
                            if sig and (sig in known_signatures or sig in candidate_sigs):
                                structure_skipped += 1
                                continue
                            generated.append(
                                {
                                    "name": "ShortFlip",
                                    "logic": "CAND_SHORTFLIP from negative signal",
                                    "expression": flip_expr,
                                    "_structure_sig": sig,
                                    "_used_fields": shortflip_fields,
                                    "_operator_calls": int(validation.get("operator_calls") or _count_operator_calls(flip_expr)),
                                    "_opcheck": validation,
                                    "_tags": ["CAND_SHORTFLIP"],
                                    "_source_expr": src_expr,
                                }
                            )
                            candidate_exprs.add(flip_expr)
                            if sig:
                                candidate_sigs.add(sig)
                            shortflip_generated_count += 1

                for attempt in range(max(generation_attempts, 5)):
                    if stop_event.is_set():
                        break
                    if len(generated) >= pool_target:
                        break
                    try:
                        generated_raw = generator.generate_alphas(
                            fields=fields_for_generation,
                            report_text=combined_report,
                            patterns=patterns,
                            context=generation_context,
                            operators=operators or None,
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
                        original_expr = _normalize_expression(str(candidate.get("expression") or ""))
                        if not original_expr:
                            continue
                        if original_expr in known_exprs or original_expr in candidate_exprs:
                            continue
                        if stage == "A" and baseline_expression and _is_pure_parameter_tweak(original_expr, baseline_expression):
                            stage_tune_skipped += 1
                            continue
                        expr = original_expr
                        repaired_by_llm = False
                        repair_round = 0
                        max_op_calls = int(cfg.get("max_operator_calls", 8))

                        while True:
                            op_calls = _count_operator_calls(expr)
                            used_fields = _extract_expression_fields(expr, field_ids_for_detection)
                            sig = _expression_structure_signature(expr, field_ids_for_detection)
                            validation = _validate_expression_locally(expr, operator_index, max_op_calls)
                            failure_code = ""
                            failure_reasons: List[str] = []

                            if op_calls > max_op_calls:
                                failure_code = "operator_limit"
                                failure_reasons = [f"operator budget exceeded: estimated={op_calls}, limit={max_op_calls}"]
                            elif not used_fields:
                                failure_code = "single_dataset"
                                failure_reasons = ["no valid fields detected in expression"]
                            elif len(used_fields) > max_expression_fields:
                                failure_code = "field_limit"
                                failure_reasons = [f"field budget exceeded: used={len(used_fields)}, limit={max_expression_fields}"]
                            elif all(_field_is_low_information(fid) for fid in used_fields):
                                failure_code = "low_info"
                                failure_reasons = ["all used fields look low-information"]
                            elif _is_weak_self_normalization(expr, used_fields):
                                failure_code = "weak_structure"
                                failure_reasons = ["weak self-normalization structure detected"]
                            elif _to_bool(cfg.get("single_dataset_only"), True):
                                datasets = set(
                                    str(field_to_dataset.get(fid) or "").strip()
                                    for fid in used_fields
                                    if str(field_to_dataset.get(fid) or "").strip()
                                )
                                unresolved = [fid for fid in used_fields if not str(field_to_dataset.get(fid) or "").strip()]
                                if len(datasets) > 1 or (len(used_fields) > 1 and unresolved):
                                    failure_code = "single_dataset"
                                    failure_reasons = ["single_dataset_only violated"]
                            if not failure_code and sig and (sig in known_signatures or sig in candidate_sigs):
                                failure_code = "structure"
                                failure_reasons = ["duplicate structure signature"]
                            if not failure_code and not validation.get("is_valid"):
                                failure_code = "local_invalid"
                                failure_reasons = list(validation.get("errors") or [])[:6]
                            if not failure_code and prefer_simple_operators and _violates_simple_operator_policy(expr):
                                failure_code = "simple_policy"
                                failure_reasons = ["violates simple operator policy"]

                            repairable = failure_code in {"operator_limit", "field_limit", "local_invalid"}
                            if (
                                failure_code
                                and repairable
                                and enable_llm_repair
                                and repair_round < repair_attempts
                            ):
                                repair_round += 1
                                repair_requested += 1
                                try:
                                    repaired_expr = _normalize_expression(
                                        generator.repair_expression(
                                            expression=expr,
                                            errors=failure_reasons,
                                            fields=fields_for_generation,
                                            context=generation_context,
                                            operators=operators or [],
                                        )
                                    )
                                except Exception:
                                    repaired_expr = ""
                                if repaired_expr and repaired_expr != expr and repaired_expr not in known_exprs and repaired_expr not in candidate_exprs:
                                    expr = repaired_expr
                                    repaired_by_llm = True
                                    continue

                            if failure_code:
                                if failure_code == "operator_limit":
                                    operator_limit_skipped += 1
                                elif failure_code == "field_limit":
                                    field_limit_skipped += 1
                                elif failure_code == "single_dataset":
                                    single_dataset_skipped += 1
                                elif failure_code == "low_info":
                                    low_info_skipped += 1
                                elif failure_code == "weak_structure":
                                    weak_structure_skipped += 1
                                elif failure_code == "structure":
                                    structure_skipped += 1
                                elif failure_code == "simple_policy":
                                    simple_policy_skipped += 1
                                else:
                                    local_invalid_skipped += 1
                                break

                            normalized_candidate = dict(candidate)
                            normalized_candidate["expression"] = expr
                            normalized_candidate["_structure_sig"] = sig
                            normalized_candidate["_used_fields"] = used_fields
                            normalized_candidate["_operator_calls"] = int(validation.get("operator_calls") or op_calls)
                            normalized_candidate["_opcheck"] = validation
                            tags = list(candidate.get("tags") or [])
                            if repaired_by_llm:
                                tags.append("LLM_REPAIRED")
                                repair_success += 1
                            normalized_candidate["_tags"] = tags
                            generated.append(normalized_candidate)
                            candidate_exprs.add(expr)
                            if sig:
                                candidate_sigs.add(sig)
                            break
                        if len(generated) >= pool_target:
                            break
                    if len(generated) >= pool_target:
                        break

                if len(generated) > target_count:
                    selected_candidates, next_backlog = _split_candidates_with_mutation(
                        candidates=generated,
                        target_count=target_count,
                        max_operator_calls=int(cfg.get("max_operator_calls", 8)),
                        max_expression_fields=max_expression_fields,
                        mutation_ratio=_to_float(cfg.get("mutation_keep_ratio"), 0.4),
                    )
                    generated = selected_candidates
                    candidate_backlog = _merge_candidate_backlog(
                        existing=candidate_backlog,
                        incoming=next_backlog,
                        limit=backlog_limit,
                    )
                elif generated:
                    # Keep whatever backlog remains for the next cycle.
                    candidate_backlog = _merge_candidate_backlog(
                        existing=candidate_backlog,
                        incoming=[],
                        limit=backlog_limit,
                    )

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
                    mark_no_success("generate_error", str(generate_error))
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
                    self._inc_stat_locked("field_limit_skipped", field_limit_skipped)
                    self._inc_stat_locked("local_invalid", local_invalid_skipped)
                    if backlog_reused > 0:
                        self._inc_stat_locked("backlog_reused", backlog_reused)
                    if repair_requested > 0:
                        self._inc_stat_locked("repair_requested", repair_requested)
                    if repair_success > 0:
                        self._inc_stat_locked("repair_success", repair_success)
                    if shortflip_generated_count > 0:
                        self._inc_stat_locked("shortflip_generated", shortflip_generated_count)
                    self._append_event_locked(
                        {
                            "at": _utc_now(),
                            "type": "generate_summary",
                            "stage": stage,
                            "raw_generated": raw_generated_total,
                            "generated": len(generated),
                            "single_dataset_skipped": single_dataset_skipped,
                            "structure_skipped": structure_skipped,
                            "operator_limit_skipped": operator_limit_skipped,
                            "field_limit_skipped": field_limit_skipped,
                            "core_lock_skipped": core_lock_skipped,
                            "stage_tune_skipped": stage_tune_skipped,
                            "local_invalid_skipped": local_invalid_skipped,
                            "low_info_skipped": low_info_skipped,
                            "weak_structure_skipped": weak_structure_skipped,
                            "simple_policy_skipped": simple_policy_skipped,
                            "backlog_reused": backlog_reused,
                            "backlog_size": len(candidate_backlog),
                            "repair_requested": repair_requested,
                            "repair_success": repair_success,
                            "shortflip_generated": shortflip_generated_count,
                        }
                    )
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

                if len(generated) < target_count:
                    failure_streak += 1
                    with self._lock:
                        self._append_event_locked(
                            {
                                "at": _utc_now(),
                                "type": "warn",
                                "stage": "generate_short",
                                "got": len(generated),
                                "need": target_count,
                                "core_lock_skipped": core_lock_skipped,
                                "stage_tune_skipped": stage_tune_skipped,
                                "local_invalid_skipped": local_invalid_skipped,
                                "field_limit_skipped": field_limit_skipped,
                                "low_info_skipped": low_info_skipped,
                                "weak_structure_skipped": weak_structure_skipped,
                                "simple_policy_skipped": simple_policy_skipped,
                                "repair_requested": repair_requested,
                                "repair_success": repair_success,
                            }
                        )
                        self._persist_state_locked()
                    mark_no_success("generate_short", f"got={len(generated)} need={target_count}")
                    time.sleep(min(15, cfg["interval_sec"]))
                    continue

                generated = generated[:target_count]
                if (not prefer_simple_operators) and _to_bool(cfg.get("strict_quota_enabled"), False):
                    quota_report = _check_batch_quotas(
                        [str(item.get("expression") or "") for item in generated],
                        stage=stage,
                        batch_size=target_count,
                    )
                    if not quota_report.get("ok"):
                        with self._lock:
                            self._inc_stat_locked("quota_rebuilds", 1)
                            self._append_event_locked(
                                {
                                    "at": _utc_now(),
                                    "type": "warn",
                                    "stage": "quota",
                                    "errors": list(quota_report.get("errors") or []),
                                }
                            )
                            self._persist_state_locked()
                        mark_no_success("quota_reject", "; ".join(str(x) for x in list(quota_report.get("errors") or [])[:2]))
                        time.sleep(min(15, cfg["interval_sec"]))
                        continue

                settings = self._build_settings_from_context(cfg.get("context") or {})
                max_wait_sec = int(cfg.get("max_wait_sec", 1800))
                sim_concurrency = int(cfg.get("simulation_concurrency", 5))
                dispatch_tasks: List[Dict[str, Any]] = []
                preflight_report: List[Dict[str, Any]] = []
                local_validation_failures: List[Dict[str, Any]] = []

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
                        local_validation_failures.append(
                            {"slot": idx + 1, "expression": expr, "error": "duplicate expression"}
                        )
                        continue
                    if expr_sig and expr_sig in known_signatures:
                        local_validation_failures.append(
                            {"slot": idx + 1, "expression": expr, "error": "duplicate structure signature"}
                        )
                        continue

                    opcheck = _validate_expression_locally(
                        expr,
                        operator_index,
                        int(cfg.get("max_operator_calls", 8)),
                    )
                    preflight_report.append(
                        {
                            "slot": idx + 1,
                            "expression": expr,
                            "operators": list(opcheck.get("operators") or []),
                            "operator_calls": int(opcheck.get("operator_calls") or 0),
                            "is_valid": bool(opcheck.get("is_valid")),
                            "errors": list(opcheck.get("errors") or []),
                        }
                    )
                    if not opcheck.get("is_valid"):
                        local_validation_failures.append(
                            {
                                "slot": idx + 1,
                                "expression": expr,
                                "error": "; ".join(str(x) for x in (opcheck.get("errors") or []))[:260],
                            }
                        )
                        continue

                    payload_item = {"expression": expr, "settings": settings}
                    sim_payload = build_brain_payload(payload_item)

                    dispatch_tasks.append(
                        {
                            "idx": idx,
                            "slot": idx + 1,
                            "candidate": candidate,
                            "expression": expr,
                            "operator_calls": int(opcheck.get("operator_calls") or candidate.get("_operator_calls") or _count_operator_calls(expr)),
                            "opcheck": opcheck,
                            "expr_sig": expr_sig,
                            "sim_payload": sim_payload,
                        }
                    )

                if len(dispatch_tasks) != target_count:
                    with self._lock:
                        self._inc_stat_locked("errors", 1)
                        self._inc_cursor_locked("error", 1)
                        self._inc_stat_locked("local_invalid", len(local_validation_failures))
                        self._append_event_locked(
                            {
                                "at": _utc_now(),
                                "type": "warn",
                                "stage": "preflight",
                                "message": f"unable to build validated candidates (need={target_count})",
                                "failures": local_validation_failures[:8],
                            }
                        )
                        self._persist_state_locked()
                    mark_no_success("preflight_short", f"dispatch={len(dispatch_tasks)} need={target_count}")
                    time.sleep(min(15, cfg["interval_sec"]))
                    continue

                with self._lock:
                    self._append_event_locked(
                        {
                            "at": _utc_now(),
                            "type": "opcheck",
                            "stage": "preflight",
                            "items": preflight_report,
                        },
                        max_events=150,
                    )
                    self._persist_state_locked()

                for task in dispatch_tasks:
                    expr = str(task.get("expression") or "")
                    expr_sig = str(task.get("expr_sig") or "")
                    known_exprs.add(expr)
                    if expr_sig:
                        known_signatures.add(expr_sig)

                with self._lock:
                    seen = self._state.setdefault("seen_expressions", [])
                    for task in dispatch_tasks:
                        seen.append(str(task.get("expression") or ""))
                    if len(seen) > 20000:
                        del seen[:-20000]
                    sigs = self._state.setdefault("seen_signatures", [])
                    for task in dispatch_tasks:
                        expr_sig = str(task.get("expr_sig") or "")
                        if expr_sig:
                            sigs.append(expr_sig)
                    if len(sigs) > 30000:
                        del sigs[:-30000]
                    self._inc_cursor_locked("candidate", len(dispatch_tasks))
                    self._persist_state_locked()

                if prefetch_future is None and not stop_event.is_set():
                    prefetch_count = max(target_count, target_count * mutation_multiplier)
                    prefetch_attempts = max(1, min(generation_attempts, 3))
                    prefetch_patterns = list(patterns) if isinstance(patterns, list) else patterns
                    prefetch_future = prefetch_executor.submit(
                        self._prefetch_candidate_pool,
                        brain=dict(brain),
                        fields=[dict(item) for item in fields_for_generation],
                        report_text=combined_report,
                        patterns=prefetch_patterns,
                        context=dict(generation_context),
                        operators=[dict(op) for op in (operators or []) if isinstance(op, dict)],
                        count=prefetch_count,
                        attempts=prefetch_attempts,
                        stop_event=stop_event,
                    )
                    with self._lock:
                        self._append_event_locked(
                            {
                                "at": _utc_now(),
                                "type": "prefetch",
                                "stage": "start",
                                "target": prefetch_count,
                                "attempts": prefetch_attempts,
                            }
                        )
                        self._persist_state_locked()

                batch_results: List[Dict[str, Any]] = []
                simulation_error: Optional[Exception] = None

                def _simulate_single(task: Dict[str, Any]) -> Dict[str, Any]:
                    worker_client = BrainClient(
                        username=username,
                        password=password,
                        api_base=api_base,
                        use_proxy=cfg.get("use_proxy", False),
                    )
                    # Pre-login to reduce repeated 401 churn in fallback workers.
                    worker_client.login()
                    outcome = worker_client.simulate(
                        task.get("sim_payload") or {},
                        max_wait=max_wait_sec,
                        stop_event=stop_event,
                    )
                    return {
                        "task": task,
                        "simulation_id": str(outcome.simulation_id),
                        "alpha_id": str(outcome.alpha_id),
                        "alpha_detail": outcome.result if isinstance(outcome.result, dict) else {},
                    }

                try:
                    payloads = [task.get("sim_payload") or {} for task in dispatch_tasks]
                    outcomes = client.simulate_multiple(payloads, max_wait=max_wait_sec, stop_event=stop_event)
                    if len(outcomes) != len(dispatch_tasks):
                        raise RuntimeError(
                            f"multi simulation size mismatch: got={len(outcomes)} expected={len(dispatch_tasks)}"
                        )
                    task_by_expr = {
                        _normalize_expression(str(task.get("expression") or "")): task
                        for task in dispatch_tasks
                    }
                    used_slots: Set[int] = set()
                    for idx, outcome in enumerate(outcomes):
                        alpha_detail = outcome.result if isinstance(outcome.result, dict) else {}
                        expr_in_result = _normalize_expression(self._extract_baseline_expression(alpha_detail))
                        picked_task = task_by_expr.get(expr_in_result)
                        if not picked_task:
                            picked_task = dispatch_tasks[idx]
                        slot_key = _to_int(picked_task.get("slot"), idx + 1)
                        while slot_key in used_slots and idx < len(dispatch_tasks):
                            slot_key += 1
                        used_slots.add(slot_key)
                        batch_results.append(
                            {
                                "task": picked_task,
                                "simulation_id": str(outcome.simulation_id),
                                "alpha_id": str(outcome.alpha_id),
                                "alpha_detail": alpha_detail,
                            }
                        )
                except Exception as exc:
                    simulation_error = exc

                if simulation_error is not None:
                    with self._lock:
                        self._append_event_locked(
                            {
                                "at": _utc_now(),
                                "type": "warn",
                                "stage": "simulate_multi_fallback",
                                "message": str(simulation_error),
                            }
                        )
                        self._persist_state_locked()
                    batch_results = []
                    max_workers = max(1, min(sim_concurrency, len(dispatch_tasks)))
                    with ThreadPoolExecutor(max_workers=max_workers) as executor:
                        future_map = {executor.submit(_simulate_single, task): task for task in dispatch_tasks}
                        fallback_timeout_sec = max(60, max_wait_sec + min(180, max_wait_sec // 3))
                        try:
                            for fut in as_completed(future_map, timeout=fallback_timeout_sec):
                                task = future_map[fut]
                                try:
                                    one = fut.result()
                                    batch_results.append(one)
                                except Exception as exc:
                                    expr = str(task.get("expression") or "")
                                    with self._lock:
                                        self._inc_stat_locked("errors", 1)
                                        self._inc_cursor_locked("error", 1)
                                        self._state["last_error"] = f"simulate failed: {exc}"
                                        self._append_event_locked(
                                            {
                                                "at": _utc_now(),
                                                "type": "error",
                                                "stage": "simulate_single",
                                                "expression": expr[:200],
                                                "message": str(exc),
                                            }
                                        )
                                        self._persist_state_locked()
                                    self._notify("ERROR simulate", f"{exc}\nexpr={expr[:500]}")
                        except FuturesTimeoutError:
                            pending_tasks: List[Dict[str, Any]] = []
                            for fut, task in future_map.items():
                                if fut.done():
                                    continue
                                fut.cancel()
                                pending_tasks.append(task)
                            with self._lock:
                                self._inc_stat_locked("errors", 1)
                                self._inc_cursor_locked("error", 1)
                                self._state["last_error"] = (
                                    f"simulate timeout: pending={len(pending_tasks)} after {fallback_timeout_sec}s"
                                )
                                self._append_event_locked(
                                    {
                                        "at": _utc_now(),
                                        "type": "error",
                                        "stage": "simulate_timeout",
                                        "pending": len(pending_tasks),
                                        "timeout_sec": fallback_timeout_sec,
                                        "samples": [
                                            str(task.get("expression") or "")[:120]
                                            for task in pending_tasks[:4]
                                        ],
                                    }
                                )
                                self._persist_state_locked()
                            self._notify(
                                "ERROR simulate_timeout",
                                f"pending={len(pending_tasks)} timeout={fallback_timeout_sec}s",
                            )

                missing_count = max(0, len(dispatch_tasks) - len(batch_results))
                if missing_count > 0:
                    with self._lock:
                        self._inc_stat_locked("errors", 1)
                        self._inc_cursor_locked("error", 1)
                        self._append_event_locked(
                            {
                                "at": _utc_now(),
                                "type": "warn",
                                "stage": "simulate_partial",
                                "message": (
                                    f"batch partial: got={len(batch_results)} "
                                    f"expected={len(dispatch_tasks)} missing={missing_count}"
                                ),
                            }
                        )
                        self._persist_state_locked()
                    if not batch_results:
                        mark_no_success("simulate_incomplete", f"got={len(batch_results)} need={len(dispatch_tasks)}")
                        time.sleep(min(20, cfg["interval_sec"]))
                        continue

                mark_progress_success("simulate_batch_ok" if missing_count == 0 else "simulate_partial_ok")
                round_index += 1
                next_shortflip_sources: List[str] = []
                round_items_for_file: List[Dict[str, Any]] = []
                pg_seed_candidates: List[Dict[str, Any]] = []
                next_actions: List[str] = []
                done_hit = False
                done_alpha_id = ""
                done_expr = ""

                # Keep slot order deterministic in reports.
                batch_results.sort(key=lambda item: _to_int(_dig(item, ["task", "slot"]), 0))
                for item in batch_results:
                    task = item.get("task") if isinstance(item.get("task"), dict) else {}
                    candidate = task.get("candidate") if isinstance(task.get("candidate"), dict) else {}
                    expr = _normalize_expression(str(task.get("expression") or ""))
                    alpha_id = str(item.get("alpha_id") or "")
                    simulation_id = str(item.get("simulation_id") or "")
                    wrapped = {
                        "alpha_id": alpha_id,
                        "alpha": item.get("alpha_detail") if isinstance(item.get("alpha_detail"), dict) else {},
                        "simulation_id": simulation_id,
                    }
                    metrics = _extract_metrics(wrapped)
                    sharpe = float(metrics.get("sharpe", 0.0))
                    fitness = float(metrics.get("fitness", 0.0))
                    turnover = _extract_turnover(wrapped)
                    max_weight = _extract_max_weight(wrapped)
                    platform_operator_count = _extract_operator_count(wrapped)
                    if platform_operator_count < 0:
                        platform_operator_count = int(task.get("operator_calls", -1))
                    fail_items = _extract_fail_list(wrapped)
                    sub_universe_pass = _extract_sub_universe_pass(wrapped)
                    tags = list(candidate.get("_tags") or [])

                    hard_disqualify = any(
                        ("WEIGHT" in str(name).upper()) or ("UNIT" in str(name).upper())
                        for name in fail_items
                    )
                    if sharpe <= -1.20 and fitness <= -0.50 and not hard_disqualify:
                        tags.append("CAND_NEG")
                        next_shortflip_sources.append(expr)

                    if platform_operator_count > int(cfg.get("max_operator_calls", 8)):
                        fail_items = list(dict.fromkeys(fail_items + ["OPERATOR_COUNT"]))

                    should_check_submission = self._meets_performance_gate(
                        sharpe=sharpe,
                        fitness=fitness,
                        turnover=turnover,
                        max_weight=max_weight,
                        fails=fail_items,
                        operator_count=platform_operator_count,
                    ) and sub_universe_pass

                    submission_checked = False
                    prod_corr: Optional[float] = None
                    prod_corr_blocked = False
                    if should_check_submission and alpha_id:
                        try:
                            check_payload = client.get_submission_check(alpha_id)
                            submission_checked = True
                            prod_corr = _extract_prod_corr(check_payload)
                            if prod_corr >= 0.7:
                                prod_corr_blocked = True
                                fail_items = list(dict.fromkeys(fail_items + ["PROD_CORR"]))
                                next_actions.append(f"decorrelate alpha={alpha_id} prod_corr={prod_corr:.4f}")
                        except Exception as exc:
                            fail_items = list(dict.fromkeys(fail_items + ["SUBMISSION_CHECK_ERROR"]))
                            next_actions.append(f"submission-check retry alpha={alpha_id}: {exc}")

                    done_candidate = bool(should_check_submission and submission_checked and prod_corr is not None and prod_corr < 0.7)
                    accepted = (
                        abs(sharpe) > float(cfg["sharpe_abs_threshold"])
                        and fitness > float(cfg["fitness_threshold"])
                        and platform_operator_count <= int(cfg.get("max_operator_calls", 8))
                    )
                    high_template = sharpe > float(cfg["template_sharpe_threshold"]) and fitness > float(cfg["fitness_threshold"])

                    event = {
                        "at": _utc_now(),
                        "type": "result",
                        "slot": _to_int(task.get("slot"), 0),
                        "name": str(candidate.get("name") or ""),
                        "expression": expr,
                        "logic": str(candidate.get("logic") or ""),
                        "alpha_id": alpha_id,
                        "simulation_id": simulation_id,
                        "sharpe": sharpe,
                        "fitness": fitness,
                        "turnover": turnover,
                        "max_weight": max_weight,
                        "operator_count": platform_operator_count,
                        "accepted": accepted,
                        "high_template": high_template,
                        "fails": fail_items,
                        "stage": stage,
                        "tags": tags,
                        "submission_checked": submission_checked,
                        "prod_corr": prod_corr,
                        "done_candidate": done_candidate,
                    }

                    with self._lock:
                        self._inc_stat_locked("simulated", 1)
                        if accepted:
                            self._inc_stat_locked("accepted", 1)
                            self._inc_cursor_locked("accepted", 1)
                        if high_template:
                            self._inc_stat_locked("high_templates", 1)
                            self._inc_cursor_locked("high_template", 1)
                        if submission_checked:
                            self._inc_stat_locked("submission_checks", 1)
                        if prod_corr_blocked:
                            self._inc_stat_locked("prod_corr_blocked", 1)
                        if "CAND_NEG" in tags:
                            self._inc_stat_locked("cand_neg", 1)
                        self._append_event_locked(event)
                        self._state["last_error"] = ""
                        self._persist_state_locked()

                    learning_seed = (
                        (
                            abs(sharpe) >= float(cfg.get("learning_seed_sharpe_min", 0.20))
                            or fitness >= float(cfg.get("learning_seed_fitness_min", 0.05))
                        )
                        and platform_operator_count <= int(cfg.get("max_operator_calls", 8))
                        and ("OPERATOR_COUNT" not in fail_items)
                    )
                    if accepted or learning_seed:
                        if not any(str(it.get("expression") or "") == expr for it in seed_items):
                            seed_items.append(
                                {
                                    "expression": expr,
                                    "name": str(candidate.get("name") or ""),
                                    "logic": str(candidate.get("logic") or ""),
                                    "sharpe": sharpe,
                                    "fitness": fitness,
                                    "created_at": _utc_now(),
                                    "source": "dream_alpha_loop" if accepted else "dream_alpha_learning",
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

                    if done_candidate:
                        done_hit = True
                        done_alpha_id = alpha_id
                        done_expr = expr

                    if sharpe > best_sharpe or (abs(sharpe - best_sharpe) < 1e-9 and fitness > best_fitness):
                        best_sharpe = sharpe
                        best_fitness = fitness
                        baseline_expression = expr
                        if alpha_id:
                            baseline_alpha_id = alpha_id

                    round_items_for_file.append(
                        {
                            "slot": _to_int(task.get("slot"), 0),
                            "alpha_id": alpha_id,
                            "sharpe": sharpe,
                            "fitness": fitness,
                            "turnover": turnover,
                            "max_weight": max_weight,
                            "platform_operator_count": platform_operator_count,
                            "fails": fail_items,
                            "tags": tags,
                            "prod_corr": prod_corr,
                        }
                    )

                    if self._is_pg_good_seed(fitness=fitness, turnover=turnover):
                        pg_seed_candidates.append(
                            {
                                "slot": _to_int(task.get("slot"), 0),
                                "expression": expr,
                                "name": str(candidate.get("name") or ""),
                                "logic": str(candidate.get("logic") or ""),
                                "alpha_id": alpha_id,
                                "simulation_id": simulation_id,
                                "baseline_alpha_id": baseline_alpha_id,
                                "stage": stage,
                                "sharpe": sharpe,
                                "fitness": fitness,
                                "turnover": turnover,
                                "max_weight": max_weight,
                                "operator_count": platform_operator_count,
                                "tags": list(dict.fromkeys(tags)),
                                "fails": fail_items,
                                "source": "dream_alpha_loop",
                            }
                        )

                shortflip_queue = list(dict.fromkeys(next_shortflip_sources))[:8]
                if shortflip_queue:
                    next_actions.append(f"inject short-flip >=2 from {len(shortflip_queue)} CAND_NEG seeds")
                if stage == "A":
                    next_actions.append("stay Stage A structural exploration (fine-tune forbidden)")
                else:
                    if prefer_simple_operators:
                        next_actions.append("Stage B enabled: keep operators simple and tune horizons/thresholds")
                    else:
                        next_actions.append("Stage B enabled: keep #6-#8 explore-heavy")
                if any(
                    _to_int(item.get("platform_operator_count"), -1) > int(cfg.get("max_operator_calls", 8))
                    for item in round_items_for_file
                ):
                    next_actions.append("compress expressions with operatorCount overflow")

                round_file = self._append_round_result_file(
                    baseline_alpha_id=baseline_alpha_id,
                    round_index=round_index,
                    context=cfg.get("context") or {},
                    stage=stage,
                    items=round_items_for_file,
                    next_actions=list(dict.fromkeys(next_actions)),
                )

                pg_write = self._persist_good_seeds_to_postgres(pg_seed_candidates)
                pg_saved = _to_int(pg_write.get("saved"), 0) if isinstance(pg_write, dict) else 0
                pg_error = str(pg_write.get("error") or "") if isinstance(pg_write, dict) else ""
                with self._lock:
                    if pg_saved > 0:
                        self._inc_stat_locked("pg_seed_saved", pg_saved)
                    if pg_error:
                        self._inc_stat_locked("pg_seed_errors", 1)
                    self._append_event_locked(
                        {
                            "at": _utc_now(),
                            "type": "pg_seed_store",
                            "candidates": len(pg_seed_candidates),
                            "saved": pg_saved,
                            "error": pg_error[:300],
                            "table": str(pg_write.get("table") or "") if isinstance(pg_write, dict) else "",
                        }
                    )
                    self._persist_state_locked()
                if pg_error:
                    self._notify("ERROR pg_seed_store", pg_error)

                with self._lock:
                    self._inc_stat_locked("round_batches", 1)
                    optimizer = self._state.setdefault("optimizer", {})
                    optimizer["stage"] = stage
                    optimizer["baseline_alpha_id"] = ""
                    optimizer["baseline_expression"] = baseline_expression
                    optimizer["core_fields"] = []
                    optimizer["core_datasets"] = []
                    optimizer["shortflip_queue_size"] = len(shortflip_queue)
                    optimizer["last_round_file"] = round_file
                    self._persist_state_locked()

                if done_hit:
                    self._notify(
                        "DONE",
                        (
                            f"alpha={done_alpha_id}\n"
                            f"stage={stage}\n"
                            f"reference={baseline_expression[:200] if baseline_expression else '-'}\n"
                            f"expr={done_expr[:600]}"
                        ),
                        force=True,
                    )
                    break

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
            try:
                if "prefetch_future" in locals():
                    maybe_prefetch_future = locals().get("prefetch_future")
                    if maybe_prefetch_future is not None and not maybe_prefetch_future.done():
                        maybe_prefetch_future.cancel()
                if "prefetch_executor" in locals():
                    maybe_prefetch_executor = locals().get("prefetch_executor")
                    if maybe_prefetch_executor is not None:
                        maybe_prefetch_executor.shutdown(wait=False)
            except Exception:
                pass
            with self._lock:
                self._state["running"] = False
                self._state["stopping"] = False
                self._state["stopped_at"] = _utc_now()
                self._persist_state_locked()
                self._thread = None
            self._notify("STOP", f"DreamAlpha loop stopped at {self._state.get('stopped_at')}", force=True)
