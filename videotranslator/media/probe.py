"""Owner module for: get_media_duration, extract_audio_for_whisper, output_has_video_and_audio, assemble_final_video."""

from __future__ import annotations

from videotranslator.core.compat_bridge import call_legacy_override

import json
import math
import os
from fractions import Fraction
from videotranslator.core.cancel import CancelledError
from videotranslator.media.process import run_subprocess as _legacy_run_subprocess


def run_subprocess(*args, **kwargs):
    return call_legacy_override('run_subprocess', _legacy_run_subprocess, *args, **kwargs)

def get_media_duration(ffprobe: str, path: str, log=None) -> float:
    """Длительность видео/медиа через ffprobe. Fallback — moviepy."""
    if ffprobe:
        try:
            cmd = [
                ffprobe, "-v", "error",
                "-show_entries", "format=duration",
                "-of", "default=noprint_wrappers=1:nokey=1",
                path,
            ]
            result = run_subprocess(cmd, timeout=20, log=log)
            if result.returncode == 0 and result.stdout.strip():
                return float(result.stdout.strip())
            if log and result.stderr:
                log(f"      ⚠️ ffprobe не смог определить длительность видео: {result.stderr[-350:].strip()}")
        except Exception as exc:
            if log:
                log(f"      ⚠️ ffprobe duration fallback: {exc}")

    try:
        from moviepy.editor import VideoFileClip
        clip = None
        try:
            clip = VideoFileClip(path, audio=False)
            return float(clip.duration or 0.0)
        finally:
            if clip:
                clip.close()
    except Exception as exc:
        if log:
            log(f"      ⚠️ Не удалось определить длительность видео: {exc}")
        return 0.0

def select_audio_stream(ffprobe: str, input_path: str, log=None, cancel_event=None) -> int:
    """Use the first default audio stream, otherwise the first audio stream."""
    if cancel_event is not None and cancel_event.is_set():
        raise CancelledError()
    result = run_subprocess([
        ffprobe, "-v", "error", "-select_streams", "a", "-show_entries",
        "stream=index:stream_disposition=default", "-of", "json", input_path,
    ], timeout=20, log=log, cancel_event=cancel_event)
    if result.returncode != 0:
        raise RuntimeError("Не удалось определить исходную аудиодорожку.")
    streams = json.loads(result.stdout or "{}").get("streams", [])
    if not streams:
        raise RuntimeError("В исходном видео нет аудиодорожки.")
    selected = next((s for s in streams if s.get("disposition", {}).get("default")), streams[0])
    index = int(selected["index"])
    if index < 0:
        raise ValueError("Некорректный номер исходной аудиодорожки")
    if log:
        log(f"   🔊 Исходная аудиодорожка: поток #{index} (основная, иначе первая)")
    return index


def extract_audio_for_whisper(ffmpeg: str, input_path: str, wav_path: str, log=None,
                              cancel_event=None, timeout: int = 600,
                              audio_stream_index: int | None = None) -> bool:
    """
    Извлекает аудио для Whisper напрямую через ffmpeg.
    Это надёжнее, чем MoviePy, и не создаёт TEMP_MPY_* файлы рядом с программой.
    """
    cmd = [
        ffmpeg, "-y",
        "-i", input_path,
        *(["-map", f"0:{int(audio_stream_index)}"] if audio_stream_index is not None else []),
        "-vn",
        "-af", "aresample=16000:first_pts=0",
        "-ac", "1",
        "-ar", "16000",
        "-acodec", "pcm_s16le",
        wav_path,
    ]
    try:
        result = run_subprocess(cmd, timeout=timeout, log=log, cancel_event=cancel_event)
        ok = result.returncode == 0 and os.path.exists(wav_path) and os.path.getsize(wav_path) > 1000
        if not ok and log:
            log(f"      ⚠️ ffmpeg не смог извлечь аудио: {(result.stderr or '')[-800:].strip()}")
        return ok
    except CancelledError:
        raise
    except Exception as exc:
        if log:
            log(f"      ⚠️ Исключение при извлечении аудио: {exc}")
        return False

def _finite_float(value, default=None):
    try:
        number = float(value)
    except (TypeError, ValueError):
        return default
    return number if math.isfinite(number) else default


def probe_media_timing(ffprobe: str, path: str, log=None) -> dict:
    """Возвращает длительности контейнера и основных A/V-потоков для диагностики/валидации."""
    details = {
        "path": os.path.abspath(path),
        "format_duration": None,
        "video": None,
        "audio": None,
        "streams_count": 0,
    }
    if not ffprobe or not os.path.exists(path):
        return details
    try:
        cmd = [
            ffprobe, "-v", "error",
            "-show_entries",
            "format=duration:stream=index,codec_type,codec_name,duration,start_time,avg_frame_rate:stream_disposition=attached_pic,default",
            "-of", "json",
            path,
        ]
        result = run_subprocess(cmd, timeout=20, log=log)
        details["ffprobe_returncode"] = result.returncode
        if result.returncode != 0:
            details["ffprobe_stderr_tail"] = (result.stderr or "")[-1200:].strip()
            return details
        data = json.loads(result.stdout or "{}")
        details["format_duration"] = _finite_float((data.get("format") or {}).get("duration"))
        streams = data.get("streams", []) or []
        details["streams_count"] = len(streams)
        for kind in ("video", "audio"):
            candidates = [
                stream for stream in streams
                if stream.get("codec_type") == kind
                and (kind != "video" or not (stream.get("disposition") or {}).get("attached_pic"))
            ]
            if not candidates:
                continue
            stream = next((s for s in candidates if (s.get("disposition") or {}).get("default")), candidates[0])
            start = _finite_float(stream.get("start_time"), 0.0)
            duration = _finite_float(stream.get("duration"))
            fps = None
            if kind == "video":
                try:
                    parsed = float(Fraction(stream.get("avg_frame_rate", "0/1")))
                    fps = parsed if math.isfinite(parsed) and parsed > 0 else None
                except (ValueError, ZeroDivisionError):
                    fps = None
            details[kind] = {
                "index": stream.get("index"),
                "codec": stream.get("codec_name") or "",
                "start_time": start,
                "duration": duration,
                "end_time": (start + duration) if duration is not None and start is not None else None,
                "fps": fps,
            }
        return details
    except Exception as exc:
        details["probe_exception"] = f"{type(exc).__name__}: {exc}"
        if log:
            log(f"      ⚠️ Не удалось прочитать временные характеристики медиа: {exc}")
        return details


def validate_output_media(ffprobe: str, path: str, log=None,
                          expected_duration: float | None = None,
                          expected_video_duration: float | None = None,
                          expected_audio_duration: float | None = None) -> tuple[bool, dict]:
    """Строго проверяет итоговый MP4 и возвращает машинно-читаемое объяснение решения."""
    details = {
        "path": os.path.abspath(path),
        "exists": os.path.exists(path),
        "size": os.path.getsize(path) if os.path.exists(path) else 0,
        "valid": False,
        "reason": "",
        "checks": [],
    }
    if not ffprobe:
        details["reason"] = "ffprobe_missing"
        return False, details
    if not details["exists"] or details["size"] < 1000:
        details["reason"] = "output_missing_or_too_small"
        return False, details

    timing = probe_media_timing(ffprobe, path, log=log)
    details["timing"] = timing
    video = timing.get("video")
    audio = timing.get("audio")
    if not video or not audio:
        details["reason"] = "missing_video_or_audio_stream"
        return False, details

    shared_expected = _finite_float(expected_duration)
    expected_map = {
        "video": _finite_float(expected_video_duration, shared_expected),
        "audio": _finite_float(expected_audio_duration, shared_expected),
    }
    for kind, stream in (("video", video), ("audio", audio)):
        expected = expected_map[kind]
        start = _finite_float(stream.get("start_time"))
        duration = _finite_float(stream.get("duration"))
        tolerance = 0.12
        if kind == "video" and stream.get("fps"):
            tolerance = max(tolerance, 1.0 / float(stream["fps"]))
        check = {
            "stream": kind,
            "expected_duration": expected,
            "actual_start": start,
            "actual_duration": duration,
            "tolerance": tolerance,
            "ok": True,
        }
        if expected is not None:
            if expected <= 0:
                check["ok"] = False
                check["failure"] = "invalid_expected_duration"
            elif duration is None or duration <= 0 or start is None:
                check["ok"] = False
                check["failure"] = "invalid_stream_timing"
            else:
                endpoint = start + duration
                check["actual_end"] = endpoint
                check["end_delta_sec"] = endpoint - expected
                if abs(start) > tolerance:
                    check["ok"] = False
                    check["failure"] = "unexpected_start_time"
                elif abs(endpoint - expected) > tolerance:
                    check["ok"] = False
                    check["failure"] = "duration_mismatch"
        details["checks"].append(check)
        if not check["ok"]:
            details["reason"] = f"{kind}_{check.get('failure', 'validation_failed')}"
            if log:
                actual = "N/A" if duration is None else f"{duration:.3f}"
                exp = "N/A" if expected is None else f"{expected:.3f}"
                delta = check.get("end_delta_sec")
                delta_text = "N/A" if delta is None else f"{delta:+.3f}"
                log(
                    f"      ⚠️ Проверка результата ({kind}) не пройдена: "
                    f"start={start}, duration={actual}с, expected={exp}с, "
                    f"delta={delta_text}с, tolerance={tolerance:.3f}с, "
                    f"reason={check.get('failure')}."
                )
            return False, details

    details["valid"] = True
    details["reason"] = "ok"
    return True, details


def output_has_video_and_audio(ffprobe: str, path: str, log=None,
                               expected_duration: float | None = None,
                               expected_video_duration: float | None = None,
                               expected_audio_duration: float | None = None) -> bool:
    """Совместимый bool-wrapper над подробной проверкой итогового MP4."""
    valid, _ = validate_output_media(
        ffprobe, path, log=log, expected_duration=expected_duration,
        expected_video_duration=expected_video_duration,
        expected_audio_duration=expected_audio_duration,
    )
    return valid
