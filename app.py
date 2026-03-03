from __future__ import annotations

from pathlib import Path

from textual import on
from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical
from textual.screen import ModalScreen
from textual.widgets import (
    Button,
    DataTable,
    Footer,
    Header,
    Input,
    Label,
    ListItem,
    ListView,
    Static,
)

from config import Config, load_config, save_config
from saves import (
    BackupEntry,
    SaveSource,
    delete_backup,
    do_backup,
    do_restore,
    get_backups,
    get_save_sources,
)


# ---------------------------------------------------------------------------
# Modal Screens
# ---------------------------------------------------------------------------

class ConfirmScreen(ModalScreen[bool]):
    """Generic yes/no confirmation dialog."""

    DEFAULT_CSS = """
    ConfirmScreen {
        align: center middle;
        background: $background 60%;
    }
    ConfirmScreen > Vertical {
        border: thick $primary;
        background: $surface;
        padding: 1 2;
        width: 64;
        height: auto;
    }
    ConfirmScreen Label {
        margin-bottom: 1;
    }
    ConfirmScreen Horizontal {
        height: auto;
        align: center middle;
    }
    ConfirmScreen Button {
        margin: 0 1;
    }
    """

    def __init__(self, message: str) -> None:
        super().__init__()
        self._message = message

    def compose(self) -> ComposeResult:
        with Vertical():
            yield Label(self._message)
            with Horizontal():
                yield Button("Yes", id="yes", variant="success")
                yield Button("No", id="no", variant="error")

    @on(Button.Pressed, "#yes")
    def _yes(self) -> None:
        self.dismiss(True)

    @on(Button.Pressed, "#no")
    def _no(self) -> None:
        self.dismiss(False)


class SettingsScreen(ModalScreen["Config | None"]):
    """Edit backup_dir and saves_dir paths."""

    DEFAULT_CSS = """
    SettingsScreen {
        align: center middle;
        background: $background 60%;
    }
    SettingsScreen > Vertical {
        border: thick $primary;
        background: $surface;
        padding: 1 2;
        width: 80;
        height: auto;
    }
    SettingsScreen Label {
        margin-top: 1;
    }
    SettingsScreen Input {
        margin-bottom: 1;
    }
    SettingsScreen #error-label {
        color: $error;
        height: 1;
    }
    SettingsScreen Horizontal {
        height: auto;
        align: center middle;
    }
    SettingsScreen Button {
        margin: 0 1;
    }
    """

    def __init__(self, config: Config) -> None:
        super().__init__()
        self._config = config

    def compose(self) -> ComposeResult:
        with Vertical():
            yield Label("Saves Directory:")
            yield Input(str(self._config.saves_dir), id="saves-dir")
            yield Label("Backup Directory:")
            yield Input(str(self._config.backup_dir), id="backup-dir")
            yield Label("", id="error-label")
            with Horizontal():
                yield Button("Save", id="save", variant="success")
                yield Button("Cancel", id="cancel", variant="default")

    @on(Button.Pressed, "#save")
    def _save(self) -> None:
        saves_input = self.query_one("#saves-dir", Input).value.strip()
        backup_input = self.query_one("#backup-dir", Input).value.strip()
        error_label = self.query_one("#error-label", Label)

        if not saves_input:
            error_label.update("Saves directory cannot be empty.")
            return
        if not backup_input:
            error_label.update("Backup directory cannot be empty.")
            return

        try:
            saves_dir = Path(saves_input).expanduser().resolve()
            backup_dir = Path(backup_input).expanduser().resolve()
        except Exception as exc:
            error_label.update(f"Invalid path: {exc}")
            return

        new_config = Config(saves_dir=saves_dir, backup_dir=backup_dir)
        self.dismiss(new_config)

    @on(Button.Pressed, "#cancel")
    def _cancel(self) -> None:
        self.dismiss(None)


# ---------------------------------------------------------------------------
# Custom Widgets
# ---------------------------------------------------------------------------

class SourceItem(ListItem):
    def __init__(self, source: SaveSource) -> None:
        super().__init__(Label(source.name))
        self.source = source


# ---------------------------------------------------------------------------
# Main App
# ---------------------------------------------------------------------------

class D2RSaveManager(App):
    TITLE = "D2R Save Manager"
    SUB_TITLE = "Backup & Restore Diablo II Resurrected saves"

    BINDINGS = [
        Binding("b", "backup", "Backup"),
        Binding("r", "restore", "Restore"),
        Binding("d", "delete", "Delete"),
        Binding("s", "settings", "Settings"),
        Binding("f5", "refresh", "Refresh"),
        Binding("q", "quit", "Quit"),
    ]

    CSS = """
    #main-pane {
        layout: horizontal;
        height: 1fr;
    }

    #source-panel {
        width: 28;
        border: round $primary;
    }

    #backup-panel {
        width: 1fr;
        border: round $primary;
    }

    #status-bar {
        height: 1;
        padding: 0 1;
        background: $panel;
    }
    """

    def __init__(self) -> None:
        super().__init__()
        self.config = load_config()
        self._current_source: SaveSource | None = None
        self._current_backup: BackupEntry | None = None
        self._backup_map: dict[str, BackupEntry] = {}

    def compose(self) -> ComposeResult:
        yield Header()
        with Horizontal(id="main-pane"):
            with Vertical(id="source-panel"):
                yield ListView(id="source-list")
            with Vertical(id="backup-panel"):
                yield DataTable(cursor_type="row", id="backup-table")
        yield Static("Ready.", id="status-bar")
        yield Footer()

    def on_mount(self) -> None:
        table = self.query_one("#backup-table", DataTable)
        table.add_columns("Date / Time", "Source", "Size", "Notes")

        self.query_one("#source-panel", Vertical).border_title = "Save Sources"
        self.query_one("#backup-panel", Vertical).border_title = "Backups"

        self.action_refresh()

    # ------------------------------------------------------------------
    # Event handlers
    # ------------------------------------------------------------------

    def on_list_view_highlighted(self, event: ListView.Highlighted) -> None:
        if event.item is None:
            return
        if not isinstance(event.item, SourceItem):
            return
        self._current_source = event.item.source
        self._refresh_backup_table()

    def on_data_table_row_highlighted(self, event: DataTable.RowHighlighted) -> None:
        if event.row_key is None:
            return
        self._current_backup = self._backup_map.get(str(event.row_key.value))

    # ------------------------------------------------------------------
    # Internal refresh helpers
    # ------------------------------------------------------------------

    def action_refresh(self) -> None:
        self._refresh_source_list()

    def _refresh_source_list(self) -> None:
        source_list = self.query_one("#source-list", ListView)
        source_list.clear()
        sources = get_save_sources(self.config)
        for source in sources:
            source_list.append(SourceItem(source))
        if sources:
            source_list.index = 0

    def _refresh_backup_table(self) -> None:
        table = self.query_one("#backup-table", DataTable)
        table.clear()
        self._backup_map.clear()
        self._current_backup = None

        if self._current_source is None:
            return

        entries = get_backups(self.config, self._current_source)
        for entry in entries:
            key = str(entry.path)
            self._backup_map[key] = entry
            table.add_row(
                entry.display_timestamp,
                entry.display_source,
                entry.display_size,
                entry.display_notes,
                key=key,
            )

    def set_status(self, message: str) -> None:
        self.query_one("#status-bar", Static).update(message)

    # ------------------------------------------------------------------
    # Actions
    # ------------------------------------------------------------------

    def action_backup(self) -> None:
        if self._current_source is None:
            self.set_status("No save source selected.")
            return
        source = self._current_source

        def _do_backup(confirmed: bool) -> None:
            if not confirmed:
                return
            try:
                entry = do_backup(self.config, source)
                self.set_status(
                    f"Backed up {source.name} → {entry.display_timestamp} ({entry.display_size})"
                )
            except Exception as exc:
                self.set_status(f"Backup failed: {exc}")
            finally:
                self._refresh_backup_table()

        self.push_screen(
            ConfirmScreen(f"Back up {source.name}?"),
            _do_backup,
        )

    def action_restore(self) -> None:
        if self._current_source is None:
            self.set_status("No save source selected.")
            return
        if self._current_backup is None:
            self.set_status("No backup selected.")
            return
        source = self._current_source
        backup = self._current_backup

        msg = (
            f"Restore {source.name} from {backup.display_timestamp}?\n\n"
            "IMPORTANT: Close D2R before restoring!\n"
            "Current saves will be auto-backed up first."
        )

        def _do_restore(confirmed: bool) -> None:
            if not confirmed:
                return
            try:
                do_restore(self.config, source, backup)
                self.set_status(
                    f"Restored {source.name} from {backup.display_timestamp}."
                )
            except Exception as exc:
                self.set_status(f"Restore failed: {exc}")
            finally:
                self._refresh_backup_table()

        self.push_screen(ConfirmScreen(msg), _do_restore)

    def action_delete(self) -> None:
        if self._current_backup is None:
            self.set_status("No backup selected.")
            return
        backup = self._current_backup

        def _do_delete(confirmed: bool) -> None:
            if not confirmed:
                return
            try:
                delete_backup(backup)
                self.set_status(
                    f"Deleted backup {backup.display_source} {backup.display_timestamp}."
                )
            except Exception as exc:
                self.set_status(f"Delete failed: {exc}")
            finally:
                self._refresh_backup_table()

        self.push_screen(
            ConfirmScreen(
                f"Delete backup {backup.display_source} {backup.display_timestamp}?\n"
                "This cannot be undone."
            ),
            _do_delete,
        )

    def action_settings(self) -> None:
        def _apply_settings(new_config: "Config | None") -> None:
            if new_config is None:
                return
            self.config = new_config
            try:
                save_config(new_config)
                self.set_status("Settings saved.")
            except Exception as exc:
                self.set_status(f"Failed to save settings: {exc}")
            self.action_refresh()

        self.push_screen(SettingsScreen(self.config), _apply_settings)


if __name__ == "__main__":
    D2RSaveManager().run()
