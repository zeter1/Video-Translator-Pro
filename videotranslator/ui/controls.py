"""Method owner for App: _threadsafe_progress, cancel, _set_busy, _set_progress, _apply_keep_state, _refresh_voice_list, _on_language_changed, _get_audio_settings, _on_vol_changed, _on_voice_volume_changed, _on_highpass_changed, _on_lowpass_changed, _on_loudness_changed, _clear_log."""

from __future__ import annotations

import tkinter as tk
from videotranslator.config import DEFAULT_TARGET_LANGUAGE, TOTAL_MAX_SPEECH_SPEED, get_voice_options, normalize_audio_settings


class UIControlsMixin:
    """Behavior-preserving methods extracted from the legacy monolith."""

    def _threadsafe_progress(self, value: int, status: str = ""):
        self._safe_after(0, self._set_progress, value, status)


    def cancel(self):
        if self._processing:
            self._cancel_event.set()
            self._log("⏹ Отмена...")
            self.btn_cancel.config(state="disabled")


    def _set_busy(self, busy: bool):
        self._processing = busy
        self.btn_start.config(
            state="disabled" if busy else "normal",
            text="⏳ Обработка..." if busy else "▶  НАЧАТЬ ОБРАБОТКУ",
        )
        self.btn_cancel.config(state="normal" if busy else "disabled")
        for widget in (
            self.btn_add,
            self.btn_remove,
            self.btn_clear,
            self.chk_keep,
            self.chk_review,
            self.scale_vol,
            self.scale_voice_volume,
            self.scale_highpass,
            self.scale_lowpass,
            self.scale_loudness,
            self.chk_denoise,
        ):
            try:
                widget.config(state="disabled" if busy else "normal")
            except Exception:
                pass
        for combo in (self.combo_language, self.combo_voice, self.combo_model, self.combo_speed):
            try:
                combo.config(state="disabled" if busy else "readonly")
            except Exception:
                pass
        self._apply_keep_state(save=False)


    def _set_progress(self, value: int, status: str = ""):
        self.var_prog.set(max(0, min(100, int(value or 0))))
        if status:
            self.lbl_status.config(text=status)


    def _apply_keep_state(self, save: bool = False):
        keep_original = bool(self.var_keep.get())
        if self._processing:
            self.scale_vol.state(["disabled"])
            self.lbl_vol.config(state="disabled")
        else:
            self.scale_vol.state(["!disabled"] if keep_original else ["disabled"])
            self.lbl_vol.config(state="normal" if keep_original else "disabled")
        if save:
            self.save_settings()


    def _refresh_voice_list(self, language_label: str | None = None, preferred_voice_code: str | None = None):
        language_label = language_label or self.combo_language.get() or DEFAULT_TARGET_LANGUAGE
        voices = get_voice_options(language_label)
        labels = list(voices.keys())
        self.combo_voice.config(values=labels)
        selected = labels[0] if labels else ""
        if preferred_voice_code:
            for label, code in voices.items():
                if code == preferred_voice_code:
                    selected = label
                    break
        if selected:
            self.combo_voice.set(selected)


    def _on_language_changed(self, _event=None):
        self._refresh_voice_list(self.combo_language.get())
        self.save_settings()


    def _get_audio_settings(self) -> dict:
        return normalize_audio_settings({
            "voice_volume_pct": int(float(self.var_voice_volume.get())),
            "highpass_hz": int(float(self.var_highpass.get())),
            "lowpass_hz": int(float(self.var_lowpass.get())),
            "noise_reduction": bool(self.var_denoise.get()),
            "master_loudness_i": int(float(self.var_loudness.get())),
            "speech_speed_limit": float(self.combo_speed.get() or TOTAL_MAX_SPEECH_SPEED),
        })


    def _on_vol_changed(self, value):
        volume = int(float(value))
        self.lbl_vol.config(text=f"Громкость оригинала: {volume}%")
        self.save_settings()


    def _on_voice_volume_changed(self, value):
        volume = int(float(value))
        self.lbl_voice_vol.config(text=f"Громкость новой озвучки: {volume}%")
        self.save_settings()


    def _on_highpass_changed(self, value):
        hz = int(float(value))
        label = "выкл" if hz <= 0 else f"{hz} Гц"
        self.lbl_highpass.config(text=f"Убрать гул ниже: {label}")
        self.save_settings()


    def _on_lowpass_changed(self, value):
        hz = int(float(value))
        label = "выкл" if hz < 2500 else f"{hz} Гц"
        self.lbl_lowpass.config(text=f"Смягчить верх: {label}")
        self.save_settings()


    def _on_loudness_changed(self, value):
        loudness = int(float(value))
        self.lbl_loudness.config(text=f"Итоговая громкость: {loudness} LUFS")
        self.save_settings()


    def _clear_log(self):
        self.txt_log.config(state="normal")
        self.txt_log.delete("1.0", tk.END)
        self.txt_log.config(state="disabled")
