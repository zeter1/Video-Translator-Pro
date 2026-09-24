"""Method owner for App: _restore_batch_settings, _offer_batch_recovery, start, _worker."""

from __future__ import annotations

from videotranslator.core.compat_bridge import call_legacy_override

from tkinter import messagebox
import os
import tkinter as tk
from videotranslator.config import MODELS_MAP, TARGET_LANGUAGES, get_target_language_by_code, normalize_audio_settings
from videotranslator.core.diagnostics import classify_exception, compact_exception
from videotranslator.media.process import find_ffmpeg as _legacy_find_ffmpeg, find_ffprobe as _legacy_find_ffprobe
from videotranslator.media.video import output_has_video_and_audio as _legacy_output_has_video_and_audio
from videotranslator.pipeline.translator import VideoTranslator as _legacy_VideoTranslator
from videotranslator.recovery.batch import batch_recovery_pending_entries, get_batch_recovery_state_path as _legacy_get_batch_recovery_state_path, input_file_signature, load_batch_recovery_state, reconstruct_batch_recovery_state_from_problem_log, save_batch_recovery_state


def VideoTranslator(*args, **kwargs):
    return call_legacy_override('VideoTranslator', _legacy_VideoTranslator, *args, **kwargs)

def get_batch_recovery_state_path(*args, **kwargs):
    return call_legacy_override('get_batch_recovery_state_path', _legacy_get_batch_recovery_state_path, *args, **kwargs)

def find_ffmpeg(*args, **kwargs):
    return call_legacy_override('find_ffmpeg', _legacy_find_ffmpeg, *args, **kwargs)

def find_ffprobe(*args, **kwargs):
    return call_legacy_override('find_ffprobe', _legacy_find_ffprobe, *args, **kwargs)

def output_has_video_and_audio(*args, **kwargs):
    return call_legacy_override('output_has_video_and_audio', _legacy_output_has_video_and_audio, *args, **kwargs)

class UIBatchRecoveryMixin:
    """Focused behavior-preserving mixin extracted for AI-local navigation."""

    def _restore_batch_settings(self, settings: dict):
        if not isinstance(settings, dict):
            settings = {}
        language_label = str(settings.get("language_label") or "")
        if language_label not in TARGET_LANGUAGES:
            language_label = get_target_language_by_code(settings.get("target_language"))
        self.combo_language.set(language_label)
        self._refresh_voice_list(language_label, preferred_voice_code=settings.get("voice"))

        model_code = str(settings.get("model") or "")
        for index, code in enumerate(MODELS_MAP.values()):
            if code == model_code:
                self.combo_model.current(index)
                break

        self.var_keep.set(bool(settings.get("keep_original_audio")))
        try:
            original_volume = int(settings.get("original_volume_pct", 15))
        except (TypeError, ValueError, OverflowError):
            original_volume = 15
        self.var_volume.set(max(0, min(100, original_volume)))
        self.var_review.set(bool(settings.get("review_before_tts")))
        audio = normalize_audio_settings(settings.get("audio") or {})
        self.var_voice_volume.set(audio["voice_volume_pct"])
        self.var_highpass.set(audio["highpass_hz"])
        self.var_lowpass.set(audio["lowpass_hz"])
        self.var_denoise.set(audio["noise_reduction"])
        self.var_loudness.set(audio["master_loudness_i"])
        self.combo_speed.set(f"{audio['speech_speed_limit']:.2f}")
        self._apply_keep_state(save=False)

    def _offer_batch_recovery(self):
        if self._closing or self._processing:
            return
        # The new durable Tasks queue owns restart recovery. Avoid offering the
        # same legacy batch twice after it has already been migrated.
        if hasattr(self, "_task_queue_state") and (self._task_queue_state.get("tasks") or []):
            return
        state = load_batch_recovery_state()
        if not state and self.problem_logger:
            previous = self.problem_logger._summary.get("previous_session") or {}
            state = reconstruct_batch_recovery_state_from_problem_log(previous.get("problem_log") or "")
            if state:
                try:
                    save_batch_recovery_state(state)
                    self._problem(
                        "batch_recovery_reconstructed_from_problem_log",
                        level="warning",
                        message="Пакет старой версии восстановлен по сохранённому JSONL-журналу.",
                        batch_id=state.get("batch_id") or "",
                        source_problem_log=state.get("source_problem_log") or "",
                        files_count=len(state.get("files") or []),
                        successful=sum(
                            str(item.get("status") or "") == "succeeded"
                            for item in state.get("files", [])
                        ),
                    )
                except (OSError, TypeError, ValueError) as exc:
                    self._problem(
                        "batch_recovery_save_failed",
                        level="warning",
                        message="Пакет найден в старом журнале, но его новую контрольную точку сохранить не удалось.",
                        exception={
                            "type": type(exc).__name__,
                            "category": classify_exception(exc),
                            "message": compact_exception(exc),
                        },
                    )
        if not state or str(state.get("status") or "") == "completed":
            return

        # The durable queue keeps historical batch IDs even after the user removes
        # a task. The legacy single-batch recovery dialog must respect that tombstone
        # or it can resurrect an intentionally deleted/cleared queue item.
        if hasattr(self, "_task_queue_state"):
            batch_id = str(state.get("batch_id") or "")
            known_ids = {
                str(value or "")
                for value in (self._task_queue_state.get("known_task_batch_ids") or [])
                if str(value or "")
            }
            if batch_id and batch_id in known_ids:
                return

        pending = batch_recovery_pending_entries(state)
        if not pending:
            return
        available = []
        missing = []
        changed = []
        for entry in pending:
            input_info = entry.get("input")
            if not isinstance(input_info, dict):
                input_info = {}
            path = str(input_info.get("path") or "")
            if not path or not os.path.isfile(path):
                missing.append(path or "<путь не записан>")
                continue
            current = input_file_signature(path)
            signature_changed = False
            if input_info.get("size") is not None:
                try:
                    signature_changed = (
                        int(input_info.get("size")) != int(current.get("size", -1))
                        or int(input_info.get("modified_ns", -1)) != int(current.get("modified_ns", -2))
                    )
                except (TypeError, ValueError, OverflowError):
                    # Corrupt legacy metadata is evidence that the old identity cannot
                    # be trusted. Treat it as changed instead of crashing the startup
                    # recovery dialog.
                    signature_changed = True
            if signature_changed:
                changed.append(path)
            available.append(path)

        if not available:
            self._problem(
                "batch_recovery_unavailable",
                level="warning",
                message="Сохранённую пакетную задачу нельзя продолжить: входные файлы недоступны.",
                batch_id=state.get("batch_id") or "",
                missing_files=missing,
            )
            return

        succeeded = sum(str(item.get("status") or "") == "succeeded" for item in state.get("files", []))
        text = (
            "Найдена незавершённая пакетная обработка.\n\n"
            f"Уже готово: {succeeded}\n"
            f"Осталось доступных файлов: {len(available)}\n"
            f"Папка результатов: {state.get('output_dir')}\n"
        )
        if missing:
            text += f"Недоступно файлов: {len(missing)}\n"
        if changed:
            text += f"Изменено после прошлого запуска: {len(changed)}\n"
        text += "\nВосстановить список и продолжить с незавершённого файла?"
        accepted = messagebox.askyesno("Продолжить пакет", text)
        self._problem(
            "batch_recovery_offer_answered",
            level="warning" if missing or changed else "info",
            message="Пользователь выбрал, продолжать ли незавершённую пакетную задачу.",
            batch_id=state.get("batch_id") or "",
            accepted=accepted,
            available_files=len(available),
            missing_files=missing,
            changed_files=changed,
        )
        if not accepted:
            return

        self._resume_batch_state = state
        self.video_files = available
        self.listbox.delete(0, tk.END)
        for path in available:
            self.listbox.insert(tk.END, os.path.basename(path))
        self._update_count()
        self._restore_batch_settings(state.get("settings") or {})
        self._set_progress(0, "Пакет восстановлен — нажмите «Начать обработку»")
