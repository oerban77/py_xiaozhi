"""Textual App: dashboard + settings screen."""

from __future__ import annotations

import asyncio
import json
from collections.abc import Callable

from textual import on
from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical, VerticalScroll
from textual.events import Paste
from textual.reactive import reactive
from textual.screen import ModalScreen
from textual.widgets import (
    Button,
    Checkbox,
    Footer,
    Header,
    Input,
    Label,
    RichLog,
    Select,
    Static,
    TabbedContent,
    TabPane,
)

from src.constants.system import SystemConstants
from src.logging import get_logger, load_logging_config
from src.ui.tui.settings_data import (
    SETTING_SECTIONS,
    load_setting_values,
    save_settings,
)

logger = get_logger()


class ClipboardAttachmentInput(Input):
    """Chat input that attaches pasted images or files while keeping text inline."""

    BINDINGS = [
        Binding("up", "history_previous", show=False, priority=True),
        Binding("down", "history_next", show=False, priority=True),
    ]

    def __init__(self, on_paste_attachment: Callable[[str], bool], **kwargs) -> None:
        self._on_paste_attachment = on_paste_attachment
        self._history: list[str] = []
        self._history_position: int | None = None
        self._history_draft = ""
        super().__init__(**kwargs)

    def add_history(self, text: str) -> None:
        if text:
            self._history.append(text)
        self._history_position = None
        self._history_draft = ""

    def action_history_previous(self) -> None:
        if not self._history:
            return
        if self._history_position is None:
            self._history_draft = self.value
            self._history_position = len(self._history)
        if self._history_position > 0:
            self._history_position -= 1
            self.value = self._history[self._history_position]
            self.cursor_position = len(self.value)

    def action_history_next(self) -> None:
        if self._history_position is None:
            return
        if self._history_position < len(self._history) - 1:
            self._history_position += 1
            self.value = self._history[self._history_position]
        else:
            self._history_position = None
            self.value = self._history_draft
        self.cursor_position = len(self.value)

    def _on_paste(self, event: Paste) -> None:
        if self._on_paste_attachment(event.text):
            event.stop()
            return
        super()._on_paste(event)


class SettingsScreen(ModalScreen[tuple[bool, bool]]):
    """Configuration editing overlay; returns True when saved."""

    BINDINGS = [
        Binding("escape", "cancel", "Cancel", show=True),
    ]

    CSS = """
    SettingsScreen {
        align: center middle;
    }
    #settings-dialog {
        layout: vertical;
        width: 90%;
        max-width: 100;
        height: 85%;
        border: thick $primary;
        background: $surface;
        padding: 1 2;
    }
    #settings-title {
        text-style: bold;
        margin-bottom: 1;
    }
    #settings-hint {
        color: $text-muted;
        margin-bottom: 1;
    }
    TabbedContent {
        height: 1fr;
        min-height: 3;
    }
    TabPane,
    VerticalScroll {
        height: 1fr;
    }
    .field-row {
        height: auto;
        margin-bottom: 1;
    }
    .field-label {
        width: 20;
        color: $text-muted;
        padding-top: 1;
    }
    .field-input {
        width: 1fr;
    }
    #settings-actions {
        height: 3;
        min-height: 3;
        align: right middle;
        margin-top: 0;
    }
    #settings-status {
        color: $accent;
        height: 1;
        min-height: 1;
        margin-top: 0;
    }
    """

    def __init__(self) -> None:
        super().__init__()
        self._values = load_setting_values()
        self._audio_selection_values: dict[str, dict[str, dict]] = {}
        self._audio_devices = {"input": [], "output": []}
        self._camera_devices = []
        self._mcp_tool_rows = []
        self._mcp_tool_ids: dict[str, str] = {}
        try:
            disabled = json.loads(self._values.get("MCP_TOOLS.DISABLED", "[]"))
        except (TypeError, ValueError):
            disabled = []
        self._mcp_disabled_before = set(disabled if isinstance(disabled, list) else [])
        try:
            from src.mcp.tool_catalog import full_catalog_rows

            self._mcp_tool_rows = full_catalog_rows(extra_disabled=list(self._mcp_disabled_before))
        except Exception as e:
            logger.warning(f"Failed to load MCP tool catalog for TUI settings: {e}")
        try:
            from src.utils.audio_utils import list_audio_devices

            self._audio_devices = list_audio_devices(include_virtual=True)
        except Exception as e:
            logger.warning(f"Failed to enumerate audio devices for TUI settings: {e}")

    def on_mount(self) -> None:
        self.run_worker(self._load_camera_devices(), name="settings:camera-scan")

    async def _load_camera_devices(self) -> None:
        try:
            from src.mcp.tools.camera.capture_backend import list_camera_devices

            devices = await asyncio.to_thread(
                list_camera_devices,
                max_index=5,
                consecutive_fail_limit=2,
            )
            self._camera_devices = devices
            field_path = "CAMERA.selected_device"
            current = self._values.get(field_path, "0")
            options = [(device.name, device.key) for device in devices]
            keys = {device.key for device in devices}
            if current not in keys:
                label = f"Keep configured device ({current or 'default'})" if devices else "No camera detected; keep current setting"
                options.append((label, current))
            if not options:
                options = [("No camera detected", "0")]
            selector = self.query_one(f"#fld-{field_path.replace('.', '-')}", Select)
            selector.set_options(options)
            selector.value = current if current in {value for _, value in options} else options[0][1]
        except Exception as e:
            logger.warning(f"Failed to enumerate camera devices for TUI settings: {e}")

    def compose(self) -> ComposeResult:
        with Vertical(id="settings-dialog"):
            yield Static("Settings", id="settings-title")
            yield Static(
                "After editing, click \"Save\" to write to disk and hot-apply | Esc Cancel | "
                "Fill choice fields with a valid value (see the placeholder hint)",
                id="settings-hint",
            )
            with TabbedContent():
                for section_name, fields in SETTING_SECTIONS:
                    with TabPane(section_name):
                        with VerticalScroll():
                            for f in fields:
                                if f.kind == "mcp_tools":
                                    yield Static(
                                        "Checked tools are exposed to the assistant. Changes apply after reconnect.",
                                        classes="settings-help",
                                    )
                                    grouped: dict[str, list[dict]] = {}
                                    for row in self._mcp_tool_rows:
                                        grouped.setdefault(row.get("groupLabel") or row.get("group") or "Other", []).append(row)
                                    for group_label, rows in grouped.items():
                                        yield Label(group_label, classes="settings-group-title")
                                        for row in rows:
                                            tool_id = f"mcp-tool-{len(self._mcp_tool_ids)}"
                                            self._mcp_tool_ids[tool_id] = row["name"]
                                            yield Checkbox(
                                                f"{row.get('label') or row['name']} ({row['name']})",
                                                value=row["name"] not in self._mcp_disabled_before,
                                                id=tool_id,
                                                classes="mcp-tool-row",
                                            )
                                    continue
                                with Horizontal(classes="field-row"):
                                    yield Label(f.label, classes="field-label")
                                    current = self._values.get(f.path, "")
                                    placeholder = f.help or f.path
                                    if f.kind == "choice" and f.choices:
                                        placeholder = f"Choices: {', '.join(f.choices)}"
                                    elif f.kind == "bool":
                                        placeholder = "true / false"
                                    widget_id = f"fld-{f.path.replace('.', '-')}"
                                    if f.kind in ("audio_input", "audio_output"):
                                        kind = "input" if f.kind == "audio_input" else "output"
                                        options, selected = self._audio_options(f.path, kind, current)
                                        yield Select(
                                            options,
                                            value=selected,
                                            id=widget_id,
                                            classes="field-input",
                                        )
                                    elif f.kind == "camera_device":
                                        yield Select(
                                            [("Scanning cameras...", current or "0")],
                                            value=current or "0",
                                            id=widget_id,
                                            classes="field-input",
                                        )
                                    elif f.kind == "choice":
                                        choices = list(f.choices)
                                        selected = current
                                        if selected not in choices:
                                            if selected:
                                                choices.append(selected)
                                            elif choices:
                                                selected = choices[0]
                                        yield Select(
                                            [(choice, choice) for choice in choices],
                                            value=selected,
                                            id=widget_id,
                                            classes="field-input",
                                        )
                                    elif f.kind == "bool":
                                        yield Checkbox(
                                            "",
                                            value=current.lower() in ("1", "true", "yes", "on"),
                                            id=widget_id,
                                            classes="field-input",
                                        )
                                    else:
                                        yield Input(
                                            value=current,
                                            placeholder=placeholder,
                                            password=f.kind == "password",
                                            id=widget_id,
                                            classes="field-input",
                                        )
            yield Static("", id="settings-status")
            with Horizontal(id="settings-actions"):
                yield Button("Cancel", id="btn-cancel", variant="default")
                yield Button("Save", id="btn-save", variant="primary")

    def _audio_options(
        self, path: str, kind: str, current: str
    ) -> tuple[list[tuple[str, str]], str]:
        options = [("System default", "")]
        values: dict[str, dict] = {}
        selected = ""
        for device in self._audio_devices.get(kind, []):
            raw_name = str(device.get("raw_name") or device.get("name") or "")
            token = f"{kind}:{device.get('index', len(values))}"
            values[token] = dict(device)
            options.append((str(device.get("name") or raw_name), token))
            if raw_name == current and not selected:
                selected = token
        if current and not selected:
            token = f"{kind}:configured"
            values[token] = {"raw_name": current, "configured": True}
            options.append((f"Configured: {current} (not detected)", token))
            selected = token
        self._audio_selection_values[path] = values
        return options, selected

    def _collect_values(self) -> dict[str, str]:
        out: dict[str, str] = {}
        for _section, fields in SETTING_SECTIONS:
            for f in fields:
                wid = f"fld-{f.path.replace('.', '-')}"
                try:
                    if f.kind == "mcp_tools":
                        disabled = [
                            name
                            for widget_id, name in self._mcp_tool_ids.items()
                            if not self.query_one(f"#{widget_id}", Checkbox).value
                        ]
                        out[f.path] = json.dumps(disabled, ensure_ascii=False)
                        continue
                    widget = self.query_one(f"#{wid}")
                    if isinstance(widget, Select):
                        selection = str(widget.value or "")
                        if f.kind in ("audio_input", "audio_output"):
                            selection = json.dumps(
                                self._audio_selection_values.get(f.path, {}).get(selection),
                                ensure_ascii=False,
                            ) if selection else ""
                        out[f.path] = selection
                    elif isinstance(widget, Checkbox):
                        out[f.path] = "true" if widget.value else "false"
                    else:
                        out[f.path] = widget.value
                except Exception:
                    out[f.path] = self._values.get(f.path, "")
        return out

    @on(Button.Pressed, "#btn-cancel")
    def on_cancel_btn(self) -> None:
        self.dismiss((False, False))

    def action_cancel(self) -> None:
        self.dismiss((False, False))

    @on(Button.Pressed, "#btn-save")
    def on_save_btn(self) -> None:
        values = self._collect_values()
        ok, msg = save_settings(values)
        try:
            self.query_one("#settings-status", Static).update(msg)
        except Exception:
            pass
        if ok:
            try:
                from src.mcp.tool_catalog import normalize_disabled
                from src.utils.config_manager import get_config

                disabled_after = set(
                    normalize_disabled(get_config().get_config("MCP_TOOLS.DISABLED", []) or [])
                )
                mcp_changed = disabled_after != self._mcp_disabled_before
            except Exception:
                mcp_changed = False
            self.dismiss((True, mcp_changed))


class XiaozhiTuiApp(App[None]):
    """Xiaozhi TUI main application."""

    TITLE = SystemConstants.APP_DISPLAY_NAME
    SUB_TITLE = "TUI"
    CSS = """
    Screen {
        layout: vertical;
    }
    #status-panel {
        height: auto;
        max-height: 8;
        border: solid $primary;
        margin: 0 1;
        padding: 0 1;
    }
    #status-line {
        text-style: bold;
    }
    #meta-line {
        color: $text-muted;
    }
    #log-panel {
        height: 1fr;
        border: solid $accent;
        margin: 0 1;
    }
    #input-row {
        height: 3;
        margin: 0 1 1 1;
    }
    #cmd-input {
        width: 1fr;
    }
    """

    BINDINGS = [
        Binding("ctrl+c", "quit_app", "Quit", show=True, priority=True),
        Binding("f3", "interrupt_speech", "Interrupt", show=True),
        Binding("f2", "open_settings", "Settings", show=True),
        Binding("f1", "show_help", "Help", show=True),
    ]

    status_text: reactive[str] = reactive("Idle")
    connected: reactive[bool] = reactive(False)
    auto_mode: reactive[bool] = reactive(False)
    chat_text: reactive[str] = reactive("")
    music_line: reactive[str] = reactive("")
    emotion: reactive[str] = reactive("neutral")

    def __init__(
        self,
        on_command: Callable[[str], None] | None = None,
        on_settings_saved: Callable[[bool], None] | None = None,
        **kwargs,
    ) -> None:
        super().__init__(**kwargs)
        self._on_command = on_command
        self._on_settings_saved = on_settings_saved
        self._log_handler_installed = False
        self._tui_log_handler = None
        self._status_widgets_ready = False

    def compose(self) -> ComposeResult:
        yield Header(show_clock=True)
        with Vertical(id="status-panel"):
            yield Static("Status: Idle", id="status-line")
            yield Static("Connection: Disconnected | Mode: Manual | Emotion: neutral", id="meta-line")
            yield Static("Conversation: —", id="chat-line")
            yield Static("Music: —", id="music-line")
        yield RichLog(id="log-panel", highlight=True, markup=True, max_lines=500)
        with Horizontal(id="input-row"):
            yield ClipboardAttachmentInput(
                self._handle_clipboard_paste,
                placeholder=(
                    "Type text | paste attaches temporary text/image | /workspace [path] | r Talk | x/F3 Interrupt | s Settings | q Quit"
                ),
                id="cmd-input",
            )
        yield Footer()

    def on_mount(self) -> None:
        self.query_one("#cmd-input", Input).focus()
        self._status_widgets_ready = True
        self._install_log_handler()
        self._refresh_status_widgets()
        self.write_log(
            f"[bold cyan]{SystemConstants.APP_DISPLAY_NAME} TUI[/]  "
            "F2 Settings · F1 Help · Ctrl+C Quit"
        )

    def watch_status_text(self, _value: str) -> None:
        self._refresh_status_line()

    def watch_connected(self, _value: bool) -> None:
        self._refresh_meta_line()

    def watch_auto_mode(self, _value: bool) -> None:
        self._refresh_meta_line()

    def watch_chat_text(self, _value: str) -> None:
        self._refresh_chat_line()

    def watch_music_line(self, _value: str) -> None:
        self._refresh_music_line()

    def watch_emotion(self, _value: str) -> None:
        self._refresh_meta_line()

    def _refresh_status_widgets(self) -> None:
        try:
            self._refresh_status_line()
            self._refresh_meta_line()
            self._refresh_chat_line()
            self._refresh_music_line()
        except Exception:
            pass

    def _refresh_status_line(self) -> None:
        if not self._status_widgets_ready:
            return
        self.query_one("#status-line", Static).update(f"Status: {self.status_text}")

    def _refresh_meta_line(self) -> None:
        if not self._status_widgets_ready:
            return
        conn = "Connected" if self.connected else "Disconnected"
        mode = "Auto" if self.auto_mode else "Manual"
        self.query_one("#meta-line", Static).update(
            f"Connection: {conn} | Mode: {mode} | Emotion: {self.emotion}"
        )

    def _refresh_chat_line(self) -> None:
        if not self._status_widgets_ready:
            return
        self.query_one("#chat-line", Static).update(
            f"Conversation: {self.chat_text or '—'}"
        )

    def _refresh_music_line(self) -> None:
        if not self._status_widgets_ready:
            return
        self.query_one("#music-line", Static).update(
            f"Music: {self.music_line or '—'}"
        )

    def write_log(self, message: str) -> None:
        try:
            self.query_one("#log-panel", RichLog).write(message)
        except Exception:
            pass

    @on(Input.Submitted, "#cmd-input")
    def on_input_submitted(self, event: Input.Submitted) -> None:
        text = (event.value or "").strip()
        if isinstance(event.input, ClipboardAttachmentInput):
            event.input.add_history(text)
        event.input.value = ""
        if text:
            self._dispatch_command(text)

    def _handle_clipboard_paste(self, text: str) -> bool:
        path = None
        try:
            if not text:
                from PIL import ImageGrab

                clipboard = ImageGrab.grabclipboard()
                if hasattr(clipboard, "save") and hasattr(clipboard, "size"):
                    from src.ui.shared.clipboard_attachments import save_pasted_image

                    path = save_pasted_image(clipboard)
                elif isinstance(clipboard, list):
                    from pathlib import Path

                    path = next(
                        (Path(item) for item in clipboard if Path(item).is_file()),
                        None,
                    )
        except Exception as e:
            logger.warning("Could not read pasted clipboard content: %s", e)
            return False

        if path is None:
            return False
        self.write_log(f"[cyan]Clipboard attached:[/] {path.name}; sending to manage_document")
        self._dispatch_command(f"/attachment {path}")
        return True

    def _dispatch_command(self, text: str) -> None:
        raw = text.strip()
        key = raw.lower()
        if key.startswith("/"):
            key = key[1:]

        if key in ("s", "settings", "set"):
            self.action_open_settings()
            return
        if key in ("h", "help", "?"):
            self.action_show_help()
            return
        if key in ("q", "quit", "exit"):
            self.action_quit_app()
            return

        if self._on_command:
            if key in ("r", "x") and raw.startswith("/"):
                self._on_command(key)
            else:
                self._on_command(raw)

    def action_show_help(self) -> None:
        self.write_log(
            "[bold cyan]Help[/]\n"
            "  Text -> send to the assistant\n"
            "  r -> start/stop the conversation\n"
            "  x -> interrupt (Enter)\n"
            "  F3 -> interrupt immediately\n"
            "  /workspace [path] -> show or switch coding workspace\n"
            "  s / F2 -> Settings\n"
            "  q / Ctrl+C -> Quit\n"
            "  h / F1 -> Help"
        )

    def action_open_settings(self) -> None:
        def _done(result: tuple[bool, bool] | None) -> None:
            saved, mcp_tools_changed = result or (False, False)
            if saved:
                self.write_log("[green]Configuration saved; hot-applying...[/]")
                if self._on_settings_saved:
                    try:
                        self._on_settings_saved(mcp_tools_changed)
                    except Exception as e:
                        logger.error(f"Settings save callback failed: {e}", exc_info=True)
                        self.write_log(f"[red]Hot-apply failed: {e}[/]")

        self.push_screen(SettingsScreen(), _done)

    def action_quit_app(self) -> None:
        if self._on_command:
            self._on_command("q")
        self.exit()

    def action_interrupt_speech(self) -> None:
        if self._on_command:
            self._on_command("x")

    def _install_log_handler(self) -> None:
        if self._log_handler_installed:
            return
        import logging

        app = self

        class TuiLogHandler(logging.Handler):
            def emit(self, record: logging.LogRecord) -> None:
                try:
                    msg = self.format(record)
                    app.call_from_thread(app.write_log, msg)
                except Exception:
                    pass

        handler = TuiLogHandler()
        handler.setFormatter(
            logging.Formatter(
                "%(asctime)s %(levelname)s %(name)s: %(message)s", "%H:%M:%S"
            )
        )
        configured_level = load_logging_config().level.upper()
        handler.setLevel(getattr(logging, configured_level, logging.INFO))
        root = logging.getLogger()
        for h in list(root.handlers):
            if isinstance(h, logging.StreamHandler) and not isinstance(
                h, logging.FileHandler
            ):
                try:
                    root.removeHandler(h)
                except Exception:
                    pass
        root.addHandler(handler)
        self._tui_log_handler = handler
        self._log_handler_installed = True

    def uninstall_log_handler(self) -> None:
        if not self._log_handler_installed:
            return
        import logging

        h = self._tui_log_handler
        if h is not None:
            try:
                logging.getLogger().removeHandler(h)
            except Exception:
                pass
        self._log_handler_installed = False
        self._tui_log_handler = None
