from __future__ import annotations

import datetime
import json
from pathlib import Path

from textual.app import ComposeResult
from textual.containers import Container, Horizontal, Vertical, ScrollableContainer
from textual.widgets import Button, Input, Label, Static, DataTable
from textual.screen import ModalScreen
from textual import work, on

from ..modules.library.engine import (
    connect, 
    init_db, 
    archive_from_state, 
    archive_from_factors, 
    load_state, 
    load_factors, 
    list_factors, 
    delete_factor,
    clear_library
)

ROOT_DIR = Path(__file__).resolve().parents[2]

class ConfirmClearModal(ModalScreen[bool]):
    """清空全库确认弹窗"""
    def compose(self) -> ComposeResult:
        with Vertical(id="clear_confirm_root"):
            yield Label("[bold red]危险操作确认[/bold red]", classes="modal_title")
            yield Label("您确定要清空因子库中的所有记录吗？此操作不可撤销！", classes="modal_msg")
            with Horizontal(classes="modal_buttons"):
                yield Button("取消", id="cancel_btn")
                yield Button("确定清空", id="confirm_btn", variant="error")

    @on(Button.Pressed, "#cancel_btn")
    def action_cancel(self) -> None: self.dismiss(False)
    @on(Button.Pressed, "#confirm_btn")
    def action_confirm(self) -> None: self.dismiss(True)

class LibraryPane(Vertical):
    """全量因子库面板 (支持一键清空)"""
    
    def __init__(self) -> None:
        super().__init__()
        self.cached_factors: list[dict] = []
        self.selected_index: int | None = None

    def compose(self) -> ComposeResult:
        with Container(classes="form"):
            with Horizontal(classes="field-row"):
                yield Label("因子库路径", classes="field-label")
                yield Input("db/factor_library.db", id="db_path")
                yield Button("刷新列表", id="refresh_db_btn", variant="primary")
                yield Button("删除选中", id="delete_btn", variant="warning")
                yield Button("清空全库", id="clear_all_btn", variant="error")
            
            with Horizontal(classes="field-row"):
                yield Label("导入文件", classes="field-label")
                yield Input("", id="archive_file", placeholder="输入 JSON 路径...")
                yield Button("一键归档", id="archive_btn")
                yield Button("初始化DB", id="init_db_btn")

        yield DataTable(id="library_table", zebra_stripes=True, cursor_type="row")
        
        with ScrollableContainer(classes="log", id="library_details_container"):
            yield Static("选中上方因子查看详细信息...", id="library_details")

    def on_mount(self) -> None:
        table = self.query_one("#library_table", DataTable)
        table.add_column("状态", width=10)
        table.add_column("Sharpe", width=8)
        table.add_column("地区", width=8)
        table.add_column("宇宙", width=12)
        table.add_column("表达式", width=40)
        table.add_column("最后提交时间", width=20)
        self.refresh_library_list()

    @work(thread=True, exclusive=True)
    def refresh_library_list(self) -> None:
        db_path_str = self.query_one("#db_path", Input).value
        db_path = ROOT_DIR / db_path_str
        if not db_path.exists(): return
        try:
            with connect(db_path) as conn:
                factors = list_factors(conn, limit=1000)
                self.app.call_from_thread(self._update_table, factors)
        except Exception as e:
            self.app.call_from_thread(self._set_details, f"加载库失败: {e}")

    def _update_table(self, factors: list[dict]) -> None:
        self.cached_factors = factors
        self.selected_index = None
        table = self.query_one("#library_table", DataTable)
        table.clear()
        for i, f in enumerate(factors):
            sharpe = f.get("display_sharpe", "-")
            if isinstance(sharpe, (int, float)): sharpe = f"{sharpe:.3f}"
            last_at = f.get("last_submitted_at") or f.get("created_at") or "-"
            if last_at != "-":
                try: last_at = last_at.split(".")[0].replace("T", " ")
                except: pass
            table.add_row(f.get("status", "new").upper(), str(sharpe), f.get("display_region", "-"), f.get("display_universe", "-"), f.get("expression", ""), last_at, key=str(i))
        self._set_details(f"库加载完成: 共计 {len(factors)} 条记录。")

    def _set_details(self, text: str) -> None:
        self.query_one("#library_details", Static).update(text)

    def on_data_table_row_selected(self, event: DataTable.RowSelected) -> None:
        try:
            self.selected_index = int(event.row_key.value)
            factor = self.cached_factors[self.selected_index]
            self._set_details(f"[bold cyan]选中记录[/bold cyan]\n\n{json.dumps(factor, indent=2, ensure_ascii=False)}")
        except: pass

    @on(Button.Pressed, "#clear_all_btn")
    def action_clear_all(self) -> None:
        def check_result(confirm: bool) -> None:
            if confirm: self._do_clear_all()
        self.app.push_screen(ConfirmClearModal(), check_result)

    def _do_clear_all(self) -> None:
        db_path = ROOT_DIR / self.query_one("#db_path", Input).value
        try:
            with connect(db_path) as conn:
                clear_library(conn)
            self._set_details("[bold green]成功: 已清空所有记录！[/bold green]")
            self.refresh_library_list()
        except Exception as e: self._set_details(f"清空失败: {e}")

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "refresh_db_btn": self.refresh_library_list()
        elif event.button.id == "delete_btn": self._do_delete()
        elif event.button.id == "init_db_btn": self._do_init_db()
        elif event.button.id == "archive_btn": self._do_archive()

    def _do_delete(self) -> None:
        if self.selected_index is None: return
        factor = self.cached_factors[self.selected_index]
        db_path = ROOT_DIR / self.query_one("#db_path", Input).value
        try:
            with connect(db_path) as conn:
                if delete_factor(conn, factor["fingerprint"]):
                    self._set_details("[bold green]已删除记录。[/bold green]")
                    self.refresh_library_list()
        except Exception as e: self._set_details(f"操作出错: {e}")

    def _do_init_db(self) -> None:
        db_path = ROOT_DIR / self.query_one("#db_path", Input).value
        try:
            db_path.parent.mkdir(parents=True, exist_ok=True)
            with connect(db_path) as conn: init_db(conn)
            self._set_details(f"成功: 已初始化数据库。")
        except Exception as e: self._set_details(f"失败: {e}")

    def _do_archive(self) -> None:
        file_str = self.query_one("#archive_file", Input).value
        db_path = ROOT_DIR / self.query_one("#db_path", Input).value
        if not file_str: return
        file_path = ROOT_DIR / file_str
        if not file_path.exists(): return
        try:
            with connect(db_path) as conn:
                if "submit_state" in file_path.name: count = archive_from_state(conn, load_state(file_path))
                else: count = archive_from_factors(conn, load_factors(file_path))
            self._set_details(f"归档完成: 处理 {count} 条数据。")
            self.refresh_library_list()
        except Exception as e: self._set_details(f"失败: {e}")
