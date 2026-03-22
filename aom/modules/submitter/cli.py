from __future__ import annotations

import argparse
import uuid
from pathlib import Path
from typing import Dict

from ...config import ConfigError, load_config
from ..library.engine import connect as lib_connect, init_db as lib_init_db, load_fingerprints as lib_load_fingerprints
from .engine import (
    BrainApiAdapter,
    BrainBackfillAdapter,
    SubmitterError,
    backfill_state,
    init_state,
    init_state_stream,
    load_factors,
    load_state,
    run_submitter_concurrent,
    run_submitter,
    run_submitter_stream,
    save_state,
)


def add_submit_subparser(subparsers: argparse._SubParsersAction) -> None:
    parser = subparsers.add_parser("submit", help="submitter tools")
    submit_sub = parser.add_subparsers(dest="submit_command")

    init_parser = submit_sub.add_parser("init", help="initialize a submit state")
    init_parser.add_argument("--file", required=True, help="factors JSON file")
    init_parser.add_argument("--state", required=True, help="state JSON output")
    init_parser.add_argument("--run-id", default="", help="run id")
    init_parser.add_argument("--no-dedup", action="store_true", help="disable dedup")
    init_parser.add_argument("--library", default="", help="library db path for dedup")
    init_parser.add_argument("--ordered", action="store_true", help="create ordered stream state")
    init_parser.add_argument("--start", type=int, default=0, help="start index for ordered mode")

    run_parser = submit_sub.add_parser("run", help="run submitter")
    run_parser.add_argument("--file", default="", help="factors JSON file (for new run)")
    run_parser.add_argument("--state", default="", help="state JSON file (optional)")
    run_parser.add_argument("--run-id", default="", help="run id when creating state")
    run_parser.add_argument("--max-wait", type=int, default=1800, help="max wait seconds for brain mode")
    run_parser.add_argument("--concurrency", type=int, default=1, help="concurrent workers (1-8)")
    run_parser.add_argument("--batch-size", type=int, default=1, help="batch size for multiple mode (1-10)")
    run_parser.add_argument("--ordered", action="store_true", help="ordered sequential by index")
    run_parser.add_argument("--start", type=int, default=-1, help="start index (ordered mode)")
    run_parser.add_argument("--legacy-queue", action="store_true", help="use legacy queue mode")
    run_parser.add_argument("--retry-failed", action="store_true", help="retry failed items marked retryable")
    run_parser.add_argument("--library", default="db/factor_library.db", help="library db path for persistence/dedup")
    run_parser.add_argument("--region", help="override region (e.g., USA, CHN)")
    run_parser.add_argument("--universe", help="override universe (e.g., TOP3000)")

    status_parser = submit_sub.add_parser("status", help="show submitter status")
    status_parser.add_argument("--state", required=True, help="state JSON file")

    backfill_parser = submit_sub.add_parser("backfill", help="backfill results from platform")
    backfill_parser.add_argument("--state", required=True, help="state JSON file")
    backfill_parser.add_argument("--force", action="store_true", help="force re-fetch results")


def handle_submit_command(args: argparse.Namespace) -> int:
    if args.submit_command == "init":
        return submit_init(args)
    if args.submit_command == "run":
        return submit_run(args)
    if args.submit_command == "status":
        return submit_status(args)
    if args.submit_command == "backfill":
        return submit_backfill(args)

    print("submit command required: init|run|status")
    return 1


def submit_init(args: argparse.Namespace) -> int:
    run_id = args.run_id or _new_run_id()
    if args.ordered:
        state = init_state_stream(
            source_file=Path(args.file),
            run_id=run_id,
            config=_default_config(),
            start_index=args.start,
            dedup=not args.no_dedup,
        )
    else:
        factors_path = Path(args.file)
        try:
            factors = load_factors(factors_path)
        except SubmitterError as exc:
            print(str(exc))
            return 1

        existing = None
        if args.library:
            conn = lib_connect(Path(args.library))
            lib_init_db(conn)
            existing = load_fingerprints(conn)
            conn.close()

        state = init_state(
            factors=factors,
            run_id=run_id,
            config=_default_config(),
            dedup=not args.no_dedup,
            existing_fingerprints=existing,
        )

    save_state(Path(args.state), state)
    print(f"state created: {args.state}")
    return 0


def submit_run(args: argparse.Namespace) -> int:
    state_path = Path(args.state) if args.state else None
    ordered = bool(args.ordered) and not args.legacy_queue
    library_path = Path(args.library) if args.library else None

    overrides = {}
    if args.region: overrides["region"] = args.region
    if args.universe: overrides["universe"] = args.universe

    if state_path and state_path.exists():
        try:
            state = load_state(state_path)
        except SubmitterError as exc:
            print(str(exc))
            return 1
        if state.get("mode") == "stream":
            ordered = True
    else:
        if not args.file:
            print("--file is required when state does not exist")
            return 1
        if not args.legacy_queue and _should_stream(Path(args.file)):
            ordered = True
            print("auto: large file detected, using ordered stream mode")
        run_id = args.run_id or _new_run_id()
        if ordered:
            state = init_state_stream(
                source_file=Path(args.file),
                run_id=run_id,
                config=_default_config(),
                start_index=args.start if args.start >= 0 else 0,
                dedup=True,
            )
        else:
            try:
                factors = load_factors(Path(args.file))
            except SubmitterError as exc:
                print(str(exc))
                return 1
            existing = None
            if library_path and library_path.exists():
                conn = lib_connect(library_path)
                existing = lib_load_fingerprints(conn)
                conn.close()
            state = init_state(
                factors=factors,
                run_id=run_id,
                config=_default_config(),
                dedup=True,
                existing_fingerprints=existing,
            )

    try:
        brain_cfg = _load_brain_config()
        adapter = BrainApiAdapter(
            username=brain_cfg["username"],
            password=brain_cfg["password"],
            api_base=brain_cfg["api_base"],
            max_wait=args.max_wait,
            settings_override=overrides,
        )
    except Exception as exc:
        print(f"brain adapter error: {exc}")
        return 1
    
    db_to_pass = library_path if library_path else None

    try:
        if ordered:
            source_file = args.file or str(state.get("config", {}).get("source_file") or "")
            if not source_file:
                print("--file is required for ordered mode")
                return 1
            start_index = args.start if args.start >= 0 else None
            state, processed = run_submitter_stream(
                state=state,
                adapter=adapter,
                source_file=Path(source_file),
                start_index=start_index,
                db_path=db_to_pass,
            )
        elif (args.concurrency and args.concurrency > 1) or (args.batch_size and args.batch_size > 1):
            state, processed = run_submitter_concurrent(
                state=state,
                adapter=adapter,
                concurrency=args.concurrency,
                batch_size=args.batch_size,
                db_path=db_to_pass,
                source_file=Path(args.file) if args.file else None,
            )
        else:
            state, processed = run_submitter(
                state=state,
                adapter=adapter,
                db_path=db_to_pass,
            )
    except SubmitterError as exc:
        print(str(exc))
        return 1

    if state_path:
        save_state(state_path, state)
        print(f"state saved: {state_path}")
    
    stats = state.get("stats", {})
    print(f"processed: {processed}")
    cursor = stats.get("cursor")
    if cursor is not None:
        print(f"cursor={cursor} completed={stats.get('completed', 0)} failed={stats.get('failed', 0)}")
    else:
        print(f"queue={stats.get('queue', 0)} completed={stats.get('completed', 0)} failed={stats.get('failed', 0)}")
    
    return 0


def submit_status(args: argparse.Namespace) -> int:
    try:
        state = load_state(Path(args.state))
    except SubmitterError as exc:
        print(str(exc))
        return 1

    stats = state.get("stats", {})
    print(f"run_id: {state.get('run_id')}")
    print(f"queue={stats.get('queue', 0)} completed={stats.get('completed', 0)} failed={stats.get('failed', 0)}")
    return 0


def submit_backfill(args: argparse.Namespace) -> int:
    try:
        state = load_state(Path(args.state))
    except SubmitterError as exc:
        print(str(exc))
        return 1

    try:
        brain_cfg = _load_brain_config()
        adapter = BrainBackfillAdapter(
            username=brain_cfg["username"],
            password=brain_cfg["password"],
            api_base=brain_cfg["api_base"],
        )
    except Exception as exc:
        print(f"brain adapter error: {exc}")
        return 1

    updated = backfill_state(state, adapter=adapter, force=args.force)
    save_state(Path(args.state), state)
    print(f"backfilled: {updated}")
    return 0


def _default_config() -> Dict[str, str]:
    return {"mode": "brain"}


def _new_run_id() -> str:
    return uuid.uuid4().hex[:8]


def _should_stream(path: Path) -> bool:
    try:
        return path.stat().st_size >= 2_000_000
    except OSError:
        return False


def _load_brain_config() -> Dict[str, str]:
    cfg, _ = load_config()
    brain = cfg.get("brain", {})
    if not isinstance(brain, dict):
        raise ConfigError("brain config must be a table")
    username = brain.get("username")
    password = brain.get("password")
    api_base = brain.get("api_base") or ""
    if not username or not password:
        raise ConfigError("brain username/password missing in config")
    return {
        "username": str(username),
        "password": str(password),
        "api_base": api_base or "https://api.worldquantbrain.com",
    }
