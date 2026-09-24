"""Durable FIFO queue for user-created translation tasks."""

from __future__ import annotations

from copy import deepcopy
from datetime import datetime
from pathlib import Path
import os
from uuid import uuid4

from videotranslator.config import TASK_QUEUE_SCHEMA_VERSION
from videotranslator.core.io import atomic_write_json, backup_corrupt_file
from videotranslator.core.paths import get_translation_checkpoints_dir


RUNNABLE_TASK_STATUSES = {"queued", "interrupted"}
TERMINAL_TASK_STATUSES = {"completed", "failed", "cancelled"}
KNOWN_TASK_STATUSES = RUNNABLE_TASK_STATUSES | TERMINAL_TASK_STATUSES | {"running"}


def _now_iso() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")


def get_task_queue_state_path() -> Path:
    return get_translation_checkpoints_dir() / "translation_tasks.json"


def create_task_queue_state() -> dict:
    now = _now_iso()
    return {
        "schema_version": TASK_QUEUE_SCHEMA_VERSION,
        "created_at": now,
        "updated_at": now,
        # Historical queue-owned batch IDs are retained after a task is removed so
        # latest_batch_recovery.json cannot resurrect an intentionally deleted task.
        "known_task_batch_ids": [],
        "tasks": [],
    }


def _backup_and_reset_queue(path: Path, *, label: str) -> dict:
    """Preserve invalid queue bytes once, then publish a clean durable state when possible."""
    fresh = create_task_queue_state()
    backup_created = False
    try:
        backup_created = backup_corrupt_file(path, label=label) is not None
    except OSError:
        backup_created = False
    if backup_created:
        try:
            atomic_write_json(path, fresh)
        except OSError:
            # The backup is already safe. Keep returning a usable in-memory state; a
            # later enqueue/save can retry replacing the invalid primary file.
            pass
    return fresh


def create_translation_task(batch_state: dict) -> dict:
    """Wrap one durable batch state as a FIFO task without losing its checkpoints."""
    batch = deepcopy(dict(batch_state or {}))
    batch_id = str(batch.get("batch_id") or "").strip()
    if not batch_id:
        batch_id = f"task_{datetime.now():%Y%m%d_%H%M%S_%f}_{os.getpid()}_{uuid4().hex}"
        batch["batch_id"] = batch_id
    now = _now_iso()
    task = {
        "task_id": batch_id,
        "created_at": now,
        "updated_at": now,
        "started_at": "",
        "finished_at": "",
        "status": "queued",
        "batch_state": batch,
        "runtime": {
            "current_file": "",
            "file_index": 0,
            "files_total": len(batch.get("files") or []),
            "progress": 0,
            "status_text": "В очереди",
        },
    }
    # Legacy single-batch recovery may contain older/partially damaged metadata.
    # Normalize at enqueue time too, not only when reloading translation_tasks.json.
    return _normalize_task(task) or task


def _normalize_batch_file_entry(entry, position: int) -> dict:
    if not isinstance(entry, dict):
        return {
            "file_index": position,
            "input": {},
            "status": "missing",
            "output_path": "",
            "elapsed_sec": 0.0,
            "last_error": "Повреждена запись файла в контрольной точке.",
        }

    try:
        file_index = max(1, int(entry.get("file_index") or position))
    except (TypeError, ValueError, OverflowError):
        file_index = position
    entry["file_index"] = file_index

    input_info = entry.get("input")
    if not isinstance(input_info, dict):
        input_info = {}
        entry["input"] = input_info
    path = input_info.get("path")
    input_info["path"] = path if isinstance(path, str) else ""

    entry["status"] = str(entry.get("status") or "pending")
    output_path = entry.get("output_path")
    entry["output_path"] = output_path if isinstance(output_path, str) else ""
    try:
        elapsed = float(entry.get("elapsed_sec") or 0.0)
        if elapsed < 0 or elapsed == float("inf") or elapsed == float("-inf") or elapsed != elapsed:
            raise ValueError("invalid elapsed_sec")
        entry["elapsed_sec"] = elapsed
    except (TypeError, ValueError, OverflowError):
        entry["elapsed_sec"] = 0.0
    entry["last_error"] = str(entry.get("last_error") or "")
    return entry


def _normalize_task(task: dict) -> dict | None:
    if not isinstance(task, dict):
        return None
    batch = task.get("batch_state")
    if not isinstance(batch, dict) or not isinstance(batch.get("files"), list):
        return None
    batch["files"] = [
        _normalize_batch_file_entry(entry, position)
        for position, entry in enumerate(batch.get("files") or [], 1)
    ]
    task_id = str(task.get("task_id") or batch.get("batch_id") or "").strip()
    if not task_id:
        return None
    task["task_id"] = task_id
    # Queue events/checkpoints use batch_id as the operation identity. Keep both
    # representations canonical so a stale/corrupt mismatch cannot route worker
    # updates into the wrong task or make them disappear.
    batch["batch_id"] = task_id
    task.setdefault("created_at", _now_iso())
    task.setdefault("updated_at", task["created_at"])
    task.setdefault("started_at", "")
    task.setdefault("finished_at", "")
    status = str(task.get("status") or "queued")
    if status not in KNOWN_TASK_STATUSES:
        # A syntactically valid JSON file with an unknown status must not leave a
        # task permanently invisible to the dispatcher. Treat it as interrupted:
        # the worker will still validate per-file checkpoints/outputs before redo.
        status = "interrupted"
    task["status"] = status
    runtime = task.setdefault("runtime", {})
    if not isinstance(runtime, dict):
        runtime = {}
        task["runtime"] = runtime
    runtime["current_file"] = str(runtime.get("current_file") or "")
    try:
        runtime["file_index"] = max(0, int(runtime.get("file_index") or 0))
    except (TypeError, ValueError, OverflowError):
        runtime["file_index"] = 0
    try:
        runtime["files_total"] = max(0, int(runtime.get("files_total") or len(batch.get("files") or [])))
    except (TypeError, ValueError, OverflowError):
        runtime["files_total"] = len(batch.get("files") or [])
    try:
        runtime["progress"] = max(0, min(100, int(runtime.get("progress") or 0)))
    except (TypeError, ValueError, OverflowError):
        runtime["progress"] = 0
    runtime["status_text"] = str(runtime.get("status_text") or "")
    return task


def load_task_queue_state(path: Path | None = None) -> dict:
    path = path or get_task_queue_state_path()
    if not path.exists():
        return create_task_queue_state()
    try:
        import json

        with open(path, "r", encoding="utf-8-sig") as file:
            data = json.load(file)
        if not isinstance(data, dict):
            raise ValueError("task queue root must be a JSON object")
        if int(data.get("schema_version", 0)) != TASK_QUEUE_SCHEMA_VERSION:
            return _backup_and_reset_queue(path, label="unsupported")
        raw_tasks = data.get("tasks", [])
        if not isinstance(raw_tasks, list):
            raise ValueError("task queue tasks must be a JSON array")
        tasks = []
        seen_task_ids: set[str] = set()
        dropped_invalid_task = False
        for raw in raw_tasks:
            normalized = _normalize_task(raw)
            if normalized:
                task_id = str(normalized.get("task_id") or "")
                if task_id in seen_task_ids:
                    # Duplicate IDs make find_task()/Treeview/worker checkpoint routing
                    # ambiguous. Preserve the duplicate as a visible terminal item with
                    # a repaired identity; the user can explicitly retry or remove it.
                    suffix = 2
                    repaired_id = f"{task_id}__recovered_{suffix}"
                    while repaired_id in seen_task_ids:
                        suffix += 1
                        repaired_id = f"{task_id}__recovered_{suffix}"
                    normalized["task_id"] = repaired_id
                    normalized.setdefault("batch_state", {})["batch_id"] = repaired_id
                    normalized["status"] = "failed"
                    runtime = normalized.setdefault("runtime", {})
                    runtime["status_text"] = "Конфликт ID восстановлен — повторите задачу вручную"
                    runtime["current_file"] = "Конфликт ID очереди"
                    task_id = repaired_id
                seen_task_ids.add(task_id)
                tasks.append(normalized)
            else:
                dropped_invalid_task = True
        data["tasks"] = tasks
        known_ids = data.get("known_task_batch_ids") or []
        if isinstance(known_ids, str):
            known_ids = [known_ids] if known_ids.strip() else []
        elif not isinstance(known_ids, list):
            known_ids = []
        # Accept the short-lived Pass 11 development key if a local test build wrote
        # it, but canonicalize to one field from now on.
        legacy_known_ids = data.pop("migrated_legacy_batch_ids", []) or []
        if isinstance(legacy_known_ids, str):
            legacy_known_ids = [legacy_known_ids] if legacy_known_ids.strip() else []
        if isinstance(legacy_known_ids, list):
            known_ids = [*known_ids, *legacy_known_ids]
        data["known_task_batch_ids"] = list(dict.fromkeys(
            str(value).strip() for value in known_ids if str(value).strip()
        ))
        data.setdefault("created_at", _now_iso())
        data.setdefault("updated_at", data["created_at"])
        if dropped_invalid_task:
            # Syntactically valid JSON can still contain an unrecoverable task
            # object. Never drop it without evidence: preserve the exact source,
            # then atomically publish the valid subset so the same bad entry does
            # not disappear silently on every startup.
            try:
                backup_corrupt_file(path, label="partial")
                atomic_write_json(path, data)
            except OSError:
                # Keep the usable in-memory subset. The untouched primary (or the
                # backup if copying succeeded) remains available for diagnosis.
                pass
        return data
    except (OSError, ValueError, TypeError, OverflowError, UnicodeError):
        return _backup_and_reset_queue(path, label="corrupt")


def save_task_queue_state(state: dict, path: Path | None = None) -> None:
    state["schema_version"] = TASK_QUEUE_SCHEMA_VERSION
    state["updated_at"] = _now_iso()
    atomic_write_json(path or get_task_queue_state_path(), state)


def recover_interrupted_tasks(state: dict) -> bool:
    """Convert tasks left running by process exit into resumable FIFO entries."""
    changed = False
    for task in state.get("tasks") or []:
        status = str(task.get("status") or "queued")
        batch = task.get("batch_state") or {}
        if status == "running":
            if str(batch.get("status") or "") == "cancellation_requested":
                # The old process (and therefore its FFmpeg/TTS children) no longer
                # exists after restart. Preserve the user's explicit Cancel intent
                # instead of silently turning it into an automatic resume.
                task["status"] = "cancelled"
                task["finished_at"] = task.get("finished_at") or _now_iso()
                task["updated_at"] = _now_iso()
                batch["status"] = "cancelled"
                runtime = task.setdefault("runtime", {})
                runtime["status_text"] = "Отменено"
                changed = True
                continue
            entries = list(batch.get("files") or [])
            completed_on_disk = (
                str(batch.get("status") or "") == "completed"
                and bool(entries)
                and all(
                    isinstance(entry, dict)
                    and str(entry.get("status") or "") == "succeeded"
                    and bool(str(entry.get("output_path") or "").strip())
                    and os.path.isfile(str(entry.get("output_path") or ""))
                    for entry in entries
                )
            )
            task["status"] = "completed" if completed_on_disk else "interrupted"
            task["updated_at"] = _now_iso()
            runtime = task.setdefault("runtime", {})
            if completed_on_disk:
                task["finished_at"] = task.get("finished_at") or _now_iso()
                runtime["progress"] = 100
                runtime["status_text"] = "Готово"
            else:
                runtime["status_text"] = "Прервано — будет продолжено"
            changed = True
        if task.get("status") != "completed" and (status == "running" or str(batch.get("status") or "") in {
            "running",
            "cancellation_requested",
            "worker_failed",
        }):
            batch["status"] = "interrupted"
            changed = True
    return changed


def next_runnable_task(state: dict) -> dict | None:
    for task in state.get("tasks") or []:
        if str(task.get("status") or "queued") in RUNNABLE_TASK_STATUSES:
            return task
    return None


def find_task(state: dict, task_id: str) -> dict | None:
    target = str(task_id or "")
    for task in state.get("tasks") or []:
        if str(task.get("task_id") or "") == target:
            return task
    return None
