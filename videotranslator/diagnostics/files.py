"""Owner module for: problem_log_relative_path, find_previous_problem_summary, archive_legacy_problem_logs, write_problem_logs_guide, _is_owned_problem_session...."""

from __future__ import annotations

from pathlib import Path
import importlib.metadata as importlib_metadata
import os
import re
import shutil
import time
from videotranslator.config import PROBLEM_LOG_MAX_FILES, PROBLEM_LOG_RETENTION_DAYS, PROBLEM_MANIFEST_SCHEMA_VERSION
from videotranslator.core.io import atomic_write_text, read_json_file


def problem_log_relative_path(root: Path, path: Path) -> str:
    """Возвращает переносимый путь внутри папки журналов."""
    try:
        return path.resolve().relative_to(root.resolve()).as_posix()
    except (OSError, ValueError):
        return path.name


def find_previous_problem_summary(directory: Path) -> tuple[dict, Path | None]:
    """Находит сводку прошлой сессии в новой или старой структуре."""
    index = read_json_file(directory / "latest_session.json")
    files = index.get("files") if isinstance(index.get("files"), dict) else {}
    relative_summary = files.get("summary") or index.get("summary") or ""
    if relative_summary:
        try:
            candidate = (directory / str(relative_summary)).resolve()
            if candidate.is_relative_to(directory.resolve()):
                summary = read_json_file(candidate)
                if summary:
                    return summary, candidate
        except (OSError, ValueError):
            pass

    legacy_path = directory / "latest_problem_summary.json"
    legacy = read_json_file(legacy_path)
    if legacy:
        return legacy, legacy_path
    return {}, None


def archive_legacy_problem_logs(directory: Path) -> dict[str, Path]:
    """Переносит только известные старые журналы в legacy_flat_logs без удаления."""
    candidates = []
    for pattern in ("problems_*.jsonl", "session_summary_*.json", "latest_problem_summary.json"):
        candidates.extend(path for path in directory.glob(pattern) if path.is_file())
    if not candidates:
        return {}

    legacy_dir = directory / "legacy_flat_logs"
    legacy_dir.mkdir(parents=True, exist_ok=True)
    moved = {}
    for source in sorted(set(candidates), key=lambda path: path.name.lower()):
        target = legacy_dir / source.name
        if target.exists():
            stem, suffix = source.stem, source.suffix
            counter = 2
            while target.exists():
                target = legacy_dir / f"{stem}_{counter}{suffix}"
                counter += 1
        try:
            source_key = str(source.resolve())
            os.replace(source, target)
            moved[source_key] = target
        except OSError:
            continue
    return moved


def write_problem_logs_guide(directory: Path):
    """Создаёт короткую инструкцию, с которой Codex начинает анализ журналов."""
    guide = """# Логи проблем — инструкция для Codex

Каждый запуск программы хранится в отдельной папке `session_<ID>`.

## С чего начинать анализ

1. Откройте `latest_session.json` — это указатель на последнюю сессию.
2. В папке сессии сначала прочитайте `report.md` и `summary.json`.
3. Затем изучите `problems.jsonl`: там только предупреждения и ошибки.
4. При необходимости объедините `events.jsonl` и `problems.jsonl` по `sequence` — это полная сохранённая хронология.
5. `manifest.json` описывает назначение файлов, версию схемы и границы проверки.

## Правила интерпретации

- `summary.json` и `report.md` обновляются во время работы; статус `running` не является ошибкой.
- Сессия `interrupted` означает, что прошлый процесс не записал штатное завершение.
- Поле `codex_analysis` содержит подсказки, а не неподтверждённые факты.
- Одинаковые проблемы объединены по `signature`; `occurrences_total` показывает точное число повторов.
- В JSONL сохраняются первые 3 примера и редкие контрольные повторы; точная политика описана в `manifest.json`, а `stored_samples` показывает число строк на диске.
- Карточки `issue_cards` содержат доказательства, вероятную причину, место поиска, исправление и проверку.
- Секреты в URL и полях токенов маскируются. Большие списки сокращаются с сохранением общего количества.
- Нельзя считать GUI, сеть, реальный FFmpeg или итоговое видео проверенными только по статическому анализу кода.

Старые журналы прежнего плоского формата сохранены в `legacy_flat_logs`.
"""
    atomic_write_text(directory / "README_FOR_CODEX.md", guide)


def _is_owned_problem_session(path: Path) -> bool:
    if not path.is_dir() or not re.fullmatch(r"session_[0-9]{8}_[0-9]{6}_[0-9]{6}_[0-9]+", path.name):
        return False
    manifest = read_json_file(path / "manifest.json")
    return (
        int(manifest.get("schema_version") or 0) == PROBLEM_MANIFEST_SCHEMA_VERSION
        and manifest.get("artifact_type") == "video_translator_problem_session"
    )


def cleanup_old_problem_logs(directory: Path):
    """Ограничивает хранение только помеченных папок сессий и старых журналов приложения."""
    try:
        cutoff = time.time() - PROBLEM_LOG_RETENTION_DAYS * 24 * 60 * 60
        sessions = []
        for path in directory.glob("session_*"):
            try:
                if _is_owned_problem_session(path):
                    sessions.append(path)
            except OSError:
                continue
        sessions.sort(key=lambda path: path.stat().st_mtime)
        expired = {path for path in sessions if path.stat().st_mtime < cutoff}
        expired.update(sessions[:-PROBLEM_LOG_MAX_FILES])
        for path in sorted(expired, key=lambda item: item.name):
            try:
                shutil.rmtree(path)
            except OSError:
                pass

        legacy_dir = directory / "legacy_flat_logs"
        if legacy_dir.is_dir():
            for pattern in ("problems_*.jsonl", "session_summary_*.json"):
                for path in legacy_dir.glob(pattern):
                    try:
                        if path.is_file() and path.stat().st_mtime < cutoff:
                            path.unlink()
                    except OSError:
                        pass
    except OSError:
        pass


def installed_package_versions() -> dict:
    versions = {}
    for package in ("openai-whisper", "torch", "edge-tts", "gTTS", "deep-translator", "moviepy", "numpy", "requests"):
        try:
            versions[package] = importlib_metadata.version(package)
        except importlib_metadata.PackageNotFoundError:
            versions[package] = "not-installed"
        except Exception as exc:
            versions[package] = f"unknown ({type(exc).__name__})"
    return versions
