"""Owner module for: get_media_duration, extract_audio_for_whisper, output_has_video_and_audio, assemble_final_video."""

from __future__ import annotations

from videotranslator.core.compat_bridge import call_legacy_override

import math
import os
import shutil
import subprocess
import time
import tempfile
from videotranslator.config import HEAVY_PAUSE_SYNC_COUNT, SAMPLE_RATE
from videotranslator.core.cancel import CancelledError
from videotranslator.core.diagnostics import compact_exception
from videotranslator.media.audio import calc_final_encode_timeout, final_video_encoder_attempts
from videotranslator.media.process import run_subprocess as _legacy_run_subprocess
from videotranslator.sync.pause import make_filter_script_for_pauses, sanitize_pause_plan


def run_subprocess(*args, **kwargs):
    return call_legacy_override('run_subprocess', _legacy_run_subprocess, *args, **kwargs)
from videotranslator.media.probe import output_has_video_and_audio, probe_media_timing, validate_output_media


class FinalVideoSaveError(RuntimeError):
    """A completed encode must survive a failure at the destination boundary."""

    def __init__(self, candidate_path: str, cause: Exception):
        self.candidate_path = os.path.abspath(candidate_path)
        super().__init__(f"Не удалось сохранить итоговое видео: {cause}. "
                         f"Файл для восстановления: {self.candidate_path}")


def publish_video_candidate(source: str, destination: str, validate) -> None:
    """Publish without exposing an incomplete copy or replacing another file."""
    try:
        # Atomic, no overwrite, and no bulk I/O on the same filesystem.
        os.link(source, destination)
        return
    except FileExistsError:
        raise
    except OSError:
        # Cross-device or a filesystem without hard links.
        pass
    fd, staged = tempfile.mkstemp(prefix='.vt-video-', suffix='.partial.mp4',
                                  dir=os.path.dirname(os.path.abspath(destination)))
    os.close(fd)
    try:
        shutil.copyfile(source, staged)
        if os.path.getsize(source) != os.path.getsize(staged) or not validate(staged):
            raise RuntimeError('Копия видео не прошла проверку; исходный результат сохранён.')
        if os.name == 'nt':
            # Windows rename refuses an existing destination.
            os.rename(staged, destination)
        else:
            os.link(staged, destination)
    finally:
        if os.path.exists(staged):
            os.remove(staged)


def _quantize_pause_total_to_video_frames(pause_plan: list, fps: float | None) -> tuple[float, int | None]:
    """Mirror FFmpeg tpad duration→frame quantization for Pause Sync video.

    ``tpad`` converts every start/stop duration into an integer number of frames.
    With many independent pauses, comparing the encoded video against the raw sum of
    floating-point pause seconds accumulates a false duration mismatch.  For positive
    durations FFmpeg's av_rescale_q nearest rounding is equivalent to floor(x + 0.5).
    """
    try:
        frame_rate = float(fps) if fps is not None else 0.0
    except (TypeError, ValueError):
        frame_rate = 0.0
    if not math.isfinite(frame_rate) or frame_rate <= 0.0:
        return sum(max(0.0, float(item.get("duration", 0.0))) for item in pause_plan), None

    total_frames = 0
    for item in pause_plan:
        duration = max(0.0, float(item.get("duration", 0.0)))
        total_frames += int(math.floor(duration * frame_rate + 0.5))
    return total_frames / frame_rate, total_frames


def assemble_final_video(ffmpeg: str, ffprobe: str, input_video: str, russian_audio: str,
                         output_path: str, temp_dir: str, video_dur: float,
                         keep_original: bool, orig_vol_pct: int, log=None,
                         pause_plan: list | None = None, final_dur: float | None = None,
                         cancel_event=None, diagnostic=None,
                         audio_stream_index: int | None = None) -> str:
    """
    Финальная сборка только через ffmpeg.
    Важно: MoviePy write_videofile здесь не используется, поэтому не появляются
    *_TEMP_MPY_wvf_snd.mp4 аудио-файлы, которые пользователь мог принять за результат.

    Исправление: для длинных роликов и сотен Pause Sync-вставок больше не используется
    жёсткий timeout=3600. Программа выбирает быстрый/аппаратный H.264-кодер,
    считает динамический лимит и не тратит ещё один час на заведомо медленный fallback.
    """
    pause_plan = sanitize_pause_plan(pause_plan or [], video_dur)
    total_pause = sum(float(p.get("duration", 0.0)) for p in pause_plan)
    final_dur = float(final_dur or (video_dur + total_pause))
    encode_timeout = calc_final_encode_timeout(final_dur, len(pause_plan))
    original_audio = f"[0:{int(audio_stream_index)}]" if audio_stream_index is not None else "[0:a:0]"

    source_timing = probe_media_timing(ffprobe, input_video, log=log)
    source_video = source_timing.get("video") or {}
    source_video_duration = source_video.get("duration")
    source_video_fps = source_video.get("fps")
    effective_video_pause, effective_pause_frames = _quantize_pause_total_to_video_frames(
        pause_plan, source_video_fps
    )
    if source_video_duration is not None:
        source_video_duration = max(0.0, min(float(source_video_duration), float(video_dur)))
        # Pause Sync uses FFmpeg tpad once per pause. tpad rounds every duration to an
        # integer frame count, so the encoded video is intentionally shorter/longer than
        # the raw floating-point pause sum by up to half a frame per pause.
        expected_video_duration = source_video_duration + effective_video_pause
    else:
        expected_video_duration = final_dur
    expected_audio_duration = final_dur

    def diag(event: str, level: str = "info", message: str = "", **details):
        if diagnostic:
            try:
                diagnostic(event, level=level, message=message, **details)
            except Exception:
                pass

    def remove_if_exists(path: str):
        if path and os.path.exists(path):
            try:
                os.remove(path)
            except Exception:
                pass

    def validate_candidate(path: str, *, encoder_name: str = "", return_code: int | None = None):
        # Keep the historical bool validation hook patchable for tests/legacy callers.
        valid = output_has_video_and_audio(
            ffprobe, path, log=log,
            expected_video_duration=expected_video_duration,
            expected_audio_duration=expected_audio_duration,
        )
        if valid:
            validation = {
                "valid": True,
                "reason": "ok",
                "expected_video_duration": expected_video_duration,
                "expected_audio_duration": expected_audio_duration,
            }
        else:
            # On failure, collect a second, detailed snapshot so diagnostics explain the exact rule.
            _, validation = validate_output_media(
                ffprobe, path, log=None,
                expected_video_duration=expected_video_duration,
                expected_audio_duration=expected_audio_duration,
            )
        if not valid:
            diag(
                "video_output_validation_failed",
                level="warning",
                message="FFmpeg завершил попытку, но созданный MP4 не прошёл проверку A/V-потоков.",
                encoder_name=encoder_name,
                return_code=return_code,
                ffmpeg_completed_ok=(return_code == 0),
                expected_video_duration_sec=round(expected_video_duration, 3),
                expected_audio_duration_sec=round(expected_audio_duration, 3),
                source_video_fps=round(float(source_video_fps), 6) if source_video_fps else None,
                pause_count=len(pause_plan),
                raw_pause_total_sec=round(total_pause, 3),
                effective_video_pause_sec=round(effective_video_pause, 3),
                pause_frame_quantization_delta_sec=round(effective_video_pause - total_pause, 3),
                validation=validation,
            )
        else:
            diag(
                "video_output_validation_passed",
                message="Созданный MP4 прошёл проверку видео, аудио и длительности потоков.",
                encoder_name=encoder_name,
                return_code=return_code,
                expected_video_duration_sec=round(expected_video_duration, 3),
                expected_audio_duration_sec=round(expected_audio_duration, 3),
                source_video_fps=round(float(source_video_fps), 6) if source_video_fps else None,
                pause_count=len(pause_plan),
                raw_pause_total_sec=round(total_pause, 3),
                effective_video_pause_sec=round(effective_video_pause, 3),
                pause_frame_quantization_delta_sec=round(effective_video_pause - total_pause, 3),
                validation=validation,
            )
        return valid, validation

    diag(
        "final_video_timing_expectation",
        message="Рассчитаны отдельные ожидания длительности видео и аудио для финальной проверки.",
        source_media_duration_sec=round(float(video_dur), 3),
        source_timing=source_timing,
        source_av_duration_delta_sec=(
            round(float((source_timing.get("audio") or {}).get("duration")) - float(source_video_duration), 3)
            if source_video_duration is not None and (source_timing.get("audio") or {}).get("duration") is not None
            else None
        ),
        pause_total_sec=round(total_pause, 3),
        source_video_fps=round(float(source_video_fps), 6) if source_video_fps else None,
        effective_video_pause_sec=round(effective_video_pause, 3),
        effective_pause_frames=effective_pause_frames,
        pause_frame_quantization_delta_sec=round(effective_video_pause - total_pause, 3),
        expected_video_duration_sec=round(expected_video_duration, 3),
        expected_audio_duration_sec=round(expected_audio_duration, 3),
    )
    if log and effective_pause_frames is not None and abs(effective_video_pause - total_pause) > 0.04:
        log(
            "   ℹ️ Pause Sync учитывает кадровое округление FFmpeg: "
            f"{len(pause_plan)} пауз = {effective_pause_frames} кадров при {float(source_video_fps):.3f} fps; "
            f"для видео реально добавится ≈{effective_video_pause:.3f}с вместо {total_pause:.3f}с "
            f"(разница {effective_video_pause - total_pause:+.3f}с)."
        )
    if log and abs(expected_audio_duration - expected_video_duration) > 0.12:
        log(
            "   ℹ️ Для финальных A/V-потоков ожидается разная длительность: "
            f"video≈{expected_video_duration:.3f}с, audio≈{expected_audio_duration:.3f}с. "
            "Учитываются исходный A/V-разбег и кадровое округление Pause Sync."
        )

    def move_checked_result(build_path: str) -> str:
        # Every caller has already validated the encoded candidate.
        try:
            os.makedirs(os.path.dirname(output_path) or ".", exist_ok=True)
            if os.path.exists(output_path):
                raise FileExistsError(
                    f"Итоговый путь появился во время обработки и не будет перезаписан: {output_path}"
                )
            publish_video_candidate(build_path, output_path, lambda path:
                output_has_video_and_audio(
                    ffprobe, path, log=log,
                    expected_video_duration=expected_video_duration,
                    expected_audio_duration=expected_audio_duration,
                ))

            if not output_has_video_and_audio(
                ffprobe, output_path, log=log,
                expected_video_duration=expected_video_duration,
                expected_audio_duration=expected_audio_duration,
            ):
                raise RuntimeError("Файл перенесён, но проверка video+audio не прошла.")
            remove_if_exists(build_path)
            return output_path
        except Exception as exc:
            candidate = build_path if os.path.exists(build_path) else output_path
            raise FinalVideoSaveError(candidate, exc) from exc

    # Если есть вставленные паузы, собираем расширенное видео через filter_complex_script.
    # Это предотвращает длинную командную строку на Windows и гарантирует, что на выходе будет именно видео.
# CODEX-PHASE FM1 PAUSE_SYNC_ENCODE — filter script + encoder fallback for extended timeline
# CODEX-PHASE FM1A FILTER_BUILD — build extended video/audio filter script for stop-frame timeline
    if pause_plan:
        if log:
            log(f"   ⏸️ Pause Sync: вставляем {len(pause_plan)} стоп-кадр(ов), +{total_pause:.2f}с к видео")
            if len(pause_plan) >= HEAVY_PAUSE_SYNC_COUNT:
                log("      ℹ️ Много стоп-кадров: включён ускоренный режим финального кодирования")

        filter_script = make_filter_script_for_pauses(temp_dir, pause_plan, video_dur, final_dur, keep_original, orig_vol_pct,
                                                     audio_stream_index=audio_stream_index)
        duration_args = ["-t", f"{final_dur:.3f}"]
        base_cmd = [
            ffmpeg, "-y",
            "-i", input_video,
            "-i", russian_audio,
            "-filter_complex_script", filter_script,
            "-map", "[vout]",
            "-map", "[aout]",
            "-map_metadata", "0",
        ]
        audio_args = [
            "-c:a", "aac",
            "-b:a", "192k",
            "-ar", str(SAMPLE_RATE),
            "-ac", "2",
            "-movflags", "+faststart",
        ]

        last_error = ""
        if log:
            log("   🎞️ Финальный MP4 собирается через ffmpeg с вставкой стоп-кадров...")
            log(f"      ⏱️ Лимит финальной сборки: {encode_timeout}с")

# CODEX-PHASE FM1B ENCODER_ATTEMPTS — validated hardware/software encoder attempts for pause timeline
        encoder_attempts = final_video_encoder_attempts(
            ffmpeg, len(pause_plan), final_dur, log=log, diagnostic=diagnostic
        )
        for attempt_index, (encoder_name, video_args) in enumerate(encoder_attempts, 1):
            build_path = os.path.join(temp_dir, f"final_build_pause_{attempt_index:02d}.mp4")
            remove_if_exists(build_path)
            cmd = base_cmd + video_args + audio_args + duration_args + [build_path]
            attempt_started_at = time.monotonic()
            try:
                if log:
                    log(f"      ▶️ Кодер {attempt_index}: {encoder_name}")
                diag(
                    "video_encoder_attempt_started",
                    message="Запущена попытка финального видеокодирования.",
                    encoder_name=encoder_name,
                    attempt=attempt_index,
                    attempts_total=len(encoder_attempts),
                    hardware=any(name in encoder_name for name in ("NVIDIA", "AMD", "Intel")),
                    timeout_sec=encode_timeout,
                    command=cmd,
                )
                result = run_subprocess(
                    cmd,
                    timeout=encode_timeout,
                    log=log,
                    cancel_event=cancel_event,
                    heartbeat_cb=diagnostic,
                )
                validation = None
                if result.returncode == 0:
                    valid, validation = validate_candidate(
                        build_path, encoder_name=encoder_name, return_code=result.returncode
                    )
                    if valid:
                        elapsed = max(0.001, time.monotonic() - attempt_started_at)
                        diag(
                            "video_encoder_selected",
                            message="Финальное видео успешно закодировано.",
                            encoder_name=encoder_name,
                            command=cmd,
                            elapsed_sec=round(elapsed, 3),
                            processing_speed_x=round(final_dur / elapsed, 3),
                            output_size=os.path.getsize(build_path),
                        )
                        return move_checked_result(build_path)
                    last_error = (
                        f"{encoder_name}: ffmpeg code 0, но MP4 не прошёл проверку: "
                        f"{validation.get('reason', 'unknown_validation_error')}"
                    )
                else:
                    last_error = f"{encoder_name}: ffmpeg code {result.returncode}: {(result.stderr or '')[-1200:].strip()}"
                    diag(
                        "video_encoder_attempt_failed",
                        level="warning",
                        message="FFmpeg завершил попытку видеокодирования с ненулевым кодом.",
                        encoder_name=encoder_name,
                        command=cmd,
                        elapsed_sec=round(time.monotonic() - attempt_started_at, 3),
                        return_code=result.returncode,
                        stderr_tail=(result.stderr or "")[-2000:],
                    )
                if log:
                    log(f"      ⚠️ {encoder_name} не принят: {last_error[-700:]}")
            except subprocess.TimeoutExpired:
                last_error = f"{encoder_name}: timeout {encode_timeout}с"
                diag(
                    "video_encoder_attempt_failed",
                    level="warning",
                    message="Видеокодирование превысило установленный лимит времени.",
                    encoder_name=encoder_name,
                    command=cmd,
                    elapsed_sec=round(time.monotonic() - attempt_started_at, 3),
                    timeout_sec=encode_timeout,
                )
                if log:
                    log(f"      ⚠️ {last_error}")
                # После timeout не запускаем ещё один час такой же тяжёлой работы на MPEG-4.
                # Следующий вариант пробуем только если это аппаратный кодер, который упал быстро.
                break
            except (CancelledError, FinalVideoSaveError):
                raise
            except Exception as exc:
                last_error = f"{encoder_name}: {exc}"
                diag(
                    "video_encoder_attempt_failed",
                    level="warning",
                    message="Попытка видеокодирования завершилась исключением.",
                    encoder_name=encoder_name,
                    command=cmd,
                    elapsed_sec=round(time.monotonic() - attempt_started_at, 3),
                    exception={"type": type(exc).__name__, "message": compact_exception(exc)},
                )
                if log:
                    log(f"      ⚠️ {last_error}")

        raise RuntimeError("Финальная сборка MP4 с паузами не удалась: " + (last_error or "неизвестная ошибка ffmpeg"))

    duration_args = []
    if final_dur and final_dur > 0:
        duration_args = ["-t", f"{final_dur:.3f}"]

# CODEX-PHASE FM2 AUDIO_GRAPH — mix translated voice with optional original background
    if keep_original:
        original_volume = max(0.0, min(1.0, float(orig_vol_pct) / 100.0))
        filter_complex = (
            f"{original_audio}volume={original_volume:.4f},"
            f"aresample={SAMPLE_RATE}:first_pts=0,aformat=sample_fmts=fltp:channel_layouts=stereo[orig];"
            f"[1:a]aresample={SAMPLE_RATE},aformat=sample_fmts=fltp:channel_layouts=stereo,"
            f"alimiter=limit=0.95[ru];"
            f"[orig][ru]amix=inputs=2:duration=longest:dropout_transition=0:normalize=0,"
            f"alimiter=limit=0.95[aout]"
        )
    else:
        filter_complex = (
            f"[1:a]aresample={SAMPLE_RATE},aformat=sample_fmts=fltp:channel_layouts=stereo,"
            f"alimiter=limit=0.95[aout]"
        )

    # Без stop-frame пауз видеоряд не меняется, поэтому сначала пробуем быстрый stream copy.
# CODEX-PHASE FM3 FAST_COPY — try stream-copy when video timeline is unchanged
    copy_path = os.path.join(temp_dir, "final_build_copy.mp4")
    remove_if_exists(copy_path)
    copy_cmd = [
        ffmpeg, "-y",
        "-i", input_video,
        "-i", russian_audio,
        "-filter_complex", filter_complex,
        "-map", "0:V:0",
        "-map", "[aout]",
        "-map_metadata", "0",
        "-c:v", "copy",
        "-c:a", "aac",
        "-b:a", "192k",
        "-ar", str(SAMPLE_RATE),
        "-ac", "2",
        "-movflags", "+faststart",
    ] + duration_args + [copy_path]

    if log:
        log("   🎞️ Финальный MP4 собирается через ffmpeg, без MoviePy TEMP_MPY файлов...")
        log("      ▶️ Сначала пробуем быстро скопировать видеоряд без перекодирования")

    try:
        copy_timeout = max(600, min(encode_timeout, int(final_dur * 0.35 + 600)))
        copy_started_at = time.monotonic()
        diag(
            "video_encoder_attempt_started",
            message="Запущена попытка копирования видеопотока без перекодирования.",
            encoder_name="stream copy",
            attempt=1,
            attempts_total=1,
            hardware=False,
            timeout_sec=copy_timeout,
            command=copy_cmd,
        )
        result = run_subprocess(copy_cmd, timeout=copy_timeout, log=log, cancel_event=cancel_event)
        copy_valid = False
        if result.returncode == 0:
            copy_valid, _ = validate_candidate(copy_path, encoder_name="stream copy", return_code=result.returncode)
        if result.returncode == 0 and copy_valid:
            elapsed = max(0.001, time.monotonic() - copy_started_at)
            diag(
                "video_encoder_selected",
                message="Видеоряд успешно скопирован без перекодирования.",
                encoder_name="stream copy",
                command=copy_cmd,
                elapsed_sec=round(elapsed, 3),
                processing_speed_x=round(final_dur / elapsed, 3),
                output_size=os.path.getsize(copy_path),
            )
            return move_checked_result(copy_path)
        if result.returncode != 0:
            diag(
                "video_encoder_attempt_failed",
                level="warning",
                message="Копирование видеопотока завершилось с ненулевым кодом; будет перекодирование.",
                encoder_name="stream copy",
                command=copy_cmd,
                elapsed_sec=round(time.monotonic() - copy_started_at, 3),
                return_code=result.returncode,
                stderr_tail=(result.stderr or "")[-2000:],
            )
        if log:
            reason = (result.stderr or "")[-600:].strip() if result.returncode != 0 else "созданный MP4 не прошёл проверку"
            log(f"      ⚠️ stream copy не принят, будет перекодирование: {reason}")
    except (CancelledError, FinalVideoSaveError):
        raise
    except Exception as exc:
        diag(
            "video_encoder_attempt_failed",
            level="warning",
            message="Копирование видеопотока завершилось исключением; будет перекодирование.",
            encoder_name="stream copy",
            command=copy_cmd,
            exception={"type": type(exc).__name__, "message": compact_exception(exc)},
        )
        if log:
            log(f"      ⚠️ stream copy не сработал, будет перекодирование: {exc}")

    # CODEX-PHASE FM4 REENCODE_FALLBACK — validated H.264 encoder attempts
    base_cmd = [
        ffmpeg, "-y",
        "-i", input_video,
        "-i", russian_audio,
        "-filter_complex", filter_complex,
        "-map", "0:V:0",
        "-map", "[aout]",
        "-map_metadata", "0",
    ]
    audio_args = [
        "-c:a", "aac",
        "-b:a", "192k",
        "-ar", str(SAMPLE_RATE),
        "-ac", "2",
        "-movflags", "+faststart",
    ]

    last_error = ""
    encoder_attempts = final_video_encoder_attempts(
        ffmpeg, 0, final_dur, log=log, diagnostic=diagnostic
    )
    for attempt_index, (encoder_name, video_args) in enumerate(encoder_attempts, 1):
        build_path = os.path.join(temp_dir, f"final_build_reencode_{attempt_index:02d}.mp4")
        remove_if_exists(build_path)
        cmd = base_cmd + video_args + audio_args + duration_args + [build_path]
        attempt_started_at = time.monotonic()
        try:
            if log:
                log(f"      ▶️ Кодер {attempt_index}: {encoder_name}")
            diag(
                "video_encoder_attempt_started",
                message="Запущена попытка финального видеокодирования.",
                encoder_name=encoder_name,
                attempt=attempt_index,
                attempts_total=len(encoder_attempts),
                hardware=any(name in encoder_name for name in ("NVIDIA", "AMD", "Intel")),
                timeout_sec=encode_timeout,
                command=cmd,
            )
            result = run_subprocess(
                cmd,
                timeout=encode_timeout,
                log=log,
                cancel_event=cancel_event,
                heartbeat_cb=diagnostic,
            )
            reencode_valid = False
            if result.returncode == 0:
                reencode_valid, _ = validate_candidate(build_path, encoder_name=encoder_name, return_code=result.returncode)
            if result.returncode == 0 and reencode_valid:
                elapsed = max(0.001, time.monotonic() - attempt_started_at)
                diag(
                    "video_encoder_selected",
                    message="Финальное видео успешно закодировано.",
                    encoder_name=encoder_name,
                    command=cmd,
                    elapsed_sec=round(elapsed, 3),
                    processing_speed_x=round(final_dur / elapsed, 3),
                    output_size=os.path.getsize(build_path),
                )
                return move_checked_result(build_path)
            if result.returncode == 0:
                last_error = f"{encoder_name}: ffmpeg code 0, но MP4 не прошёл проверку"
            else:
                last_error = f"{encoder_name}: ffmpeg code {result.returncode}: {(result.stderr or '')[-1200:].strip()}"
                diag(
                    "video_encoder_attempt_failed",
                    level="warning",
                    message="FFmpeg завершил попытку видеокодирования с ненулевым кодом.",
                    encoder_name=encoder_name,
                    command=cmd,
                    elapsed_sec=round(time.monotonic() - attempt_started_at, 3),
                    return_code=result.returncode,
                    stderr_tail=(result.stderr or "")[-2000:],
                )
            if log:
                log(f"      ⚠️ {encoder_name} не принят: {last_error[-700:]}")
        except subprocess.TimeoutExpired:
            last_error = f"{encoder_name}: timeout {encode_timeout}с"
            diag(
                "video_encoder_attempt_failed",
                level="warning",
                message="Видеокодирование превысило установленный лимит времени.",
                encoder_name=encoder_name,
                command=cmd,
                elapsed_sec=round(time.monotonic() - attempt_started_at, 3),
                timeout_sec=encode_timeout,
            )
            if log:
                log(f"      ⚠️ {last_error}")
            break
        except (CancelledError, FinalVideoSaveError):
            raise
        except Exception as exc:
            last_error = f"{encoder_name}: {exc}"
            diag(
                "video_encoder_attempt_failed",
                level="warning",
                message="Попытка видеокодирования завершилась исключением.",
                encoder_name=encoder_name,
                command=cmd,
                elapsed_sec=round(time.monotonic() - attempt_started_at, 3),
                exception={"type": type(exc).__name__, "message": compact_exception(exc)},
            )
            if log:
                log(f"      ⚠️ {last_error}")

    raise RuntimeError("Финальная сборка MP4 не удалась: " + (last_error or "неизвестная ошибка ffmpeg"))
