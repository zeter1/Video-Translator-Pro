"""Owner module for: FileLogger."""

from __future__ import annotations

from datetime import datetime
import threading
import traceback
from videotranslator.core.diagnostics import safe_log_filename
from videotranslator.core.paths import get_logs_dir, get_program_dir


class FileLogger:
    """Дублирует журнал программы в .txt файл, чтобы его можно было отправить на разбор."""

    def __init__(self, prefix: str = "video_translator"):
        self.lock = threading.Lock()
        self.path = get_logs_dir() / f"{safe_log_filename(prefix)}_{datetime.now():%Y-%m-%d_%H-%M-%S}.txt"
        self.file = open(self.path, "a", encoding="utf-8", buffering=1)
        self.write("=" * 72)
        self.write("ЛОГ ПРОГРАММЫ: Видео Переводчик PRO")
        self.write(f"Дата запуска: {datetime.now():%Y-%m-%d %H:%M:%S}")
        self.write(f"Папка программы: {get_program_dir()}")
        self.write(f"Файл лога: {self.path}")
        self.write("=" * 72)

    def write(self, msg: str):
        if not hasattr(self, "file") or self.file.closed:
            return
        text = str(msg)
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
