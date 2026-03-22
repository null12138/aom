from __future__ import annotations

from pathlib import Path

from textual.app import App, ComposeResult
from textual.containers import Container
from textual.widgets import Footer, Header, Static, TabbedContent, TabPane

from .ui.submitter import SubmitterPane
from .ui.library import LibraryPane
from .ui.template import TemplatePane
from .ui.viewer import ViewerPane


ROOT_DIR = Path(__file__).resolve().parents[1]


class AOMApp(App):
    CSS = """
    Screen {
        align: center middle;
    }
    #root {
        width: 100%;
        height: 100%;
        border: double $primary;
        background: $surface;
    }
    #title {
        height: 3;
        content-align: center middle;
        color: $text;
        text-style: bold;
        background: $primary-darken-2;
    }
    TabbedContent {
        height: 1fr;
    }
    .pane-root {
        padding: 1 2;
        height: 100%;
    }
    .form {
        height: auto;
        border: tall $primary-lighten-1;
        margin-bottom: 1;
        padding: 1 2;
    }
    .field-row {
        height: 3;
        margin-bottom: 0;
        align: left middle;
    }
    .field-label {
        width: 16;
        text-style: bold;
        content-align: left middle;
    }
    .small-label {
        width: 8;
        margin-left: 2;
        text-style: bold;
        content-align: left middle;
    }
    Input, Select {
        width: 1fr;
        height: 3;
    }
    Checkbox {
        height: 3;
    }
    Button {
        height: 3;
        margin-right: 1;
    }
    /* 全屏编辑器样式 */
    ExpressionEditorModal {
        align: center middle;
    }
    #editor_root {
        width: 90%;
        height: 90%;
        border: thick $primary;
        background: $surface;
        padding: 1;
    }
    .modal_title {
        height: 3;
        content-align: center middle;
    }
    #full_text_area {
        height: 1fr;
        border: solid $secondary;
        margin: 1 0;
    }
    .modal_buttons {
        height: 3;
        align: right middle;
    }
    #clear_confirm_root {
        width: 60;
        height: auto;
        border: thick $error;
        background: $surface;
        padding: 1 2;
        align: center middle;
    }
    .modal_msg {
        margin: 1 0;
        text-align: center;
    }
    """

    TITLE = "Auto Opener Miner"
    SUB_TITLE = "WorldQuant 一站式工作流"

    def compose(self) -> ComposeResult:
        yield Header(show_clock=True)
        with Container(id="root"):
            yield Static("Auto Opener Miner", id="title")
            with TabbedContent():
                with TabPane("提交器回测"):
                    yield SubmitterPane()
                with TabPane("模板管理"):
                    yield TemplatePane()
                with TabPane("因子查看"):
                    yield ViewerPane()
                with TabPane("因子库管理"):
                    yield LibraryPane()
        yield Footer()

if __name__ == "__main__":
    AOMApp().run()
