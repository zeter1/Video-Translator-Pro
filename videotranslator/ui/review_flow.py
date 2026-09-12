"""Method owner for App: review_translations, _open_translation_review_window."""

from __future__ import annotations

import os
import threading
import time
from videotranslator.core.diagnostics import compact_exception
from videotranslator.core.timefmt import fmt_time

class UIReviewFlowMixin:
    """Focused behavior-preserving mixin extracted for AI-local navigation."""

    def review_translations(self, segments: list, input_path: str, source_lang: str, target_info: dict) -> list | None:
        if self._cancel_event.is_set() or self._closing:
            return None

        done = threading.Event()
        result = {"segments": None, "error": None}

        def show_editor():
            try:
                if self._cancel_event.is_set() or self._closing:
                    done.set()
                    return
                self._open_translation_review_window(segments, input_path, source_lang, target_info, done, result)
                self._problem(
                    "manual_review_window_opened",
                    message="Окно ручной проверки создано и показано пользователю.",
                    input_path=os.path.abspath(input_path),
                    segments=len(segments),
                )
            except Exception as exc:
                result["error"] = exc
                try:
                    if self.problem_logger:
                        self.problem_logger.write_exception(
                            "manual_review_window_failed",
                            exc,
                            message="Не удалось создать окно ручной проверки; ожидание остановлено.",
                            input_path=os.path.abspath(input_path),
                            segments=len(segments),
                        )
                except Exception:
                    pass
                done.set()

        if not self._safe_after(0, show_editor):
            result["error"] = RuntimeError("Не удалось запланировать открытие окна ручной проверки.")
            done.set()
        wait_started_at = time.monotonic()
        next_wait_log_at = wait_started_at + 300
        while not done.wait(0.1):
            if self._closing:
                self._cancel_event.set()
                return None
            if time.monotonic() >= next_wait_log_at:
                elapsed = int(time.monotonic() - wait_started_at)
                self._log(
                    f"   ⏸️ Всё ещё жду ручную проверку ({fmt_time(elapsed)}). "
                    "Нажмите «Применить и продолжить» в окне проверки."
                )
                self._problem(
                    "manual_review_still_waiting",
                    level="warning",
                    message="Обработка продолжает ждать действия пользователя в окне проверки.",
                    input_path=os.path.abspath(input_path),
                    elapsed_sec=elapsed,
                )
                next_wait_log_at = time.monotonic() + 300
        if result["error"] is not None:
            raise RuntimeError(
                f"Не удалось открыть окно ручной проверки: {compact_exception(result['error'])}"
            ) from result["error"]
        return result["segments"]
