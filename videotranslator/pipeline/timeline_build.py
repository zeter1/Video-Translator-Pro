"""Method owner for VideoTranslator: build_timeline, _mix_in_batches, _amix_batch, _amix_wavs, _mix_numpy, _create_silence."""

from __future__ import annotations

from videotranslator.core.compat_bridge import call_legacy_override

from concurrent.futures import ThreadPoolExecutor
import hashlib
import os
from videotranslator.config import MIN_INSERTED_PAUSE, PAUSE_FINISH_MARGIN, TTS_CACHE_SCHEMA_VERSION, TTS_FINAL_VOICE_REPAIR_MAX_SEGMENTS, TTS_PREPARED_CACHE_SCHEMA_VERSION, TTS_PREPARE_WORKERS
from videotranslator.core.cancel import CancelledError
from videotranslator.core.diagnostics import classify_exception, compact_exception, exception_chain
from videotranslator.core.timefmt import fmt_time
from videotranslator.media.audio import calc_audio_work_timeout, master_voice_audio as _legacy_master_voice_audio
from videotranslator.media.process import run_subprocess as _legacy_run_subprocess
from videotranslator.network.http import progressive_retry_delay
from videotranslator.sync.pause import calc_quality_slot, merge_nearby_pause_plan, normalize_tts_text, sanitize_pause_plan


def master_voice_audio(*args, **kwargs):
    return call_legacy_override('master_voice_audio', _legacy_master_voice_audio, *args, **kwargs)

def run_subprocess(*args, **kwargs):
    return call_legacy_override('run_subprocess', _legacy_run_subprocess, *args, **kwargs)

class TimelineBuildMixin:
    """Focused behavior-preserving mixin extracted for AI-local navigation."""

    def build_timeline(self, segments: list, video_dur: float, voice: str) -> tuple[str, list, float]:
        total = len(segments or [])
        if total == 0:
            raise RuntimeError("Нет сегментов для озвучки.")

        # Сортировка важна для расчёта следующей реплики и использования естественных пауз.
        segments = sorted((dict(seg) for seg in segments), key=lambda seg: float(seg.get("start", 0.0)))

        self.log(f"   ⚙️ PAUSE SYNC Quality: обработка {total} сегментов...")
        self.log(
            f"   🎧 Лимит ускорения голоса: максимум x{self.speech_speed_limit:.2f}; "
            "выше — стоп-кадр видео, без обрезки слов"
        )
        ready_segments = []
        pause_plan = []
        accumulated_pause = 0.0
        self._tts_allow_gtts_fallback = False
        self._tts_fallback_reason = ""

        with self.tts_stats_lock:
            self.tts_gtts_segments = set()
            self.tts_stats = {
                "requested": total,
                "created": 0,
                "skipped": 0,
                "failed": 0,
                "cache_hits": 0,
                "edge_cache_hits": 0,
                "prepared_cache_hits": 0,
                "edge_created": 0,
                "edge_bypassed": 0,
                "edge_deferred": 0,
                "gtts_fallback": 0,
                "gtts_retries": 0,
                "gtts_prepared_cache_hits": 0,
                "recovered_on_second_pass": 0,
            }

# CODEX-PHASE TL1 PLAN_JOBS — derive per-segment timing slots
        jobs = []
        for zero_index, seg in enumerate(segments):
            i = zero_index + 1
            start = float(seg.get("start", 0.0))
            end = float(seg.get("end", 0.0))
            spoken_slot, target_slot, hard_slot = calc_quality_slot(segments, zero_index, video_dur)
            text = normalize_tts_text(seg.get("translated") or "")
            if not text:
                self.log(f"      ⏭️ [{i}] пропуск: пустой перевод")
                self._increment_tts_stat("skipped")
                continue
            jobs.append((zero_index, i, seg, text, start, end, spoken_slot, target_slot, hard_slot))

        jobs_by_index = {job[1]: job for job in jobs}

        self._problem(
            "tts_parallel_preparation_started",
            message="Начата параллельная подготовка сегментов озвучки.",
            segments=len(jobs),
            workers=TTS_PREPARE_WORKERS,
            cache_dir=str(self.tts_cache.directory),
            speed_limit=self.speech_speed_limit,
        )

# CODEX-PHASE TL2 PARALLEL_TTS — bounded parallel preparation
        prepared = {}
        preparation_failures = []
        with ThreadPoolExecutor(max_workers=TTS_PREPARE_WORKERS, thread_name_prefix="tts-prepare") as executor:
            future_by_index = {
                i: executor.submit(self._prepare_tts_segment, text, voice, i, target_slot, hard_slot)
                for _zero_index, i, _seg, text, _start, _end, _spoken_slot, target_slot, hard_slot in jobs
            }
            for completed, job in enumerate(jobs, 1):
                self._check_cancel()
                zero_index, i, seg, text, start, end, spoken_slot, target_slot, hard_slot = job
                self.set_progress(55 + int(completed / max(1, len(jobs)) * 28), f"Озвучка {completed}/{len(jobs)}...")
                try:
                    final_path, tts_dur, stats = future_by_index[i].result()
                    prepared[i] = (final_path, tts_dur, stats)
                    if not final_path:
                        preparation_failures.append(i)
                except CancelledError:
                    raise
                except Exception as exc:
                    preparation_failures.append(i)
                    prepared[i] = (None, 0.0, {})
                    self.log(f"      ⚠️ TTS-сегмент {i} не подготовлен: {compact_exception(exc)}")
                    self._problem(
                        "tts_segment_failed",
                        level="warning",
                        message="Первый проход подготовки TTS-сегмента завершился исключением; будет второй проход.",
                        tts_segment_index=i,
                        voice=voice,
                        text_length=len(text),
                        text_sha256=hashlib.sha256(text.encode("utf-8", errors="replace")).hexdigest(),
                        exception={
                            "type": type(exc).__name__,
                            "category": classify_exception(exc),
                            "message": compact_exception(exc, max_len=1000),
                            "chain": exception_chain(exc),
                        },
                    )

# CODEX-PHASE TL3 RECOVERY — sequential retry and controlled gTTS fallback
# CODEX-PHASE TL3A EDGE_RECOVERY — sequential selected-voice recovery for first-pass failures
        if preparation_failures:
            first_pass_failures = list(dict.fromkeys(preparation_failures))
            first_failed_job = jobs_by_index[first_pass_failures[0]]
            decision = self._await_selected_voice_or_fallback(
                first_failed_job[3],
                voice,
                first_failed_job[1],
            )
            fallback_allowed = decision == "gtts_allowed"
            self.log(
                f"   🔁 TTS-восстановление: повторяю {len(first_pass_failures)} сегментов "
                + (
                    "по одному через резервный gTTS..."
                    if fallback_allowed
                    else "по одному выбранным голосом Edge TTS..."
                )
            )
            self._problem(
                "tts_recovery_pass_started",
                level="warning",
                message="Начат последовательный второй проход по TTS-сегментам после сетевых сбоев.",
                failed_indices=first_pass_failures,
                edge_storm_active=self.edge_network_guard.is_active(),
                gtts_storm_active=self.gtts_network_guard.is_active(),
                fallback_allowed=fallback_allowed,
                decision=decision,
            )
            remaining_failures = []
            consecutive_recovery_failures = 0
            for recovery_no, index in enumerate(first_pass_failures, 1):
                self._check_cancel()
                job = jobs_by_index[index]
                _zero_index, i, _seg, text, _start, _end, _spoken_slot, target_slot, hard_slot = job
                self.set_progress(
                    82,
                    f"Восстановление озвучки {recovery_no}/{len(first_pass_failures)} (сегмент {i})...",
                )
                failed_this_attempt = False
                try:
                    final_path, tts_dur, stats = self._prepare_tts_segment(
                        text, voice, i, target_slot, hard_slot
                    )
                    if final_path:
                        prepared[i] = (final_path, tts_dur, stats)
                        self._increment_tts_stat("recovered_on_second_pass")
                        consecutive_recovery_failures = 0
                    else:
                        remaining_failures.append(i)
                        failed_this_attempt = True
                except CancelledError:
                    raise
                except Exception as exc:
                    remaining_failures.append(i)
                    failed_this_attempt = True
                    self._problem(
                        "tts_recovery_segment_failed",
                        level="warning",
                        message="Повторная подготовка TTS-сегмента завершилась исключением.",
                        tts_segment_index=i,
                        recovery_attempt=recovery_no,
                        exception={
                            "type": type(exc).__name__,
                            "category": classify_exception(exc),
                            "message": compact_exception(exc, max_len=1000),
                            "chain": exception_chain(exc),
                        },
                    )
                if failed_this_attempt:
                    consecutive_recovery_failures += 1
                    # NetworkStormGuard сам выполняет длительную передышку и контрольные
                    # запросы. Здесь нужна лишь короткая пауза после текущего реального
                    # сбоя, а не после каждого следующего успешного сегмента.
                    if (
                        recovery_no < len(first_pass_failures)
                        and not self.edge_network_guard.is_active()
                    ):
                        self._sleep_or_cancel(
                            progressive_retry_delay(
                                consecutive_recovery_failures,
                                base=0.5,
                                maximum=4.0,
                            )
                        )

            preparation_failures = remaining_failures

            # Edge мог ответить на контрольный запрос, но снова сорваться на следующих
            # фразах. Не запускаем ещё один многоминутный цикл: после уже выполненного
            # защитного ожидания разрешаем gTTS только для оставшихся сегментов.
# CODEX-PHASE TL3B CONTROLLED_GTTS_FALLBACK — bounded fallback only after Edge preservation/recovery
            if preparation_failures and not self._tts_allow_gtts_fallback:
                self._enable_gtts_fallback("edge_failed_after_successful_probe")
                fallback_indices = list(preparation_failures)
                remaining_failures = []
                with self.tts_stats_lock:
                    gtts_before_fallback_pass = int(self.tts_stats.get("gtts_fallback", 0))
                self.log(
                    f"   ↩️ Финальный восстановительный проход Edge-кэш/gTTS: "
                    f"{len(fallback_indices)} оставшихся сегментов..."
                )
                self._problem(
                    "tts_final_fallback_pass_started",
                    level="warning",
                    message=(
                        "После защитного ожидания Edge снова нестабилен; запущен ограниченный "
                        "проход через готовый Edge-кэш и, только при необходимости, gTTS."
                    ),
                    failed_indices=fallback_indices,
                    voice=voice,
                    fallback_reason=self._tts_fallback_reason,
                    recovery_sources=["prepared_edge_cache", "edge_cache", "gtts"],
                    voice_may_differ_possible=True,
                )
                for fallback_no, index in enumerate(fallback_indices, 1):
                    self._check_cancel()
                    job = jobs_by_index[index]
                    _zero_index, i, _seg, text, _start, _end, _spoken_slot, target_slot, hard_slot = job
                    self.set_progress(
                        83,
                        f"Резервная озвучка {fallback_no}/{len(fallback_indices)} (сегмент {i})...",
                    )
                    try:
                        final_path, tts_dur, stats = self._prepare_tts_segment(
                            text, voice, i, target_slot, hard_slot
                        )
                        if final_path:
                            prepared[i] = (final_path, tts_dur, stats)
                            self._increment_tts_stat("recovered_on_second_pass")
                        else:
                            remaining_failures.append(i)
                    except CancelledError:
                        raise
                    except Exception as exc:
                        remaining_failures.append(i)
                        self._problem(
                            "tts_final_fallback_segment_failed",
                            level="warning",
                            message="Не удалось восстановить сегмент ни из Edge-кэша, ни через резервный gTTS.",
                            tts_segment_index=i,
                            fallback_attempt=fallback_no,
                            exception={
                                "type": type(exc).__name__,
                                "category": classify_exception(exc),
                                "message": compact_exception(exc, max_len=1000),
                                "chain": exception_chain(exc),
                            },
                        )
                preparation_failures = remaining_failures
                with self.tts_stats_lock:
                    gtts_after_fallback_pass = int(self.tts_stats.get("gtts_fallback", 0))
                gtts_used_in_pass = max(0, gtts_after_fallback_pass - gtts_before_fallback_pass)
                self._problem(
                    "tts_final_fallback_pass_finished",
                    level="error" if preparation_failures else "info",
                    message="Финальный восстановительный проход Edge-кэш/gTTS завершён.",
                    requested=len(fallback_indices),
                    recovered=len(fallback_indices) - len(preparation_failures),
                    remaining_failed=len(preparation_failures),
                    failed_indices=preparation_failures,
                    gtts_segments_used=gtts_used_in_pass,
                    voice_may_differ=bool(gtts_used_in_pass),
                )

            self._problem(
                "tts_recovery_pass_finished",
                level="error" if preparation_failures else "info",
                message="Последовательный второй проход TTS завершён.",
                requested=len(first_pass_failures),
                recovered=len(first_pass_failures) - len(preparation_failures),
                remaining_failed=len(preparation_failures),
                failed_indices=preparation_failures,
            )

# CODEX-PHASE TL3C VOICE_CONSISTENCY_REPAIR — best-effort replacement of final gTTS audio
        with self.tts_stats_lock:
            final_gtts_indices = sorted(self.tts_gtts_segments)
        if final_gtts_indices and not preparation_failures:
            repair_candidates = final_gtts_indices[:TTS_FINAL_VOICE_REPAIR_MAX_SEGMENTS]
            first_index = repair_candidates[0]
            first_job = jobs_by_index.get(first_index)
            probe_ok = False
            if first_job is not None:
                self.log(
                    f"   🎙️ Проверяю Edge TTS перед выравниванием тембра "
                    f"({len(final_gtts_indices)} резервных gTTS-фраз)..."
                )
                probe_ok = self._probe_edge_voice_once(first_job[3], voice, first_index)

            repaired_to_edge = 0
            attempted_repairs = 0
            repair_stopped_reason = "probe_failed" if not probe_ok else ""
            if probe_ok:
                previous_fallback_allowed = self._tts_allow_gtts_fallback
                self._tts_allow_gtts_fallback = False
                self._problem(
                    "tts_voice_consistency_repair_started",
                    message="Edge TTS восстановился; начата финальная замена резервных gTTS-фраз выбранным голосом.",
                    candidates_total=len(final_gtts_indices),
                    candidates_limited=len(repair_candidates),
                    voice=voice,
                )
                try:
                    for repair_no, index in enumerate(repair_candidates, 1):
                        self._check_cancel()
                        job = jobs_by_index.get(index)
                        if job is None:
                            continue
                        _zero_index, i, _seg, text, _start, _end, _spoken_slot, target_slot, hard_slot = job
                        attempted_repairs += 1
                        self.set_progress(84, f"Выравнивание голоса {repair_no}/{len(repair_candidates)} (сегмент {i})...")
                        try:
                            final_path, tts_dur, stats = self._prepare_tts_segment(
                                text, voice, i, target_slot, hard_slot
                            )
                        except CancelledError:
                            raise
                        except Exception as exc:
                            repair_stopped_reason = "segment_exception"
                            self._problem(
                                "tts_voice_consistency_repair_segment_failed",
                                level="warning",
                                message="Финальная замена gTTS-фразы выбранным голосом завершилась исключением; дальнейший repair-pass остановлен.",
                                tts_segment_index=i,
                                repair_attempt=repair_no,
                                exception={
                                    "type": type(exc).__name__,
                                    "category": classify_exception(exc),
                                    "message": compact_exception(exc, max_len=1000),
                                    "chain": exception_chain(exc),
                                },
                            )
                            break
                        if final_path and stats.get("source_provider") == "edge_tts":
                            prepared[i] = (final_path, tts_dur, stats)
                            repaired_to_edge += 1
                            self._increment_tts_stat("gtts_repaired_to_edge")
                            continue
                        repair_stopped_reason = "edge_unavailable_during_repair"
                        break
                finally:
                    self._tts_allow_gtts_fallback = previous_fallback_allowed
                    self._finish_edge_voice_preservation(reason="final_voice_repair_finished")

            with self.tts_stats_lock:
                remaining_gtts = len(self.tts_gtts_segments)
                self.tts_stats["voice_repair_probe_success"] = bool(probe_ok)
                self.tts_stats["voice_repair_attempted"] = attempted_repairs
                self.tts_stats["voice_repair_remaining_gtts"] = remaining_gtts
            if repaired_to_edge:
                self.log(
                    f"   ✅ Тембр выровнен: {repaired_to_edge} резервных фраз заменено "
                    f"на {voice}; осталось gTTS: {remaining_gtts}."
                )
            elif not probe_ok:
                self.log("   ℹ️ Edge TTS всё ещё нестабилен; дополнительное ожидание не добавляю, сохраняю готовую озвучку.")
            self._problem(
                "tts_voice_consistency_repair_finished",
                level="warning" if remaining_gtts else "info",
                message="Финальная проверка однородности голоса завершена.",
                probe_success=bool(probe_ok),
                candidates_total=len(final_gtts_indices),
                attempted=attempted_repairs,
                repaired_to_edge=repaired_to_edge,
                remaining_gtts=remaining_gtts,
                stopped_reason=repair_stopped_reason,
                voice=voice,
            )

# CODEX-PHASE TL3D READY_TIMELINE — place prepared speech on timeline and derive pause deficits
        for job in jobs:
            zero_index, i, seg, _text, start, end, spoken_slot, _target_slot, hard_slot = job
            final_path, tts_dur, stats = prepared.get(i, (None, 0.0, {}))
            if not final_path:
                self._increment_tts_stat("failed")
                continue

            self._increment_tts_stat("created")
            shifted_start = start + accumulated_pause
            delay_ms = int(max(0.0, shifted_start) * 1000)
            ready_segments.append((delay_ms, final_path))

            # Сначала используется весь естественный промежуток до следующей фразы.
            # Только оставшийся дефицит превращается в стоп-кадр у конца этого промежутка.
            pause_extra = max(0.0, (tts_dur + PAUSE_FINISH_MARGIN) - hard_slot)
            if pause_extra >= MIN_INSERTED_PAUSE:
                freeze_at_original = min(float(video_dur), start + hard_slot)
                pause_plan.append({
                    "at": freeze_at_original,
                    "duration": pause_extra,
                    "segments": [i],
                    "natural_gap_used": max(0.0, hard_slot - spoken_slot),
                })
                accumulated_pause += pause_extra

            details = []
            natural_gap_used = max(0.0, hard_slot - spoken_slot)
            if natural_gap_used > 0.08:
                details.append(f"естественная пауза +{natural_gap_used:.2f}с")
            if stats.get("native_rate", 0):
                details.append(f"Edge rate +{stats['native_rate']}%")
            if abs(stats.get("tempo", 1.0) - 1.0) > 0.025:
                method = stats.get("tempo_method") or "tempo"
                details.append(f"{method} x{stats['tempo']:.2f}")
            if pause_extra >= MIN_INSERTED_PAUSE:
                details.append(f"⏸ стоп-кадр +{pause_extra:.2f}с в естественном промежутке")

            details_text = " | " + ", ".join(details) if details else ""
            self.log(
                f"      ✅ [{i}/{total}] {fmt_time(start)}→{fmt_time(end)} "
                f"слот={spoken_slot:.2f}с окно={hard_slot:.2f}с tts={tts_dur:.2f}с "
                f"speed≤x{stats.get('total_speed', 1.0):.2f}{details_text}"
            )

# CODEX-PHASE TL3E TTS_SUMMARY — publish provider/cache/recovery outcome and enforce completeness
        with self.tts_stats_lock:
            self.tts_stats["selected_voice"] = voice
            self.tts_stats["gtts_fallback_reason"] = self._tts_fallback_reason
            self.tts_stats["voice_consistency"] = (
                "mixed_gtts_fallback"
                if self.tts_stats.get("gtts_fallback", 0)
                else "selected_voice_only"
            )
            self.tts_stats["cache_schema_version"] = TTS_CACHE_SCHEMA_VERSION
            self.tts_stats["prepared_cache_schema_version"] = TTS_PREPARED_CACHE_SCHEMA_VERSION
            tts_summary = dict(self.tts_stats)
        self.log(
            "   📊 TTS: "
            f"создано {tts_summary.get('created', 0)} / "
            f"пропущено {tts_summary.get('skipped', 0)} / "
            f"ошибок {tts_summary.get('failed', 0)} / "
            f"резервный gTTS {tts_summary.get('gtts_fallback', 0)} / "
            f"из постоянного кэша {tts_summary.get('cache_hits', 0)} / "
            f"восстановлено вторым проходом {tts_summary.get('recovered_on_second_pass', 0)}"
        )
        if tts_summary.get("gtts_fallback", 0):
            self.log(
                "   ⚠️ Часть оставшихся фраз создана резервным gTTS после ожидания Edge; "
                "тембр этих фраз может отличаться от выбранного голоса."
            )
        self._problem(
            "tts_summary",
            level="error" if preparation_failures else "info",
            message="Итог подготовки TTS-сегментов.",
            **tts_summary,
            failed_indices=preparation_failures,
            workers=TTS_PREPARE_WORKERS,
            cache_dir=str(self.tts_cache.directory),
        )
        self._finish_edge_voice_preservation(
            reason="tts_stage_finished",
            notify_action="stage_finished",
        )

        if preparation_failures:
            preview = ", ".join(str(index) for index in preparation_failures[:20])
            raise RuntimeError(
                f"Не удалось создать озвучку для {len(preparation_failures)} сегментов ({preview}). "
                "Уже созданные фразы сохранены в постоянном TTS-кэше; после перезапуска работа продолжится без их повторной загрузки."
            )

        if not ready_segments:
            raise RuntimeError("Нет ни одного успешно обработанного TTS-сегмента.")

# CODEX-PHASE TL4 PAUSE_PLAN — validate/merge stop-frame insertions
        pause_count_before = len(sanitize_pause_plan(pause_plan, video_dur))
        pause_plan = merge_nearby_pause_plan(pause_plan, video_dur, segments)
        total_pause_sec = sum(float(item.get("duration", 0.0)) for item in pause_plan)
        self._problem(
            "pause_plan_optimized",
            message="Близкие стоп-кадры безопасно объединены внутри естественных промежутков.",
            before=pause_count_before,
            after=len(pause_plan),
            merged=max(0, pause_count_before - len(pause_plan)),
            total_pause_sec=round(total_pause_sec, 3),
        )
        severe_pauses = sorted(
            (item for item in pause_plan if float(item.get("duration", 0.0)) >= 2.0),
            key=lambda item: float(item.get("duration", 0.0)),
            reverse=True,
        )
        top_pause_segments = [
            {
                "segments": list(item.get("segments") or []),
                "at_sec": round(float(item.get("at", 0.0)), 3),
                "duration_sec": round(float(item.get("duration", 0.0)), 3),
            }
            for item in severe_pauses[:10]
        ]
        pause_ratio = (total_pause_sec / float(video_dur)) if float(video_dur) > 0 else 0.0
        self._problem(
            "pause_sync_quality_summary",
            message="Сводка качества Pause Sync: объём добавленных стоп-кадров и самые длинные дефициты речи.",
            pause_count=len(pause_plan),
            total_pause_sec=round(total_pause_sec, 3),
            source_video_duration_sec=round(float(video_dur), 3),
            pause_ratio=round(pause_ratio, 5),
            severe_pause_count=len(severe_pauses),
            max_pause_sec=round(max((float(item.get("duration", 0.0)) for item in pause_plan), default=0.0), 3),
            top_pause_segments=top_pause_segments,
            speed_limit=self.speech_speed_limit,
        )
        final_dur = float(video_dur) + total_pause_sec
        if pause_plan:
            self.log(f"   ⏸️ Будет добавлено стоп-кадров: {len(pause_plan)}, новая длительность: {fmt_time(final_dur)}")

        self.log(f"\n   🎛️ Смешивание {len(ready_segments)} сегментов...")
# CODEX-PHASE TL5 MIX_MASTER — mix ordered segments and master voice track
        mixed = self._mix_in_batches(ready_segments, final_dur)
        mastered = os.path.join(self.temp_dir, "final_voice_master.wav")
        master_timeout = calc_audio_work_timeout(final_dur, min_timeout=240, factor=0.60, extra=180)
        return master_voice_audio(
            self.ffmpeg,
            mixed,
            mastered,
            self.log,
            cancel_event=self.cancel,
            timeout=master_timeout,
            audio_settings=self.audio_settings,
        ), pause_plan, final_dur
