"""Method owner for App: save_settings, load_settings, _on_close."""

from __future__ import annotations

from tkinter import messagebox
import time
from videotranslator.config import DEFAULT_TARGET_LANGUAGE, MODELS_MAP, SETTINGS_LANGUAGE_VERSION, SPEECH_SPEED_LIMITS, TARGET_LANGUAGES, TOTAL_MAX_SPEECH_SPEED, get_target_language, get_target_language_by_code, get_voice_options
from videotranslator.core.diagnostics import classify_exception, compact_exception
from videotranslator.core.io import backup_corrupt_file
from videotranslator.recovery.batch import save_batch_recovery_state
from videotranslator.core.settings_io import atomic_write_json, read_json


class UISettingsMixin:
    """Behavior-preserving methods extracted from the legacy monolith."""

    def _cancel_scheduled_settings_save(self):
        callback_id = getattr(self, "_settings_save_after_id", None)
        self._settings_save_after_id = None
        if not callback_id:
            return
        try:
            self.root.after_cancel(callback_id)
        except Exception:
            pass

    def _schedule_settings_save(self, delay_ms: int = 300):
        """Coalesce noisy slider callbacks into one durable settings commit."""
        if self._suspend_save or getattr(self, "_closing", False):
            return
        self._cancel_scheduled_settings_save()

        def persist():
            self._settings_save_after_id = None
            self.save_settings()

        try:
            self._settings_save_after_id = self.root.after(max(0, int(delay_ms)), persist)
        except Exception:
            # If Tk scheduling is unavailable during an unusual lifecycle edge,
            # prefer one synchronous save over silently losing the user setting.
            self._settings_save_after_id = None
            self.save_settings()

    def save_settings(self, *_):
        if self._suspend_save:
            return
        self._cancel_scheduled_settings_save()
        try:
            language_label = self.combo_language.get() or DEFAULT_TARGET_LANGUAGE
            target_info = get_target_language(language_label)
            voices = get_voice_options(language_label)
            data = {
                "language_default_version": SETTINGS_LANGUAGE_VERSION,
                "language_code": target_info.get("code", "en"),
                "language_label": language_label,
                "voice_code": voices.get(self.combo_voice.get(), ""),
                "model_idx": max(0, self.combo_model.current()),
                "review": bool(self.var_review.get()),
                "keep": bool(self.var_keep.get()),
                "volume": int(self.var_volume.get()),
                "voice_volume": int(self.var_voice_volume.get()),
                "highpass": int(self.var_highpass.get()),
                "lowpass": int(self.var_lowpass.get()),
                "denoise": bool(self.var_denoise.get()),
                "loudness": int(self.var_loudness.get()),
                "speed_limit": float(self.combo_speed.get() or TOTAL_MAX_SPEECH_SPEED),
                "hybrid_local_first": bool(getattr(self, "var_hybrid_local_first", None).get()) if hasattr(self, "var_hybrid_local_first") else True,
                "auto_install_argos_pairs": bool(getattr(self, "var_auto_install_argos", None).get()) if hasattr(self, "var_auto_install_argos") else True,
                "local_piper_fallback": bool(getattr(self, "var_local_piper_fallback", None).get()) if hasattr(self, "var_local_piper_fallback") else True,
                "auto_update_local_ai": bool(getattr(self, "var_auto_update_local_ai", None).get()) if hasattr(self, "var_auto_update_local_ai") else True,
                "preferred_piper_voice": self._preferred_piper_voice_id() if hasattr(self, "_preferred_piper_voice_id") else "piper-dmitri-ru",
                "output_dir": str(self.var_output_dir.get() or "") if hasattr(self, "var_output_dir") else "",
            }
            atomic_write_json(self.config_file, data)
        except Exception as exc:
            self._problem(
                "settings_save_failed",
                level="warning",
                message="Не удалось атомарно сохранить настройки программы.",
                config_path=str(self.config_file),
                exception={
                    "type": type(exc).__name__,
                    "category": classify_exception(exc),
                    "message": compact_exception(exc, max_len=1000),
                },
            )


    def load_settings(self):
        if not self.config_file.exists():
            return
        try:
            data = read_json(self.config_file)
            if not isinstance(data, dict):
                raise ValueError("Корень файла настроек должен быть JSON-объектом")

            if int(data.get("language_default_version", 0)) >= SETTINGS_LANGUAGE_VERSION:
                saved_label = data.get("language_label")
                language_label = saved_label if saved_label in TARGET_LANGUAGES else get_target_language_by_code(data.get("language_code"))
            else:
                language_label = DEFAULT_TARGET_LANGUAGE
            self.combo_language.set(language_label)
            self._refresh_voice_list(language_label, preferred_voice_code=data.get("voice_code"))

            model_idx = max(0, min(int(data.get("model_idx", 1)), len(MODELS_MAP) - 1))
            volume = max(0, min(100, int(data.get("volume", 15))))
            voice_volume = max(50, min(150, int(data.get("voice_volume", 100))))
            highpass = max(0, min(220, int(data.get("highpass", 55))))
            lowpass = max(0, min(16000, int(data.get("lowpass", 0))))
            loudness = max(-24, min(-12, int(data.get("loudness", -16))))
            try:
                requested_speed = float(data.get("speed_limit", TOTAL_MAX_SPEECH_SPEED))
            except (TypeError, ValueError):
                requested_speed = TOTAL_MAX_SPEECH_SPEED
            speed_limit = min(SPEECH_SPEED_LIMITS, key=lambda value: abs(value - requested_speed))

            self.combo_model.current(model_idx)
            self.var_review.set(bool(data.get("review", True)))
            self.var_keep.set(bool(data.get("keep", False)))
            self.var_volume.set(volume)
            self.var_voice_volume.set(voice_volume)
            self.var_highpass.set(highpass)
            self.var_lowpass.set(lowpass)
            self.var_denoise.set(bool(data.get("denoise", False)))
            self.var_loudness.set(loudness)
            self.combo_speed.set(f"{speed_limit:.2f}")
            if hasattr(self, "var_hybrid_local_first"):
                self.var_hybrid_local_first.set(bool(data.get("hybrid_local_first", True)))
            if hasattr(self, "var_auto_install_argos"):
                self.var_auto_install_argos.set(bool(data.get("auto_install_argos_pairs", True)))
            if hasattr(self, "var_local_piper_fallback"):
                self.var_local_piper_fallback.set(bool(data.get("local_piper_fallback", True)))
            if hasattr(self, "var_auto_update_local_ai"):
                self.var_auto_update_local_ai.set(bool(data.get("auto_update_local_ai", True)))
            if hasattr(self, "_set_preferred_piper_voice_id"):
                self._set_preferred_piper_voice_id(str(data.get("preferred_piper_voice") or "piper-dmitri-ru"))
            if hasattr(self, "var_output_dir"):
                self.var_output_dir.set(str(data.get("output_dir") or ""))
            self.lbl_vol.config(text=f"Громкость оригинала: {volume}%")
            self.lbl_voice_vol.config(text=f"Громкость новой озвучки: {voice_volume}%")
            self.lbl_highpass.config(text=f"Убрать гул ниже: {'выкл' if highpass <= 0 else str(highpass) + ' Гц'}")
            self.lbl_lowpass.config(text=f"Смягчить верх: {'выкл' if lowpass < 2500 else str(lowpass) + ' Гц'}")
            self.lbl_loudness.config(text=f"Итоговая громкость: {loudness} LUFS")
        except Exception as exc:
            backup_path = ""
            backup_error = ""
            if isinstance(exc, (OSError, ValueError, TypeError, UnicodeError)):
                try:
                    backup = backup_corrupt_file(self.config_file, label="corrupt")
                    backup_path = str(backup or "")
                except OSError as backup_exc:
                    backup_error = compact_exception(backup_exc, max_len=700)
            self._problem(
                "settings_load_failed",
                level="warning",
                message=(
                    "Файл настроек не прочитан; программа продолжит с безопасными значениями интерфейса. "
                    "Исходный файл сохранён отдельной копией, если это было возможно."
                ),
                config_path=str(self.config_file),
                corrupt_backup_path=backup_path,
                corrupt_backup_error=backup_error,
                exception={
                    "type": type(exc).__name__,
                    "category": classify_exception(exc),
                    "message": compact_exception(exc, max_len=1000),
                },
            )


    def _finalize_close(self):
        """Close owned resources only after the worker had a chance to observe cancel."""
        self._cancel_scheduled_settings_save()
        callback_id = getattr(self, "_task_dispatch_after_id", None)
        self._task_dispatch_after_id = None
        if callback_id:
            try:
                self.root.after_cancel(callback_id)
            except Exception:
                pass
        self._close_vpn_notice()
        try:
            if self.file_logger:
                self.file_logger.close()
        except Exception:
            pass
        try:
            if self.problem_logger:
                self.problem_logger.close()
        except Exception:
            pass
        try:
            self.root.destroy()
        except Exception:
            pass

    def _wait_for_worker_before_close(self):
        thread = getattr(self, "_worker_thread", None)
        worker_alive = bool(thread is not None and thread.is_alive())
        if not worker_alive:
            self._finalize_close()
            return

        deadline = getattr(self, "_close_deadline_monotonic", None)
        if deadline is not None and time.monotonic() >= deadline:
            self._problem(
                "app_close_worker_timeout",
                level="warning",
                message=(
                    "Рабочий поток не завершился за короткое окно мягкой отмены; "
                    "GUI закрывается, recovery-state уже сохранён."
                ),
                worker_name=getattr(thread, "name", "worker"),
            )
            self._finalize_close()
            return

        try:
            # _safe_after intentionally rejects callbacks after _closing=True.
            # This private shutdown poll is the one callback allowed to survive.
            self.root.after(100, self._wait_for_worker_before_close)
        except Exception:
            self._finalize_close()

    def _on_close(self):
        if self._closing:
            return

        if bool(getattr(self, "_model_action_running", False)):
            self._problem(
                "app_close_blocked_model_operation",
                level="warning",
                message=(
                    "Закрытие отложено: менеджер локальных моделей ещё выполняет проверку, "
                    "установку, обновление или удаление."
                ),
            )
            messagebox.showinfo(
                "Операция с локальными моделями",
                "Дождитесь завершения текущей операции с локальными моделями/голосами.\n\n"
                "Принудительное закрытие во время установки или обновления может оставить "
                "Python-пакет или языковую модель в неполном состоянии.",
            )
            return

        thread = getattr(self, "_worker_thread", None)
        worker_alive = bool(thread is not None and thread.is_alive())
        processing = bool(self._processing)
        if processing:
            if not messagebox.askyesno("Выход", "Идёт обработка. Прервать и выйти?"):
                return
            self._cancel_event.set()
            if self._resume_batch_state:
                self._resume_batch_state["status"] = "cancellation_requested"
                try:
                    save_batch_recovery_state(self._resume_batch_state)
                except (OSError, TypeError, ValueError) as exc:
                    self._problem(
                        "batch_recovery_save_failed",
                        level="warning",
                        message="Не удалось сохранить отметку об отмене пакетной обработки.",
                        exception={
                            "type": type(exc).__name__,
                            "category": classify_exception(exc),
                            "message": compact_exception(exc),
                        },
                    )
                if hasattr(self, "_persist_task_queue_safely"):
                    # In queue mode _resume_batch_state is the same batch object owned
                    # by the active task. Persist the cancellation request before GUI
                    # teardown so restart recovery does not depend only on the legacy
                    # single-batch compatibility file.
                    self._task_queue_dirty = True
                    self._persist_task_queue_safely()
        elif worker_alive:
            # A final UI callback may already have cleared the busy flag while the
            # worker is still unwinding/closing its logger. Do not destroy Tk-owned
            # resources under that live thread.
            self._cancel_event.set()

        if bool(getattr(self, "_task_queue_dirty", False)) and hasattr(self, "_persist_task_queue_safely"):
            self._persist_task_queue_safely()

        self._problem(
            "app_close_requested",
            level="warning" if processing or worker_alive else "info",
            message="Пользователь закрыл программу.",
            processing=processing,
            worker_alive=worker_alive,
        )
        self.save_settings()
        self._closing = True

        if processing or worker_alive:
            # FFmpeg cancellation is polled every ~250 ms. Give the batch worker a
            # bounded chance to kill/collect the child process and persist its final
            # cancellation state before GUI-owned loggers disappear. A daemon worker
            # is retained only as last-resort process-exit protection for blocking
            # third-party/network calls that Python cannot safely interrupt.
            self._close_deadline_monotonic = time.monotonic() + 5.0
            self._wait_for_worker_before_close()
            return

        self._finalize_close()
