from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

CONFIG_FILE = Path("~/.config/d2r-save-manager/config.json").expanduser()

DEFAULT_SAVES_DIR = Path(
    "~/.steam/steam/steamapps/compatdata/2536520/pfx/drive_c/users/steamuser"
    "/Saved Games/Diablo II Resurrected"
).expanduser()

DEFAULT_BACKUP_DIR = Path("~/d2r-backups").expanduser()

DEFAULT_MODS_INSTALL_DIR = Path(
    "~/.steam/steam/steamapps/common/Diablo II Resurrected/mods/"
).expanduser()


@dataclass
class Config:
    saves_dir: Path
    backup_dir: Path
    mods_install_dir: Path


def load_config() -> Config:
    """Load config from disk. Never raises; returns defaults if missing or malformed."""
    try:
        data = json.loads(CONFIG_FILE.read_text())
        saves_dir = Path(data["saves_dir"]).expanduser().resolve()
        backup_dir = Path(data["backup_dir"]).expanduser().resolve()
        mods_install_dir = (
            Path(data["mods_install_dir"]).expanduser().resolve()
            if "mods_install_dir" in data
            else DEFAULT_MODS_INSTALL_DIR.resolve()
        )
        return Config(saves_dir=saves_dir, backup_dir=backup_dir, mods_install_dir=mods_install_dir)
    except Exception:
        return Config(
            saves_dir=DEFAULT_SAVES_DIR.resolve(),
            backup_dir=DEFAULT_BACKUP_DIR.resolve(),
            mods_install_dir=DEFAULT_MODS_INSTALL_DIR.resolve(),
        )


def save_config(config: Config) -> None:
    """Write config to disk; creates parent dirs as needed."""
    CONFIG_FILE.parent.mkdir(parents=True, exist_ok=True)
    CONFIG_FILE.write_text(
        json.dumps(
            {
                "saves_dir": str(config.saves_dir),
                "backup_dir": str(config.backup_dir),
                "mods_install_dir": str(config.mods_install_dir),
            },
            indent=2,
        )
    )
