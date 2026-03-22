from __future__ import annotations

import json
import re
from pathlib import Path
from datetime import datetime
from typing import Any, Dict, List, Tuple

from textual.app import ComposeResult
from textual.containers import Container, Horizontal, ScrollableContainer, Vertical
from textual.widgets import Button, Input, Label, Select, Static, TextArea, Checkbox
from textual.screen import ModalScreen
from textual import work, on

from ..config import load_config
from ..modules.template_filler.engine import (
    Template,
    build_template_skeleton,
    load_template_file,
    expand_template,
    write_factors_bundle,
    TemplateError,
)
from ..api.brain import BrainClient

ROOT_DIR = Path(__file__).resolve().parents[2]

# 全屏编辑器弹窗
class ExpressionEditorModal(ModalScreen[str]):
    def __init__(self, initial_value: str = "") -> None:
        super().__init__()
        self.initial_value = initial_value
    def compose(self) -> ComposeResult:
        with Vertical(id="editor_root"):
            yield Label("[bold cyan]全屏编辑表达式[/bold cyan] (按下 Ctrl+S 确定并退出)", classes="modal_title")
            yield TextArea(self.initial_value, id="full_text_area", language="python", show_line_numbers=True)
            with Horizontal(classes="modal_buttons"):
                yield Button("取消", id="cancel_btn", variant="error")
                yield Button("确定 (Ctrl+S)", id="save_btn", variant="primary")
    def on_mount(self) -> None: self.query_one(TextArea).focus()
    @on(Button.Pressed, "#cancel_btn")
    def action_cancel(self) -> None: self.dismiss(self.initial_value)
    @on(Button.Pressed, "#save_btn")
    def action_save(self) -> None: self.dismiss(self.query_one(TextArea).text)
    BINDINGS = [("ctrl+s", "action_save", "保存并退出")]

class TemplatePane(Vertical):
    """模板管理面板组件 (深度解析修复版)"""
    
    def __init__(self) -> None:
        super().__init__()
        self.template_map: dict[str, Path] = {}
        self.api_options: Dict[str, Any] = {}
        self.current_rules: Dict[str, Any] = {}
        self.log_lines: list[str] = [] # 修复初始化

    def compose(self) -> ComposeResult:
        with Container(classes="form", id="template_form"):
            yield Horizontal(Label("选择模板", classes="field-label"), Select([], id="template_select"), classes="field-row")
            yield Horizontal(Label("模板 ID", classes="field-label"), Input("demo_template", id="template_id"), classes="field-row")
            yield Horizontal(Label("表达式", classes="field-label"), Input("", id="expression"), Button("全屏", id="fullscreen_btn", variant="success"), classes="field-row")
            yield Horizontal(
                Label("地区/宇宙", classes="field-label"),
                Select([("USA","USA"),("EUR","EUR"),("ASI","ASI"),("HKG","HKG"),("GLB","GLB")], value="USA", id="region"),
                Select([("TOP3000","TOP3000"),("TOP2000","TOP2000"),("MINVOL1M","MINVOL1M")], value="TOP3000", id="universe"),
                classes="field-row"
            )
            yield Horizontal(Label("设置", classes="field-label"), Select([("1","1"),("0","0")], value="1", id="delay"), Label("规则数: 0", id="rule_stats", classes="muted"), classes="field-row")
            yield Horizontal(Label("输出文件", classes="field-label"), Input("generated/factors_exp.json", id="output_file"), Button("生成精简版因子", id="expand_btn", variant="primary"), classes="field-row")
            yield Horizontal(
                Button("刷新列表", id="ref_tmp_btn"),
                Button("验证字段", id="validate_fields_btn", variant="warning"),
                Button("新建模板", id="init_tmp_btn"),
                Button("保存修改", id="save_tmp_btn"),
                classes="field-row"
            )
        with ScrollableContainer(classes="log"):
            yield Static("就绪。", id="tmp_log_body")

    def on_mount(self) -> None:
        self.refresh_templates()
        self.fetch_api_options()

    def _log(self, text: str) -> None:
        self.log_lines.append(f"[{datetime.now().strftime('%H:%M:%S')}] {text}")
        if len(self.log_lines) > 500: self.log_lines = self.log_lines[-500:]
        try:
            body = self.query_one("#tmp_log_body", Static)
            body.update("\n".join(self.log_lines))
            self.query_one(".log", ScrollableContainer).scroll_end(animate=False)
        except: pass

    @on(Button.Pressed, "#fullscreen_btn")
    def action_fullscreen_edit(self) -> None:
        current_val = self.query_one("#expression", Input).value
        self.app.push_screen(ExpressionEditorModal(current_val), lambda v: self._set_expr(v))

    def _set_expr(self, val: str) -> None:
        self.query_one("#expression", Input).value = val

    @work(thread=True, exclusive=True)
    def fetch_api_options(self) -> None:
        try:
            cfg = self._load_brain_config()
            if not cfg.get("username"): return
            client = BrainClient(username=cfg["username"], password=cfg["password"], api_base=cfg["api_base"], timeout=10)
            client.login()
            opts = client.get_settings_options()
            if opts: self.app.call_from_thread(self._apply_api_options, opts)
        except Exception as e:
            self.app.call_from_thread(self._log, f"API 同步提醒: {e}")

    def _apply_api_options(self, opts: Any) -> None:
        self.api_options = {}
        if isinstance(opts, list):
            for item in opts:
                if isinstance(item, dict) and "name" in item: self.api_options[item["name"]] = item
        elif isinstance(opts, dict):
            self.api_options = opts

        # 解析 Region (深度适配)
        reg_cfg = self.api_options.get("region", {})
        choices = reg_cfg.get("choices", [])
        region_list = []
        if isinstance(choices, dict):
            raw_list = choices.get("instrumentType", {}).get("EQUITY", [])
            if isinstance(raw_list, list):
                region_list = [str(r["value"]) for r in raw_list if isinstance(r, dict) and "value" in r]
        elif isinstance(choices, list):
            region_list = [str(c["value"]) for c in choices if isinstance(c, dict) and "value" in c]

        if region_list:
            self.query_one("#region", Select).set_options([(r, r) for r in region_list])
            self.refresh_cascading_options("USA")
        self._log("Brain API 选项同步完成。")

    def refresh_cascading_options(self, region: str) -> None:
        if not self.api_options: return
        # 解析 Universe (深度适配)
        uni_cfg = self.api_options.get("universe", {})
        choices = uni_cfg.get("choices", [])
        u_list = []
        if isinstance(choices, dict):
            u_list = choices.get("instrumentType", {}).get("EQUITY", {}).get("region", {}).get(region, [])
        elif isinstance(choices, list):
            u_list = [c for c in choices if isinstance(c, dict) and (c.get("region") == region or not c.get("region"))]
        
        u_choices = [(str(u["value"]), str(u["value"])) for u in u_list if isinstance(u, dict) and "value" in u]
        if u_choices: self.query_one("#universe", Select).set_options(u_choices)

        # 解析 Delay
        delay_cfg = self.api_options.get("delay", {})
        d_choices_raw = delay_cfg.get("choices", [])
        d_list = []
        if isinstance(d_choices_raw, dict):
            d_list = d_choices_raw.get("instrumentType", {}).get("EQUITY", {}).get("region", {}).get(region, [])
        elif isinstance(d_choices_raw, list):
            d_list = d_choices_raw
        d_choices = [(str(d["value"]), str(d["value"])) for d in d_list if isinstance(d, dict) and d.get("value") is not None]
        if d_choices: self.query_one("#delay", Select).set_options(d_choices)

    def on_select_changed(self, event: Select.Changed) -> None:
        if event.select.id == "region" and event.value:
            self.refresh_cascading_options(str(event.value))
        elif event.select.id == "template_select" and event.value != "none":
            self.load_template_data(str(event.value))

    def load_template_data(self, filename: str) -> None:
        path = self.template_map.get(filename)
        if not path: return
        try:
            tpl = load_template_file(path)
            self.query_one("#template_id", Input).value = tpl.template_id
            self.query_one("#expression", Input).value = tpl.template
            self.current_rules = tpl.rules
            count = sum(len(v) for v in self.current_rules.values())
            self.query_one("#rule_stats", Label).update(f"规则总数: {count}")
            
            overrides = tpl.metadata.get("settings_override", {})
            if "region" in overrides: self.query_one("#region", Select).value = overrides["region"]
            if "universe" in overrides: self.query_one("#universe", Select).value = overrides["universe"]
            if "delay" in overrides: self.query_one("#delay", Select).value = str(overrides["delay"])
            self._log(f"已加载模板: {filename}")
        except Exception as exc:
            self._log(f"加载失败: {exc}")

    def refresh_templates(self) -> None:
        try:
            temp_select = self.query_one("#template_select", Select)
            self.template_map = {}
            base = ROOT_DIR / "templates"
            if base.exists():
                for path in sorted(base.rglob("*.json")):
                    rel_path = str(path.relative_to(base))
                    self.template_map[rel_path] = path
            options = [(name, name) for name in self.template_map.keys()]
            temp_select.set_options(options or [("未发现模板", "none")])
        except: pass

    def _load_brain_config(self) -> dict[str, str]:
        try:
            cfg, _ = load_config()
            brain = cfg.get("brain", {})
            return {"username": str(brain.get("username", "")), "password": str(brain.get("password", "")), "api_base": str(brain.get("api_base") or "https://api.worldquantbrain.com")}
        except: return {"username": "", "password": "", "api_base": ""}

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "ref_tmp_btn":
            self.refresh_templates()
            self.fetch_api_options()
        elif event.button.id == "validate_fields_btn":
            self.run_fields_validation()
        elif event.button.id == "init_tmp_btn": self.create_new_template()
        elif event.button.id == "save_tmp_btn": self.save_current_template()
        elif event.button.id == "expand_btn": self.run_expansion()

    @work(thread=True, exclusive=True)
    def run_fields_validation(self) -> None:
        try:
            region = self.app.call_from_thread(lambda: self.query_one("#region", Select).value)
            universe = self.app.call_from_thread(lambda: self.query_one("#universe", Select).value)
            self.app.call_from_thread(self._log, f"开始验证 {region} 字段...")
            cfg = self._load_brain_config()
            client = BrainClient(username=cfg["username"], password=cfg["password"], api_base=cfg["api_base"])
            client.login()
            all_fields = set()
            for vals in self.current_rules.values():
                for v in vals:
                    if re.match(r"^[a-zA-Z0-9_]+$", str(v)): all_fields.add(str(v))
            if not all_fields: return
            invalid_count = 0
            for field in sorted(list(all_fields)):
                res = client.get_datafields(region=region, universe=universe, search=field, limit=10)
                if not any(f.get("id") == field for f in res.get("results", [])):
                    self.app.call_from_thread(self._log, f"[bold red]✗ 未知:[/bold red] {field}")
                    invalid_count += 1
                else: self.app.call_from_thread(self._log, f"[bold green]✓ 有效:[/bold green] {field}")
            self.app.call_from_thread(self._log, f"验证完成，无效: {invalid_count}")
        except Exception as e:
            self.app.call_from_thread(self._log, f"验证失败: {e}")

    def create_new_template(self) -> None:
        tid = self.query_one("#template_id", Input).value
        expr = self.query_one("#expression", Input).value
        out_path = ROOT_DIR / "templates" / f"{tid}.json"
        try:
            out_path.parent.mkdir(parents=True, exist_ok=True)
            skel = build_template_skeleton(tid, expr)
            out_path.write_text(json.dumps(skel, indent=2, ensure_ascii=False), encoding="utf-8")
            self._log(f"已新建模板: {tid}")
            self.refresh_templates()
        except Exception as e:
            self._log(f"新建失败: {e}")

    def save_current_template(self) -> None:
        temp_select = self.query_one("#template_select", Select)
        if not temp_select.value or temp_select.value == "none": return
        path = self.template_map[temp_select.value]
        try:
            tpl_data = json.loads(path.read_text(encoding="utf-8"))
            tpl_data["template_id"] = self.query_one("#template_id", Input).value
            tpl_data["template"] = self.query_one("#expression", Input).value
            metadata = tpl_data.setdefault("metadata", {})
            overrides = metadata.setdefault("settings_override", {})
            overrides["region"] = self.query_one("#region", Select).value
            overrides["universe"] = self.query_one("#universe", Select).value
            overrides["delay"] = int(self.query_one("#delay", Select).value or 1)
            path.write_text(json.dumps(tpl_data, indent=2, ensure_ascii=False), encoding="utf-8")
            self._log(f"已保存修改: {temp_select.value}")
        except Exception as exc: self._log(f"保存失败: {exc}")

    def run_expansion(self) -> None:
        out_path = ROOT_DIR / self.query_one("#output_file", Input).value
        try:
            cfg, _ = load_config(); base_settings = cfg.get("defaults", {})
            virtual_tpl = Template(
                template_id=self.query_one("#template_id", Input).value,
                template=self.query_one("#expression", Input).value,
                placeholders=[], rules=self.current_rules, metadata={}
            )
            overrides = {
                "region": self.query_one("#region", Select).value,
                "universe": self.query_one("#universe", Select).value,
                "delay": int(self.query_one("#delay", Select).value or 1),
            }
            factors = expand_template(virtual_tpl, base_settings=base_settings, settings_override=overrides)
            out_path.parent.mkdir(parents=True, exist_ok=True)
            write_factors_bundle(out_path, factors)
            self._log(f"成功生成精简版: {len(factors)} 条记录。")
        except TemplateError as te: self._log(f"生成中止: {te}")
        except Exception as exc: self._log(f"生成失败: {exc}")
