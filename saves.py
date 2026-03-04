from __future__ import annotations

import json
import re
import shutil
import tempfile
import zipfile
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from config import Config

TIMESTAMP_FMT = "%Y%m%d_%H%M%S"
PRE_RESTORE_MARKER = ".pre_restore"
FULL_MOD_SAVE_MARKER = ".full_mod_save"
FULL_MOD_SAVE_META = ".full_mod_save.json"
FULL_MOD_SAVE_ARCHIVE = "mod_files.zip"
COMMENT_FILE = ".comment"

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
    is_full_mod_save: bool
    comment: str
    display_source: str
    display_timestamp: str  # "YYYY-MM-DD HH:MM:SS"
    display_size: str       # "2.3 MB"
    display_notes: str      # flags + comment, e.g. "Pre-Restore — my note"


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
    is_full_mod_save: bool = False,
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

    comment_path = path / COMMENT_FILE
    try:
        comment = comment_path.read_text(encoding="utf-8").strip()
    except (OSError, ValueError):
        comment = ""

    flags: list[str] = []
    if is_pre_restore:
        flags.append("Pre-Restore")
    if is_full_mod_save:
        flags.append("Full Mod+Save")

    flag_str = ", ".join(flags)
    if flag_str and comment:
        display_notes = f"{flag_str} — {comment}"
    elif flag_str:
        display_notes = flag_str
    else:
        display_notes = comment

    return BackupEntry(
        source_type=source_type,
        mod_name=mod_name,
        character_name=character_name,
        timestamp=timestamp,
        path=path,
        size_bytes=size,
        is_pre_restore=is_pre_restore,
        is_full_mod_save=is_full_mod_save,
        comment=comment,
        display_source=display_source,
        display_timestamp=timestamp.strftime("%Y-%m-%d %H:%M:%S"),
        display_size=_fmt_size(size),
        display_notes=display_notes,
    )


def _normalize_save_path_name(raw_value: str | None) -> str | None:
    if not raw_value:
        return None
    normalized = raw_value.strip().replace("\\", "/").rstrip("/")
    if not normalized:
        return None
    return Path(normalized).name.lower()


def _read_mod_save_folder(modinfo_path: Path) -> str | None:
    def _walk_for_savepath(value: object) -> str | None:
        if isinstance(value, dict):
            for raw_key, nested in value.items():
                key = str(raw_key).lower().replace("_", "")
                if key in {"savepath", "savefolder", "savedir", "savefoldername"}:
                    if isinstance(nested, str):
                        normalized = _normalize_save_path_name(nested)
                        if normalized:
                            return normalized
                    # Key matched but value wasn't a usable string — skip recursive
                    # descent into this value to avoid false positives from nested keys.
                    continue
                nested_match = _walk_for_savepath(nested)
                if nested_match:
                    return nested_match
        elif isinstance(value, list):
            for item in value:
                nested_match = _walk_for_savepath(item)
                if nested_match:
                    return nested_match
        return None

    try:
        with modinfo_path.open("r", encoding="utf-8") as f:
            raw_text = f.read()
    except (OSError, TypeError):
        return None
    try:
        data = json.loads(raw_text)
    except (json.JSONDecodeError, TypeError):
        # Fallback for malformed JSON-like files.
        match = re.search(r'"save[_ ]?path"\s*:\s*"([^"]+)"', raw_text, re.IGNORECASE)
        if match:
            return _normalize_save_path_name(match.group(1))
        return None
    return _walk_for_savepath(data)


def _resolve_mod_install_dir(save_mod_name: str, mods_root: Path) -> Path | None:
    if not mods_root.is_dir():
        return None

    save_name_norm = _normalize_save_path_name(save_mod_name)
    if save_name_norm is None:
        return None

    top_level_mod_dirs = sorted(
        (p for p in mods_root.iterdir() if p.is_dir()),
        key=lambda p: p.name.lower(),
    )

    for mod_dir in top_level_mod_dirs:
        if mod_dir.name.lower() == save_name_norm:
            return mod_dir

    # Search recursively for modinfo.json under each top-level mod directory.
    # If a nested modinfo.json matches the save folder (e.g. Reimagined.mpq/modinfo.json),
    # use the top-level mod directory as the install folder backup target.
    for mod_dir in top_level_mod_dirs:
        for modinfo_path in sorted(mod_dir.rglob("modinfo.json")):
            mapped_save_folder = _read_mod_save_folder(modinfo_path)
            if mapped_save_folder == save_name_norm:
                return mod_dir

    return None


def _copy_source_to_backup(source: SaveSource, dest: Path) -> None:
    if source.source_type == "character":
        dest.mkdir(parents=True, exist_ok=True)
        for f in source.path.iterdir():
            if f.is_file() and _is_character_file(f.name, source.character_name):
                shutil.copy2(f, dest / f.name)
    elif source.source_type == "vanilla":
        shutil.copytree(
            source.path,
            dest,
            ignore=shutil.ignore_patterns("mods"),
        )
    else:  # mod
        shutil.copytree(source.path, dest)


def _zip_directory(directory: Path, zip_path: Path) -> None:
    with zipfile.ZipFile(zip_path, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        for path in directory.rglob("*"):
            if path.is_file():
                arcname = str(path.relative_to(directory.parent))
                zf.write(path, arcname=arcname)


def _read_full_mod_save_meta(path: Path) -> dict[str, object]:
    meta_path = path / FULL_MOD_SAVE_META
    if not meta_path.is_file():
        return {}
    try:
        with meta_path.open("r", encoding="utf-8") as f:
            data = json.load(f)
    except (OSError, json.JSONDecodeError, TypeError):
        return {}
    if isinstance(data, dict):
        return data
    return {}


def _restore_mod_archive(
    backup_path: Path,
    archive_name: str,
    target_mod_dir: Path,
    archived_mod_name: str | None = None,
) -> None:
    archive_path = backup_path / archive_name
    if not archive_path.is_file():
        raise FileNotFoundError(f"Missing mod archive: {archive_path}")

    with tempfile.TemporaryDirectory(prefix="d2r_mod_restore_") as tmp:
        temp_root = Path(tmp)
        with zipfile.ZipFile(archive_path, "r") as zf:
            zf.extractall(temp_root)

        inferred_name = archived_mod_name
        if not inferred_name:
            roots = sorted(
                p.name for p in temp_root.iterdir()
                if p.is_dir()
            )
            if len(roots) == 1:
                inferred_name = roots[0]

        if not inferred_name:
            raise ValueError("Unable to determine archived mod folder name.")

        extracted_mod_dir = temp_root / inferred_name
        if not extracted_mod_dir.is_dir():
            raise FileNotFoundError(
                f"Archive did not contain expected mod folder '{inferred_name}'."
            )

        if target_mod_dir.exists():
            shutil.rmtree(target_mod_dir)
        target_mod_dir.parent.mkdir(parents=True, exist_ok=True)
        shutil.copytree(extracted_mod_dir, target_mod_dir)


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
            is_full_mod_save = (
                (ts_dir / FULL_MOD_SAVE_MARKER).exists()
                or (ts_dir / FULL_MOD_SAVE_META).exists()
            )
            entries.append(
                _make_backup_entry(
                    ts_dir,
                    source_type,
                    mod_name,
                    character_name,
                    ts,
                    is_pre_restore,
                    is_full_mod_save,
                )
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
    config: Config,
    source: SaveSource,
    is_pre_restore: bool = False,
    comment: str = "",
) -> BackupEntry:
    """Copy current save files into a new timestamped backup directory."""
    timestamp = datetime.now()
    ts_str = timestamp.strftime(TIMESTAMP_FMT)

    if source.source_type == "character":
        if source.mod_name:
            dest = config.backup_dir / "mods" / source.mod_name / "chars" / source.character_name / ts_str
        else:
            dest = config.backup_dir / "vanilla" / "chars" / source.character_name / ts_str
    elif source.source_type == "vanilla":
        dest = config.backup_dir / "vanilla" / ts_str
    else:  # mod
        dest = config.backup_dir / "mods" / source.mod_name / ts_str

    _copy_source_to_backup(source, dest)

    if is_pre_restore:
        (dest / PRE_RESTORE_MARKER).touch()

    if comment:
        (dest / COMMENT_FILE).write_text(comment.strip(), encoding="utf-8")

    return _make_backup_entry(
        dest, source.source_type, source.mod_name, source.character_name,
        timestamp, is_pre_restore, is_full_mod_save=False,
    )


def do_full_mod_save_backup(config: Config, source: SaveSource, comment: str = "") -> BackupEntry:
    """Backup save data plus its matching installed mod directory as a zip archive."""
    if source.mod_name is None or source.source_type == "vanilla":
        raise ValueError("Full mod+save backup only applies to mod saves.")

    install_mod_dir = _resolve_mod_install_dir(source.mod_name, config.mods_install_dir)
    if install_mod_dir is None:
        raise FileNotFoundError(
            f"Could not find installed mod folder for save folder '{source.mod_name}' in {config.mods_install_dir}."
        )

    timestamp = datetime.now()
    ts_str = timestamp.strftime(TIMESTAMP_FMT)

    if source.source_type == "character":
        dest = config.backup_dir / "mods" / source.mod_name / "chars" / source.character_name / ts_str
    else:  # mod
        dest = config.backup_dir / "mods" / source.mod_name / ts_str

    _copy_source_to_backup(source, dest)
    _zip_directory(install_mod_dir, dest / FULL_MOD_SAVE_ARCHIVE)
    (dest / FULL_MOD_SAVE_MARKER).touch()
    if comment:
        (dest / COMMENT_FILE).write_text(comment.strip(), encoding="utf-8")
    with (dest / FULL_MOD_SAVE_META).open("w", encoding="utf-8") as f:
        json.dump(
            {
                "install_mod_name": install_mod_dir.name,
                "save_mod_name": source.mod_name,
                "archive": FULL_MOD_SAVE_ARCHIVE,
                "restore_targets": {
                    "save_path": str(source.path),
                    "install_mod_dir": str(install_mod_dir),
                    "mods_root": str(config.mods_install_dir),
                },
            },
            f,
            indent=2,
        )

    return _make_backup_entry(
        dest,
        source.source_type,
        source.mod_name,
        source.character_name,
        timestamp,
        is_pre_restore=False,
        is_full_mod_save=True,
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
        # Copy back only character files; ignore backup metadata/archives.
        for f in backup.path.iterdir():
            if f.is_file() and _is_character_file(f.name, source.character_name):
                shutil.copy2(f, source.path / f.name)

    else:
        if source.path.exists():
            shutil.rmtree(source.path)
        source.path.mkdir(parents=True, exist_ok=True)
        shutil.copytree(
            backup.path,
            source.path,
            dirs_exist_ok=True,
            ignore=shutil.ignore_patterns(
                PRE_RESTORE_MARKER,
                FULL_MOD_SAVE_MARKER,
                FULL_MOD_SAVE_META,
                FULL_MOD_SAVE_ARCHIVE,
                COMMENT_FILE,
            ),
        )

    # For "full mod+save" snapshots, restore the installed mod folder as well.
    if backup.is_full_mod_save:
        meta = _read_full_mod_save_meta(backup.path)
        restore_targets = meta.get("restore_targets")
        install_target_str = None
        if isinstance(restore_targets, dict):
            raw_target = restore_targets.get("install_mod_dir")
            if isinstance(raw_target, str) and raw_target.strip():
                install_target_str = raw_target

        if install_target_str:
            target_mod_dir = Path(install_target_str).expanduser()
        else:
            install_mod_name = meta.get("install_mod_name")
            if isinstance(install_mod_name, str) and install_mod_name.strip():
                target_mod_dir = config.mods_install_dir / install_mod_name
            else:
                resolved = _resolve_mod_install_dir(source.mod_name or "", config.mods_install_dir)
                if resolved is None:
                    raise FileNotFoundError(
                        f"Unable to determine mod restore target for '{source.mod_name}'."
                    )
                target_mod_dir = resolved

        archive_name = meta.get("archive")
        if not isinstance(archive_name, str) or not archive_name.strip():
            archive_name = FULL_MOD_SAVE_ARCHIVE
        archived_mod_name = meta.get("install_mod_name")
        if not isinstance(archived_mod_name, str) or not archived_mod_name.strip():
            archived_mod_name = None

        _restore_mod_archive(
            backup.path,
            archive_name,
            target_mod_dir,
            archived_mod_name=archived_mod_name,
        )


def delete_backup(backup: BackupEntry) -> None:
    """Permanently remove a backup snapshot from disk."""
    shutil.rmtree(backup.path)
