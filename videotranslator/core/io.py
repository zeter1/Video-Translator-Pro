"""Owner module for: atomic_write_text, read_json_file, atomic_write_json."""

from __future__ import annotations

from pathlib import Path
from datetime import datetime
import json
import os
import shutil
import threading


def atomic_write_text(path: Path, text: str):
    """Атомарно записывает UTF-8 текст, не оставляя частично записанный отчёт."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temp_path = path.with_name(f".{path.name}.{os.getpid()}.{threading.get_ident()}.tmp")
    try:
        with open(temp_path, "w", encoding="utf-8", newline="\n") as file:
            file.write(text)
            file.flush()
            os.fsync(file.fileno())
        os.replace(temp_path, path)
    finally:
        try:
            if temp_path.exists():
                temp_path.unlink()
        except OSError:
            pass


def read_json_file(path: Path) -> dict:
    try:
        with open(path, "r", encoding="utf-8-sig") as file:
            value = json.load(file)
        return value if isinstance(value, dict) else {}
    except (OSError, ValueError, TypeError):
        return {}


def atomic_write_json(path: Path, data: dict):
    """Атомарно записывает небольшой служебный JSON рядом с программой."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temp_path = path.with_name(f".{path.name}.{os.getpid()}.{threading.get_ident()}.tmp")
    try:
        with open(temp_path, "w", encoding="utf-8", newline="\n") as file:
            json.dump(data, file, ensure_ascii=False, indent=2)
            file.flush()
            os.fsync(file.fileno())
        os.replace(temp_path, path)
    finally:
        try:
            if temp_path.exists():
                temp_path.unlink()
        except OSError:
            pass


def backup_corrupt_file(path: Path, label: str = "corrupt") -> Path | None:
    """Preserve unreadable persistent state before a later save can replace it."""
    path = Path(path)
    if not path.is_file():
        return None
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S_%f")
    backup = path.with_name(
        f"{path.stem}.{label}.{stamp}.{os.getpid()}.{threading.get_ident()}{path.suffix}"
    )
    shutil.copy2(path, backup)
    return backup
