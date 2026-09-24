"""Owner module for: get_program_dir, get_logs_dir, get_problem_logs_dir, get_translated_texts_dir, get_translation_checkpoints_dir...."""

from __future__ import annotations

from pathlib import Path
import os
import sys


def get_program_dir() -> Path:
    """Папка, где лежит .py/.exe программы. Рядом с ней создаётся папка logs."""
    try:
        if getattr(sys, "frozen", False):
            return Path(sys.executable).resolve().parent
        return Path(__file__).resolve().parents[2]
    except Exception:
        return Path.cwd()


def get_runtime_bin_dir() -> Path:
    """Directory containing binaries bundled by PyInstaller at runtime."""
    try:
        bundle_dir = getattr(sys, "_MEIPASS", None)
        if getattr(sys, "frozen", False) and bundle_dir:
            return Path(bundle_dir).resolve()
    except Exception:
        pass
    return get_program_dir()


def configure_runtime_path() -> None:
    """Make bundled FFmpeg/ffprobe discoverable by existing subprocess code."""
    if not getattr(sys, "frozen", False):
        return
    current = os.environ.get("PATH", "")
    prefixes = [str(get_runtime_bin_dir()), str(get_program_dir())]
    os.environ["PATH"] = os.pathsep.join([*prefixes, current])


def get_logs_dir() -> Path:
    """Возвращает папку logs рядом с программой и создаёт её при необходимости."""
    logs_dir = get_program_dir() / "logs"
    logs_dir.mkdir(parents=True, exist_ok=True)
    return logs_dir


def get_problem_logs_dir() -> Path:
    """Папка с подробными JSONL-журналами, рассчитанными на разбор в Codex."""
    logs_dir = get_program_dir() / "Логи проблем"
    logs_dir.mkdir(parents=True, exist_ok=True)
    return logs_dir


def get_translated_texts_dir() -> Path:
    """Папка с .txt-файлами готовых переводов рядом с программой."""
    texts_dir = get_program_dir() / "translated_texts"
    texts_dir.mkdir(parents=True, exist_ok=True)
    return texts_dir


def get_translation_checkpoints_dir() -> Path:
    """Контрольные точки перевода, чтобы сетевой сбой не обнулял долгую работу."""
    checkpoint_dir = get_translated_texts_dir() / "Контрольные точки"
    checkpoint_dir.mkdir(parents=True, exist_ok=True)
    return checkpoint_dir


def get_tts_cache_dir() -> Path:
    """Постоянный кэш озвучки, переживающий перезапуск программы."""
    cache_dir = get_program_dir() / "tts_cache"
    cache_dir.mkdir(parents=True, exist_ok=True)
    return cache_dir
