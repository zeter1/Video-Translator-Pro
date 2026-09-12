"""Method owner for App: _restore_batch_settings, _offer_batch_recovery, start, _worker."""

from __future__ import annotations

from videotranslator.core.compat_bridge import call_legacy_override

from pathlib import Path
from tkinter import filedialog
from tkinter import messagebox
import os
import threading
from videotranslator.config import DEFAULT_TARGET_LANGUAGE, MODELS_MAP, get_target_language, get_voice_options
from videotranslator.core.diagnostics import classify_exception, compact_exception, diagnostic_json_value, safe_log_filename
from videotranslator.diagnostics.file_logger import FileLogger
from videotranslator.media.process import find_ffmpeg as _legacy_find_ffmpeg, find_ffprobe as _legacy_find_ffprobe
from videotranslator.media.video import output_has_video_and_audio as _legacy_output_has_video_and_audio
from videotranslator.pipeline.translator import VideoTranslator as _legacy_VideoTranslator
from videotranslator.recovery.batch import batch_recovery_pending_entries, create_batch_recovery_state, get_batch_recovery_state_path as _legacy_get_batch_recovery_state_path, save_batch_recovery_state


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

class UIBatchStartMixin:
    """Focused behavior-preserving mixin extracted for AI-local navigation."""

    def start(self):
        if not self.video_files:
            messagebox.showwarning("Нет файлов", "Добавьте хотя бы одно видео.")
            return

        files = list(self.video_files)
        recovery_state = self._resume_batch_state
        if recovery_state:
            recoverable_paths = [
                str((entry.get("input") or {}).get("path") or "")
                for entry in batch_recovery_pending_entries(recovery_state)
                if os.path.isfile(str((entry.get("input") or {}).get("path") or ""))
            ]
            current_norm = [os.path.normcase(os.path.abspath(path)) for path in files]
            recovery_norm = [os.path.normcase(os.path.abspath(path)) for path in recoverable_paths]
            if current_norm != recovery_norm:
                recovery_state = None

        out_folder = str((recovery_state or {}).get("output_dir") or "")
        if not out_folder or not os.path.isdir(out_folder):
            out_folder = filedialog.askdirectory(title="Папка для сохранения")
        if not out_folder:
            return

        try:
            if self.file_logger:
                self.file_logger.close()
        except Exception:
            pass

        try:
            if recovery_state:
                prefix = "video_translator_batch_resume"
            elif len(files) > 1:
                prefix = "video_translator_batch"
            else:
                prefix = f"video_translator_{safe_log_filename(Path(files[0]).stem)}"
            self.file_logger = FileLogger(prefix=prefix)
            self.last_log_path = str(self.file_logger.path)
        except Exception as exc:
            self.file_logger = None
            self.last_log_path = ""
            messagebox.showwarning(
                "Логи не созданы",
                f"Не удалось создать папку/файл логов рядом с программой:\n{exc}",
            )

        if self.problem_logger_error:
            messagebox.showwarning(
                "Логи проблем не созданы",
                "Не удалось создать подробный журнал в папке «Логи проблем».\n"
                f"Причина: {self.problem_logger_error}",
            )
            self.problem_logger_error = ""

        self._cancel_event.clear()
        self._processing = True
        self._set_busy(True)

        language_label = self.combo_language.get() or DEFAULT_TARGET_LANGUAGE
        target_info = get_target_language(language_label)
        voices = get_voice_options(language_label)
        voice = voices.get(self.combo_voice.get()) or next(iter(voices.values()))
        model = MODELS_MAP[self.combo_model.get()]
        keep = bool(self.var_keep.get())
        volume = int(self.var_volume.get())
        review = bool(self.var_review.get())
        audio_settings = self._get_audio_settings()
        batch_settings = {
            "language_label": language_label,
            "voice": voice,
            "model": model,
            "target_language": target_info.get("code"),
            "review_before_tts": review,
            "keep_original_audio": keep,
            "original_volume_pct": volume,
            "audio": audio_settings,
        }
        if recovery_state and recovery_state.get("settings") != diagnostic_json_value(batch_settings):
            self._log("   ⚠️ Настройки изменены: оставшиеся файлы будут обработаны заново; старые результаты сохранены.")
            recovery_state = None
        if recovery_state:
            batch_state = recovery_state
            batch_state["status"] = "running"
            batch_state["output_dir"] = os.path.abspath(out_folder)
            batch_state["settings"] = diagnostic_json_value(batch_settings)
            resumed = True
        else:
            batch_state = create_batch_recovery_state(files, out_folder, batch_settings)
            resumed = False
        self._resume_batch_state = batch_state
        try:
            save_batch_recovery_state(batch_state)
        except OSError as exc:
            self._problem(
                "batch_recovery_save_failed",
                level="warning",
                message="Не удалось сохранить состояние пакетной обработки; сама обработка будет продолжена.",
                exception={
                    "type": type(exc).__name__,
                    "category": classify_exception(exc),
                    "message": compact_exception(exc),
                },
            )

        state_files = [
            str((entry.get("input") or {}).get("path") or "")
            for entry in batch_state.get("files", [])
        ]
        file_states = [
            {
                "file_index": int(entry.get("file_index") or index),
                "input_path": str((entry.get("input") or {}).get("path") or ""),
                "output_path": str(entry.get("output_path") or ""),
                "status": str(entry.get("status") or "pending"),
            }
            for index, entry in enumerate(batch_state.get("files", []), 1)
        ]
        previously_successful = sum(
            str(entry.get("status") or "") == "succeeded"
            for entry in batch_state.get("files", [])
        )
        self._problem(
            "batch_started",
            message="Пользователь продолжил пакетную обработку." if resumed else "Пользователь запустил пакетную обработку.",
            batch_id=batch_state.get("batch_id") or "",
            resumed=resumed,
            previously_successful=previously_successful,
            files_count=len(state_files),
            files=state_files,
            file_states=file_states,
            output_dir=os.path.abspath(out_folder),
            normal_log=self.last_log_path,
            recovery_state_path=str(get_batch_recovery_state_path()),
            settings=batch_settings,
        )

        thread = threading.Thread(
            target=self._worker,
            args=(batch_state, out_folder, voice, model, keep, volume, target_info, review, audio_settings),
            daemon=True,
        )
        thread.start()
