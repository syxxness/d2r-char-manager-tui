from __future__ import annotations

import re
import shutil
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from config import Config

TIMESTAMP_FMT = "%Y%m%d_%H%M%S"
PRE_RESTORE_MARKER = ".pre_restore"

# Extensions that make up a single character's save data.
# .ma* (e.g. .ma0, .ma1) are map cache files — included so maps survive a restore.
# .ctlo marks an online-only character; used for exclusion, not backup.
CHARACTER_EXTENSIONS = frozenset({".d2s", ".ctl", ".key"})
_MAP_EXT_RE = re.compile(r"\.ma\d+$", re.IGNORECASE)


# ---------------------------------------------------------------------------
# Dataclasses
# ---------------------------------------------------------------------------

@dataclass
class SaveSource:
    name: str            # "Vanilla" | "  Aragorn" | "Mod: plugy"
    source_type: str     # "vanilla" | "mod" | "character"
    mod_name: str | None
    path: Path           # parent save dir (for characters, files live directly here)
    character_name: str | None = None


@dataclass
class BackupEntry:
    source_type: str
    mod_name: str | None
    character_name: str | None
    timestamp: datetime
    path: Path
    size_bytes: int
    is_pre_restore: bool
    display_source: str
    display_timestamp: str  # "YYYY-MM-DD HH:MM:SS"
    display_size: str       # "2.3 MB"
    display_notes: str      # "Pre-Restore" | ""


# ---------------------------------------------------------------------------
# Private helpers
# ---------------------------------------------------------------------------

def _is_character_file(filename: str, character_name: str) -> bool:
    """True if filename belongs to character_name with an allowed extension."""
    p = Path(filename)
    if p.stem != character_name:
        return False
    ext = p.suffix.lower()
    return ext in CHARACTER_EXTENSIONS or bool(_MAP_EXT_RE.match(ext))


def _dir_size(path: Path) -> int:
    return sum(f.stat().st_size for f in path.rglob("*") if f.is_file())


def _fmt_size(size_bytes: int) -> str:
    for unit in ("B", "KB", "MB", "GB"):
        if size_bytes < 1024:
            return f"{size_bytes:.1f} {unit}"
        size_bytes //= 1024
    return f"{size_bytes:.1f} TB"


def _make_backup_entry(
    path: Path,
    source_type: str,
    mod_name: str | None,
    character_name: str | None,
    timestamp: datetime,
    is_pre_restore: bool = False,
) -> BackupEntry:
    size = _dir_size(path)

    if source_type == "character":
        display_source = (
            f"Vanilla: {character_name}"
            if mod_name is None
            else f"Mod {mod_name}: {character_name}"
        )
    elif source_type == "vanilla":
        display_source = "Vanilla"
    else:
        display_source = f"Mod: {mod_name}"

    return BackupEntry(
        source_type=source_type,
        mod_name=mod_name,
        character_name=character_name,
        timestamp=timestamp,
        path=path,
        size_bytes=size,
        is_pre_restore=is_pre_restore,
        display_source=display_source,
        display_timestamp=timestamp.strftime("%Y-%m-%d %H:%M:%S"),
        display_size=_fmt_size(size),
        display_notes="Pre-Restore" if is_pre_restore else "",
    )


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def get_characters(save_dir: Path) -> list[str]:
    """Return sorted list of offline character names found in save_dir.

    A character is offline-eligible when it has a .d2s file but no .ctlo file
    (online-only characters have .ctlo and are excluded).
    """
    if not save_dir.is_dir():
        return []
    all_names: set[str] = set()
    online_names: set[str] = set()
    for f in save_dir.iterdir():
        if not f.is_file():
            continue
        ext = f.suffix.lower()
        if ext == ".d2s":
            all_names.add(f.stem)
        elif ext == ".ctlo":
            online_names.add(f.stem)
    return sorted(all_names - online_names)


def get_save_sources(config: Config) -> list[SaveSource]:
    """Yield Vanilla (full) then its characters, then each mod (full) then its characters."""
    sources: list[SaveSource] = []

    # --- Vanilla ---
    sources.append(SaveSource(
        name="Vanilla",
        source_type="vanilla",
        mod_name=None,
        path=config.saves_dir,
    ))
    for char_name in get_characters(config.saves_dir):
        sources.append(SaveSource(
            name=f"  {char_name}",
            source_type="character",
            mod_name=None,
            path=config.saves_dir,
            character_name=char_name,
        ))

    # --- Mods ---
    mods_dir = config.saves_dir / "mods"
    if mods_dir.is_dir():
        for subdir in sorted(mods_dir.iterdir()):
            if not subdir.is_dir():
                continue
            sources.append(SaveSource(
                name=f"Mod: {subdir.name}",
                source_type="mod",
                mod_name=subdir.name,
                path=subdir,
            ))
            for char_name in get_characters(subdir):
                sources.append(SaveSource(
                    name=f"  {char_name}",
                    source_type="character",
                    mod_name=subdir.name,
                    path=subdir,
                    character_name=char_name,
                ))

    return sources


def get_backups(
    config: Config, source: SaveSource | None = None
) -> list[BackupEntry]:
    """Scan backup_dir; sorted newest-first. Skips dirs that don't match timestamp format."""
    entries: list[BackupEntry] = []

    def _scan(
        backup_subdir: Path,
        source_type: str,
        mod_name: str | None,
        character_name: str | None = None,
    ) -> None:
        if not backup_subdir.is_dir():
            return
        for ts_dir in backup_subdir.iterdir():
            if not ts_dir.is_dir():
                continue
            try:
                ts = datetime.strptime(ts_dir.name, TIMESTAMP_FMT)
            except ValueError:
                continue
            is_pre_restore = (ts_dir / PRE_RESTORE_MARKER).exists()
            entries.append(
                _make_backup_entry(ts_dir, source_type, mod_name, character_name, ts, is_pre_restore)
            )

    if source is None:
        # All backups across all sources
        _scan(config.backup_dir / "vanilla", "vanilla", None)
        vanilla_chars = config.backup_dir / "vanilla" / "chars"
        if vanilla_chars.is_dir():
            for char_dir in vanilla_chars.iterdir():
                if char_dir.is_dir():
                    _scan(char_dir, "character", None, char_dir.name)
        mods_backup = config.backup_dir / "mods"
        if mods_backup.is_dir():
            for mod_dir in mods_backup.iterdir():
                if not mod_dir.is_dir():
                    continue
                _scan(mod_dir, "mod", mod_dir.name)
                mod_chars = mod_dir / "chars"
                if mod_chars.is_dir():
                    for char_dir in mod_chars.iterdir():
                        if char_dir.is_dir():
                            _scan(char_dir, "character", mod_dir.name, char_dir.name)

    elif source.source_type == "vanilla":
        _scan(config.backup_dir / "vanilla", "vanilla", None)

    elif source.source_type == "mod":
        _scan(config.backup_dir / "mods" / source.mod_name, "mod", source.mod_name)

    elif source.source_type == "character":
        if source.mod_name:
            char_dir = config.backup_dir / "mods" / source.mod_name / "chars" / source.character_name
        else:
            char_dir = config.backup_dir / "vanilla" / "chars" / source.character_name
        _scan(char_dir, "character", source.mod_name, source.character_name)

    entries.sort(key=lambda e: e.timestamp, reverse=True)
    return entries


def do_backup(
    config: Config, source: SaveSource, is_pre_restore: bool = False
) -> BackupEntry:
    """Copy current save files into a new timestamped backup directory."""
    timestamp = datetime.now()
    ts_str = timestamp.strftime(TIMESTAMP_FMT)

    if source.source_type == "character":
        if source.mod_name:
            dest = config.backup_dir / "mods" / source.mod_name / "chars" / source.character_name / ts_str
        else:
            dest = config.backup_dir / "vanilla" / "chars" / source.character_name / ts_str
        dest.mkdir(parents=True, exist_ok=True)
        for f in source.path.iterdir():
            if f.is_file() and _is_character_file(f.name, source.character_name):
                shutil.copy2(f, dest / f.name)

    elif source.source_type == "vanilla":
        dest = config.backup_dir / "vanilla" / ts_str
        shutil.copytree(
            source.path,
            dest,
            ignore=shutil.ignore_patterns("mods"),
        )

    else:  # mod
        dest = config.backup_dir / "mods" / source.mod_name / ts_str
        shutil.copytree(source.path, dest)

    if is_pre_restore:
        (dest / PRE_RESTORE_MARKER).touch()

    return _make_backup_entry(
        dest, source.source_type, source.mod_name, source.character_name,
        timestamp, is_pre_restore,
    )


def do_restore(config: Config, source: SaveSource, backup: BackupEntry) -> None:
    """
    1. Auto-backup current state (marked as pre-restore).
    2. Overwrite current saves with the chosen backup.
    """
    do_backup(config, source, is_pre_restore=True)

    if source.source_type == "character":
        # Remove only this character's existing files; leave other characters intact.
        for f in source.path.iterdir():
            if f.is_file() and _is_character_file(f.name, source.character_name):
                f.unlink()
        # Copy backed-up character files back (skip the marker file).
        for f in backup.path.iterdir():
            if f.is_file() and f.name != PRE_RESTORE_MARKER:
                shutil.copy2(f, source.path / f.name)

    else:
        if source.path.exists():
            shutil.rmtree(source.path)
        source.path.mkdir(parents=True, exist_ok=True)
        shutil.copytree(
            backup.path,
            source.path,
            dirs_exist_ok=True,
            ignore=shutil.ignore_patterns(PRE_RESTORE_MARKER),
        )


def delete_backup(backup: BackupEntry) -> None:
    """Permanently remove a backup snapshot from disk."""
    shutil.rmtree(backup.path)
