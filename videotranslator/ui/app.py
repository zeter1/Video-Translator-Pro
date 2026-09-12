"""Tkinter App composition root and shared UI logging helpers."""

from __future__ import annotations

from pathlib import Path
import threading
import tkinter as tk
from videotranslator.core.diagnostics import classify_exception, compact_exception, redact_diagnostic_text
from videotranslator.diagnostics.problem_logger import ProblemLogger
from videotranslator.ui.layout import UILayoutMixin
from videotranslator.ui.network import UINetworkMixin
from videotranslator.ui.queue import UIQueueMixin
from videotranslator.ui.batch import UIBatchMixin
from videotranslator.ui.review import UIReviewMixin
from videotranslator.ui.controls import UIControlsMixin
from videotranslator.ui.settings import UISettingsMixin


class App(UILayoutMixin, UINetworkMixin, UIQueueMixin, UIBatchMixin, UIReviewMixin, UIControlsMixin, UISettingsMixin):
    """Tkinter application composition root."""

    def __init__(self, root: tk.Tk):
        self.root = root
        self.root.title("Видео Переводчик PRO — автоязык → выбранный язык")
        self.root.geometry("760x930")
        self.root.resizable(True, True)

        self.config_file = Path.home() / ".video_translator_max_quality_sync.json"
        self.video_files = []
        self._cancel_event = threading.Event()
        self._processing = False
        self._closing = False
        self._suspend_save = True
        self.file_logger = None
        self.last_log_path = ""
        self.problem_logger = None
        self.problem_logger_error = ""
        self._resume_batch_state = None
        self._active_translator = None
        self._vpn_notice_window = None
        self._vpn_notice_status = None
        self._vpn_notice_detail = None
        try:
            self.problem_logger = ProblemLogger()
        except Exception as exc:
            self.problem_logger_error = f"{type(exc).__name__}: {compact_exception(exc)}"

        self._build_ui()
        self.load_settings()
        self._suspend_save = False
        self._apply_keep_state(save=False)
        self.root.protocol("WM_DELETE_WINDOW", self._on_close)
        self._safe_after(250, self._offer_batch_recovery)


    def _safe_after(self, delay_ms: int, func, *args):
        if self._closing:
            return False
        try:
            self.root.after(delay_ms, func, *args)
            return True
        except Exception as exc:
            self._problem(
                "ui_callback_schedule_failed",
                level="warning",
                message="Не удалось передать обновление в GUI-поток.",
                callback=getattr(func, "__name__", type(func).__name__),
                exception={
                    "type": type(exc).__name__,
                    "category": classify_exception(exc),
                    "message": compact_exception(exc, max_len=1000),
                },
            )
            return False


    def _problem(self, event: str, level: str = "info", message: str = "", **details):
        try:
            if self.problem_logger:
                self.problem_logger.event(event, level=level, message=message, **details)
        except Exception:
            pass


    def _log(self, msg: str):
        # Сначала пишем в .txt из любого потока, затем безопасно обновляем окно Tkinter.
        try:
            if self.file_logger:
                self.file_logger.write(msg)
        except Exception:
            pass

        text = str(msg)
        lowered = text.lower()
        error_markers = ("❌", "traceback", "критическая ошибка", "неожиданная ошибка")
        warning_markers = ("⚠", "timeout", "timed out", "ошибка", "stderr")
        # Эти сообщения уже получают отдельное структурированное событие с полным контекстом.
        # Не создаём второй runtime_* дубликат той же ошибки.
        structured_owner_markers = (
            "перевод сегмента",
            "edgetts сегмент",
            "google tts сегмент",
            "tts-сегмент",
            "сетевой шторм",
            "кодер ",
            "проверка результата (",
            "mp4 не прошёл проверку:",
            "❌ ошибка:",
            "неожиданная ошибка файла",
            "файл не обработан",
            "traceback (most recent call last)",
        )
        already_structured = any(marker in lowered for marker in structured_owner_markers)
        if not already_structured and any(marker in lowered for marker in error_markers):
            self._problem(
                "runtime_error_log",
                level="error",
                message=redact_diagnostic_text(text),
            )
        elif not already_structured and any(marker in lowered for marker in warning_markers):
            self._problem(
                "runtime_warning_log",
                level="warning",
                message=redact_diagnostic_text(text),
            )

        def do_log():
            if self._closing:
                return
            self.txt_log.config(state="normal")
            self.txt_log.insert(tk.END, msg + "\n")
            self.txt_log.see(tk.END)
            self.txt_log.config(state="disabled")

        self._safe_after(0, do_log)
