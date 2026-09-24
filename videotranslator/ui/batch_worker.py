"""Method owner for App: _restore_batch_settings, _offer_batch_recovery, start, _worker."""

from __future__ import annotations

from videotranslator.core.compat_bridge import call_legacy_override

from tkinter import messagebox
import os
import time
import traceback
from videotranslator.core.diagnostics import classify_exception, compact_exception
from videotranslator.media.process import find_ffmpeg as _legacy_find_ffmpeg, find_ffprobe as _legacy_find_ffprobe
from videotranslator.media.video import output_has_video_and_audio as _legacy_output_has_video_and_audio
from videotranslator.network.translation_circuit import SharedTranslationCircuit
from videotranslator.pipeline.translator import VideoTranslator as _legacy_VideoTranslator
from videotranslator.recovery.batch import get_batch_recovery_state_path as _legacy_get_batch_recovery_state_path, input_file_signature, save_batch_recovery_state
from videotranslator.reports.output import make_unique_output_path


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

class UIBatchWorkerMixin:
    """Focused behavior-preserving mixin extracted for AI-local navigation."""

    def _worker(self, batch_state, out_folder, voice, model, keep, volume, target_info, review, audio_settings):
# CODEX-PHASE BW1 BATCH_INIT — restore persisted batch state and counters
        entries = list(batch_state.get("files") or [])
        total = len(entries)
        successful = sum(
            isinstance(entry, dict) and str(entry.get("status") or "") == "succeeded"
            for entry in entries
        )
        cancelled = False
        log_path = self.last_log_path
        owned_file_logger = self.file_logger
        task_id = str(batch_state.get("batch_id") or "")
        # App may be instantiated without its full __init__ by legacy embedders/tests.
        # Queue callbacks exist on the class via the mixin, so method presence alone
        # does not prove that persistent queue state/lock ownership is initialized.
        queue_initialized = bool(
            hasattr(self, "_task_queue_lock")
            and isinstance(getattr(self, "_task_queue_state", None), dict)
        )
        queue_owned = bool(
            queue_initialized and task_id and hasattr(self, "_on_task_batch_finished")
        )
        batch_started_at = time.monotonic()
        # Shared across all VideoTranslator instances in this batch.  If Google
        # repair hits a rate limit on one video, following videos keep using
        # local translation instead of reopening a fresh request storm.
        shared_translation_circuit = SharedTranslationCircuit()

        def safe_nonnegative_int(value, fallback=0):
            try:
                return max(0, int(value))
            except (TypeError, ValueError, OverflowError):
                return max(0, int(fallback or 0))

        def input_signature_changed(stored_input: dict, current_input: dict) -> bool:
            if not isinstance(stored_input, dict):
                return True
            if stored_input.get("size") is None:
                return False
            try:
                return (
                    int(stored_input.get("size")) != int(current_input.get("size", -1))
                    or int(stored_input.get("modified_ns", -1)) != int(current_input.get("modified_ns", -2))
                )
            except (TypeError, ValueError, OverflowError):
                # Corrupt persisted signature must never crash the whole batch. Treat
                # it as changed so stale output is not trusted for a different input.
                return True

        def verified_output(path: str) -> bool:
            path = str(path or "")
            if not path or not os.path.isfile(path):
                return False
            try:
                ffmpeg = find_ffmpeg()
                ffprobe = find_ffprobe(ffmpeg)
                return bool(ffprobe and output_has_video_and_audio(ffprobe, path, log=self._log))
            except Exception:
                return False

        def save_state_safely():
            try:
                save_batch_recovery_state(batch_state)
            except OSError as exc:
                self._problem(
                    "batch_recovery_save_failed",
                    level="warning",
                    message="Не удалось обновить контрольную точку пакетной обработки.",
                    batch_id=batch_state.get("batch_id") or "",
                    exception={
                        "type": type(exc).__name__,
                        "category": classify_exception(exc),
                        "message": compact_exception(exc),
                    },
                )
            if queue_initialized and hasattr(self, "_save_task_queue_state_from_worker"):
                self._save_task_queue_state_from_worker(batch_state)

        try:
            problem_log_path = str(self.problem_logger.problem_path) if self.problem_logger else "не создан"
            problem_summary_path = str(self.problem_logger.summary_path) if self.problem_logger else "не создан"
            problem_report_path = str(self.problem_logger.report_path) if self.problem_logger else "не создан"
            self._log(
                f"\n{'═' * 56}\n"
                f"📦 Файлов: {total}\n"
                f"🧾 Обычный лог: {log_path or 'не создан'}\n"
                f"🧩 Предупреждения и ошибки для Codex: {problem_log_path}\n"
                f"📌 Машиночитаемая сводка: {problem_summary_path}\n"
                f"📖 Краткий диагностический отчёт: {problem_report_path}\n"
                f"{'═' * 56}"
            )

# CODEX-PHASE BW2 FILE_LOOP — one durable state machine entry per input video
            for position, raw_entry in enumerate(entries, 1):
                if not isinstance(raw_entry, dict):
                    # Keep the batch alive when an old/corrupt checkpoint contains a
                    # malformed file row. Preserve a visible failed placeholder rather
                    # than crashing the worker before later valid files can run.
                    entry = {
                        "file_index": position,
                        "input": {},
                        "status": "missing",
                        "output_path": "",
                        "elapsed_sec": 0.0,
                        "last_error": "Повреждена запись файла в контрольной точке.",
                    }
                    entries[position - 1] = entry
                    batch_state["files"] = entries
                    save_state_safely()
                else:
                    entry = raw_entry

                idx = safe_nonnegative_int(entry.get("file_index"), position) or position
                input_info = entry.get("input") if isinstance(entry.get("input"), dict) else {}
                file_path = str(input_info.get("path") or "")
                if str(entry.get("status") or "") == "succeeded":
                    existing_output = str(entry.get("output_path") or "")
                    source_changed = False
                    if file_path and os.path.isfile(file_path):
                        source_changed = input_signature_changed(input_info, input_file_signature(file_path))
                    if verified_output(existing_output) and not source_changed:
                        continue
                    successful = max(0, successful - 1)
                    entry.update({
                        "status": "pending",
                        "last_error": (
                            "Исходник изменился после предыдущего успеха; файл будет обработан заново."
                            if source_changed else
                            "Ранее успешный результат отсутствует или не прошёл повторную проверку; "
                            "файл будет обработан заново."
                        ),
                    })
                    save_state_safely()
                    self._problem(
                        "batch_verified_output_lost",
                        level="warning",
                        message=(
                            "Исходное видео изменилось после предыдущего успешного результата; "
                            "старый MP4 больше не считается результатом текущего входа."
                            if source_changed else
                            "Ранее отмеченный успешным MP4 отсутствует или больше невалиден; "
                            "элемент возвращён в обработку."
                        ),
                        file_index=idx,
                        files_total=total,
                        input_path=os.path.abspath(file_path) if file_path else "",
                        output_path=os.path.abspath(existing_output) if existing_output else "",
                        source_changed=source_changed,
                    )
                if self._cancel_event.is_set():
                    cancelled = True
                    break

                if hasattr(self, "_task_worker_file_started"):
                    self._safe_after(0, self._task_worker_file_started, task_id, idx, total, file_path)
                self._log(f"\n[{idx}/{total}] ▶ {os.path.basename(file_path)}")
                file_started_at = time.monotonic()
                if not file_path or not os.path.isfile(file_path):
                    entry.update({
                        "status": "missing",
                        "elapsed_sec": 0.0,
                        "last_error": "Входной файл недоступен.",
                    })
                    save_state_safely()
                    self._log(f"⚠️ Входной файл недоступен: {file_path or '<путь не записан>'}")
                    self._problem(
                        "batch_file_finished",
                        level="warning",
                        message="Файл пропущен: входной путь недоступен.",
                        file_index=idx,
                        files_total=total,
                        input_path=os.path.abspath(file_path) if file_path else "",
                        output_path=entry.get("output_path") or "",
                        success=False,
                        missing=True,
                        elapsed_sec=0.0,
                    )
                    continue

                base = os.path.splitext(os.path.basename(file_path))[0]
                stored_input = dict(input_info)
                current_input = input_file_signature(file_path)
                input_changed = input_signature_changed(stored_input, current_input)
                output_path = str(entry.get("output_path") or "")
                if input_changed:
                    # Старый результат относится к другой версии входа. Не перезаписываем
                    # его и не считаем завершённым — ниже будет выбрано новое уникальное имя.
                    output_path = ""
                if output_path and os.path.exists(output_path):
                    verified_existing = verified_output(output_path)
                    if verified_existing:
                        successful += 1
                        entry.update({
                            "status": "succeeded",
                            "elapsed_sec": 0.0,
                            "last_error": "",
                        })
                        save_state_safely()
                        self._problem(
                            "batch_file_recovered_from_verified_output",
                            message="Найден и проверен готовый MP4 прерванной задачи; повторная обработка не нужна.",
                            file_index=idx,
                            files_total=total,
                            input_path=os.path.abspath(file_path),
                            output_path=os.path.abspath(output_path),
                        )
                        self._problem(
                            "batch_file_finished",
                            message="Обработка файла уже была успешно завершена до прерывания.",
                            file_index=idx,
                            files_total=total,
                            input_path=os.path.abspath(file_path),
                            output_path=os.path.abspath(output_path),
                            success=True,
                            resumed_verified_output=True,
                            elapsed_sec=0.0,
                        )
                        continue
                    output_path = ""
                if not output_path:
                    output_path = make_unique_output_path(
                        out_folder,
                        base,
                        target_info.get("suffix", "_TR"),
                        ".mp4",
                    )
                entry.update({
                    "input": current_input,
                    "status": "processing",
                    "output_path": os.path.abspath(output_path),
                    "last_error": "",
                })
                save_state_safely()
                self._problem(
                    "batch_file_started",
                    message="Пакет перешёл к следующему видео.",
                    file_index=idx,
                    files_total=total,
                    input_path=os.path.abspath(file_path),
                    output_path=os.path.abspath(output_path),
                )

# CODEX-PHASE BW3 RUN_VIDEO — construct pipeline and process current file
                translator = VideoTranslator(
                    self._log,
                    self._threadsafe_progress,
                    self._cancel_event,
                    review_callback=self.review_translations,
                    problem_cb=self._problem,
                    network_notice_cb=self._threadsafe_network_notice,
                )
                translator.shared_translation_circuit = shared_translation_circuit
                self._active_translator = translator
                try:
                    file_ok = translator.process(
                        file_path,
                        output_path,
                        voice,
                        model,
                        keep,
                        volume,
                        target_info=target_info,
                        review_before_tts=review,
                        audio_settings=audio_settings,
                    )
                    if file_ok:
                        successful += 1
                        entry.update({"status": "succeeded", "last_error": ""})
                    else:
                        self._log(f"⚠️ Файл не обработан: {file_path}")
                        entry.update({
                            "status": "cancelled" if self._cancel_event.is_set() else "failed",
                            "last_error": "Обработка отменена." if self._cancel_event.is_set() else "Файл не обработан.",
                        })
                    entry["elapsed_sec"] = round(time.monotonic() - file_started_at, 3)
                    self._problem(
                        "batch_file_finished",
                        level="info" if file_ok else "warning",
                        message="Обработка файла закончена.",
                        file_index=idx,
                        files_total=total,
                        input_path=os.path.abspath(file_path),
                        output_path=os.path.abspath(output_path),
                        success=bool(file_ok),
                        elapsed_sec=entry["elapsed_sec"],
                    )
                except Exception as exc:
                    # Защита на случай, если ошибка вылетит выше process().
                    self._log(f"\n❌ НЕОЖИДАННАЯ ОШИБКА ФАЙЛА: {file_path}")
                    self._log(f"{type(exc).__name__}: {exc}")
                    self._log(traceback.format_exc())
                    entry.update({
                        "status": "failed",
                        "elapsed_sec": round(time.monotonic() - file_started_at, 3),
                        "last_error": compact_exception(exc, max_len=1000),
                    })
                    try:
                        if self.problem_logger:
                            self.problem_logger.write_exception(
                                "unexpected_batch_file_exception",
                                exc,
                                message="Исключение вышло выше защиты обработки одного видео.",
                                file_index=idx,
                                files_total=total,
                                input_path=os.path.abspath(file_path),
                                output_path=os.path.abspath(output_path),
                                elapsed_sec=round(time.monotonic() - file_started_at, 3),
                            )
                    except Exception:
                        pass
                    self._problem(
                        "batch_file_finished",
                        level="error",
                        message="Обработка файла завершилась неожиданным исключением.",
                        file_index=idx,
                        files_total=total,
                        input_path=os.path.abspath(file_path),
                        output_path=os.path.abspath(output_path),
                        success=False,
                        elapsed_sec=entry["elapsed_sec"],
                    )
                finally:
                    if self._active_translator is translator:
                        self._active_translator = None
                    self._safe_after(0, self._close_vpn_notice)
                    save_state_safely()

                if queue_owned and hasattr(self, "_set_progress_for_task"):
                    self._safe_after(300, self._set_progress_for_task, task_id, 0, "")
                else:
                    self._safe_after(300, self._set_progress, 0, "")

            if self._cancel_event.is_set():
                cancelled = True

# CODEX-PHASE BW4 FINALIZE_BATCH — persist final status and notify UI
            pending = sum(
                not isinstance(entry, dict) or str(entry.get("status") or "") != "succeeded"
                for entry in entries
            )
            failed = sum(
                not isinstance(entry, dict)
                or str(entry.get("status") or "") in {"failed", "missing"}
                for entry in entries
            )
            batch_state["status"] = (
                "completed" if successful == total else ("cancelled" if cancelled else "incomplete")
            )
            save_state_safely()
            self._resume_batch_state = None if successful == total else batch_state

            self._log(
                f"\n{'═' * 56}\n"
                f"🏁 ИТОГ: {successful}/{total}\n"
                f"📁 Результаты: {out_folder}\n"
                f"🧾 Лог: {log_path or 'не создан'}\n"
                f"{'═' * 56}"
            )
            self._problem(
                "batch_finished",
                level="warning" if successful < total else "info",
                message="Пакетная обработка завершена.",
                successful=successful,
                total=total,
                failed=failed,
                pending=pending,
                cancelled=cancelled,
                elapsed_sec=round(time.monotonic() - batch_started_at, 3),
                output_dir=os.path.abspath(out_folder),
                normal_log=log_path,
            )
            has_errors = successful < total and not cancelled
            status_text = "Отменено" if cancelled else ("Завершено с ошибками" if has_errors else "Завершено")
            if queue_owned and hasattr(self, "_set_progress_for_task"):
                self._safe_after(0, self._set_progress_for_task, task_id, 0, status_text)
            else:
                self._safe_after(0, self._set_progress, 0, status_text)
            if queue_owned:
                self._safe_after(
                    0,
                    self._on_task_batch_finished,
                    task_id,
                    batch_state,
                    successful,
                    total,
                    cancelled,
                )
            else:
                self._safe_after(0, self._set_busy, False)
                dialog_title = "Отменено" if cancelled else ("Есть ошибки" if has_errors else "Готово")
                dialog_message = (
                    f"Прервано. Успешно: {successful}/{total}\n🧾 Лог: {log_path}"
                    if cancelled else
                    f"Обработано: {successful}/{total}\n📁 {out_folder}\n🧾 Лог: {log_path}"
                )
                if successful < total:
                    dialog_message += "\n\nНезавершённые файлы сохранены. Их можно продолжить при следующем запуске."
                self._safe_after(0, messagebox.showinfo, dialog_title, dialog_message)
        except Exception as exc:
            self._log("\n❌ КРИТИЧЕСКАЯ ОШИБКА РАБОЧЕГО ПОТОКА")
            self._log(f"{type(exc).__name__}: {exc}")
            self._log(traceback.format_exc())
            batch_state["status"] = "worker_failed"
            save_state_safely()
            self._resume_batch_state = batch_state
            try:
                if self.problem_logger:
                    self.problem_logger.write_exception(
                        "worker_thread_crashed",
                        exc,
                        message="Критическая ошибка пакетного рабочего потока.",
                        elapsed_sec=round(time.monotonic() - batch_started_at, 3),
                        normal_log=log_path,
                    )
            except Exception:
                pass
            if queue_owned and hasattr(self, "_set_progress_for_task"):
                self._safe_after(0, self._set_progress_for_task, task_id, 0, "Ошибка")
            else:
                self._safe_after(0, self._set_progress, 0, "Ошибка")
            if queue_owned and hasattr(self, "_on_task_worker_crashed"):
                self._safe_after(
                    0,
                    self._on_task_worker_crashed,
                    task_id,
                    batch_state,
                    f"Произошла ошибка. Подробности сохранены в логе:\n{log_path or 'лог не создан'}",
                )
            else:
                self._safe_after(0, self._set_busy, False)
                self._safe_after(
                    0,
                    messagebox.showerror,
                    "Ошибка",
                    f"Произошла ошибка. Подробности сохранены в логе:\n{log_path or 'лог не создан'}",
                )
        finally:
            try:
                if owned_file_logger:
                    owned_file_logger.close()
            except Exception:
                pass
