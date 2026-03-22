from __future__ import annotations

import argparse
import json
from datetime import datetime
from pathlib import Path
from typing import Any, Dict

from ...config import ConfigError, load_config
from .engine import (
    ExpansionOptions,
    TemplateError,
    build_template_skeleton,
    expand_template,
    load_template_file,
    write_factors,
)
from .cache import CacheError, apply_cache_fill


def add_template_subparser(subparsers: argparse._SubParsersAction) -> None:
    parser = subparsers.add_parser("template", help="template tools")
    template_sub = parser.add_subparsers(dest="template_command")

    init_parser = template_sub.add_parser("init", help="create template skeleton")
    init_parser.add_argument("--out", required=True, help="output template JSON")
    init_parser.add_argument("--template-id", default="", help="template id")
    init_parser.add_argument(
        "--template",
        default="divide(<x/>, add(1, <y/>))",
        help="template string with placeholders like <field/>",
    )

    expand_parser = template_sub.add_parser("expand", help="expand template to factors")
    expand_parser.add_argument("--template", required=True, help="template JSON file")
    expand_parser.add_argument("--out", default="", help="output factors JSON")
    expand_parser.add_argument("--append", action="store_true", help="append to output if exists")
    expand_parser.add_argument("--max", type=int, default=0, help="max combinations")
    expand_parser.add_argument("--settings-json", default="", help="override settings JSON string")
    expand_parser.add_argument("--settings-file", default="", help="override settings JSON file")

    cache_parser = template_sub.add_parser("cache-fill", help="fill template from cached datafields")
    cache_parser.add_argument("--template", required=True, help="template JSON file")
    cache_parser.add_argument("--datafields", required=True, help="datafields cache JSON")
    cache_parser.add_argument("--rules", default="", help="rules JSON file")
    cache_parser.add_argument("--rules-json", default="", help="rules JSON string")
    cache_parser.add_argument("--limit", type=int, default=50, help="default limit per placeholder")
    cache_parser.add_argument("--out", default="", help="output template JSON (default overwrite)")


def handle_template_command(args: argparse.Namespace) -> int:
    if args.template_command == "init":
        return template_init(args)
    if args.template_command == "expand":
        return template_expand(args)
    if args.template_command == "cache-fill":
        return template_cache_fill(args)

    print("template command required: init|expand|cache-fill")
    return 1


def template_init(args: argparse.Namespace) -> int:
    out_path = Path(args.out).resolve()
    template_id = args.template_id or out_path.stem
    doc = build_template_skeleton(template_id=template_id, template=args.template)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(doc, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"template created: {out_path}")
    return 0


def template_expand(args: argparse.Namespace) -> int:
    try:
        cfg, _ = load_config()
    except ConfigError as exc:
        print(str(exc))
        return 1

    defaults = cfg.get("defaults", {})
    if not isinstance(defaults, dict):
        print("config defaults must be a table")
        return 1

    try:
        overrides = _load_settings_override(args)
    except json.JSONDecodeError as exc:
        print(f"invalid settings JSON: {exc}")
        return 1

    try:
        template = load_template_file(Path(args.template))
        factors = expand_template(
            template,
            base_settings=defaults,
            settings_override=overrides,
            options=ExpansionOptions(max_combinations=args.max or None),
        )
    except TemplateError as exc:
        print(f"template error: {exc}")
        return 1

    out_path = Path(args.out) if args.out else _default_output_path()
    write_factors(out_path, factors, append=args.append)
    print(f"wrote {len(factors)} factors to {out_path}")
    return 0


def template_cache_fill(args: argparse.Namespace) -> int:
    rules = {}
    if args.rules_json:
        try:
            rules = json.loads(args.rules_json)
        except json.JSONDecodeError as exc:
            print(f"invalid rules json: {exc}")
            return 1
    if args.rules:
        try:
            rules = json.loads(Path(args.rules).read_text(encoding="utf-8"))
        except Exception as exc:
            print(f"invalid rules file: {exc}")
            return 1

    try:
        out_path = Path(args.out) if args.out else None
        target = apply_cache_fill(
            template_path=Path(args.template),
            datafields_path=Path(args.datafields),
            rules=rules,
            default_limit=int(args.limit),
            out_path=out_path,
        )
    except (TemplateError, CacheError) as exc:
        print(f"cache-fill error: {exc}")
        return 1

    print(f"template updated: {target}")
    return 0


def _load_settings_override(args: argparse.Namespace) -> Dict[str, Any]:
    if args.settings_json:
        return json.loads(args.settings_json)
    if args.settings_file:
        return json.loads(Path(args.settings_file).read_text(encoding="utf-8"))
    return {}


def _default_output_path() -> Path:
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    return Path("generated") / f"factors_{stamp}.json"
