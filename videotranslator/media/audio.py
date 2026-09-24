"""Owner module for: get_audio_duration, build_atempo_filter, ffmpeg_has_filter, ffmpeg_has_encoder, calc_final_encode_timeout...."""

from __future__ import annotations

from videotranslator.core.compat_bridge import call_legacy_override

import os
import re
import shutil
import wave
from videotranslator.config import FINAL_ENCODE_MAX_TIMEOUT, FINAL_ENCODE_MIN_TIMEOUT, HEAVY_PAUSE_SYNC_COUNT, MASTER_TRUE_PEAK, MAX_EMERGENCY_TEMPO, MIN_TEMPO, SAMPLE_RATE, VOICE_LIMIT, normalize_audio_settings
from videotranslator.core.cancel import CancelledError
from videotranslator.core.diagnostics import compact_exception
from videotranslator.media.process import run_subprocess as _legacy_run_subprocess


def run_subprocess(*args, **kwargs):
    return call_legacy_override('run_subprocess', _legacy_run_subprocess, *args, **kwargs)


def get_audio_duration(ffprobe: str, path: str, log=None) -> float:
    """Возвращает длительность аудио; WAV читается напрямую без отдельного ffprobe-процесса."""
    if str(path or "").lower().endswith(".wav"):
        try:
            with wave.open(path, "rb") as wav_file:
                frame_rate = wav_file.getframerate()
                if frame_rate > 0:
                    return float(wav_file.getnframes()) / float(frame_rate)
        except (OSError, EOFError, wave.Error) as exc:
            if log:
                log(f"      ⚠️ WAV-заголовок не прочитан, используется ffprobe: {compact_exception(exc)}")

    if ffprobe:
        try:
            cmd = [
                ffprobe, "-v", "error",
                "-show_entries", "format=duration",
                "-of", "default=noprint_wrappers=1:nokey=1",
                path,
            ]
            result = run_subprocess(cmd, timeout=15, log=log)
            if result.returncode == 0 and result.stdout.strip():
                return float(result.stdout.strip())
            if log and result.stderr:
                log(f"      ⚠️ ffprobe не смог прочитать длительность: {result.stderr[-250:].strip()}")
        except Exception as exc:
            if log:
                log(f"      ⚠️ ffprobe fallback: {exc}")

    try:
        from moviepy.editor import AudioFileClip
        clip = None
        try:
            clip = AudioFileClip(path)
            return float(clip.duration or 0.0)
        finally:
            if clip:
                clip.close()
    except Exception as exc:
        if log:
            log(f"      ⚠️ moviepy не смог прочитать длительность {os.path.basename(path)}: {exc}")
        return 0.0


def build_atempo_filter(speed: float) -> str:
    """ffmpeg atempo сохраняет pitch, но принимает 0.5–2.0 за один фильтр."""
    speed = max(0.25, min(8.0, float(speed or 1.0)))
    filters = []

    while speed > 2.0:
        filters.append("atempo=2.00000")
        speed /= 2.0

    while speed < 0.5:
        filters.append("atempo=0.50000")
        speed /= 0.5

    filters.append(f"atempo={speed:.5f}")
    return ",".join(filters)


def ffmpeg_has_filter(ffmpeg: str, filter_name: str, log=None) -> bool:
    """Проверяет, есть ли в сборке ffmpeg нужный аудиофильтр."""
    cache = getattr(ffmpeg_has_filter, "_cache", {})
    key = (ffmpeg, filter_name)
    if key in cache:
        return cache[key]

    try:
        result = run_subprocess([ffmpeg, "-hide_banner", "-filters"], timeout=20, log=log)
        ok = result.returncode == 0 and re.search(rf"\b{re.escape(filter_name)}\b", result.stdout or "") is not None
    except Exception:
        ok = False

    cache[key] = ok
    setattr(ffmpeg_has_filter, "_cache", cache)
    return ok


def build_original_voice_mix_tail(original_label: str, voice_label: str, *,
                                  output_label: str = "[aout]", duck_original: bool = False,
                                  label_prefix: str = "vtduck") -> str:
    """Build the final original+voice mix, optionally ducking background under speech.

    ``sidechaincompress`` processes the original (main input) from the translated
    voice (sidechain).  The voice is split because one filter output pad cannot be
    consumed twice.  The non-ducking branch intentionally mirrors the historical
    ``amix`` graph for compatibility with minimal FFmpeg builds.
    """
    if not duck_original:
        return (
            f"{original_label}{voice_label}"
            "amix=inputs=2:duration=longest:dropout_transition=0:normalize=0,"
            f"alimiter=limit=0.95{output_label}"
        )

    prefix = re.sub(r"[^A-Za-z0-9_]", "_", str(label_prefix or "vtduck"))
    sidechain = f"[{prefix}_sc]"
    voice_mix = f"[{prefix}_voice]"
    ducked = f"[{prefix}_orig]"
    return (
        f"{voice_label}asplit=2{sidechain}{voice_mix};"
        f"{original_label}{sidechain}"
        "sidechaincompress=threshold=0.035:ratio=6:attack=12:release=250:knee=4:detection=rms"
        f"{ducked};"
        f"{ducked}{voice_mix}"
        "amix=inputs=2:duration=longest:dropout_transition=0:normalize=0,"
        f"alimiter=limit=0.95{output_label}"
    )


def ffmpeg_has_encoder(ffmpeg: str, encoder_name: str, log=None) -> bool:
    """Проверяет, есть ли в сборке ffmpeg нужный видеокодер."""
    cache = getattr(ffmpeg_has_encoder, "_cache", {})
    if ffmpeg in cache:
        return encoder_name in cache[ffmpeg]

    try:
        result = run_subprocess([ffmpeg, "-hide_banner", "-encoders"], timeout=20, log=log)
        if result.returncode != 0:
            return False
        encoders = set(re.findall(r"^\s*[VAS][A-Z.]{5}\s+([A-Za-z0-9_-]+)\s", result.stdout or "", re.MULTILINE))
        if not encoders:
            return False
    except CancelledError:
        raise
    except Exception:
        return False

    cache[ffmpeg] = encoders
    setattr(ffmpeg_has_encoder, "_cache", cache)
    return encoder_name in encoders


def calc_final_encode_timeout(final_dur: float, pause_count: int = 0) -> int:
    """
    Динамический лимит для финальной сборки.
    Для 2–3-часовых видео с сотнями стоп-кадров один час часто недостаточен,
    поэтому лимит зависит от длительности результата и сложности pause-sync.
    """
    duration = max(0.0, float(final_dur or 0.0))
    pauses = max(0, int(pause_count or 0))
    timeout = int(duration * 2.2 + pauses * 3 + 900)
    timeout = max(FINAL_ENCODE_MIN_TIMEOUT, timeout)
    return min(FINAL_ENCODE_MAX_TIMEOUT, timeout)


def calc_audio_work_timeout(duration: float, min_timeout: int = 360,
                            factor: float = 0.75, extra: int = 240) -> int:
    """Динамический лимит для ffmpeg-операций над аудио длинных роликов."""
    duration = max(0.0, float(duration or 0.0))
    timeout = int(duration * factor + extra)
    return min(FINAL_ENCODE_MAX_TIMEOUT, max(int(min_timeout), timeout))


def final_video_encoder_attempts(ffmpeg: str, pause_count: int, final_dur: float, log=None,
                                 diagnostic=None) -> list[tuple[str, list[str]]]:
    """
    Возвращает список кодеров от быстрого к запасному.
    На тяжёлом Pause Sync сначала пробуем аппаратный H.264, затем быстрый x264.
    """
    heavy = int(pause_count or 0) >= HEAVY_PAUSE_SYNC_COUNT or float(final_dur or 0.0) >= 3600
    attempts: list[tuple[str, list[str]]] = []

    if heavy:
        nvenc_available = ffmpeg_has_encoder(ffmpeg, "h264_nvenc", log=log)
        if diagnostic:
            diagnostic("video_encoder_candidate", message="Проверен аппаратный кодер.",
                       encoder_name="NVIDIA NVENC H.264", codec="h264_nvenc",
                       hardware=True, available=nvenc_available)
        if nvenc_available:
            attempts.append((
                "NVIDIA NVENC H.264",
                ["-c:v", "h264_nvenc", "-preset", "p5", "-cq", "21", "-b:v", "0", "-pix_fmt", "yuv420p"],
            ))
        amf_available = ffmpeg_has_encoder(ffmpeg, "h264_amf", log=log)
        if diagnostic:
            diagnostic("video_encoder_candidate", message="Проверен аппаратный кодер.",
                       encoder_name="AMD AMF H.264", codec="h264_amf",
                       hardware=True, available=amf_available)
        if amf_available:
            attempts.append((
                "AMD AMF H.264",
                ["-c:v", "h264_amf", "-quality", "speed", "-rc", "cqp", "-qp_i", "22", "-qp_p", "22", "-pix_fmt", "yuv420p"],
            ))
        qsv_available = ffmpeg_has_encoder(ffmpeg, "h264_qsv", log=log)
        if diagnostic:
            diagnostic("video_encoder_candidate", message="Проверен аппаратный кодер.",
                       encoder_name="Intel Quick Sync H.264", codec="h264_qsv",
                       hardware=True, available=qsv_available)
        if qsv_available:
            attempts.append((
                "Intel Quick Sync H.264",
                ["-c:v", "h264_qsv", "-preset", "veryfast", "-global_quality", "23", "-pix_fmt", "yuv420p"],
            ))
        attempts.extend([
            ("libx264 veryfast", ["-c:v", "libx264", "-preset", "veryfast", "-crf", "20", "-pix_fmt", "yuv420p"]),
            ("libx264 ultrafast", ["-c:v", "libx264", "-preset", "ultrafast", "-crf", "22", "-pix_fmt", "yuv420p"]),
        ])
    else:
        attempts.append(("libx264 quality", ["-c:v", "libx264", "-preset", "fast", "-crf", "18", "-pix_fmt", "yuv420p"]))

    attempts.append(("MPEG-4 fallback", ["-c:v", "mpeg4", "-q:v", "3", "-pix_fmt", "yuv420p"]))
    if diagnostic:
        diagnostic(
            "video_encoder_plan",
            message="Сформирован порядок попыток видеокодирования.",
            heavy_pause_sync=heavy,
            attempts=[name for name, _args in attempts],
        )
    return attempts


def ffmpeg_audio_ok(path: str, min_size: int = 200) -> bool:
    return bool(path and os.path.exists(path) and os.path.getsize(path) > min_size)


def polish_tts_audio(ffmpeg: str, src: str, dst: str, log=None, cancel_event=None,
                     timeout: int = 90, audio_settings: dict | None = None) -> bool:
    """
    Приводит TTS-сегмент к единому WAV-формату и мягко чистит служебные артефакты.
    Важно: не вырезаем агрессивно внутренние паузы, чтобы речь не звучала рвано.
    """
    settings = normalize_audio_settings(audio_settings)
    filter_parts = [
        "aresample={}".format(SAMPLE_RATE),
        "aformat=sample_fmts=fltp:channel_layouts=stereo",
    ]
    if settings["noise_reduction"] and ffmpeg_has_filter(ffmpeg, "afftdn", log=log):
        filter_parts.append("afftdn=nf=-25")
    if settings["highpass_hz"] > 0:
        filter_parts.append(f"highpass=f={settings['highpass_hz']}")
    if settings["lowpass_hz"] > 0:
        filter_parts.append(f"lowpass=f={settings['lowpass_hz']}")
    filter_parts.append("alimiter=limit={:.2f}".format(VOICE_LIMIT))
    filters = ",".join(filter_parts)
    cmd = [
        ffmpeg, "-y", "-i", src,
        "-af", filters,
        "-ar", str(SAMPLE_RATE),
        "-ac", "2",
        "-vn", dst,
    ]
    try:
        result = run_subprocess(cmd, timeout=timeout, log=log, cancel_event=cancel_event)
        ok = result.returncode == 0 and ffmpeg_audio_ok(dst)
        if not ok and log:
            log(f"      ⚠️ Ошибка подготовки TTS: {(result.stderr or '')[-350:].strip()}")
        return ok
    except CancelledError:
        raise
    except Exception as exc:
        if log:
            log(f"      ⚠️ Исключение при подготовке TTS: {exc}")
        return False


def time_stretch_audio(ffmpeg: str, src: str, dst: str, tempo: float, log=None,
                       cancel_event=None, timeout: int = 120) -> tuple[bool, str]:
    """
    Меняет tempo без изменения pitch.
    Сначала пробуем Rubber Band, если он есть в ffmpeg: обычно он чище на речи.
    Если фильтра нет или он упал — fallback на atempo, который тоже сохраняет pitch.
    """
    tempo = max(MIN_TEMPO, min(MAX_EMERGENCY_TEMPO, float(tempo or 1.0)))
    if abs(tempo - 1.0) < 0.015:
        try:
            shutil.copyfile(src, dst)
            return ffmpeg_audio_ok(dst), "copy"
        except Exception as exc:
            if log:
                log(f"      ⚠️ Не удалось скопировать аудио без tempo: {exc}")
            return False, "copy"

    common_tail = (
        f"aresample={SAMPLE_RATE},"
        "aformat=sample_fmts=fltp:channel_layouts=stereo,"
        f"alimiter=limit={VOICE_LIMIT:.2f}"
    )

    candidates = []
    if ffmpeg_has_filter(ffmpeg, "rubberband", log=log):
        candidates.append((
            "rubberband",
            f"aresample={SAMPLE_RATE},rubberband=tempo={tempo:.5f}:pitch=1.00000,{common_tail}",
        ))

    candidates.append(("atempo", f"{build_atempo_filter(tempo)},{common_tail}"))

    for method, filters in candidates:
        cmd = [
            ffmpeg, "-y", "-i", src,
            "-af", filters,
            "-ar", str(SAMPLE_RATE),
            "-ac", "2",
            "-vn", dst,
        ]
        try:
            result = run_subprocess(cmd, timeout=timeout, log=log, cancel_event=cancel_event)
            ok = result.returncode == 0 and ffmpeg_audio_ok(dst)
            if ok:
                return True, method
            if log:
                log(f"      ⚠️ {method} tempo ошибка: {(result.stderr or '')[-300:].strip()}")
        except CancelledError:
            raise
        except Exception as exc:
            if log:
                log(f"      ⚠️ {method} tempo исключение: {exc}")

    return False, "failed"


def speed_audio(ffmpeg: str, src: str, dst: str, speed: float, log=None) -> bool:
    """Совместимость со старым названием: tempo меняется без изменения pitch."""
    ok, _method = time_stretch_audio(ffmpeg, src, dst, speed, log=log)
    return ok


def trim_audio(ffmpeg: str, src: str, dst: str, duration: float, log=None) -> bool:
    """Крайний случай: мягко обрезает хвост, чтобы не наехать на следующую реплику."""
    duration = max(0.05, float(duration or 0.05))
    fade = min(0.10, max(0.03, duration * 0.18))
    cmd = [
        ffmpeg, "-y", "-i", src,
        "-t", f"{duration:.3f}",
        "-af", "afade=t=out:st={:.3f}:d={:.3f},alimiter=limit={:.2f}".format(
            max(0.0, duration - fade), fade, VOICE_LIMIT
        ),
        "-ar", str(SAMPLE_RATE),
        "-ac", "2",
        "-vn", dst,
    ]
    try:
        result = run_subprocess(cmd, timeout=60, log=log)
        ok = result.returncode == 0 and ffmpeg_audio_ok(dst)
        if not ok and log:
            log(f"      ⚠️ Ошибка обрезки ffmpeg: {(result.stderr or '')[-350:].strip()}")
        return ok
    except Exception as exc:
        if log:
            log(f"      ⚠️ Исключение при обрезке аудио: {exc}")
        return False


def master_voice_audio(ffmpeg: str, src: str, dst: str, log=None, cancel_event=None,
                       timeout: int = 240, audio_settings: dict | None = None) -> str:
    """Финальный мягкий мастеринг новой голосовой дорожки: ровная громкость + защита от клиппинга."""
    settings = normalize_audio_settings(audio_settings)
    filter_parts = []
    if settings["noise_reduction"] and ffmpeg_has_filter(ffmpeg, "afftdn", log=log):
        filter_parts.append("afftdn=nf=-25")
    if settings["highpass_hz"] > 0:
        filter_parts.append(f"highpass=f={settings['highpass_hz']}")
    if settings["lowpass_hz"] > 0:
        filter_parts.append(f"lowpass=f={settings['lowpass_hz']}")
    filter_parts.extend([
        f"loudnorm=I={settings['master_loudness_i']}:TP={MASTER_TRUE_PEAK}:LRA=11",
        f"volume={settings['voice_volume_pct'] / 100.0:.4f}",
        f"alimiter=limit={VOICE_LIMIT:.2f}",
    ])
    filters = ",".join(filter_parts)
    cmd = [
        ffmpeg, "-y", "-i", src,
        "-af", filters,
        "-ar", str(SAMPLE_RATE),
        "-ac", "2",
        "-vn", dst,
    ]
    try:
        result = run_subprocess(cmd, timeout=timeout, log=log, cancel_event=cancel_event)
        if result.returncode == 0 and ffmpeg_audio_ok(dst, min_size=500):
            return dst
        if log:
            log(f"      ⚠️ Мастеринг голоса пропущен: {(result.stderr or '')[-350:].strip()}")
    except CancelledError:
        raise
    except Exception as exc:
        if log:
            log(f"      ⚠️ Мастеринг голоса не сработал: {exc}")
    return src
