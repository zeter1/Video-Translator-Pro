"""Method owner for App: _threadsafe_progress, cancel, _set_busy, _set_progress, _apply_keep_state, _refresh_voice_list, _on_language_changed, _get_audio_settings, _on_vol_changed, _on_voice_volume_changed, _on_highpass_changed, _on_lowpass_changed, _on_loudness_changed, _clear_log."""

from __future__ import annotations

import os
import tkinter as tk
from tkinter import filedialog
from videotranslator.config import DEFAULT_TARGET_LANGUAGE, TOTAL_MAX_SPEECH_SPEED, get_voice_options, normalize_audio_settings


class UIControlsMixin:
    """Behavior-preserving methods extracted from the legacy monolith."""

    def _threadsafe_progress(self, value: int, status: str = ""):
        task_id = str(getattr(self, "_active_task_id", "") or "")
        self._safe_after(0, self._set_progress_for_task, task_id, value, status)
        if hasattr(self, "_update_active_task_progress"):
            self._safe_after(0, self._update_active_task_progress, value, status, task_id)


    def _set_progress_for_task(self, task_id: str, value: int, status: str = ""):
        """Ignore late progress callbacks after ownership moved to another queue task."""
        task_id = str(task_id or "")
        if task_id and task_id != str(getattr(self, "_active_task_id", "") or ""):
            return
        self._set_progress(value, status)


    def cancel(self):
        if self._processing:
            self._cancel_event.set()
            self._log("⏹ Отмена...")
            if hasattr(self, "_request_active_task_cancel"):
                self._request_active_task_cancel()
            self.btn_cancel.config(state="disabled")


    def _choose_output_folder(self):
        current = str(self.var_output_dir.get() or "").strip() if hasattr(self, "var_output_dir") else ""
        dialog_options = {"title": "Папка для готовых переведённых видео"}
        if current and os.path.isdir(current):
            dialog_options["initialdir"] = current
        selected = filedialog.askdirectory(**dialog_options)
        if not selected:
            return ""
        selected = os.path.abspath(selected)
        self.var_output_dir.set(selected)
        self.save_settings()
        return selected


    def _set_busy(self, busy: bool):
        self._processing = busy
        queue_mode = hasattr(self, "_task_queue_state")
        if queue_mode:
            # Current worker owns a snapshot of all settings, so the form remains
            # editable and the user can enqueue the next task while translation runs.
            self.btn_start.config(
                state="normal",
                text="➕ ДОБАВИТЬ В ОЧЕРЕДЬ" if busy else "▶  НАЧАТЬ ОБРАБОТКУ",
            )
            self.btn_cancel.config(state="normal" if busy else "disabled")
            if hasattr(self, "_refresh_model_button_state"):
                self._refresh_model_button_state()
            self._apply_keep_state(save=False)
            return

        self.btn_start.config(
            state="disabled" if busy else "normal",
            text="⏳ Обработка..." if busy else "▶  НАЧАТЬ ОБРАБОТКУ",
        )
        self.btn_cancel.config(state="normal" if busy else "disabled")
        if hasattr(self, "_refresh_model_button_state"):
            self._refresh_model_button_state()
        for widget in (
            self.btn_add,
            self.btn_remove,
            self.btn_clear,
            getattr(self, "btn_output_dir", None),
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
        if self._processing and not hasattr(self, "_task_queue_state"):
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
            "hybrid_local_first": bool(self.var_hybrid_local_first.get()) if hasattr(self, "var_hybrid_local_first") else True,
            "auto_install_argos_pairs": bool(self.var_auto_install_argos.get()) if hasattr(self, "var_auto_install_argos") else True,
            "local_piper_fallback": bool(self.var_local_piper_fallback.get()) if hasattr(self, "var_local_piper_fallback") else True,
            "preferred_piper_voice": self._preferred_piper_voice_id() if hasattr(self, "_preferred_piper_voice_id") else "piper-dmitri-ru",
        })


    def _on_vol_changed(self, value):
        volume = int(float(value))
        self.lbl_vol.config(text=f"Громкость оригинала: {volume}%")
        self._schedule_settings_save()


    def _on_voice_volume_changed(self, value):
        volume = int(float(value))
        self.lbl_voice_vol.config(text=f"Громкость новой озвучки: {volume}%")
        self._schedule_settings_save()


    def _on_highpass_changed(self, value):
        hz = int(float(value))
        label = "выкл" if hz <= 0 else f"{hz} Гц"
        self.lbl_highpass.config(text=f"Убрать гул ниже: {label}")
        self._schedule_settings_save()


    def _on_lowpass_changed(self, value):
        hz = int(float(value))
        label = "выкл" if hz < 2500 else f"{hz} Гц"
        self.lbl_lowpass.config(text=f"Смягчить верх: {label}")
        self._schedule_settings_save()


    def _on_loudness_changed(self, value):
        loudness = int(float(value))
        self.lbl_loudness.config(text=f"Итоговая громкость: {loudness} LUFS")
        self._schedule_settings_save()


    def _clear_log(self):
        self.txt_log.config(state="normal")
        self.txt_log.delete("1.0", tk.END)
        self.txt_log.config(state="disabled")
