from __future__ import annotations

import json
from datetime import datetime, timezone
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Dict
from urllib.parse import parse_qs, urlparse

from ..modules.factor_viewer.engine import (
    find_factor,
    load_factors,
    set_nested_value,
    validate_factors,
    write_factors as write_factors_file,
)
from ..modules.library.engine import (
    archive_from_factors,
    archive_from_state,
    connect as lib_connect,
    init_db as lib_init_db,
    load_factors as lib_load_factors,
    load_state as lib_load_state,
    stats as lib_stats,
)
from ..modules.metadata.engine import fetch_datafields, fetch_operators, fetch_settings_options, save_json
from ..modules.dream_alpha.engine import DreamAlphaDaemon
from ..config import load_config
from ..modules.submitter.engine import (
    BrainApiAdapter,
    BrainBackfillAdapter,
    backfill_state,
    init_state,
    init_state_stream,
    load_factors as submit_load_factors,
    load_state as submit_load_state,
    run_submitter_stream,
    run_submitter,
    run_submitter_concurrent,
    save_state,
)
from ..modules.template_filler.cache import extract_field_names, load_datafields
from ..modules.template_filler.engine import (
    ExpansionOptions,
    TemplateError,
    build_template_skeleton,
    expand_template,
    load_template_file,
    validate_template,
    write_factors,
)
from ..modules.template_filler.cache import CacheError, apply_cache_fill

from .file_ops import (
    WEB_DIR, UPLOAD_DIR, resolve_path, get_file_kind, normalize_filename,
    resolve_kind_path, list_files, create_file, rename_file, delete_file,
    create_folder, preview_file, load_template_library, save_template_library,
    template_key, find_template_item, normalize_template_item, upsert_template_item,
    load_dataset_cache, save_dataset_cache, load_datafields_cache, save_datafields_cache,
    build_datafields_cache_key, normalize_upload_filename,
    is_valid_settings_options_payload, write_default_settings_options,
)
from .brain_api import (
    load_brain_config, fetch_datafield_types,
    fetch_datafields_preview, fetch_datafields_preview_multi, fetch_datasets
)
from .ai_api import (
    ai_generate_alphas, ai_get_knowledge, ai_process_report
)

DREAM_ALPHA_DAEMON = DreamAlphaDaemon()


def _as_bool(value: Any, default: bool = False) -> bool:
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


def run(host: str = "0.0.0.0", port: int = 8000) -> None:
    server = ThreadingHTTPServer((host, port), AOMHandler)
    print(f"AOM Web UI running on http://{host}:{port}")
    server.serve_forever()


class AOMHandler(BaseHTTPRequestHandler):
    def log_message(self, format: str, *args: Any) -> None:  # noqa: A003
        return

    def do_GET(self) -> None:  # noqa: N802
        parsed = urlparse(self.path)
        path = parsed.path
        if path in ("/", "/index.html"):
            self._serve_file(WEB_DIR / "index.html")
            return
        if path.startswith("/static/"):
            static_path = WEB_DIR / path.lstrip("/")
            self._serve_file(static_path)
            return
        if path == "/api/files/download":
            params = parse_qs(parsed.query or "")
            kind = (params.get("kind") or [None])[0]
            name = (params.get("name") or [None])[0]
            folder = (params.get("folder") or [None])[0]
            self._send_file_download(kind, name, folder)
            return
        if path == "/api/health":
            self._send_json({"ok": True})
            return
        self._send_json({"error": "not found"}, status=HTTPStatus.NOT_FOUND)

    def do_POST(self) -> None:  # noqa: N802
        parsed = urlparse(self.path)
        path = parsed.path
        try:
            payload = self._read_json()
        except json.JSONDecodeError as exc:
            self._send_json({"error": f"invalid json: {exc}"}, status=HTTPStatus.BAD_REQUEST)
            return

        try:
            result = self._dispatch(path, payload)
            self._send_json({"ok": True, "data": result})
        except Exception as exc:  # pragma: no cover - handler safety
            self._send_json({"error": str(exc)}, status=HTTPStatus.BAD_REQUEST)

    def _dispatch(self, path: str, payload: Dict[str, Any]) -> Dict[str, Any]:
        if path == "/api/template/init":
            out_path = resolve_path(payload.get("out") or "templates/template.json")
            template_id = payload.get("template_id") or out_path.stem
            template = payload.get("template") or "divide(<x/>, add(1, <y/>))"
            doc = build_template_skeleton(template_id=template_id, template=template)
            out_path.parent.mkdir(parents=True, exist_ok=True)
            out_path.write_text(json.dumps(doc, ensure_ascii=False, indent=2), encoding="utf-8")
            return {"path": str(out_path)}

        if path == "/api/template/read":
            target = resolve_path(payload.get("file") or "templates/template.json")
            content = target.read_text(encoding="utf-8")
            return {"path": str(target), "content": content}

        if path == "/api/template/save":
            target = resolve_path(payload.get("file") or "templates/template.json")
            content = payload.get("content") or ""
            try:
                json.loads(content)
            except json.JSONDecodeError as exc:
                raise ValueError(f"invalid template json: {exc}") from exc
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(content, encoding="utf-8")
            return {"path": str(target), "saved": True}

        if path == "/api/template/validate":
            target = resolve_path(payload.get("file") or "templates/template.json")
            template = load_template_file(target)
            errors = validate_template(template)
            return {"ok": len(errors) == 0, "errors": errors}

        if path == "/api/template-lib/list":
            lib_path = resolve_path(payload.get("file") or "templates/template_library.json")
            items = load_template_library(lib_path)
            return {"path": str(lib_path), "items": items}

        if path == "/api/template-lib/get":
            lib_path = resolve_path(payload.get("file") or "templates/template_library.json")
            key = payload.get("key")
            if not key:
                raise ValueError("template library key required")
            items = load_template_library(lib_path)
            item = find_template_item(items, str(key))
            if item is None:
                raise ValueError("template not found")
            return {"item": item}

        if path == "/api/template-lib/save":
            lib_path = resolve_path(payload.get("file") or "templates/template_library.json")
            raw_item = payload.get("item")
            if not isinstance(raw_item, dict):
                raise ValueError("template item must be a JSON object")
            items = load_template_library(lib_path)
            item = normalize_template_item(raw_item)
            items = upsert_template_item(items, item)
            save_template_library(lib_path, items)
            return {"path": str(lib_path), "items": items}

        if path == "/api/template-lib/delete":
            lib_path = resolve_path(payload.get("file") or "templates/template_library.json")
            key = payload.get("key")
            if not key:
                raise ValueError("template library key required")
            items = load_template_library(lib_path)
            items = [it for it in items if template_key(it) != str(key)]
            save_template_library(lib_path, items)
            return {"path": str(lib_path), "items": items}

        if path == "/api/template/expand":
            from ..modules.template_filler.engine import write_factors_bundle, Template
            template_path = resolve_path(payload["template"])
            out_path = resolve_path(payload.get("out") or "generated/factors_web.json")
            settings_override = payload.get("settings") or {}

            cfg, _ = load_config()
            defaults = cfg.get("defaults", {})
            
            # 关键：从前端加载 rules 覆盖文件内容
            template_obj = load_template_file(template_path)
            if payload.get("rules"):
                template_obj.rules = payload["rules"]
            
            factors = expand_template(
                template_obj,
                base_settings=defaults,
                settings_override=settings_override
            )
            
            # 默认大文件使用精简模式
            if len(factors) > 200:
                write_factors_bundle(out_path, factors)
                mode = "bundle"
            else:
                write_factors(out_path, factors)
                mode = "standard"
                
            return {"count": len(factors), "path": str(out_path), "mode": mode}

        if path == "/api/template/cache-fill":
            try:
                target = apply_cache_fill(
                    template_path=resolve_path(payload["template"]),
                    datafields_path=resolve_path(payload["datafields"]),
                    rules=payload.get("rules") or {},
                    default_limit=int(payload.get("limit", 50)),
                    out_path=resolve_path(payload["out"]) if payload.get("out") else None,
                )
            except (TemplateError, CacheError) as exc:
                raise ValueError(str(exc)) from exc
            return {"path": str(target)}

        if path == "/api/files/list":
            kind = payload.get("kind")
            folder = payload.get("folder")
            return list_files(kind, folder)

        if path == "/api/files/new":
            kind = payload.get("kind")
            name = payload.get("name")
            folder = payload.get("folder")
            return create_file(kind, name, folder)

        if path == "/api/files/rename":
            kind = payload.get("kind")
            name = payload.get("name")
            new_name = payload.get("new_name")
            folder = payload.get("folder")
            return rename_file(kind, name, new_name, folder)

        if path == "/api/files/delete":
            kind = payload.get("kind")
            name = payload.get("name")
            folder = payload.get("folder")
            return delete_file(kind, name, folder)

        if path == "/api/files/mkdir":
            kind = payload.get("kind")
            name = payload.get("name")
            folder = payload.get("folder")
            return create_folder(kind, name, folder)

        if path == "/api/files/preview":
            kind = payload.get("kind")
            name = payload.get("name")
            folder = payload.get("folder")
            return preview_file(kind, name, folder)

        if path == "/api/factors/list":
            factors = load_factors(resolve_path(payload["file"]))
            rows = []
            for item in factors:
                expr = item.get("expression", "")
                rows.append(
                    {
                        "factor_id": item.get("factor_id"),
                        "priority": item.get("priority"),
                        "expression": expr if len(expr) <= 80 else expr[:77] + "...",
                    }
                )
            return {"total": len(factors), "rows": rows}

        if path == "/api/factors/show":
            factors = load_factors(resolve_path(payload["file"]))
            _, factor = find_factor(factors, payload["id"])
            return {"factor": factor}

        if path == "/api/factors/validate":
            factors = load_factors(resolve_path(payload["file"]))
            errors = validate_factors(factors)
            return {"errors": errors, "ok": len(errors) == 0}

        if path == "/api/factors/edit":
            factors = load_factors(resolve_path(payload["file"]))
            idx, factor = find_factor(factors, payload["id"])
            updates = payload.get("updates") or {}
            for key, value in updates.items():
                set_nested_value(factor, key, value)
            factors[idx] = factor
            write_factors_file(Path(payload["file"]), factors)
            return {"updated": True}

        if path == "/api/submit/run":
            state_path = resolve_path(payload.get("state")) if payload.get("state") else None
            db_path = resolve_path(payload.get("db") or "db/factor_library.db")
            mode = "brain"
            max_items = payload.get("max")
            concurrency = int(payload.get("concurrency", 1))
            ordered = bool(payload.get("ordered", False))
            start_index = payload.get("start")

            if state_path and state_path.exists():
                state = submit_load_state(state_path)
                if state.get("mode") == "stream":
                    ordered = True
            else:
                factors_path = resolve_path(payload["file"])
                if ordered:
                    state = init_state_stream(
                        source_file=factors_path,
                        run_id=payload.get("run_id") or "web",
                        config={"mode": mode},
                        start_index=int(start_index) if start_index is not None else 0,
                        dedup=True,
                    )
                else:
                    factors = submit_load_factors(factors_path)
                    state = init_state(
                        factors=factors,
                        run_id=payload.get("run_id") or "web",
                        config={"mode": mode},
                        dedup=True,
                    )

            brain = load_brain_config()
            adapter = BrainApiAdapter(
                username=brain["username"],
                password=brain["password"],
                api_base=brain["api_base"],
                max_wait=int(payload.get("max_wait", 1800)),
                use_proxy=_as_bool(brain.get("use_proxy"), False),
            )

            if ordered:
                if concurrency and concurrency > 1:
                    concurrency = 1
                source_file = payload.get("file") or state.get("config", {}).get("source_file")
                if not source_file:
                    raise ValueError("ordered mode requires factors file")
                state, processed = run_submitter_stream(
                    state=state,
                    adapter=adapter,
                    source_file=resolve_path(str(source_file)),
                    max_items=int(max_items) if max_items else None,
                    retry_failed=bool(payload.get("retry_failed")),
                    start_index=int(start_index) if start_index is not None else None,
                    db_path=db_path,
                )
            elif concurrency and concurrency > 1:
                state, processed = run_submitter_concurrent(
                    state=state,
                    adapter=adapter,
                    max_items=int(max_items) if max_items else None,
                    retry_failed=bool(payload.get("retry_failed")),
                    concurrency=concurrency,
                    db_path=db_path,
                )
            else:
                state, processed = run_submitter(
                    state=state,
                    adapter=adapter,
                    max_items=int(max_items) if max_items else None,
                    retry_failed=bool(payload.get("retry_failed")),
                    db_path=db_path,
                )
            if state_path:
                save_state(state_path, state)
            return {"processed": processed, "stats": state.get("stats", {}), "state": str(state_path) if state_path else None}

        if path == "/api/submit/upload":
            name = payload.get("name") or "upload.json"
            content = payload.get("content")
            if not isinstance(content, str) or not content.strip():
                raise ValueError("upload content required")
            filename = normalize_upload_filename(name)
            UPLOAD_DIR.mkdir(parents=True, exist_ok=True)
            target = UPLOAD_DIR / filename
            try:
                json.loads(content)
            except json.JSONDecodeError as exc:
                raise ValueError(f"invalid json upload: {exc}") from exc
            target.write_text(content, encoding="utf-8")
            return {"name": filename}

        if path == "/api/submit/status":
            state = submit_load_state(resolve_path(payload.get("state") or "runs/submit_state_web.json"))
            return {"run_id": state.get("run_id"), "stats": state.get("stats", {})}

        if path == "/api/submit/backfill":
            state_path = resolve_path(payload.get("state") or "runs/submit_state_web.json")
            state = submit_load_state(state_path)
            brain = load_brain_config()
            adapter = BrainBackfillAdapter(
                username=brain["username"],
                password=brain["password"],
                api_base=brain["api_base"],
                use_proxy=_as_bool(brain.get("use_proxy"), False),
            )
            updated = backfill_state(state, adapter=adapter, force=bool(payload.get("force")))
            save_state(state_path, state)
            return {"backfilled": updated}

        if path == "/api/datafields/list":
            cache_path = resolve_path(payload.get("file") or "metadata/datafields.json")
            search = str(payload.get("search") or "").lower()
            limit = int(payload.get("limit", 50))
            items = load_datafields(cache_path)
            names = [name for name, _ in extract_field_names(items)]
            if search:
                names = [n for n in names if search in n.lower()]
            if limit > 0:
                names = names[:limit]
            return {"count": len(names), "names": names}

        if path == "/api/datafields/fetch":
            brain = load_brain_config()
            instrument = payload.get("instrument") or "EQUITY"
            region = payload.get("region") or "USA"
            delay = int(payload.get("delay", 1))
            universe = payload.get("universe") or "TOP3000"
            limit = int(payload.get("limit", 500))
            dataset_ids = payload.get("dataset_ids") or []
            use_cache = bool(payload.get("use_cache", True))
            if isinstance(dataset_ids, str):
                dataset_ids = [dataset_ids]
            dataset_ids = [str(x) for x in dataset_ids if str(x)]
            cache_key = build_datafields_cache_key(
                instrument=instrument,
                region=region,
                delay=delay,
                universe=universe,
                dataset_ids=dataset_ids,
                limit=limit,
            )
            if use_cache:
                cached = load_datafields_cache(cache_key)
                if cached is not None:
                    return {"count": len(cached), "results": cached, "cached": True}
            try:
                if dataset_ids:
                    results = fetch_datafields_preview_multi(
                        username=brain["username"],
                        password=brain["password"],
                        api_base=brain["api_base"],
                        instrument_type=instrument,
                        region=region,
                        delay=delay,
                        universe=universe,
                        dataset_ids=dataset_ids,
                        max_count=limit,
                        use_proxy=_as_bool(brain.get("use_proxy"), False),
                    )
                else:
                    dataset_id = payload.get("dataset_id") or ""
                    results = fetch_datafields_preview(
                        username=brain["username"],
                        password=brain["password"],
                        api_base=brain["api_base"],
                        instrument_type=instrument,
                        region=region,
                        delay=delay,
                        universe=universe,
                        dataset_id=dataset_id,
                        max_count=limit,
                        use_proxy=_as_bool(brain.get("use_proxy"), False),
                    )
            except Exception as exc:
                if use_cache:
                    cached = load_datafields_cache(cache_key)
                    if cached is not None:
                        return {"count": len(cached), "results": cached, "cached": True, "warning": str(exc)}
                raise
            out_path = payload.get("out")
            if out_path:
                save_json(resolve_path(out_path), {"count": len(results), "results": results})
            save_datafields_cache(cache_key, results)
            return {"count": len(results), "results": results}

        if path == "/api/datasets/list":
            brain = load_brain_config()
            instrument = payload.get("instrument") or "EQUITY"
            region = payload.get("region") or "USA"
            delay = int(payload.get("delay", 1))
            universe = payload.get("universe") or "TOP3000"
            use_cache = bool(payload.get("use_cache", True))
            cache_key = f"{instrument}|{region}|{delay}|{universe}"
            if use_cache:
                cached = load_dataset_cache(cache_key)
                if cached is not None:
                    return {"count": len(cached), "results": cached, "cached": True}
            results = fetch_datasets(
                username=brain["username"],
                password=brain["password"],
                api_base=brain["api_base"],
                instrument_type=instrument,
                region=region,
                delay=delay,
                universe=universe,
                use_proxy=_as_bool(brain.get("use_proxy"), False),
            )
            save_dataset_cache(cache_key, results)
            return {"count": len(results), "results": results, "cached": False}

        if path == "/api/library/init":
            conn = lib_connect(resolve_path(payload.get("db") or "db/factor_library.db"))
            lib_init_db(conn)
            conn.close()
            return {"db": str(resolve_path(payload.get("db") or "db/factor_library.db"))}

        if path == "/api/library/archive":
            db_path = resolve_path(payload.get("db") or "db/factor_library.db")
            conn = lib_connect(db_path)
            lib_init_db(conn)
            inserted = 0
            if payload.get("state"):
                state = lib_load_state(resolve_path(payload["state"]))
                inserted += archive_from_state(conn, state)
            if payload.get("file"):
                factors = lib_load_factors(resolve_path(payload["file"]))
                inserted += archive_from_factors(conn, factors)
            conn.close()
            return {"archived": inserted}

        if path == "/api/library/stats":
            conn = lib_connect(resolve_path(payload.get("db") or "db/factor_library.db"))
            lib_init_db(conn)
            data = lib_stats(conn)
            conn.close()
            return data

        if path == "/api/meta/operators":
            brain = load_brain_config()
            data = fetch_operators(**brain)
            out_path = payload.get("out")
            if out_path:
                save_json(resolve_path(out_path), data)
            return {"count": len(data) if isinstance(data, list) else len(data.keys())}

        if path == "/api/meta/settings":
            brain = load_brain_config()
            data = fetch_settings_options(**brain)
            out_path = payload.get("out")
            if out_path:
                save_json(resolve_path(out_path), data)
            return {"keys": list(data.keys())}

        if path == "/api/meta/datafields":
            brain = load_brain_config()
            data = fetch_datafields(
                **brain,
                instrument_type=payload.get("instrument") or "EQUITY",
                region=payload.get("region") or "USA",
                delay=int(payload.get("delay", 1)),
                universe=payload.get("universe") or "TOP3000",
                dataset_id=payload.get("dataset") or "",
                data_type=payload.get("type") or "MATRIX",
                search=payload.get("search") or "",
            )
            out_path = payload.get("out")
            if out_path:
                save_json(resolve_path(out_path), data)
            return {"count": data.get("count", 0)}

        if path == "/api/settings-options/list":
            settings_path = resolve_path(payload.get("file") or "metadata/settings_options.json")
            if not settings_path.exists():
                write_default_settings_options(settings_path)
            raw = json.loads(settings_path.read_text(encoding="utf-8"))
            if not is_valid_settings_options_payload(raw):
                write_default_settings_options(settings_path)
                raw = json.loads(settings_path.read_text(encoding="utf-8"))
            return {"raw": raw}

        if path == "/api/datafields/types":
            brain = load_brain_config()
            instrument = payload.get("instrument") or "EQUITY"
            region = payload.get("region") or "USA"
            delay = int(payload.get("delay", 1))
            universe = payload.get("universe") or "TOP3000"
            types = fetch_datafield_types(
                username=brain["username"],
                password=brain["password"],
                api_base=brain["api_base"],
                instrument_type=instrument,
                region=region,
                delay=delay,
                universe=universe,
                use_proxy=_as_bool(brain.get("use_proxy"), False),
            )
            out_path = payload.get("out")
            if out_path:
                save_json(resolve_path(out_path), {"types": types})
            return {"types": types}

        if path == "/api/ai/generate":
            return {"alphas": ai_generate_alphas(payload)}

        if path == "/api/ai/knowledge":
            return {"patterns": ai_get_knowledge()}

        if path == "/api/ai/process_report":
            return {"text": ai_process_report(payload)}

        if path == "/api/dream-alpha/start":
            fields = payload.get("fields") or []
            if not isinstance(fields, list) or not fields:
                raise ValueError("dream-alpha start requires non-empty fields")

            brain = load_brain_config()
            cfg = {
                "brain": brain,
                "fields": fields,
                "context": payload.get("context") or {},
                "report_text": payload.get("report_text") or "",
                "include_patterns": payload.get("include_patterns", True),
                "generation_count": payload.get("generation_count", payload.get("count", 5)),
                "start_mode": str(payload.get("start_mode") or "inherit"),
                "generation_attempts": payload.get("generation_attempts", 3),
                "mutation_multiplier": payload.get("mutation_multiplier", 3),
                "simulation_concurrency": payload.get("simulation_concurrency", 5),
                "max_operator_calls": payload.get("max_operator_calls", 8),
                "interval_sec": payload.get("interval_sec", 30),
                "max_wait_sec": payload.get("max_wait_sec", 1800),
                "auth_refresh_interval_sec": payload.get("auth_refresh_interval_sec", 900),
                "operators_refresh_interval_sec": payload.get("operators_refresh_interval_sec", 1800),
                "no_success_notify_every": payload.get("no_success_notify_every", 2),
                "no_success_notify_cooldown_sec": payload.get("no_success_notify_cooldown_sec", 180),
                "single_dataset_only": payload.get("single_dataset_only", True),
                "baseline_alpha_id": str(payload.get("baseline_alpha_id") or ""),
                "force_stage": str(payload.get("force_stage") or ""),
                "operators_file": str(resolve_path(payload.get("operators_file") or "metadata/operators.json")),
                "results_file": str(resolve_path(payload.get("results_file"))) if str(payload.get("results_file") or "").strip() else "",
                "pg_seed_dsn": str(payload.get("pg_seed_dsn") or brain.get("pg_seed_dsn") or ""),
                "pg_seed_table": str(payload.get("pg_seed_table") or "dream_alpha_good_seeds"),
                "pg_seed_min_fitness": payload.get("pg_seed_min_fitness", 0.9),
                "pg_seed_min_turnover": payload.get("pg_seed_min_turnover", 5),
                "pg_seed_max_turnover": payload.get("pg_seed_max_turnover", 200),
                "sharpe_abs_threshold": payload.get("sharpe_abs_threshold", 1.0),
                "fitness_threshold": payload.get("fitness_threshold", 1.0),
                "template_sharpe_threshold": payload.get("template_sharpe_threshold", 1.58),
                "max_seed_in_prompt": payload.get("max_seed_in_prompt", 20),
                "error_notify_cooldown_sec": payload.get("error_notify_cooldown_sec", 180),
                "notify_url": str(payload.get("notify_url") or "https://tgpusher.opener.eu.org/"),
                "seed_expressions": payload.get("seed_expressions") or [],
                "cursor_file": str(resolve_path(payload.get("cursor_file") or "runs/dream_alpha_cursor.json")),
                "seed_file": str(resolve_path(payload.get("seed_file") or "runs/dream_alpha_seed_library.json")),
                "high_template_file": str(resolve_path(payload.get("high_template_file") or "runs/dream_alpha_high_templates.jsonl")),
                "field_meta_cache_file": str(resolve_path(payload.get("field_meta_cache_file") or "metadata/field_meta_cache.json")),
                "use_proxy": payload.get("use_proxy", brain.get("use_proxy", False)),
            }
            return DREAM_ALPHA_DAEMON.start(cfg)

        if path == "/api/dream-alpha/stop":
            raw_wait = payload.get("wait_timeout_sec", 120)
            try:
                wait_timeout_sec = int(raw_wait)
            except (TypeError, ValueError):
                wait_timeout_sec = 120
            return DREAM_ALPHA_DAEMON.stop(wait_timeout_sec=wait_timeout_sec)

        if path == "/api/dream-alpha/status":
            return DREAM_ALPHA_DAEMON.status()

        if path == "/api/files/share-0x0":
            import requests
            file_path = resolve_path(payload["file"])
            if not file_path.exists():
                raise ValueError("file not found")
            use_proxy = _as_bool(payload.get("use_proxy"), False)
            
            with file_path.open("rb") as f:
                with requests.Session() as sess:
                    sess.trust_env = use_proxy
                    resp = sess.post("https://0x0.st", files={"file": f}, timeout=15)
            
            if resp.status_code == 200:
                return {"url": resp.text.strip()}
            else:
                raise RuntimeError(f"0x0.st upload failed: {resp.status_code}")

        raise ValueError("unknown endpoint")

    def _serve_file(self, path: Path) -> None:
        if not path.exists():
            self._send_json({"error": "not found"}, status=HTTPStatus.NOT_FOUND)
            return
        try:
            data = path.read_bytes()
        except Exception:
            self._send_json({"error": "failed to read file"}, status=HTTPStatus.INTERNAL_SERVER_ERROR)
            return
        self.send_response(HTTPStatus.OK)
        ext = path.suffix.lower()
        if ext == ".css":
            content_type = "text/css; charset=utf-8"
        elif ext == ".js":
            content_type = "application/javascript; charset=utf-8"
        else:
            content_type = "text/html; charset=utf-8"
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def _send_file_download(self, kind: str | None, name: str | None, folder: str | None = None) -> None:
        try:
            get_file_kind(kind)
            filename = normalize_filename(kind, name)
            path = resolve_kind_path(kind, filename, folder)
            if not path.exists():
                self._send_json({"error": "file not found"}, status=HTTPStatus.NOT_FOUND)
                return
            data = path.read_bytes()
        except Exception as exc:
            self._send_json({"error": str(exc)}, status=HTTPStatus.BAD_REQUEST)
            return
        suffix = path.suffix.lower()
        if suffix == ".json":
            content_type = "application/json; charset=utf-8"
        elif suffix in {".txt", ".log", ".csv", ".toml"}:
            content_type = "text/plain; charset=utf-8"
        else:
            content_type = "application/octet-stream"
        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Disposition", f'attachment; filename="{path.name}"')
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def _read_json(self) -> Dict[str, Any]:
        length = int(self.headers.get("Content-Length", "0"))
        if length == 0:
            return {}
        raw = self.rfile.read(length).decode("utf-8")
        if not raw:
            return {}
        return json.loads(raw)

    def _send_json(self, data: Dict[str, Any], status: HTTPStatus = HTTPStatus.OK) -> None:
        payload = json.dumps(data, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)
