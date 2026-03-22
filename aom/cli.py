from __future__ import annotations

import argparse
import json
import sys
from typing import Optional

from .config import ConfigError, load_config, mask_config
from .logging import configure_logging
from .modules.factor_viewer import add_factor_subparser, handle_factor_command
from .modules.library import add_library_subparser, handle_library_command
from .modules.metadata import add_meta_subparser, handle_meta_command
from .modules.submitter import add_submit_subparser, handle_submit_command
from .modules.template_filler import add_template_subparser, handle_template_command


def _print_not_implemented(name: str) -> int:
    print(f"{name} is not implemented in v0.1.")
    return 0


def cmd_config(_: argparse.Namespace) -> int:
    try:
        cfg, path = load_config()
    except ConfigError as exc:
        print(str(exc))
        return 1

    masked = mask_config(cfg)
    print(f"Config: {path}")
    print(json.dumps(masked, indent=2, ensure_ascii=False))
    return 0


def run_tui() -> int:
    try:
        from .app import AOMApp
    except ModuleNotFoundError:
        print("Textual is not installed. Run: pip install -r requirements.txt")
        return 1

    app = AOMApp()
    app.run()
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="aom")
    subparsers = parser.add_subparsers(dest="command")

    subparsers.add_parser("tui")
    web_parser = subparsers.add_parser("web", help="start web ui")
    web_parser.add_argument("--host", default="0.0.0.0", help="bind host")
    web_parser.add_argument("--port", type=int, default=8000, help="bind port")
    add_template_subparser(subparsers)
    add_factor_subparser(subparsers)
    add_submit_subparser(subparsers)
    add_library_subparser(subparsers)
    add_meta_subparser(subparsers)
    subparsers.add_parser("config")

    return parser


def main(argv: Optional[list[str]] = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    configure_logging(level="INFO")

    if args.command in (None, "tui"):
        return run_tui()
    if args.command == "config":
        return cmd_config(args)
    if args.command == "web":
        from .webui import run as run_web

        run_web(host=args.host, port=args.port)
        return 0
    if args.command == "template":
        return handle_template_command(args)
    if args.command == "factors":
        return handle_factor_command(args)
    if args.command == "submit":
        return handle_submit_command(args)
    if args.command == "library":
        return handle_library_command(args)
    if args.command == "meta":
        return handle_meta_command(args)

    parser.print_help()
    return 1


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
