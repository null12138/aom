from __future__ import annotations

import argparse
import json
from pathlib import Path

from ...core.fingerprint import factor_fingerprint
from .engine import (
    LibraryError,
    archive_from_factors,
    archive_from_state,
    connect,
    find_by_fingerprint,
    init_db,
    load_factors,
    load_state,
    stats as stats_query,
)


def add_library_subparser(subparsers: argparse._SubParsersAction) -> None:
    parser = subparsers.add_parser("library", help="factor library tools")
    lib_sub = parser.add_subparsers(dest="library_command")

    init_parser = lib_sub.add_parser("init", help="initialize sqlite db")
    init_parser.add_argument("--db", required=True, help="db path")

    archive_parser = lib_sub.add_parser("archive", help="archive factors or state")
    archive_parser.add_argument("--db", required=True, help="db path")
    archive_parser.add_argument("--state", default="", help="submitter state JSON")
    archive_parser.add_argument("--file", default="", help="factors JSON file")

    check_parser = lib_sub.add_parser("check", help="check if factor exists")
    check_parser.add_argument("--db", required=True, help="db path")
    check_parser.add_argument("--expression", required=True, help="factor expression")
    check_parser.add_argument("--settings-json", required=True, help="settings JSON string")

    stats_parser = lib_sub.add_parser("stats", help="library stats")
    stats_parser.add_argument("--db", required=True, help="db path")


def handle_library_command(args: argparse.Namespace) -> int:
    if args.library_command == "init":
        return library_init(args)
    if args.library_command == "archive":
        return library_archive(args)
    if args.library_command == "check":
        return library_check(args)
    if args.library_command == "stats":
        return library_stats(args)

    print("library command required: init|archive|check|stats")
    return 1


def library_init(args: argparse.Namespace) -> int:
    conn = connect(Path(args.db))
    init_db(conn)
    conn.close()
    print(f"db ready: {args.db}")
    return 0


def library_archive(args: argparse.Namespace) -> int:
    if not args.state and not args.file:
        print("--state or --file is required")
        return 1

    conn = connect(Path(args.db))
    init_db(conn)

    inserted = 0
    if args.state:
        try:
            state = load_state(Path(args.state))
        except LibraryError as exc:
            print(str(exc))
            return 1
        inserted += archive_from_state(conn, state)

    if args.file:
        try:
            factors = load_factors(Path(args.file))
        except LibraryError as exc:
            print(str(exc))
            return 1
        inserted += archive_from_factors(conn, factors)

    conn.close()
    print(f"archived: {inserted}")
    return 0


def library_check(args: argparse.Namespace) -> int:
    conn = connect(Path(args.db))
    init_db(conn)
    try:
        settings = json.loads(args.settings_json)
    except json.JSONDecodeError as exc:
        print(f"invalid settings JSON: {exc}")
        return 1

    fingerprint = factor_fingerprint(args.expression, settings)
    exists = find_by_fingerprint(conn, fingerprint)
    conn.close()
    print("exists" if exists else "missing")
    return 0


def library_stats(args: argparse.Namespace) -> int:
    conn = connect(Path(args.db))
    init_db(conn)
    data = stats_query(conn)
    conn.close()
    print(json.dumps(data, indent=2, ensure_ascii=False))
    return 0
