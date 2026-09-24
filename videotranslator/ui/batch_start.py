"""Batch validation/start contract and queued-task launcher."""

from __future__ import annotations

from videotranslator.core.compat_bridge import call_legacy_override

from pathlib import Path
from tkinter import filedialog, messagebox
import os
import threading
from videotranslator.config import (
    DEFAULT_TARGET_LANGUAGE,
    MODELS_MAP,
    get_target_language,
    get_target_language_by_code,
    get_voice_options,
    normalize_audio_settings,
)
from videotranslator.core.diagnostics import classify_exception, compact_exception, diagnostic_json_value, safe_log_filename
from videotranslator.diagnostics.file_logger import FileLogger
from videotranslator.media.process import find_ffmpeg as _legacy_find_ffmpeg, find_ffprobe as _legacy_find_ffprobe
from videotranslator.media.video import output_has_video_and_audio as _legacy_output_has_video_and_audio
from videotranslator.pipeline.translator import VideoTranslator as _legacy_VideoTranslator
from videotranslator.recovery.batch import (
    batch_recovery_pending_entries,
    create_batch_recovery_state,
    get_batch_recovery_state_path as _legacy_get_batch_recovery_state_path,
    save_batch_recovery_state,
)


def VideoTranslator(*args, **kwargs):
    return call_legacy_override("VideoTranslator", _legacy_VideoTranslator, *args, **kwargs)


def get_batch_recovery_state_path(*args, **kwargs):
    return call_legacy_override("get_batch_recovery_state_path", _legacy_get_batch_recovery_state_path, *args, **kwargs)


def find_ffmpeg(*args, **kwargs):
    return call_legacy_override("find_ffmpeg", _legacy_find_ffmpeg, *args, **kwargs)


def find_ffprobe(*args, **kwargs):
    return call_legacy_override("find_ffprobe", _legacy_find_ffprobe, *args, **kwargs)


def output_has_video_and_audio(*args, **kwargs):
    return call_legacy_override("output_has_video_and_audio", _legacy_output_has_video_and_audio, *args, **kwargs)


class UIBatchStartMixin:
    """Prepare immutable job snapshots and launch one active worker at a time."""

    def _snapshot_batch_settings(self) -> tuple[dict, dict]:
        language_label = self.combo_language.get() or DEFAULT_TARGET_LANGUAGE
        target_info = get_target_language(language_label)
        voices = get_voice_options(language_label)
        voice = voices.get(self.combo_voice.get()) or next(iter(voices.values()))
        model = MODELS_MAP[self.combo_model.get()]
        keep = bool(self.var_keep.get())
        volume = int(self.var_volume.get())
        review = bool(self.var_review.get())
        audio_settings = self._get_audio_settings()
        settings = {
            "language_label": language_label,
            "voice": voice,
            "model": model,
            "target_language": target_info.get("code"),
            "review_before_tts": review,
            "keep_original_audio": keep,
            "original_volume_pct": volume,
            "audio": audio_settings,
        }
        runtime = {
            "voice": voice,
            "model": model,
            "keep": keep,
            "volume": volume,
            "target_info": target_info,
            "review": review,
            "audio_settings": audio_settings,
        }
        return settings, runtime

    def start(self):
        queue_mode = hasattr(self, "_task_queue_state")
        # Legacy direct-start harnesses keep the old re-entrancy guarantee. The real
        # application uses queue mode, where a running worker must not block enqueue.
        if getattr(self, "_processing", False) and not queue_mode:
            return
        if getattr(self, "_model_action_running", False):
            messagebox.showinfo(
                "Обновление моделей",
                "Сейчас проверяются или обновляются локальные модели/голоса. "
                "Добавление перевода станет доступно после завершения этой операции.",
            )
            return
        if not self.video_files:
            messagebox.showwarning("Нет файлов", "Добавьте хотя бы одно видео.")
            return

        files = list(self.video_files)
        # _resume_batch_state is the currently running task while queue mode is active;
        # never reuse that state for a newly enqueued job.
        recovery_state = self._resume_batch_state if not getattr(self, "_processing", False) else None
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

        recovery_out_folder = str((recovery_state or {}).get("output_dir") or "").strip()
        selected_out_folder = (
            str(self.var_output_dir.get() or "").strip()
            if hasattr(self, "var_output_dir")
            else ""
        )
        out_folder = recovery_out_folder if recovery_state else selected_out_folder
        if not out_folder or not os.path.isdir(out_folder):
            initialdir = selected_out_folder if os.path.isdir(selected_out_folder) else None
            dialog_options = {"title": "Папка для готовых переведённых видео"}
            if initialdir:
                dialog_options["initialdir"] = initialdir
            out_folder = filedialog.askdirectory(**dialog_options)
        if not out_folder:
            return
        out_folder = os.path.abspath(out_folder)
        if hasattr(self, "var_output_dir"):
            self.var_output_dir.set(out_folder)
            if hasattr(self, "save_settings"):
                self.save_settings()

        batch_settings, runtime = self._snapshot_batch_settings()
        if recovery_state and recovery_state.get("settings") != diagnostic_json_value(batch_settings):
            self._log("   ⚠️ Настройки изменены: будет создана новая задача; старые результаты сохранены.")
            recovery_state = None

        if recovery_state:
            batch_state = recovery_state
            batch_state["status"] = "queued"
            batch_state["output_dir"] = os.path.abspath(out_folder)
            batch_state["settings"] = diagnostic_json_value(batch_settings)
            resumed = True
        else:
            batch_state = create_batch_recovery_state(files, out_folder, batch_settings)
            batch_state["status"] = "queued" if queue_mode else "running"
            resumed = False

        if queue_mode:
            # Durable enqueue is the commit point. Worker/log ownership begins only when
            # this task reaches the head of the FIFO queue.
            queued_task = self._enqueue_batch_task(batch_state)
            if not queued_task:
                return
            if recovery_state is batch_state:
                self._resume_batch_state = None
            return

        # Compatibility path used by focused tests/legacy embeddings without Tasks UI.
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
        self._open_batch_logger(batch_state, resumed=resumed)
        self._emit_batch_started(batch_state, resumed=resumed)
        thread = threading.Thread(
            target=self._worker,
            args=(
                batch_state,
                out_folder,
                runtime["voice"],
                runtime["model"],
                runtime["keep"],
                runtime["volume"],
                runtime["target_info"],
                runtime["review"],
                runtime["audio_settings"],
            ),
            daemon=True,
            name="video-translation-worker",
        )
        self._start_batch_worker(thread, batch_state)

    def _open_batch_logger(self, batch_state: dict, resumed: bool) -> None:
        files = [
            str((entry.get("input") or {}).get("path") or "")
            for entry in batch_state.get("files") or []
        ]
        try:
            if self.file_logger:
                self.file_logger.close()
        except Exception:
            pass
        try:
            if resumed:
                prefix = "video_translator_task_resume"
            elif len(files) > 1:
                prefix = "video_translator_task"
            elif files:
                prefix = f"video_translator_{safe_log_filename(Path(files[0]).stem)}"
            else:
                prefix = "video_translator_task"
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

    def _emit_batch_started(self, batch_state: dict, resumed: bool) -> None:
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
            task_id=str(getattr(self, "_active_task_id", "") or ""),
            resumed=resumed,
            previously_successful=previously_successful,
            files_count=len(state_files),
            files=state_files,
            file_states=file_states,
            output_dir=os.path.abspath(str(batch_state.get("output_dir") or "")),
            normal_log=self.last_log_path,
            recovery_state_path=str(get_batch_recovery_state_path()),
            settings=batch_state.get("settings") or {},
        )

    def _launch_queued_task(self, task: dict) -> bool:
        """Launch the task currently owned by the FIFO dispatcher."""
        batch_state = task.get("batch_state") or {}
        out_folder = str(batch_state.get("output_dir") or "")
        try:
            os.makedirs(out_folder, exist_ok=True)
        except OSError as exc:
            batch_state["status"] = "start_failed"
            self._problem(
                "task_output_directory_unavailable",
                level="error",
                message="Папка результатов задачи недоступна.",
                task_id=task.get("task_id") or "",
                output_dir=out_folder,
                exception={"type": type(exc).__name__, "message": compact_exception(exc)},
            )
            self._task_start_failed(batch_state)
            self._set_busy(False)
            return False

        try:
            settings = dict(batch_state.get("settings") or {})
            language_label = str(settings.get("language_label") or "")
            target_info = (
                get_target_language(language_label)
                if language_label
                else get_target_language_by_code(settings.get("target_language"))
            )
            voice = str(settings.get("voice") or "")
            model = str(settings.get("model") or "")
            keep = bool(settings.get("keep_original_audio"))
            volume = int(settings.get("original_volume_pct", 15))
            review = bool(settings.get("review_before_tts"))
            audio_settings = normalize_audio_settings(settings.get("audio") or {})
            if not target_info or not voice or not model:
                raise ValueError("saved task settings are incomplete")
        except (KeyError, TypeError, ValueError, OverflowError) as exc:
            batch_state["status"] = "start_failed"
            self._problem(
                "task_saved_settings_invalid",
                level="error",
                message="Сохранённые настройки задачи повреждены или больше не поддерживаются.",
                task_id=task.get("task_id") or "",
                exception={"type": type(exc).__name__, "message": compact_exception(exc)},
            )
            self._task_start_failed(batch_state)
            self._set_busy(False)
            try:
                messagebox.showerror(
                    "Не удалось запустить задачу",
                    "Сохранённые настройки этой задачи повреждены или больше не поддерживаются. "
                    "Повторно создайте задачу с актуальными настройками.",
                )
            except Exception:
                pass
            return False
        resumed = any(
            str(entry.get("status") or "pending") != "pending" or str(entry.get("output_path") or "")
            for entry in batch_state.get("files") or []
        )
        batch_state["status"] = "running"
        self._resume_batch_state = batch_state
        try:
            save_batch_recovery_state(batch_state)
        except OSError as exc:
            self._problem(
                "batch_recovery_save_failed",
                level="warning",
                message="Не удалось сохранить совместимую контрольную точку активной задачи.",
                exception={"type": type(exc).__name__, "category": classify_exception(exc), "message": compact_exception(exc)},
            )
        self._open_batch_logger(batch_state, resumed=resumed)
        # Keep the task monitor useful across the whole run. The path is diagnostic
        # metadata only; queue persistence already owns/logs its own failure path.
        task.setdefault("runtime", {})["log_path"] = str(self.last_log_path or "")
        if hasattr(self, "_persist_task_queue_safely"):
            self._persist_task_queue_safely()
        self._emit_batch_started(batch_state, resumed=resumed)

        thread = threading.Thread(
            target=self._worker,
            args=(batch_state, out_folder, voice, model, keep, volume, target_info, review, audio_settings),
            daemon=True,
            name=f"video-translation-worker-{str(task.get('task_id') or '')[-12:]}",
        )
        return self._start_batch_worker(thread, batch_state)

    def _start_batch_worker(self, thread, batch_state: dict) -> bool:
        """Activate a prepared worker and restore a usable UI if it cannot start."""
        self._cancel_event.clear()
        self._set_busy(True)
        self._worker_thread = thread
        try:
            thread.start()
            return True
        except Exception as exc:
            self._worker_thread = None
            batch_state["status"] = "start_failed"
            try:
                save_batch_recovery_state(batch_state)
            except OSError:
                pass
            self._problem(
                "batch_worker_start_failed",
                level="error",
                message="Поток пакетной обработки не удалось запустить; обработка видео не начиналась.",
                exception={
                    "type": type(exc).__name__,
                    "category": classify_exception(exc),
                    "message": compact_exception(exc),
                },
            )
            if hasattr(self, "_task_start_failed"):
                self._task_start_failed(batch_state)
            self._set_busy(False)
            try:
                if self.file_logger:
                    self.file_logger.close()
            except Exception:
                pass
            messagebox.showerror(
                "Ошибка запуска обработки",
                f"Не удалось запустить фоновую обработку видео:\n{type(exc).__name__}: {exc}",
            )
            return False
