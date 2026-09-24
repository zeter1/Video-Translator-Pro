"""Owner module for: FileLogger."""

from __future__ import annotations

from datetime import datetime
import os
from pathlib import Path
import threading
import time
import traceback
from uuid import uuid4

from videotranslator.config import NORMAL_LOG_MAX_FILES, NORMAL_LOG_RETENTION_DAYS
from videotranslator.core.diagnostics import redact_diagnostic_text, safe_log_filename
from videotranslator.core.paths import get_logs_dir, get_program_dir


def cleanup_old_text_logs(directory: Path) -> None:
    """Bound only application-owned normal logs; never touch unrelated .txt files."""
    try:
        directory = Path(directory)
        candidates = [
            path for path in directory.glob("video_translator*.txt")
            if path.is_file()
        ]
        cutoff = time.time() - max(1, int(NORMAL_LOG_RETENTION_DAYS)) * 24 * 60 * 60
        for path in list(candidates):
            try:
                if path.stat().st_mtime < cutoff:
                    path.unlink()
                    candidates.remove(path)
            except OSError:
                continue

        candidates.sort(key=lambda path: path.stat().st_mtime if path.exists() else 0, reverse=True)
        for path in candidates[max(1, int(NORMAL_LOG_MAX_FILES)) :]:
            try:
                path.unlink()
            except OSError:
                pass
    except OSError:
        # Log retention is maintenance only; failure must not block translation.
        pass


class FileLogger:
    """Дублирует журнал программы в .txt файл, чтобы его можно было отправить на разбор."""

    def __init__(self, prefix: str = "video_translator"):
        self.lock = threading.Lock()
        logs_dir = get_logs_dir()
        cleanup_old_text_logs(logs_dir)
        # Microseconds + PID avoid accidental append into another session that starts
        # within the same second (including a second application instance).
        stamp = datetime.now().strftime("%Y-%m-%d_%H-%M-%S_%f")
        self.path = logs_dir / f"{safe_log_filename(prefix)}_{stamp}_{os.getpid()}_{uuid4().hex}.txt"
        self.file = open(self.path, "x", encoding="utf-8", buffering=1)
        self.write("=" * 72)
        self.write("ЛОГ ПРОГРАММЫ: Видео Переводчик PRO")
        self.write(f"Дата запуска: {datetime.now():%Y-%m-%d %H:%M:%S}")
        self.write(f"Папка программы: {get_program_dir()}")
        self.write(f"Файл лога: {self.path}")
        self.write("=" * 72)

    def write(self, msg: str):
        if not hasattr(self, "file") or self.file.closed:
            return
        # The plain-text log is user-shareable just like the structured problem log.
        # Redact before touching disk, not only later when a structured event is made.
        text = redact_diagnostic_text(msg, max_len=30000)
        stamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        with self.lock:
            for line in text.splitlines() or [""]:
                self.file.write(f"[{stamp}] {line}\n")
            self.file.flush()

    def write_exception(self, title: str, exc: Exception | None = None):
        self.write("\n" + "!" * 72)
        self.write(title)
        if exc is not None:
            self.write(f"{type(exc).__name__}: {exc}")
        self.write(traceback.format_exc())
        self.write("!" * 72)

    def close(self):
        try:
            self.write("\nЛог закрыт.")
            self.file.close()
        except Exception:
            pass
