"""Small, testable settings I/O helpers.

User settings are persistent state. Writes delegate to the canonical atomic JSON
owner so settings, checkpoints and diagnostic state share the same unique-temp
commit behavior. Reads remain strict so callers can distinguish corrupt JSON from
a missing/default value and log the real problem.
"""
from __future__ import annotations

import json
import os  # compatibility seam: tests/extensions patch settings_io.os.replace
from pathlib import Path
from typing import Any

from videotranslator.core.io import atomic_write_json as _atomic_write_json


def atomic_write_json(path: str | Path, data: Any) -> None:
    _atomic_write_json(Path(path), data)


def read_json(path: str | Path) -> Any:
    with Path(path).open("r", encoding="utf-8") as stream:
        return json.load(stream)
