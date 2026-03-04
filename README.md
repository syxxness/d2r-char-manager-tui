# D2R Save Manager

A terminal UI for backing up and restoring **Diablo II Resurrected** offline save files on Linux (Steam).

Supports full save backups and per-character backups, for both vanilla and mod saves. Restores automatically snapshot the current state first so you can always roll back.

---

## Features

- **Full save backup/restore** — snapshot your entire vanilla or mod save directory
- **Per-character backup/restore** — back up a single character's files (`.d2s`, `.ctl`, `.key`, `.ma*`) without touching other characters
- **Auto pre-restore snapshots** — before any restore, the current state is automatically backed up and labeled "Pre-Restore" in the UI
- **Mod support** — any subdirectory under `mods/` is automatically detected as a separate save source
- **Configurable paths** — backup directory and save directory are editable from within the TUI
- **Online character filtering** — characters with `.ctlo` files (online-only) are never listed

---

## Requirements

- Python 3.10+
- [Textual](https://github.com/Textualize/textual) >= 0.50.0

```
pip install textual>=0.50.0
```

---

## Running

```
python app.py
```

---

## Keybindings

| Key | Action |
|-----|--------|
| `b` | Backup selected source (full save or character) |
| `r` | Restore selected backup into the current save directory |
| `d` | Delete selected backup snapshot |
| `s` | Open settings (edit save/backup directory paths) |
| `F5` | Refresh source list and backup table |
| `q` | Quit |

---

## Layout

```
┌ Save Sources ───────┐  ┌ Backups ──────────────────────────────────────────────────┐
│ Vanilla             │  │ Date / Time          Source              Size    Notes      │
│   Aragorn           │  │ 2025-06-01 14:32:11  Vanilla: Aragorn   1.2 MB             │
│   Boromir           │  │ 2025-05-30 09:11:44  Vanilla: Aragorn   1.1 MB Pre-Restore │
│ Mod: plugy          │  └───────────────────────────────────────────────────────────-┘
│   Gandalf           │
└─────────────────────┘
```

Characters are listed indented under their parent save source. Selecting a character scopes the backup table to that character's snapshots only.

---

## Backup Directory Structure

```
~/d2r-backups/
  vanilla/
    20250601_143211/          ← full vanilla backup
    chars/
      Aragorn/
        20250530_091144/      ← full character backup
  mods/
    plugy/
      20250601_150000/        ← full mod backup
      chars/
        Gandalf/
          20250601_151200/    ← full character backup
```

Pre-restore snapshots contain a `.pre_restore` marker file inside the snapshot directory. This file is never copied back into your save directory during a restore.

---

## Default Paths

| What | Default |
|------|---------|
| D2R saves | `~/.steam/steam/steamapps/compatdata/2536520/pfx/drive_c/users/steamuser/Saved Games/Diablo II Resurrected/` |
| Backups | `~/d2r-backups/` |
| Config | `~/.config/d2r-save-manager/config.json` |

Both paths are configurable via `s` → Settings inside the TUI. Config persists across restarts.

---

## Notes

- **Close D2R before restoring.** The game holds save files open while running; restoring over live files may corrupt your save.
- File operations are synchronous. D2R save files are small (<50 MB typical) so this is not a problem in practice.
- Map cache files (`.ma0`, `.ma1`, etc.) are included in full character backups.
