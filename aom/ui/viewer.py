from __future__ import annotations

import json
import os
from pathlib import Path

from textual.app import ComposeResult
from textual.containers import Container, Horizontal, ScrollableContainer, Vertical
from textual.widgets import Button, Label, Select, Static, Input
from textual import work

from ..modules.factor_viewer.engine import load_factors

ROOT_DIR = Path(__file__).resolve().parents[2]

class ViewerPane(Vertical):
    """因子查看面板 (支持物理删除与远程下载)"""
    
    def __init__(self) -> None:
        super().__init__()
        self.factor_file_map: dict[str, Path] = {}
        self.current_factors: list[dict] = []

    def compose(self) -> ComposeResult:
        with Container(classes="form"):
            yield Horizontal(
                Label("本地文件", classes="field-label"),
                Select([], id="viewer_file_select"),
                Button("刷新", id="ref_viewer_btn"),
                Button("删除文件", id="delete_file_btn", variant="error"),
                classes="field-row"
            )
            yield Horizontal(
                Label("远程下载", classes="field-label"),
                Input("", id="download_url", placeholder="粘贴 0x0.st 等链接..."),
                Button("下载并解析", id="download_btn", variant="primary"),
                classes="field-row"
            )
            yield Horizontal(
                Label("预览因子", classes="field-label"),
                Select([], id="factor_item_select", prompt="请先选择文件"),
                classes="field-row"
            )

        with ScrollableContainer(classes="log"):
            yield Static("就绪。支持查看本地 JSON 或从链接同步因子。", id="factor_details")

    def on_mount(self) -> None:
        self.refresh_files()

    def refresh_files(self) -> None:
        try:
            file_select = self.query_one("#viewer_file_select", Select)
            self.factor_file_map = {}
            all_files = []
            # 扫描生成目录和上传目录
            for d in [ROOT_DIR / "generated", ROOT_DIR / "runs" / "uploads"]:
                if d.exists():
                    for p in d.glob("*.json"):
                        all_files.append((d.name, p))
            
            # 按修改时间降序排序 (最新的在前)
            all_files.sort(key=lambda x: x[1].stat().st_mtime, reverse=True)

            options = []
            for dname, p in all_files:
                key = f"{dname}/{p.name}"
                self.factor_file_map[key] = p
                options.append((key, key))

            file_select.set_options(options or [("无 JSON 文件", "none")])
        except: pass

    def on_select_changed(self, event: Select.Changed) -> None:
        if event.select.id == "viewer_file_select":
            if event.value and event.value != "none":
                path = self.factor_file_map.get(str(event.value))
                if path:
                    try:
                        self.current_factors = load_factors(path)
                        item_select = self.query_one("#factor_item_select", Select)
                        options = [(f.get("factor_id", f"#{i}"), str(i)) for i, f in enumerate(self.current_factors)]
                        item_select.set_options(options)
                        if options: item_select.value = options[0][1]
                    except Exception as e: self._set_details(f"读取失败: {e}")
        
        elif event.select.id == "factor_item_select":
            if event.value is not None:
                try:
                    idx = int(event.value)
                    self._set_details(json.dumps(self.current_factors[idx], indent=2, ensure_ascii=False))
                except: pass

    def _set_details(self, text: str) -> None:
        self.query_one("#factor_details", Static).update(text)

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "ref_viewer_btn":
            self.refresh_files()
        elif event.button.id == "delete_file_btn":
            self._do_delete_file()
        elif event.button.id == "download_btn":
            self._do_download()

    def _do_delete_file(self) -> None:
        val = self.query_one("#viewer_file_select", Select).value
        if not val or val == "none": return
        path = self.factor_file_map.get(str(val))
        if path and path.exists():
            try:
                os.remove(path)
                self._set_details(f"成功: 已物理删除文件 {path.name}")
                self.refresh_files()
            except Exception as e: self._set_details(f"删除失败: {e}")

    @work(thread=True, exclusive=True)
    def _do_download(self) -> None:
        import requests
        url = self.query_one("#download_url", Input).value.strip()
        if not url: return
        
        self.app.call_from_thread(self._set_details, f"正在下载: {url} ...")
        try:
            with requests.Session() as sess:
                # Default: do not use process proxy env vars.
                sess.trust_env = False
                resp = sess.get(url, timeout=10)
            data = resp.json()
            # 存入上传目录
            save_path = ROOT_DIR / "runs" / "uploads" / f"remote_{datetime.now().strftime('%H%M%S')}.json"
            save_path.parent.mkdir(parents=True, exist_ok=True)
            save_path.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")
            
            self.app.call_from_thread(self._set_details, f"下载成功！已存至: {save_path.name}\n点击'刷新'即可预览。")
            self.app.call_from_thread(self.refresh_files)
        except Exception as e:
            self.app.call_from_thread(self._set_details, f"下载或解析失败: {e}")
