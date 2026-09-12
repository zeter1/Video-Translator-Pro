"""VideoTranslator orchestration and full per-video process workflow."""

from __future__ import annotations

from videotranslator.core.compat_bridge import call_legacy_override

import hashlib
import os
import shutil
import tempfile
import time
import traceback
from videotranslator.config import DEFAULT_TARGET_LANGUAGE, EDGE_TTS_MAX_TIMEOUT_SEC, EDGE_TTS_MIN_TIMEOUT_SEC, EDGE_VOICE_PRESERVE_SEC, EDGE_VOICE_PROBE_INTERVAL_SEC, NETWORK_CONNECT_TIMEOUT_SEC, NETWORK_READ_TIMEOUT_SEC, TOTAL_MAX_SPEECH_SPEED, TRANSLATION_BATCH_MAX_CHARS, TRANSLATION_BATCH_MAX_SEGMENTS, TRANSLATION_CHECKPOINT_EVERY, TRANSLATION_PROGRESS_EVERY, TTS_CACHE_MAX_BYTES, TTS_CACHE_ORPHAN_RETENTION_HOURS, TTS_CACHE_PREPARED_RETENTION_HOURS, TTS_CACHE_RECOVERY_RETENTION_DAYS, TTS_CACHE_SCHEMA_VERSION, TTS_CACHE_TARGET_BYTES, TTS_PREPARED_CACHE_SCHEMA_VERSION, get_target_language, normalize_audio_settings
from videotranslator.core.cancel import CancelledError
from videotranslator.core.diagnostics import classify_exception, compact_exception, exception_chain, redact_diagnostic_text
from videotranslator.core.timefmt import fmt_time
from videotranslator.diagnostics.heartbeat import ActivityHeartbeat
from videotranslator.media.audio import calc_audio_work_timeout
from videotranslator.media.probe import select_audio_stream
from videotranslator.media.final_video import FinalVideoSaveError
from videotranslator.media.process import find_ffmpeg as _legacy_find_ffmpeg, find_ffprobe as _legacy_find_ffprobe
from videotranslator.media.video import assemble_final_video as _legacy_assemble_final_video, extract_audio_for_whisper as _legacy_extract_audio_for_whisper, get_media_duration as _legacy_get_media_duration
from videotranslator.recovery.translation import load_translation_checkpoint, translation_checkpoint_path as _legacy_translation_checkpoint_path, translation_segment_key
from videotranslator.reports.output import write_translated_text_file as _legacy_write_translated_text_file, write_translation_report as _legacy_write_translation_report
from videotranslator.speech.whisper import get_whisper_model as _legacy_get_whisper_model, is_cuda_available
from videotranslator.sync.pause import merge_short_segments
from videotranslator.translation.batching import split_translation_batches


def find_ffmpeg(*args, **kwargs):
    return call_legacy_override('find_ffmpeg', _legacy_find_ffmpeg, *args, **kwargs)

def find_ffprobe(*args, **kwargs):
    return call_legacy_override('find_ffprobe', _legacy_find_ffprobe, *args, **kwargs)

def get_media_duration(*args, **kwargs):
    return call_legacy_override('get_media_duration', _legacy_get_media_duration, *args, **kwargs)

def extract_audio_for_whisper(*args, **kwargs):
    return call_legacy_override('extract_audio_for_whisper', _legacy_extract_audio_for_whisper, *args, **kwargs)

def get_whisper_model(*args, **kwargs):
    return call_legacy_override('get_whisper_model', _legacy_get_whisper_model, *args, **kwargs)

def translation_checkpoint_path(*args, **kwargs):
    return call_legacy_override('translation_checkpoint_path', _legacy_translation_checkpoint_path, *args, **kwargs)

def write_translated_text_file(*args, **kwargs):
    return call_legacy_override('write_translated_text_file', _legacy_write_translated_text_file, *args, **kwargs)

def assemble_final_video(*args, **kwargs):
    return call_legacy_override('assemble_final_video', _legacy_assemble_final_video, *args, **kwargs)

def write_translation_report(*args, **kwargs):
    return call_legacy_override('write_translation_report', _legacy_write_translation_report, *args, **kwargs)

class ProcessMixin:
    """Focused behavior-preserving mixin extracted for AI-local navigation."""

    def process(self, input_path: str, output_path: str, voice: str, model_size: str,
                keep_original: bool, orig_vol_pct: int, target_info: dict | None = None,
                review_before_tts: bool = False, audio_settings: dict | None = None) -> bool:
        video = None
        tts_clip = None
        final_audio = None
        output_clip = None
        process_started_at = time.monotonic()
        checkpoint_path = None
        checkpoint_segments = None
        pipeline_succeeded = False
        preserve_temp = False

        try:
            self.target_info = dict(target_info or get_target_language(DEFAULT_TARGET_LANGUAGE))
            self.audio_settings = normalize_audio_settings(audio_settings)
            self.speech_speed_limit = float(self.audio_settings.get("speech_speed_limit", TOTAL_MAX_SPEECH_SPEED))
            self.selected_voice = str(voice or "")
            with self.edge_voice_lock:
                self.edge_voice_preservation_active = False
                self.edge_voice_deadline = 0.0
                self.edge_voice_probe_requested.clear()
                self.edge_voice_fallback_requested.clear()
                self.edge_voice_fallback_request_reason = ""
                self._tts_allow_gtts_fallback = False
                self._tts_fallback_reason = ""
            self._close_translation_client()
            self.current_input_path = os.path.abspath(input_path)
            self.current_stage = "initialization"
            try:
                input_stat = os.stat(input_path)
                input_details = {"size": input_stat.st_size, "modified_ns": input_stat.st_mtime_ns}
            except OSError:
                input_details = {}
            self._problem(
                "file_pipeline_started",
                message="Начата обработка видео.",
                output_path=os.path.abspath(output_path),
                input=input_details,
                settings={
                    "voice": voice,
                    "whisper_model": model_size,
                    "target_language": self.target_info.get("code"),
                    "review_before_tts": bool(review_before_tts),
                    "keep_original_audio": bool(keep_original),
                    "original_volume_pct": int(orig_vol_pct),
                    "audio": self.audio_settings,
                    "network_timeout_sec": {
                        "connect": NETWORK_CONNECT_TIMEOUT_SEC,
                        "read": NETWORK_READ_TIMEOUT_SEC,
                        "edge_tts_min": EDGE_TTS_MIN_TIMEOUT_SEC,
                        "edge_tts_max": EDGE_TTS_MAX_TIMEOUT_SEC,
                    },
                    "vpn_tolerant_tts": {
                        "parallel_edge_requests": 2,
                        "parallel_gtts_requests": 2,
                        "selected_voice_first": True,
                        "edge_voice_preserve_sec": EDGE_VOICE_PRESERVE_SEC,
                        "edge_storm_bypass_to_gtts": False,
                        "gtts_only_after_wait_or_user_confirmation": True,
                        "edge_probe_interval_sec": EDGE_VOICE_PROBE_INTERVAL_SEC,
                        "edge_recovery_stable_sec": 30,
                        "sequential_recovery_pass": True,
                        "cache_schema_version": TTS_CACHE_SCHEMA_VERSION,
                        "prepared_cache_schema_version": TTS_PREPARED_CACHE_SCHEMA_VERSION,
                    },
                    "tts_cache_cleanup": {
                        "release_after_successful_mp4": True,
                        "keep_compact_mp3_after_failure_or_cancel": True,
                        "recovery_retention_days": TTS_CACHE_RECOVERY_RETENTION_DAYS,
                        "prepared_retention_hours": TTS_CACHE_PREPARED_RETENTION_HOURS,
                        "orphan_retention_hours": TTS_CACHE_ORPHAN_RETENTION_HOURS,
                        "max_bytes": TTS_CACHE_MAX_BYTES,
                        "target_bytes": TTS_CACHE_TARGET_BYTES,
                    },
                },
            )

            try:
                cache_maintenance = self.tts_cache.cleanup_stale_entries()
            except Exception as exc:
                cache_maintenance = {
                    "deleted_entries": 0,
                    "deleted_files": 0,
                    "freed_bytes": 0,
                    "remaining_bytes": None,
                    "errors": [compact_exception(exc, max_len=1000)],
                }
            if cache_maintenance.get("deleted_files") or cache_maintenance.get("errors"):
                freed_mb = cache_maintenance.get("freed_bytes", 0) / (1024 ** 2)
                self.log(
                    "   🧹 Обслуживание TTS-кэша: "
                    f"удалено файлов {cache_maintenance.get('deleted_files', 0)}, "
                    f"освобождено {freed_mb:.1f} МБ"
                )
                self._problem(
                    "tts_cache_maintenance_finished",
                    level="warning" if cache_maintenance.get("errors") else "info",
                    message="Удалены старые, лишние или незавершённые записи TTS-кэша.",
                    retention_days=TTS_CACHE_RECOVERY_RETENTION_DAYS,
                    prepared_retention_hours=TTS_CACHE_PREPARED_RETENTION_HOURS,
                    max_bytes=TTS_CACHE_MAX_BYTES,
                    target_bytes=TTS_CACHE_TARGET_BYTES,
                    **cache_maintenance,
                )

            self.log(f"\n{'─' * 58}")
            self.log(f"🎬 {os.path.basename(input_path)}")
            self.log(
                f"🎙️ {voice}  |  🧠 {model_size}  |  "
                f"🌐 auto → {self.target_info.get('name', self.target_info.get('code'))}  |  Pause Sync Quality"
            )
            self.log(
                "   🎚️ Аудио: "
                f"voice {self.audio_settings['voice_volume_pct']}%, "
                f"HP {self.audio_settings['highpass_hz']}Hz, "
                f"LP {self.audio_settings['lowpass_hz'] or 'off'}, "
                f"noise {'on' if self.audio_settings['noise_reduction'] else 'off'}, "
                f"loudness {self.audio_settings['master_loudness_i']} LUFS, "
                f"speed≤x{self.speech_speed_limit:.2f}"
            )
            self.log(
                "   🛡️ VPN-режим TTS: сначала сохраняется выбранный Edge-голос; "
                f"при сбое уведомление и ожидание до {EDGE_VOICE_PRESERVE_SEC}с, "
                "затем gTTS только как последний резерв"
            )

            self.ffmpeg = find_ffmpeg()
            self.ffprobe = find_ffprobe(self.ffmpeg)
            self.log(f"   ffmpeg:  {self.ffmpeg}")
            self.log(f"   ffprobe: {self.ffprobe}")

            if model_size == "medium" and not is_cuda_available():
                self.log("   ⚠️ Модель medium на CPU может работать очень медленно.")

            # Временные файлы держим в системной TEMP-папке, а не рядом с программой и не в папке результата.
            # Итоговый MP4 появится в выбранной пользователем папке только после успешной проверки video+audio.
            self.temp_dir = tempfile.mkdtemp(prefix="video_translator_")
            tmp_wav = os.path.join(self.temp_dir, "original_audio.wav")

# CODEX-PHASE VT1 AUDIO_EXTRACT — duration probe + ffmpeg extraction for Whisper
            # Шаг 1: извлечение аудио
            self.current_stage = "audio_extraction"
            stage_started_at = time.monotonic()
            self._problem("stage_started", message="Извлечение аудиодорожки.", stage_number=1)
            self.set_progress(5, "Извлечение аудио...")
            self.log("\n🔊 Шаг 1/5: Извлечение звука...")

            video_dur = get_media_duration(self.ffprobe, input_path, self.log)
            if video_dur <= 0:
                raise RuntimeError("Не удалось определить длительность видео.")

            extract_timeout = calc_audio_work_timeout(video_dur, min_timeout=600, factor=0.55, extra=300)
            audio_stream_index = select_audio_stream(self.ffprobe, input_path, log=self.log, cancel_event=self.cancel)
            if not extract_audio_for_whisper(
                self.ffmpeg,
                input_path,
                tmp_wav,
                self.log,
                cancel_event=self.cancel,
                timeout=extract_timeout,
                audio_stream_index=audio_stream_index,
            ):
                raise RuntimeError("Не удалось извлечь аудиодорожку из видео. Проверьте, что в файле есть звук.")

            self.log(f"   ✅ {fmt_time(video_dur)} ({video_dur:.1f}с)")
            self._problem(
                "stage_finished",
                message="Аудиодорожка извлечена.",
                stage_number=1,
                elapsed_sec=round(time.monotonic() - stage_started_at, 3),
                media_duration_sec=round(video_dur, 3),
                timeout_sec=extract_timeout,
            )
            self._check_cancel()

# CODEX-PHASE VT2 WHISPER — model load + transcription + segment merge
            # Шаг 2: Whisper
            self.current_stage = "speech_recognition"
            stage_started_at = time.monotonic()
            self._problem(
                "stage_started",
                message="Загрузка модели и распознавание речи.",
                stage_number=2,
                model=model_size,
                cuda=is_cuda_available(),
            )
            self.set_progress(12, "Загрузка Whisper...")
            self.log(f"\n🧠 Шаг 2/5: Распознавание (модель: {model_size})...")
            use_cuda = is_cuda_available()
            with ActivityHeartbeat(
                self.log,
                "Загрузка модели Whisper",
                interval=60,
                event_cb=self._problem,
                stage_number=2,
            ):
                model = get_whisper_model(model_size)
            self._check_cancel()

            self.set_progress(18, "Распознавание речи...")
            with ActivityHeartbeat(
                self.log,
                "Распознавание речи",
                interval=60,
                event_cb=self._problem,
                stage_number=2,
            ):
                result = model.transcribe(
                    tmp_wav,
                    fp16=use_cuda,
                    verbose=False,
                    language=None,
                    task="transcribe",
                    condition_on_previous_text=True,
                )
            self._check_cancel()

            segs_raw = result.get("segments", []) or []
            full_text = (result.get("text") or "").strip()
            lang = result.get("language", "?")

            if not full_text or not segs_raw:
                raise RuntimeError("Whisper не смог распознать речь.")

            self.log(f"   🌐 Язык: {lang} | Сегментов: {len(segs_raw)}")
            segs = merge_short_segments(segs_raw, min_dur=1.5)
            self.log(f"   🔀 После слияния: {len(segs)} сегментов")
            self._problem(
                "stage_finished",
                message="Распознавание речи завершено.",
                stage_number=2,
                elapsed_sec=round(time.monotonic() - stage_started_at, 3),
                detected_language=lang,
                raw_segments=len(segs_raw),
                merged_segments=len(segs),
                text_length=len(full_text),
            )

# CODEX-PHASE VT3 TRANSLATION — checkpoint restore + batch translation + retry
            # Шаг 3: перевод
            self.current_stage = "translation"
            stage_started_at = time.monotonic()
            self.set_progress(30, "Перевод...")
            self.log(f"\n🌍 Шаг 3/5: Перевод {len(segs)} сегментов...")
            self._problem(
                "stage_started",
                message="Перевод распознанных сегментов.",
                stage_number=3,
                segments_total=len(segs),
                target_language=self.target_info.get("code"),
            )

# CODEX-PHASE VT3A CHECKPOINT_PLAN — restore cached segment translations and build pending plan
            checkpoint_path = translation_checkpoint_path(input_path, self.target_info.get("code") or "target")
            try:
                checkpoint_cache = load_translation_checkpoint(checkpoint_path)
            except Exception as exc:
                checkpoint_cache = {}
                self.log(f"   ⚠️ Не удалось прочитать контрольную точку перевода: {compact_exception(exc)}")
                self._problem(
                    "translation_checkpoint_load_failed",
                    level="warning",
                    message="Повреждённая или недоступная контрольная точка проигнорирована.",
                    checkpoint_path=str(checkpoint_path),
                    exception={
                        "type": type(exc).__name__,
                        "category": classify_exception(exc),
                        "message": compact_exception(exc, max_len=1000),
                    },
                )

            translated = []
            failed_records = []
            pending_records = []
            cache_hits = 0
            new_translations = 0
            total_segments = len(segs)
            for idx, seg in enumerate(segs, 1):
                self._check_cancel()
                source_text = (seg.get("text") or "").strip()
                if not source_text:
                    continue
                record = {
                    "start": float(seg.get("start", 0.0)),
                    "end": float(seg.get("end", 0.0)),
                    "source": source_text,
                    "translated": "",
                }
                key = translation_segment_key(record["start"], record["end"], source_text)
                cached_text = checkpoint_cache.get(key, "")
                if cached_text:
                    record["translated"] = cached_text
                    cache_hits += 1
                else:
                    pending_records.append((idx, record))
                translated.append(record)

            # Enable interruption saving only after the complete cached plan exists.
            # Cancelling during plan construction must not truncate an old checkpoint.
            checkpoint_segments = translated
            batches = split_translation_batches(pending_records)
            self.log(
                f"   📦 Новых запросов: {len(pending_records)} сегм. в {len(batches)} пачках "
                f"по ≤{TRANSLATION_BATCH_MAX_SEGMENTS}; кэш: {cache_hits}"
            )
            self._problem(
                "translation_batch_plan",
                message="Сформирован безопасный план пакетного перевода.",
                pending_segments=len(pending_records),
                checkpoint_hits=cache_hits,
                batches=len(batches),
                max_segments_per_batch=TRANSLATION_BATCH_MAX_SEGMENTS,
                max_request_chars=TRANSLATION_BATCH_MAX_CHARS,
            )

# CODEX-PHASE VT3B BATCH_TRANSLATE — execute bounded translation batches and periodic checkpoints
            processed = cache_hits
            saved_translation_count = 0
            for batch_no, batch in enumerate(batches, 1):
                self._check_cancel()
                first_index = batch[0][0]
                last_index = batch[-1][0]
                self.set_progress(
                    30 + int(processed / max(1, total_segments) * 22),
                    f"Перевод пачки {batch_no}/{len(batches)} (сегменты {first_index}-{last_index})...",
                )
                results, batch_failures = self.translate_records_batch(batch, attempts=2)
                for idx, record in batch:
                    value = (results.get(idx) or "").strip()
                    if value:
                        record["translated"] = value
                        new_translations += 1
                for idx, record, exc in batch_failures:
                    failed_records.append((idx, record, exc))
                    source_text = record.get("source") or ""
                    self._problem(
                        "translation_segment_deferred",
                        level="warning",
                        message="Сегмент отложен до второго прохода; остальной перевод продолжается.",
                        segment_index=idx,
                        source_length=len(source_text),
                        source_sha256=hashlib.sha256(source_text.encode("utf-8", errors="replace")).hexdigest(),
                        exception={
                            "type": type(exc).__name__,
                            "category": classify_exception(exc),
                            "message": compact_exception(exc, max_len=1000),
                            "chain": exception_chain(exc),
                        },
                    )

                processed += len(batch)
                if new_translations - saved_translation_count >= TRANSLATION_CHECKPOINT_EVERY:
                    self._save_checkpoint(checkpoint_path, input_path, lang, translated)
                    saved_translation_count = new_translations

                if processed % TRANSLATION_PROGRESS_EVERY < len(batch) or batch_no == len(batches):
                    elapsed = max(0.001, time.monotonic() - stage_started_at)
                    eta = int(elapsed / max(1, processed) * max(0, total_segments - processed))
                    self.log(
                        f"   ⏳ Перевод: {processed}/{total_segments} | из контрольной точки: {cache_hits} | "
                        f"отложено: {len(failed_records)} | примерно осталось: {fmt_time(eta)}"
                    )
                    self._problem(
                        "translation_progress",
                        message="Промежуточный прогресс перевода.",
                        processed=processed,
                        total=total_segments,
                        cache_hits=cache_hits,
                        deferred=len(failed_records),
                        elapsed_sec=round(elapsed, 3),
                        eta_sec=eta,
                        checkpoint_path=str(checkpoint_path),
                    )

            self._save_checkpoint(checkpoint_path, input_path, lang, translated)

            if cache_hits:
                self.log(f"   ♻️ Восстановлено из контрольной точки: {cache_hits} сегментов")

# CODEX-PHASE VT3C DEFERRED_RETRY — second pass for network-deferred translation segments
            if failed_records:
                self.log(
                    f"   🔁 Второй проход: повторяю {len(failed_records)} отложенных сегментов "
                    "после короткой паузы..."
                )
                self._problem(
                    "translation_deferred_retry_started",
                    level="warning",
                    message="Начат второй проход по сегментам с сетевыми ошибками.",
                    deferred_segments=len(failed_records),
                )
                self._sleep_or_cancel(4.0)
                still_failed = []
                for retry_no, (idx, record, _first_error) in enumerate(failed_records, 1):
                    self._check_cancel()
                    self.set_progress(
                        51,
                        f"Повтор перевода {retry_no}/{len(failed_records)} (сегмент {idx})...",
                    )
                    try:
                        record["translated"] = self.translate_segment(record["source"], idx, attempts=3)
                        if retry_no % 5 == 0:
                            self._save_checkpoint(checkpoint_path, input_path, lang, translated)
                    except CancelledError:
                        raise
                    except Exception as exc:
                        still_failed.append((idx, record, exc))
                failed_records = still_failed
                self._save_checkpoint(checkpoint_path, input_path, lang, translated)

# CODEX-PHASE VT3D TRANSLATION_INTEGRITY — reject incomplete translation before TTS
            failed_indices = [idx for idx, _record, _exc in failed_records]
            if failed_indices:
                preview = ", ".join(str(idx) for idx in failed_indices[:20])
                if len(failed_indices) > 20:
                    preview += ", ..."
                self._problem(
                    "translation_segments_failed",
                    level="error",
                    message="Часть сегментов не переведена после двух проходов; неполный текст не будет передан в озвучку.",
                    failed_count=len(failed_indices),
                    allowed_missing=0,
                    failed_indices=failed_indices,
                    checkpoint_path=str(checkpoint_path),
                )
                raise RuntimeError(
                    f"Сервис перевода временно недоступен для {len(failed_indices)} сегментов ({preview}). "
                    f"Чтобы не потерять текст, неполное видео не создаётся. Готовый прогресс сохранён: "
                    f"{checkpoint_path}. Повторите запуск позже — уже переведённые сегменты восстановятся автоматически."
                )

            translated_count = sum(1 for item in translated if (item.get("translated") or "").strip())
            if not translated or translated_count == 0:
                raise RuntimeError("Не удалось получить ни одного сегмента перевода.")

            self.log(f"   ✅ Переведено: {translated_count}/{len(translated)} сегментов")
            self._problem(
                "stage_finished",
                message="Этап перевода завершён.",
                stage_number=3,
                elapsed_sec=round(time.monotonic() - stage_started_at, 3),
                segments_total=len(translated),
                translated=translated_count,
                cache_hits=cache_hits,
                failed=len(failed_indices),
                checkpoint_path=str(checkpoint_path),
            )
            self._close_translation_client()
            self._check_cancel()

# CODEX-PHASE VT4 MANUAL_REVIEW — optional user correction before TTS
            if review_before_tts and self.review_callback:
                self.current_stage = "manual_review"
                review_started_at = time.monotonic()
                self.set_progress(52, "Проверка перевода...")
                self.log("   ✍️ Открываю окно ручной проверки перевода перед озвучкой...")
                self.log("   ⏸️ Обработка ждёт кнопку «Применить и продолжить» в отдельном окне.")
                self._problem(
                    "manual_review_wait_started",
                    message="Обработка штатно приостановлена и ждёт действия пользователя в окне проверки.",
                    segments=len(translated),
                )
                reviewed = self.review_callback(translated, input_path, lang, self.target_info)
                self._check_cancel()
                if reviewed is None:
                    raise CancelledError()
                translated = reviewed
                self.log("   ✅ Правки перевода применены.")
                self._save_checkpoint(checkpoint_path, input_path, lang, translated)
                self._problem(
                    "manual_review_wait_finished",
                    message="Пользователь применил правки и продолжил обработку.",
                    elapsed_sec=round(time.monotonic() - review_started_at, 3),
                )

            missing_translation_indices = [
                index
                for index, segment in enumerate(translated, 1)
                if (segment.get("source") or "").strip() and not (segment.get("translated") or "").strip()
            ]
            if missing_translation_indices:
                self._save_checkpoint(checkpoint_path, input_path, lang, translated)
                self._problem(
                    "translation_integrity_failed",
                    level="error",
                    message="Проверка полноты не пройдена: непереведённый текст не будет передан в озвучку.",
                    failed_indices=missing_translation_indices,
                    checkpoint_path=str(checkpoint_path),
                )
                raise RuntimeError(
                    f"Проверка полноты нашла {len(missing_translation_indices)} пустых переводов. "
                    "Озвучка не запущена, чтобы не потерять текст. Исправьте их в контрольной точке или повторите перевод."
                )

            write_translated_text_file(output_path, translated, lang, self.target_info, log=self.log)

# CODEX-PHASE VT5 TTS_TIMELINE — speech generation + Pause Sync timeline
            # Шаг 4: TTS + таймлайн
            self.current_stage = "tts_and_timeline"
            stage_started_at = time.monotonic()
            self._problem("stage_started", message="Озвучка и синхронизация сегментов.", stage_number=4)
            self.set_progress(53, "Генерация озвучки...")
            self.log("\n🗣️ Шаг 4/5: Озвучка и синхронизация...")
            tts_wav, pause_plan, final_dur = self.build_timeline(translated, video_dur, voice)
            self._problem(
                "stage_finished",
                message="Озвучка и синхронизация завершены.",
                stage_number=4,
                elapsed_sec=round(time.monotonic() - stage_started_at, 3),
                pause_count=len(pause_plan or []),
                final_duration_sec=round(float(final_dur or 0.0), 3),
                tts=dict(self.tts_stats),
            )
            self._check_cancel()

# CODEX-PHASE VT6 FINAL_MUX — validated final MP4 assembly
            # Шаг 5: сборка видео
            self.current_stage = "final_video_assembly"
            stage_started_at = time.monotonic()
            self._problem("stage_started", message="Сборка и проверка итогового MP4.", stage_number=5)
            self.set_progress(88, "Сборка видео...")
            self.log(f"\n🎬 Шаг 5/5: Запись MP4 с видеорядом и озвучкой ({self.target_info.get('name', 'перевод')})...")

            final_path = assemble_final_video(
                self.ffmpeg,
                self.ffprobe,
                input_path,
                tts_wav,
                output_path,
                self.temp_dir,
                video_dur,
                keep_original,
                orig_vol_pct,
                log=self.log,
                pause_plan=pause_plan,
                final_dur=final_dur,
                cancel_event=self.cancel,
                diagnostic=self._problem,
                audio_stream_index=audio_stream_index,
            )

# CODEX-PHASE VT7 REPORTS — translated text + segment/pause diagnostics
            write_translation_report(
                final_path,
                translated,
                pause_plan,
                video_dur,
                final_dur,
                log=self.log,
                source_lang=lang,
                target_info=self.target_info,
            )

            try:
                cache_release = self.tts_cache.release_used_entries()
            except Exception as exc:
                # Готовый проверенный MP4 важнее служебной очистки кэша.
                cache_release = {
                    "deleted_entries": 0,
                    "deleted_files": 0,
                    "freed_bytes": 0,
                    "remaining_bytes": None,
                    "errors": [compact_exception(exc, max_len=1000)],
                }
            freed_mb = cache_release.get("freed_bytes", 0) / (1024 ** 2)
            if cache_release.get("deleted_files"):
                self.log(
                    "   🧹 Итоговый MP4 проверен: временный TTS-кэш этого видео удалён "
                    f"({cache_release.get('deleted_files', 0)} файлов, {freed_mb:.1f} МБ)."
                )
            if cache_release.get("errors"):
                self.log(
                    "   ⚠️ Часть уже ненужного TTS-кэша не удалось удалить; "
                    "программа попробует снова при следующем запуске."
                )
            self._problem(
                "tts_cache_released_after_success",
                level="warning" if cache_release.get("errors") else "info",
                message=(
                    "После успешной проверки итогового MP4 использованный TTS-кэш удалён. "
                    "При ошибке или отмене сохраняются только компактные записи для восстановления."
                ),
                final_path=os.path.abspath(final_path),
                **cache_release,
            )

            self._problem(
                "stage_finished",
                message="Итоговый MP4 собран и проверен.",
                stage_number=5,
                elapsed_sec=round(time.monotonic() - stage_started_at, 3),
                final_path=os.path.abspath(final_path),
            )
            self.set_progress(100, "Готово!")
            self.log(f"\n✅ ГОТОВО → {final_path}")
            self.current_stage = "finished"
            self._problem(
                "file_pipeline_finished",
                message="Видео успешно обработано.",
                elapsed_sec=round(time.monotonic() - process_started_at, 3),
                final_path=os.path.abspath(final_path),
                checkpoint_path=str(checkpoint_path) if checkpoint_path else "",
            )
            pipeline_succeeded = True
            return True

        except CancelledError:
            self.log("🛑 Отменено.")
            self._problem(
                "file_pipeline_cancelled",
                level="warning",
                message="Обработка отменена пользователем.",
                elapsed_sec=round(time.monotonic() - process_started_at, 3),
                checkpoint_path=str(checkpoint_path) if checkpoint_path else "",
            )
            return False
        except Exception as exc:
            if isinstance(exc, FinalVideoSaveError):
                preserve_temp = True
            self.log(f"\n❌ ОШИБКА: {exc}")
            self.log(traceback.format_exc())
            self._problem(
                "file_pipeline_failed",
                level="error",
                message="Обработка текущего видео завершилась ошибкой.",
                elapsed_sec=round(time.monotonic() - process_started_at, 3),
                checkpoint_path=str(checkpoint_path) if checkpoint_path else "",
                exception={
                    "type": type(exc).__name__,
                    "category": classify_exception(exc),
                    "message": compact_exception(exc, max_len=2000),
                    "chain": exception_chain(exc),
                    "traceback": redact_diagnostic_text(traceback.format_exc()),
                },
            )
            return False
        finally:
            if self.current_stage == "translation" and checkpoint_segments is not None:
                self._save_checkpoint(checkpoint_path, input_path, lang, checkpoint_segments)
            self._close_translation_client()
            for clip in (output_clip, final_audio, tts_clip, video):
                if clip:
                    try:
                        clip.close()
                    except Exception:
                        pass
# CODEX-PHASE VT8 CLEANUP — release clients/cache/temp while preserving recovery state
            if preserve_temp:
                # Detach the failed job so a subsequent batch cannot clean it.
                self.log(f"   💾 Рабочая папка сохранена для восстановления видео: {self.temp_dir}")
                self.temp_dir = ""
            else:
                self.cleanup_temp()
            if not pipeline_succeeded:
                try:
                    prepared_release = self.tts_cache.release_used_entries(suffixes={".wav"})
                except Exception as exc:
                    prepared_release = {
                        "deleted_entries": 0,
                        "deleted_files": 0,
                        "freed_bytes": 0,
                        "remaining_bytes": None,
                        "errors": [compact_exception(exc, max_len=1000)],
                    }
                if prepared_release.get("deleted_files") or prepared_release.get("errors"):
                    freed_mb = prepared_release.get("freed_bytes", 0) / (1024 ** 2)
                    self.log(
                        "   🧹 Незавершённая обработка: тяжёлые подготовленные WAV удалены "
                        f"({prepared_release.get('deleted_files', 0)} файлов, {freed_mb:.1f} МБ); "
                        "компактный TTS-кэш оставлен для продолжения."
                    )
                    self._problem(
                        "tts_prepared_cache_released_after_stop",
                        level="warning" if prepared_release.get("errors") else "info",
                        message=(
                            "После остановки удалены производные WAV; компактный исходный "
                            "TTS-кэш сохранён для восстановления без повторного сетевого запроса."
                        ),
                        **prepared_release,
                    )

    def cleanup_temp(self):
        if self.temp_dir and os.path.isdir(self.temp_dir):
            try:
                shutil.rmtree(self.temp_dir, ignore_errors=True)
            except Exception as exc:
                self.log(f"      ⚠️ Не удалось удалить временную папку: {exc}")
        self.temp_dir = ""
