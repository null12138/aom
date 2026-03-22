from __future__ import annotations

import os
import json
import threading
from pathlib import Path
from datetime import datetime
from typing import Any, Dict, Optional, List, Tuple

from textual.app import ComposeResult
from textual.containers import Container, Horizontal, ScrollableContainer, Vertical
from textual.widgets import Button, Checkbox, Input, Label, Select, Static
from textual import work

from ..config import DEFAULT_CONFIG_PATH, ConfigError, load_config
from ..modules.submitter.engine import (
    BrainApiAdapter,
    BrainBackfillAdapter,
    SubmitterError,
    backfill_state,
    init_state,
    init_state_stream,
    load_factors,
    run_submitter,
    run_submitter_multiple,
    run_submitter_stream,
)
from ..modules.library.engine import connect as lib_connect, load_fingerprints as lib_load_fingerprints
from ..api.brain import BrainClient

ROOT_DIR = Path(__file__).resolve().parents[2]

DEFAULT_REGIONS = [("保持原样", "default"), ("USA", "USA"), ("EUR", "EUR"), ("ASI", "ASI"), ("HKG", "HKG"), ("CHN", "CHN"), ("GLB", "GLB")]
DEFAULT_UNIVERSES = [("保持原样", "default"), ("TOP3000", "TOP3000"), ("TOP2000", "TOP2000"), ("MINVOL1M", "MINVOL1M")]

class SubmitterPane(Vertical):
    """回测提交器面板 (全组件补全版)"""
    
    def __init__(self) -> None:
        super().__init__()
        self.factor_map: dict[str, Path] = {}
        self.log_lines: list[str] = []
        self.api_options: Dict[str, Any] = {}
        self.stop_event = threading.Event()

    def compose(self) -> ComposeResult:
        with Container(classes="form"):
            yield Horizontal(Label("因子文件", classes="field-label"), Select([], id="factor_select"), classes="field-row")
            yield Horizontal(Label("因子库 (DB)", classes="field-label"), Input("db/factor_library.db", id="db_path"), classes="field-row")
            yield Horizontal(
                Label("地区 (覆盖)", classes="field-label"), Select(DEFAULT_REGIONS, value="default", id="region_override"),
                Label("宇宙 (覆盖)", classes="field-label"), Select(DEFAULT_UNIVERSES, value="default", id="universe_override"),
                classes="field-row"
            )
            yield Horizontal(
                Label("延迟 (覆盖)", classes="field-label"), Select([("保持原样", "default"), ("1", "1"), ("0", "0")], value="default", id="delay_override"),
                Checkbox("Multiple 模式", id="use_multiple", value=False),
                Label("批次", classes="small-label"), Input("10", id="batch_size"),
                classes="field-row"
            )
            yield Horizontal(
                Label("起始序号", classes="field-label"), Input("0", id="start_index"),
                Label("并发数", classes="field-label"), Input("1", id="concurrency"),
                classes="field-row"
            )
            yield Horizontal(
                Label("最大等待", classes="field-label"), Input("1800", id="max_wait"),
                Checkbox("顺序模式", id="ordered", value=True), # 补齐缺失的 id="ordered"
                classes="field-row"
            )
            yield Horizontal(
                Button("刷新列表", id="refresh_btn"),
                Button("开始回测", id="run_btn", variant="primary"),
                Button("停止任务", id="stop_btn", variant="error"),
                Button("回填结果", id="backfill_btn"),
                classes="field-row"
            )
        with ScrollableContainer(classes="log"):
            yield Static("就绪。所有提交将实时同步并显示进度。", id="log_body")

    def on_mount(self) -> None:
        self.refresh_files()
        self.fetch_api_options()

    def _log(self, text: str) -> None:
        self.log_lines.append(f"[{datetime.now().strftime('%H:%M:%S')}] {text}")
        if len(self.log_lines) > 500: self.log_lines = self.log_lines[-500:]
        try:
            body = self.query_one("#log_body", Static)
            body.update("\n".join(self.log_lines))
            self.query_one(".log", ScrollableContainer).scroll_end(animate=False)
        except: pass

    def _on_item_progress(self, item: Dict[str, Any]) -> None:
        status = item.get("status", "unknown").upper()
        if status in ("WAITING", "WAITING_BATCH"):
            wait_time = item.get("wait_time", 0)
            if wait_time == -1:
                msg = "[bold yellow]⌛ FETCHING[/bold yellow] | 节点回测已完成，正在同步子任务 ID..."
            elif wait_time == -2:
                msg = "[bold yellow]⌛ SYNCING[/bold yellow] | 正在拉取 Alpha 指标详情..."
            elif wait_time >= 0:
                if wait_time % 20 == 0:
                    msg = f"[bold blue]⌛ WAITING[/bold blue] | 节点处理中，已等待 {wait_time}s..."
                else:
                    return # 减少日志干扰
            else:
                return
            self.app.call_from_thread(self._log, msg)
            return

        expr = item.get("expression", "")[:40]
        if status == "COMPLETED":
            aid = item.get("result", {}).get("alpha_id", "N/A")
            sim_id = item.get("submission_id", "N/A")
            msg = f"[bold green]✓ SUCCESS[/bold green] | Alpha: {aid} | Link: https://www.worldquantbrain.com/alpha-design/simulations/{sim_id} | {expr}..."
        elif status == "FAILED":
            err = item.get("last_error", "Unknown error")
            msg = f"[bold red]✗ FAILED[/bold red] | Err: {err} | {expr}..."
        else: msg = f"[{status}] {expr}..."
        self.app.call_from_thread(self._log, msg)

    @work(thread=True, exclusive=True)
    def fetch_api_options(self) -> None:
        try:
            cfg = self._load_brain_config()
            if not cfg.get("username"): return
            client = BrainClient(username=cfg["username"], password=cfg["password"], api_base=cfg["api_base"], timeout=15)
            client.login()
            opts = client.get_settings_options()
            if opts: self.app.call_from_thread(self._apply_api_options, opts)
        except Exception as e: self.app.call_from_thread(self._log, f"API 选项同步提醒: {e}")

    def _apply_api_options(self, opts: Any) -> None:
        self.api_options = opts
        try:
            reg_list = []
            choices = opts.get("region", {}).get("choices", [])
            if isinstance(choices, dict):
                reg_dict = choices.get("instrumentType", {}).get("EQUITY", {})
                if isinstance(reg_dict, dict): reg_list = sorted(list(reg_dict.keys()))
            elif isinstance(choices, list):
                reg_list = [str(c["value"]) for c in choices if isinstance(c, dict) and "value" in c]
            if reg_list:
                self.query_one("#region_override", Select).set_options([("保持原样", "default")] + [(r, r) for r in reg_list])
        except: pass

    def refresh_cascading_options(self, region: str) -> None:
        if not self.api_options: return
        uni_cfg = self.api_options.get("universe", {}).get("choices", [])
        u_list = []
        if isinstance(uni_cfg, dict):
            u_list = uni_cfg.get("instrumentType", {}).get("EQUITY", {}).get("region", {}).get(region, [])
        elif isinstance(uni_cfg, list):
            u_list = [c for c in uni_cfg if isinstance(c, dict) and (c.get("region") == region or not c.get("region"))]
        u_choices = [("保持原样", "default")] + [(str(u["value"]), str(u["value"])) for u in u_list if isinstance(u, dict) and u.get("value")]
        if len(u_choices) > 1: self.query_one("#universe_override", Select).set_options(u_choices)

    def on_select_changed(self, event: Select.Changed) -> None:
        if event.select.id == "region_override" and event.value and event.value != "default":
            self.refresh_cascading_options(str(event.value))

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "refresh_btn": self.refresh_files(); self.fetch_api_options()
        elif event.button.id == "run_btn":
            params = self._get_ui_params_sync()
            if params:
                self.stop_event.clear(); self._log("🚀 正在将回测任务移交后台线程...")
                self.run_submitter_job_worker(params)
        elif event.button.id == "stop_btn":
            self.stop_event.set(); self._log("[bold red]已发出停止信号...[/bold red]")

    def _get_ui_params_sync(self) -> Dict[str, Any] | None:
        try:
            from textual.widgets import Select

            # Helper to handle NoSelection/BLANK values from Textual Select widgets
            def get_val(selector):
                v = self.query_one(selector, Select).value
                # In Textual, Select.BLANK is the NoSelection object
                if v is Select.BLANK or str(v) == "NoSelection":
                    return None
                return v

            f_key = get_val("#factor_select")
            if not f_key or f_key == "none":
                self._log("错误: 请选择因子文件。"); return None

            ovs = {}
            r = get_val("#region_override")
            if r and r != "default": ovs["region"] = r

            u = get_val("#universe_override")
            if u and u != "default": ovs["universe"] = u

            d = get_val("#delay_override")
            if d and d != "default": ovs["delay"] = int(d)

            res = {
                "factors_path": self.factor_map[f_key][1],
                "db_path_raw": self.query_one("#db_path", Input).value,
                "start_idx": int(self.query_one("#start_index", Input).value or 0),
                "concurrency": int(self.query_one("#concurrency", Input).value or 1),
                "ordered": self.query_one("#ordered", Checkbox).value,
                "use_multiple": self.query_one("#use_multiple", Checkbox).value,
                "batch_size": int(self.query_one("#batch_size", Input).value or 10),
                "max_wait": int(self.query_one("#max_wait", Input).value or 1800),
                "overrides": ovs
            }

            # --- DEEP CLEAN NOSELECTION ---
            def clean_dict(d):
                if not isinstance(d, dict): return d
                new_d = {}
                for k, v in d.items():
                    if v is Select.BLANK or str(v) == "NoSelection":
                        new_d[k] = None
                    elif isinstance(v, dict):
                        new_d[k] = clean_dict(v)
                    else:
                        new_d[k] = v
                return new_d

            return clean_dict(res)
        except Exception as e:
            self._log(f"参数抓取失败: {e}"); return None
    @work(thread=True, exclusive=True)
    def run_submitter_job_worker(self, params: Dict[str, Any]) -> None:
        try:
            factors_path = params["factors_path"]; db_path = ROOT_DIR / params["db_path_raw"]
            brain_cfg = self._load_brain_config()
            adapter = BrainApiAdapter(max_wait=params["max_wait"], settings_override=params["overrides"], **brain_cfg)

            # Use the unified concurrent engine which handles both concurrency and multiple (batch_size)
            state = init_state_stream(factors_path, run_id=datetime.now().strftime("%H%M%S"), config={"mode":"brain"}, start_index=params["start_idx"])

            from ..modules.submitter.engine import run_submitter_concurrent
            state, proc = run_submitter_concurrent(
                state, 
                adapter, 
                concurrency=params["concurrency"], 
                batch_size=params["batch_size"] if params["use_multiple"] else 1,
                source_file=factors_path, 
                db_path=db_path, 
                on_progress=self._on_item_progress, 
                stop_event=self.stop_event
            )

            msg = "已停止" if self.stop_event.is_set() else "完成"
            self.app.call_from_thread(self._log, f"[bold green]任务{msg}！处理条数: {proc}[/bold green]")
        except Exception as exc: self.app.call_from_thread(self._log, f"❌ 任务报错: {exc}")

    def refresh_files(self) -> None:
        try:
            self.factor_map = self._collect_factor_files()
            options = [(label, key) for key, (label, _) in self.factor_map.items()]
            self.query_one("#factor_select", Select).set_options(options or [("未发现文件", "none")])
        except: pass

    def _collect_factor_files(self) -> dict[str, tuple[str, Path]]:
        all_files = []
        for l, b in [("生成", ROOT_DIR / "generated"), ("上传", ROOT_DIR / "runs" / "uploads")]:
            if b.exists():
                for p in b.glob("*.json"): all_files.append((l, p))
        all_files.sort(key=lambda x: x[1].stat().st_mtime, reverse=True)
        return {f"{l}:{p.name}": (f"{l} · {p.name}", p) for l, p in all_files}

    def _load_brain_config(self) -> dict[str, object]:
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

        try:
            cfg, _ = load_config(); brain = cfg.get("brain", {})
            return {
                "username": str(brain.get("username", "")),
                "password": str(brain.get("password", "")),
                "api_base": str(brain.get("api_base") or "https://api.worldquantbrain.com"),
                "use_proxy": _as_bool(brain.get("use_proxy"), False),
            }
        except:
            return {"username": "", "password": "", "api_base": "", "use_proxy": False}
