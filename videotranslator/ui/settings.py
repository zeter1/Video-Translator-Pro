"""Method owner for App: save_settings, load_settings, _on_close."""

from __future__ import annotations

import json
from tkinter import messagebox
from videotranslator.config import DEFAULT_TARGET_LANGUAGE, MODELS_MAP, SETTINGS_LANGUAGE_VERSION, SPEECH_SPEED_LIMITS, TARGET_LANGUAGES, TOTAL_MAX_SPEECH_SPEED, get_target_language, get_target_language_by_code, get_voice_options
from videotranslator.core.diagnostics import classify_exception, compact_exception
from videotranslator.recovery.batch import save_batch_recovery_state


class UISettingsMixin:
    """Behavior-preserving methods extracted from the legacy monolith."""

    def save_settings(self, *_):
        if self._suspend_save:
            return
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
            }
            with open(self.config_file, "w", encoding="utf-8") as file:
                json.dump(data, file, ensure_ascii=False, indent=2)
        except Exception:
            pass


    def load_settings(self):
        if not self.config_file.exists():
            return
        try:
            with open(self.config_file, encoding="utf-8") as file:
                data = json.load(file)

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
            self.lbl_vol.config(text=f"Громкость оригинала: {volume}%")
            self.lbl_voice_vol.config(text=f"Громкость новой озвучки: {voice_volume}%")
            self.lbl_highpass.config(text=f"Убрать гул ниже: {'выкл' if highpass <= 0 else str(highpass) + ' Гц'}")
            self.lbl_lowpass.config(text=f"Смягчить верх: {'выкл' if lowpass < 2500 else str(lowpass) + ' Гц'}")
            self.lbl_loudness.config(text=f"Итоговая громкость: {loudness} LUFS")
        except Exception:
            pass


    def _on_close(self):
        if self._processing:
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
        self._problem(
            "app_close_requested",
            level="warning" if self._processing else "info",
            message="Пользователь закрыл программу.",
            processing=bool(self._processing),
        )
        self.save_settings()
        self._closing = True
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
