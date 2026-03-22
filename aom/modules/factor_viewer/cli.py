from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any, Dict

from .engine import FactorError, find_factor, load_factors, set_nested_value, validate_factors, write_factors


def add_factor_subparser(subparsers: argparse._SubParsersAction) -> None:
    parser = subparsers.add_parser("factors", help="factor viewer tools")
    factor_sub = parser.add_subparsers(dest="factor_command")

    list_parser = factor_sub.add_parser("list", help="list factors")
    list_parser.add_argument("--file", required=True, help="factors JSON file")

    show_parser = factor_sub.add_parser("show", help="show factor JSON")
    show_parser.add_argument("--file", required=True, help="factors JSON file")
    show_parser.add_argument("--id", required=True, help="factor_id")

    validate_parser = factor_sub.add_parser("validate", help="validate factors JSON")
    validate_parser.add_argument("--file", required=True, help="factors JSON file")

    edit_parser = factor_sub.add_parser("edit", help="edit a factor")
    edit_parser.add_argument("--file", required=True, help="factors JSON file")
    edit_parser.add_argument("--id", required=True, help="factor_id")
    edit_parser.add_argument("--set", action="append", default=[], help="set key=value (supports settings.xxx)")


def handle_factor_command(args: argparse.Namespace) -> int:
    if args.factor_command == "list":
        return factors_list(args)
    if args.factor_command == "show":
        return factors_show(args)
    if args.factor_command == "validate":
        return factors_validate(args)
    if args.factor_command == "edit":
        return factors_edit(args)

    print("factors command required: list|show|validate|edit")
    return 1


def factors_list(args: argparse.Namespace) -> int:
    try:
        factors = load_factors(Path(args.file))
    except FactorError as exc:
        print(str(exc))
        return 1

    print(f"total: {len(factors)}")
    for item in factors:
        factor_id = item.get("factor_id", "?")
        priority = item.get("priority", "?")
        expression = item.get("expression", "")
        short_expr = expression if len(expression) <= 80 else expression[:77] + "..."
        print(f"{factor_id} | p={priority} | {short_expr}")
    return 0


def factors_show(args: argparse.Namespace) -> int:
    try:
        factors = load_factors(Path(args.file))
        _, factor = find_factor(factors, args.id)
    except FactorError as exc:
        print(str(exc))
        return 1

    print(json.dumps(factor, indent=2, ensure_ascii=False))
    return 0


def factors_validate(args: argparse.Namespace) -> int:
    try:
        factors = load_factors(Path(args.file))
    except FactorError as exc:
        print(str(exc))
        return 1

    errors = validate_factors(factors)
    if errors:
        for err in errors:
            print(err)
        return 1

    print("ok")
    return 0


def factors_edit(args: argparse.Namespace) -> int:
    if not args.set:
        print("--set is required")
        return 1

    try:
        factors = load_factors(Path(args.file))
        idx, factor = find_factor(factors, args.id)
    except FactorError as exc:
        print(str(exc))
        return 1

    for raw in args.set:
        if "=" not in raw:
            print(f"invalid set expression: {raw}")
            return 1
        key, value = raw.split("=", 1)
        parsed_value: Any = _parse_value(value)
        set_nested_value(factor, key, parsed_value)

    factors[idx] = factor
    write_factors(Path(args.file), factors)
    print("updated")
    return 0


def _parse_value(value: str) -> Any:
    lowered = value.lower()
    if lowered in {"true", "false"}:
        return lowered == "true"
    if lowered == "null":
        return None
    try:
        if "." in value:
            return float(value)
        return int(value)
    except ValueError:
        return value
