"""Owner module for: input_file_signature, get_batch_recovery_state_path, create_batch_recovery_state, save_batch_recovery_state, load_batch_recovery_state...."""

from __future__ import annotations

from videotranslator.core.compat_bridge import resolve_legacy_override

from pathlib import Path
from datetime import datetime
import json
import os
from uuid import uuid4
from videotranslator.config import BATCH_RECOVERY_SCHEMA_VERSION
from videotranslator.core.diagnostics import diagnostic_json_value, safe_log_filename
from videotranslator.core.io import atomic_write_json
from videotranslator.core.paths import get_translation_checkpoints_dir


def input_file_signature(path: str) -> dict:
    """Минимальная сигнатура входа для безопасного продолжения пакетной задачи."""
    result = {"path": os.path.abspath(path)}
    try:
        stat = os.stat(path)
        result.update({"size": int(stat.st_size), "modified_ns": int(stat.st_mtime_ns)})
    except OSError:
        pass
    return result


def get_batch_recovery_state_path() -> Path:
    return get_translation_checkpoints_dir() / "latest_batch_recovery.json"


def create_batch_recovery_state(files: list[str], output_dir: str, settings: dict) -> dict:
    now = datetime.now().astimezone().isoformat(timespec="seconds")
    return {
        "schema_version": BATCH_RECOVERY_SCHEMA_VERSION,
        "batch_id": f"{datetime.now():%Y%m%d_%H%M%S_%f}_{os.getpid()}_{uuid4().hex}",
        "status": "running",
        "created_at": now,
        "updated_at": now,
        "output_dir": os.path.abspath(output_dir),
        "settings": diagnostic_json_value(dict(settings or {})),
        "files": [
            {
                "file_index": index,
                "input": input_file_signature(path),
                "status": "pending",
                "output_path": "",
                "elapsed_sec": 0.0,
                "last_error": "",
            }
            for index, path in enumerate(files, 1)
        ],
    }


def save_batch_recovery_state(state: dict, path: Path | None = None):
    state["updated_at"] = datetime.now().astimezone().isoformat(timespec="seconds")
    atomic_write_json(path or resolve_legacy_override("get_batch_recovery_state_path", get_batch_recovery_state_path)(), state)


def load_batch_recovery_state(path: Path | None = None) -> dict:
    path = path or resolve_legacy_override("get_batch_recovery_state_path", get_batch_recovery_state_path)()
    if not path.exists():
        return {}
    try:
        with open(path, "r", encoding="utf-8-sig") as file:
            data = json.load(file)
        if not isinstance(data, dict):
            return {}
        if int(data.get("schema_version", 0)) != BATCH_RECOVERY_SCHEMA_VERSION:
            return {}
        files = data.get("files")
        output_dir = data.get("output_dir")
        if not isinstance(files, list) or not isinstance(output_dir, str) or not output_dir.strip():
            return {}
        if any(not isinstance(entry, dict) for entry in files):
            return {}
        return data
    except (OSError, ValueError, TypeError, OverflowError, UnicodeError):
        return {}


def batch_recovery_pending_entries(state: dict) -> list[dict]:
    if not isinstance(state, dict):
        return []
    files = state.get("files") or []
    if not isinstance(files, list):
        return []
    return [
        entry for entry in files
        if isinstance(entry, dict) and str(entry.get("status") or "pending") != "succeeded"
    ]


def reconstruct_batch_recovery_state_from_problem_log(path: str | Path) -> dict:
    """Восстанавливает пакет старой версии по её JSONL, не изменяя исходные видео.

    Пустой путь исторически превращался в ``Path('.')``. На Windows последующий
    ``with_name('problems.jsonl')`` падал с ``ValueError: WindowsPath('.') has an empty name``
    прямо из Tkinter callback при запуске приложения. Recovery — best-effort функция,
    поэтому пустой путь, каталог или отсутствующий файл означают просто "восстанавливать нечего".
    """
    raw_path = str(path or "").strip()
    if not raw_path:
        return {}
    path = Path(raw_path)
    if not path.is_file():
        return {}

    batch_started = None
    records = []
    sibling_problem_log = path.with_name("problems.jsonl")
    source_paths = [path]
    if path.name == "events.jsonl" and sibling_problem_log.exists():
        source_paths.append(sibling_problem_log)
    try:
        for source_path in source_paths:
            with open(source_path, "r", encoding="utf-8-sig") as file:
                for line in file:
                    try:
                        record = json.loads(line)
                    except (ValueError, TypeError):
                        continue
                    records.append(record)
                    if record.get("event") == "batch_started":
                        batch_started = record
    except OSError:
        return {}
    records.sort(key=lambda record: int(record.get("sequence") or 0))
    if not batch_started:
        return {}

    details = batch_started.get("details") or {}
    files = details.get("files") or []
    if isinstance(files, dict):
        files = files.get("items") or []
    files = [str(item or "") for item in files if str(item or "")]
    output_dir = str(details.get("output_dir") or "")
    if not files or not output_dir:
        return {}

    state = create_batch_recovery_state(files, output_dir, details.get("settings") or {})
    state["batch_id"] = f"recovered_{safe_log_filename(str(batch_started.get('session_id') or path.stem), 96)}"
    state["status"] = "interrupted"
    state["source_problem_log"] = str(path.resolve())
    by_path = {
        os.path.normcase(os.path.abspath(str((entry.get("input") or {}).get("path") or ""))): entry
        for entry in state.get("files", [])
    }

    for record in records:
        event = str(record.get("event") or "")
        event_details = record.get("details") or {}
        input_path = str(event_details.get("input_path") or "")
        entry = by_path.get(os.path.normcase(os.path.abspath(input_path))) if input_path else None
        if not entry:
            continue
        if event == "batch_file_started":
            entry["status"] = "pending"
            entry["output_path"] = str(event_details.get("output_path") or entry.get("output_path") or "")
        elif event == "file_pipeline_finished":
            entry["output_path"] = str(event_details.get("final_path") or entry.get("output_path") or "")
        elif event == "batch_file_finished":
            success = bool(event_details.get("success"))
            entry["status"] = "succeeded" if success else "failed"
            entry["elapsed_sec"] = float(event_details.get("elapsed_sec") or 0.0)
            if not success:
                entry["last_error"] = "Файл не был завершён в прежней сессии."

    batch_finished = next(
        (record for record in reversed(records) if record.get("event") == "batch_finished"),
        None,
    )
    if batch_finished:
        final_details = batch_finished.get("details") or {}
        if int(final_details.get("successful") or 0) == int(final_details.get("total") or len(files)):
            state["status"] = "completed"
    return state
