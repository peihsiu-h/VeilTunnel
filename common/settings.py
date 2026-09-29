"""Runtime file locations and JSON settings I/O (no GUI dependencies)."""

from __future__ import annotations

import json
import os
import sys


def data_dir() -> str:
    """Directory that holds runtime files (settings, certs, logs, ban list).

    * Packaged exe (PyInstaller): the folder containing the .exe, so the app
      stays portable — copy the folder and the settings come along.
    * Running from source: the project root.
    """
    if getattr(sys, "frozen", False):
        return os.path.dirname(os.path.abspath(sys.executable))
    return os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def data_path(*parts: str) -> str:
    return os.path.join(data_dir(), *parts)


def load_json(path: str, defaults: dict) -> dict:
    """Return *defaults* overlaid with the JSON file at *path* (if readable).

    Keys starting with "_" are treated as comments and ignored.
    """
    cfg = dict(defaults)
    try:
        with open(path, encoding="utf-8") as f:
            raw = json.load(f)
        if isinstance(raw, dict):
            cfg.update({k: v for k, v in raw.items() if not k.startswith("_")})
    except (OSError, ValueError):
        pass
    return cfg


def save_json(path: str, data: dict) -> None:
    """Write JSON atomically (temp file + rename) so a crash can't truncate it."""
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)
    os.replace(tmp, path)
