"""Owner module for: get_program_dir, get_logs_dir, get_problem_logs_dir, get_translated_texts_dir, get_translation_checkpoints_dir...."""

from __future__ import annotations

import os
from pathlib import Path
import sys
import tempfile


def get_program_dir() -> Path:
    """Папка, где лежит .py/.exe программы. Рядом с ней создаётся папка logs."""
    try:
        if getattr(sys, "frozen", False):
            return Path(sys.executable).resolve().parent
        return Path(__file__).resolve().parents[2]
    except Exception:
        return Path.cwd()



def get_bundled_resource_dir() -> Path:
    """Root for read-only files bundled with the application.

    In source runs this is the project root. In PyInstaller builds ``__file__``
    points inside the bundle/extraction tree, which is the correct owner for
    --add-binary resources (unlike sys.executable, which identifies the launcher).
    """
    try:
        return Path(__file__).resolve().parents[2]
    except Exception:
        return get_program_dir()


def _fallback_runtime_root() -> Path:
    """Writable per-user fallback for mutable runtime state.

    Portable/source runs keep using directories next to the application whenever
    that location is actually writable.  Packaged copies installed under a
    protected directory (for example Program Files) fall back here instead of
    failing during logger/cache/checkpoint initialization.
    """
    configured = str(os.environ.get("VIDEO_TRANSLATOR_RUNTIME_DIR") or "").strip()
    if configured:
        root = Path(configured).expanduser()
    elif os.name == "nt":
        local_app_data = str(os.environ.get("LOCALAPPDATA") or "").strip()
        base = Path(local_app_data).expanduser() if local_app_data else Path.home() / "AppData" / "Local"
        root = base / "VideoTranslatorPRO"
    else:
        root = Path.home() / ".video_translator"
    root.mkdir(parents=True, exist_ok=True)
    return root


def _directory_is_writable(path: Path) -> bool:
    """Prove write access instead of trusting mkdir/os.access on Windows ACLs."""
    probe_path = None
    try:
        path.mkdir(parents=True, exist_ok=True)
        fd, probe_name = tempfile.mkstemp(prefix=".vt-write-probe-", suffix=".tmp", dir=str(path))
        os.close(fd)
        probe_path = Path(probe_name)
        probe_path.unlink()
        return True
    except OSError:
        if probe_path is not None:
            try:
                probe_path.unlink(missing_ok=True)
            except OSError:
                pass
        return False


def _get_runtime_subdir(name: str) -> Path:
    """Resolve one mutable runtime directory with a portable-first policy."""
    configured = str(os.environ.get("VIDEO_TRANSLATOR_RUNTIME_DIR") or "").strip()
    if not configured:
        primary = get_program_dir() / name
        if _directory_is_writable(primary):
            return primary

    fallback = _fallback_runtime_root() / name
    fallback.mkdir(parents=True, exist_ok=True)
    return fallback

def get_logs_dir() -> Path:
    """Папка логов: рядом с программой, либо writable user fallback."""
    return _get_runtime_subdir("logs")


def get_problem_logs_dir() -> Path:
    """Папка с подробными JSONL-журналами, рассчитанными на разбор в Codex."""
    return _get_runtime_subdir("Логи проблем")


def get_translated_texts_dir() -> Path:
    """Папка готовых переводов/checkpoints с writable fallback."""
    return _get_runtime_subdir("translated_texts")


def get_translation_checkpoints_dir() -> Path:
    """Контрольные точки перевода, чтобы сетевой сбой не обнулял долгую работу."""
    checkpoint_dir = get_translated_texts_dir() / "Контрольные точки"
    checkpoint_dir.mkdir(parents=True, exist_ok=True)
    return checkpoint_dir


def get_translation_glossary_path() -> Path:
    """Writable glossary path while preserving an existing portable glossary.

    Older builds looked for ``translation_glossary.json`` next to the program.  Keep
    reading that file when it exists, but new/protected installations fall back to
    the per-user runtime root instead of failing under Program Files ACLs.
    """
    primary = get_program_dir() / "translation_glossary.json"
    if primary.exists():
        return primary

    configured = str(os.environ.get("VIDEO_TRANSLATOR_RUNTIME_DIR") or "").strip()
    if not configured and _directory_is_writable(get_program_dir()):
        return primary

    fallback = _fallback_runtime_root() / "translation_glossary.json"
    fallback.parent.mkdir(parents=True, exist_ok=True)
    return fallback


def get_tts_cache_dir() -> Path:
    """Постоянный кэш озвучки, переживающий перезапуск программы."""
    return _get_runtime_subdir("tts_cache")
