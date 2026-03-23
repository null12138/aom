from __future__ import annotations

import argparse
import json
import sys
import uuid
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List

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
    run_parser.add_argument("--file", default="", help="factors JSON file (for new run, optional in interactive wizard)")
    run_parser.add_argument("--state", default="", help="state JSON file (optional, auto-generated in wizard)")
    run_parser.add_argument("--run-id", default="", help="run id when creating state")
    run_parser.add_argument("--max-wait", type=int, default=1800, help="max wait seconds for brain mode")
    run_parser.add_argument("--concurrency", type=int, default=1, help="concurrent workers (1-8)")
    run_parser.add_argument("--batch-size", type=int, default=1, help="batch size for multiple mode (1-10)")
    run_parser.add_argument("--ordered", action="store_true", help="ordered sequential by index")
    run_parser.add_argument("--start", type=int, default=-1, help="start index (ordered: absolute index, queue/concurrent: skip first N pending)")
    run_parser.add_argument("--legacy-queue", action="store_true", help="use legacy queue mode")
    run_parser.add_argument("--retry-failed", action="store_true", help="retry failed items marked retryable")
    run_parser.add_argument("--library", default="db/factor_library.db", help="library db path for persistence/dedup")
    run_parser.add_argument("--interactive", action="store_true", help="interactive numeric picker for settings overrides")
    run_parser.add_argument("--instrument-type", help="override instrument type (e.g., EQUITY)")
    run_parser.add_argument("--region", help="override region (e.g., USA, CHN)")
    run_parser.add_argument("--delay", type=int, help="override delay (e.g., 1)")
    run_parser.add_argument("--universe", help="override universe (e.g., TOP3000)")
    run_parser.add_argument("--neutralization", help="override neutralization (e.g., INDUSTRY, FAST)")

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
    try:
        if not _prepare_run_wizard_args(args):
            return 1
    except KeyboardInterrupt:
        print("\n已取消提交流程。")
        return 130

    state_path = Path(args.state) if args.state else None
    ordered = bool(args.ordered) and not args.legacy_queue
    library_path = Path(args.library) if args.library else None

    overrides = {}
    if args.instrument_type: overrides["instrumentType"] = args.instrument_type
    if args.region: overrides["region"] = args.region
    if args.delay is not None: overrides["delay"] = args.delay
    if args.universe: overrides["universe"] = args.universe
    if args.neutralization: overrides["neutralization"] = args.neutralization
    if args.interactive:
        if not sys.stdin.isatty():
            print("--interactive requires a TTY terminal")
            return 1
        overrides = _interactive_settings_overrides(overrides)

    if state_path and state_path.exists():
        try:
            state = load_state(state_path)
        except SubmitterError as exc:
            print(str(exc))
            return 1
        if state.get("mode") == "stream":
            if not ordered:
                print("state mode=stream detected; forcing ordered mode (sequential). --batch-size/--concurrency are ignored.")
            ordered = True
    else:
        if not args.file:
            print("--file is required when state does not exist")
            return 1
        if not args.legacy_queue and _should_stream(Path(args.file)):
            ordered = True
            print("auto: large file detected, using ordered stream mode (sequential). --batch-size/--concurrency are ignored.")
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
            use_proxy=_as_bool(brain_cfg.get("use_proxy", False), False),
        )
    except Exception as exc:
        print(f"brain adapter error: {exc}")
        return 1
    
    db_to_pass = library_path if library_path else None
    queue_start = args.start if args.start >= 0 else 0

    try:
        if ordered:
            print("run mode: ordered stream (sequential submit)")
            source_file = args.file or str(state.get("config", {}).get("source_file") or "")
            if not source_file:
                print("--file is required for ordered mode")
                return 1
            if args.start >= 0:
                start_index = args.start
            else:
                start_index = int(state.get("cursor", 0) or 0)
            state, processed = run_submitter_stream(
                state=state,
                adapter=adapter,
                source_file=Path(source_file),
                start_index=start_index,
                retry_failed=bool(args.retry_failed),
                db_path=db_to_pass,
            )
        elif (args.concurrency and args.concurrency > 1) or (args.batch_size and args.batch_size > 1):
            print(f"run mode: concurrent multiple (concurrency={args.concurrency}, batch_size={args.batch_size})")
            if queue_start > 0:
                print(f"queue start offset: {queue_start}")
            state, processed = run_submitter_concurrent(
                state=state,
                adapter=adapter,
                concurrency=args.concurrency,
                batch_size=args.batch_size,
                start_index=queue_start,
                retry_failed=bool(args.retry_failed),
                db_path=db_to_pass,
                source_file=Path(args.file) if args.file else None,
            )
        else:
            print("run mode: single submit")
            if queue_start > 0:
                print(f"queue start offset: {queue_start}")
            state, processed = run_submitter(
                state=state,
                adapter=adapter,
                start_index=queue_start,
                retry_failed=bool(args.retry_failed),
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
            use_proxy=_as_bool(brain_cfg.get("use_proxy", False), False),
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


def _as_bool(value: object, default: bool = False) -> bool:
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


def _load_settings_options() -> Dict[str, Any]:
    candidates = [
        Path("metadata/settings_options.json"),
        Path(__file__).resolve().parents[3] / "metadata" / "settings_options.json",
    ]
    for path in candidates:
        try:
            if path.exists():
                raw = json.loads(path.read_text(encoding="utf-8"))
                if isinstance(raw, dict):
                    return raw
        except Exception:
            continue
    return {}


def _discover_factor_files() -> List[str]:
    candidates: List[str] = []
    seen = set()
    roots = [Path("generated"), Path("runs/uploads"), Path(".")]
    for root in roots:
        if not root.exists():
            continue
        try:
            files = sorted(
                [p for p in root.glob("*.json") if p.is_file()],
                key=lambda p: p.stat().st_mtime,
                reverse=True,
            )
        except Exception:
            files = []
        for path in files:
            text = str(path)
            if text in seen:
                continue
            seen.add(text)
            candidates.append(text)
    return candidates


def _prompt_text(prompt: str, default: str) -> str:
    raw = input(f"{prompt} (默认: {default}): ").strip()
    return raw or default


def _prompt_int(prompt: str, default: int, min_value: int, max_value: int) -> int:
    while True:
        raw = input(f"{prompt} [{min_value}-{max_value}] (默认: {default}): ").strip()
        if not raw:
            return default
        if raw.isdigit():
            value = int(raw)
            if min_value <= value <= max_value:
                return value
        print("输入无效，请输入范围内的整数。")


def _prompt_yes_no(prompt: str, default: bool) -> bool:
    mark = "Y/n" if default else "y/N"
    raw = input(f"{prompt} ({mark}): ").strip().lower()
    if not raw:
        return default
    return raw in {"y", "yes", "1", "true"}


def _default_state_path(file_path: str) -> str:
    base = Path(file_path).stem.strip() or datetime.now().strftime("%Y%m%d_%H%M%S")
    return str(Path("runs") / f"submit_state_{base}.json")


def _choose_factor_file() -> str:
    files = _discover_factor_files()
    if files:
        print("\n选择因子文件:")
        for idx, path in enumerate(files[:20], start=1):
            print(f"  {idx}. {path}")
        print("  0. 手动输入路径")
        while True:
            raw = input(f"输入序号 [0-{min(20, len(files))}]，默认1: ").strip()
            if not raw:
                return files[0]
            if raw.isdigit():
                pick = int(raw)
                if pick == 0:
                    break
                if 1 <= pick <= min(20, len(files)):
                    return files[pick - 1]
            print("输入无效，请输入数字序号。")

    while True:
        path = input("输入因子文件路径: ").strip()
        if not path:
            print("因子文件不能为空。")
            continue
        if Path(path).exists():
            return path
        print("文件不存在，请重试。")


def _prepare_run_wizard_args(args: argparse.Namespace) -> bool:
    # One-command flow: `python3 -m aom submit run` in TTY opens interactive wizard.
    wants_wizard = (
        not args.file
        and not args.state
        and not args.interactive
        and not args.instrument_type
        and not args.region
        and args.delay is None
        and not args.universe
        and not args.neutralization
    )
    if not wants_wizard:
        return True
    if not sys.stdin.isatty():
        print("--file is required when running non-interactively")
        return False

    print("进入提交流程向导（单命令模式）")
    selected_file = _choose_factor_file()
    args.file = selected_file
    args.state = _prompt_text("状态文件路径", _default_state_path(selected_file))
    args.concurrency = _prompt_int("并发数", int(args.concurrency), 1, 8)
    args.batch_size = _prompt_int("批大小", int(args.batch_size), 1, 10)
    args.max_wait = _prompt_int("单次最大等待秒", int(args.max_wait), 60, 7200)
    args.ordered = _prompt_yes_no("使用有序流式模式(ordered)", bool(args.ordered))
    args.interactive = _prompt_yes_no("继续用数字选择 region/universe/neutralization", True)
    return True


def _extract_choice_values(node: Any) -> List[str]:
    if not isinstance(node, list):
        return []
    out: List[str] = []
    for item in node:
        value: Any
        if isinstance(item, dict):
            value = item.get("value", item.get("label"))
        else:
            value = item
        if value is None:
            continue
        text = str(value).strip()
        if text:
            out.append(text)
    return out


def _dict_get_ci(node: Any, key: str) -> Any:
    if not isinstance(node, dict):
        return None
    if key in node:
        return node[key]
    needle = str(key).strip().upper()
    for k, v in node.items():
        if str(k).strip().upper() == needle:
            return v
    return None


def _choices_for_key(options: Dict[str, Any], key: str, instrument_type: str = "", region: str = "") -> List[str]:
    node = options.get(key) if isinstance(options, dict) else None
    choices = node.get("choices") if isinstance(node, dict) else None
    if choices is None:
        return []

    # Shape A: choices is already a list of selectable values.
    if isinstance(choices, list):
        return _extract_choice_values(choices)

    if not isinstance(choices, dict):
        return []

    # Shape B: nested by instrumentType.
    inst_layer = _dict_get_ci(choices, "instrumentType")
    if inst_layer is None:
        inst_layer = choices

    # Some schemas keep instrumentType as a plain list.
    if isinstance(inst_layer, list):
        return _extract_choice_values(inst_layer)

    if not isinstance(inst_layer, dict):
        return []

    inst_key = (instrument_type or "EQUITY").strip()
    inst_node = _dict_get_ci(inst_layer, inst_key)
    if inst_node is None:
        # Fallback to first available instrument branch.
        try:
            inst_node = next(iter(inst_layer.values()))
        except StopIteration:
            return []

    # Region choice itself may be directly a list.
    if isinstance(inst_node, list):
        return _extract_choice_values(inst_node)

    if not isinstance(inst_node, dict):
        return []

    # For keys like delay/universe/neutralization, usually nested by region.
    region_layer = _dict_get_ci(inst_node, "region")
    if region_layer is None:
        # Some schemas may directly use region keys at this level.
        region_layer = inst_node

    if isinstance(region_layer, list):
        return _extract_choice_values(region_layer)
    if not isinstance(region_layer, dict):
        return []

    region_key = (region or "USA").strip()
    region_node = _dict_get_ci(region_layer, region_key)
    if region_node is None:
        try:
            region_node = next(iter(region_layer.values()))
        except StopIteration:
            return []
    return _extract_choice_values(region_node)


def _pick_choice(options: List[str], default_value: str, title: str) -> str:
    if not options:
        return default_value
    default = str(default_value or "").strip()
    if not default:
        default = options[0]

    print(f"\n{title}:")
    for idx, value in enumerate(options, start=1):
        marker = " (默认)" if value.upper() == default.upper() else ""
        print(f"  {idx}. {value}{marker}")

    while True:
        raw = input(f"输入序号 [1-{len(options)}]，回车使用默认({default}): ").strip()
        if not raw:
            return default
        if raw.isdigit():
            pick = int(raw)
            if 1 <= pick <= len(options):
                return options[pick - 1]
        print("输入无效，请输入数字序号。")


def _choices_instrument_type(options: Dict[str, Any]) -> List[str]:
    return _choices_for_key(options, "instrumentType", instrument_type="", region="")


def _choices_region(options: Dict[str, Any], instrument_type: str) -> List[str]:
    return _choices_for_key(options, "region", instrument_type=instrument_type, region="")


def _choices_region_dependent(options: Dict[str, Any], key: str, instrument_type: str, region: str) -> List[str]:
    return _choices_for_key(options, key, instrument_type=instrument_type, region=region)


def _interactive_settings_overrides(current: Dict[str, Any]) -> Dict[str, Any]:
    overrides = dict(current)
    options = _load_settings_options()
    if not options:
        print("未找到 metadata/settings_options.json，跳过交互选择。")
        return overrides

    inst_default = str(overrides.get("instrumentType") or "EQUITY").strip().upper()
    inst_choices = _choices_instrument_type(options) or ["EQUITY"]
    instrument_type = _pick_choice(inst_choices, inst_default, "选择 instrumentType")

    region_default = str(overrides.get("region") or "USA").strip().upper()
    region_choices = _choices_region(options, instrument_type)
    if not region_choices:
        region_choices = ["USA"]
    region = _pick_choice(region_choices, region_default, "选择 region")

    delay_default = str(overrides.get("delay") if overrides.get("delay") is not None else "1").strip()
    delay_choices = _choices_region_dependent(options, "delay", instrument_type, region) or ["1"]
    delay_text = _pick_choice(delay_choices, delay_default, "选择 delay")

    universe_default = str(overrides.get("universe") or "TOP3000").strip().upper()
    universe_choices = _choices_region_dependent(options, "universe", instrument_type, region) or ["TOP3000"]
    universe = _pick_choice(universe_choices, universe_default, "选择 universe")

    neutral_default = str(overrides.get("neutralization") or "INDUSTRY").strip().upper()
    neutral_choices = _choices_region_dependent(options, "neutralization", instrument_type, region) or ["INDUSTRY"]
    neutralization = _pick_choice(neutral_choices, neutral_default, "选择 neutralization")

    overrides["instrumentType"] = instrument_type
    overrides["region"] = region
    overrides["universe"] = universe
    overrides["neutralization"] = neutralization
    try:
        overrides["delay"] = int(delay_text)
    except ValueError:
        overrides["delay"] = delay_text

    print(
        "settings override => "
        f"instrumentType={overrides['instrumentType']} "
        f"region={overrides['region']} "
        f"delay={overrides['delay']} "
        f"universe={overrides['universe']} "
        f"neutralization={overrides['neutralization']}"
    )
    return overrides


def _load_brain_config() -> Dict[str, object]:
    cfg, _ = load_config()
    brain = cfg.get("brain", {})
    if not isinstance(brain, dict):
        raise ConfigError("brain config must be a table")
    username = brain.get("username")
    password = brain.get("password")
    api_base = brain.get("api_base") or ""
    use_proxy = brain.get("use_proxy", False)
    if not username or not password:
        raise ConfigError("brain username/password missing in config")
    return {
        "username": str(username),
        "password": str(password),
        "api_base": api_base or "https://api.worldquantbrain.com",
        "use_proxy": _as_bool(use_proxy, False),
    }
