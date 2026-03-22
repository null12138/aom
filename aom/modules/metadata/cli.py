from __future__ import annotations

import argparse
from pathlib import Path

from ...config import ConfigError, load_config
from .engine import fetch_datafields, fetch_operators, fetch_settings_options, save_json


def add_meta_subparser(subparsers: argparse._SubParsersAction) -> None:
    parser = subparsers.add_parser("meta", help="metadata downloader")
    meta_sub = parser.add_subparsers(dest="meta_command")

    ops_parser = meta_sub.add_parser("operators", help="download operators")
    ops_parser.add_argument("--out", required=True, help="output JSON")

    settings_parser = meta_sub.add_parser("settings", help="download settings options")
    settings_parser.add_argument("--out", required=True, help="output JSON")

    fields_parser = meta_sub.add_parser("datafields", help="download datafields")
    fields_parser.add_argument("--out", required=True, help="output JSON")
    fields_parser.add_argument("--instrument", default="EQUITY", help="instrument type")
    fields_parser.add_argument("--region", default="USA", help="region")
    fields_parser.add_argument("--delay", type=int, default=1, help="delay")
    fields_parser.add_argument("--universe", default="TOP3000", help="universe")
    fields_parser.add_argument("--dataset", default="", help="dataset id")
    fields_parser.add_argument("--type", default="MATRIX", help="data type")
    fields_parser.add_argument("--search", default="", help="search term")


def handle_meta_command(args: argparse.Namespace) -> int:
    if args.meta_command == "operators":
        return meta_operators(args)
    if args.meta_command == "settings":
        return meta_settings(args)
    if args.meta_command == "datafields":
        return meta_datafields(args)

    print("meta command required: operators|settings|datafields")
    return 1


def meta_operators(args: argparse.Namespace) -> int:
    try:
        brain = _load_brain_config()
    except ConfigError as exc:
        print(str(exc))
        return 1
    data = fetch_operators(**brain)
    save_json(Path(args.out), data)
    print(f"saved: {args.out}")
    return 0


def meta_settings(args: argparse.Namespace) -> int:
    try:
        brain = _load_brain_config()
    except ConfigError as exc:
        print(str(exc))
        return 1
    data = fetch_settings_options(**brain)
    save_json(Path(args.out), data)
    print(f"saved: {args.out}")
    return 0


def meta_datafields(args: argparse.Namespace) -> int:
    try:
        brain = _load_brain_config()
    except ConfigError as exc:
        print(str(exc))
        return 1
    data = fetch_datafields(
        **brain,
        instrument_type=args.instrument,
        region=args.region,
        delay=args.delay,
        universe=args.universe,
        dataset_id=args.dataset,
        data_type=args.type,
        search=args.search,
    )
    save_json(Path(args.out), data)
    print(f"saved: {args.out}")
    return 0


def _load_brain_config() -> dict:
    cfg, _ = load_config()
    brain = cfg.get("brain", {})
    if not isinstance(brain, dict):
        raise ConfigError("brain config must be a table")
    username = brain.get("username")
    password = brain.get("password")
    api_base = brain.get("api_base") or "https://api.worldquantbrain.com"
    if not username or not password:
        raise ConfigError("brain username/password missing in config")
    return {
        "username": str(username),
        "password": str(password),
        "api_base": str(api_base),
    }
