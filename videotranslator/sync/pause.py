"""Owner module for: merge_short_segments, normalize_tts_text, edge_rate_from_speed, calc_quality_slot, sanitize_pause_plan...."""

from __future__ import annotations

import os
import re
from videotranslator.config import MAX_NATIVE_TTS_RATE, MIN_INSERTED_PAUSE, MIN_SYNC_GAP, SAMPLE_RATE, TARGET_HEADROOM, TOTAL_MAX_SPEECH_SPEED
from videotranslator.media.audio import build_original_voice_mix_tail


def merge_short_segments(segments: list, min_dur: float = 1.20,
                         max_gap: float = 0.38, max_merged_dur: float = 5.8) -> list:
    """
    Объединяет короткие соседние сегменты только если между ними почти нет паузы.
    Старый вариант мог склеить короткую реплику с фразой после длинной паузы — это портило синхрон.
    """
    if not segments:
        return []

    sorted_segments = sorted((dict(seg) for seg in segments), key=lambda seg: float(seg.get("start", 0.0)))
    merged = []
    current = sorted_segments[0]

    for seg in sorted_segments[1:]:
        cur_start = float(current.get("start", 0.0))
        cur_end = float(current.get("end", cur_start))
        next_start = float(seg.get("start", cur_end))
        next_end = float(seg.get("end", next_start))
        current_duration = max(0.0, cur_end - cur_start)
        gap = max(0.0, next_start - cur_end)
        combined_duration = max(0.0, max(cur_end, next_end) - cur_start)

        should_merge = (
            current_duration < min_dur
            and gap <= max_gap
            and combined_duration <= max_merged_dur
        )

        if should_merge:
            current["text"] = (current.get("text", "").rstrip() + " " + seg.get("text", "").lstrip()).strip()
            current["end"] = max(cur_end, next_end)
        else:
            merged.append(current)
            current = seg

    merged.append(current)
    return merged


def normalize_tts_text(text: str) -> str:
    """Лёгкая чистка текста для TTS без изменения смысла."""
    text = (text or "").strip()
    text = re.sub(r"\s+", " ", text)
    text = text.replace("—", ", ").replace("–", ", ")
    text = re.sub(r"\s+([,.!?;:])", r"\1", text)
    text = re.sub(r"([,.!?;:]){3,}", r"\1", text)
    return text.strip()


def edge_rate_from_speed(speed_needed: float, speed_limit: float = TOTAL_MAX_SPEECH_SPEED) -> int:
    """
    Часть ускорения отдаём самому Edge TTS: так голос часто звучит естественнее,
    чем если потом сильно растягивать/сжимать готовый файл.
    """
    if speed_needed <= 1.04:
        return 0
    allowed_rate = min(MAX_NATIVE_TTS_RATE, max(0.0, float(speed_limit) - 1.0))
    return int(max(0, min(allowed_rate * 100, round((speed_needed - 1.0) * 100))))


def calc_quality_slot(segments: list, index: int, video_dur: float) -> tuple[float, float, float]:
    """
    Возвращает spoken_slot, target_slot, hard_slot.
    hard_slot — это реальное доступное окно до следующей оригинальной реплики
    с учётом естественной паузы. Если новая озвучка длиннее этого окна даже
    после качественного ускорения ≤1.15x, дальше будет вставлена пауза видео.
    """
    seg = segments[index]
    start = float(seg.get("start", 0.0))
    end = float(seg.get("end", start))
    spoken_slot = max(0.05, end - start)

    if index + 1 < len(segments):
        next_start = float(segments[index + 1].get("start", end))
        available_until = max(start, next_start - MIN_SYNC_GAP)
    else:
        available_until = max(end, float(video_dur or end))

    hard_slot = max(0.0, min(float(video_dur or end), available_until) - start)
    target_slot = max(0.08, hard_slot * TARGET_HEADROOM)
    return spoken_slot, target_slot, hard_slot


def sanitize_pause_plan(pause_plan: list, video_dur: float) -> list:
    """Сортирует и объединяет паузы, чтобы ffmpeg получил чистый план стоп-кадров."""
    cleaned = []
    for item in sorted(pause_plan or [], key=lambda x: float(x.get("at", 0.0))):
        at = max(0.0, min(float(video_dur or 0.0), float(item.get("at", 0.0))))
        duration = max(0.0, float(item.get("duration", 0.0)))
        if duration < MIN_INSERTED_PAUSE:
            continue
        if cleaned and abs(cleaned[-1]["at"] - at) < 0.020:
            cleaned[-1]["duration"] += duration
            cleaned[-1]["segments"].extend(item.get("segments", []))
        else:
            cleaned.append({
                "at": at,
                "duration": duration,
                "segments": list(item.get("segments", [])),
            })
    return cleaned


def merge_nearby_pause_plan(pause_plan: list, video_dur: float, segments: list,
                            max_distance: float = 0.30) -> list:
    """
    Объединяет близкие стоп-кадры только внутри одного естественного промежутка.
    Если между ними начинается/заканчивается фраза, паузы остаются раздельными,
    чтобы не сдвинуть озвучку относительно изображения.
    """
    cleaned = sanitize_pause_plan(pause_plan, video_dur)
    if len(cleaned) < 2:
        return cleaned

    speech_boundaries = sorted({
        round(float(value), 6)
        for segment in (segments or [])
        for value in (segment.get("start", 0.0), segment.get("end", 0.0))
    })
    merged = []
    for item in cleaned:
        if not merged:
            merged.append(dict(item))
            continue
        previous = merged[-1]
        left = float(previous["at"])
        right = float(item["at"])
        boundary_between = any(left + 0.001 < boundary < right - 0.001 for boundary in speech_boundaries)
        if right - left <= max_distance and not boundary_between:
            # Поздняя точка обычно ближе к началу следующей фразы и выглядит естественнее.
            previous["at"] = right
            previous["duration"] += float(item["duration"])
            previous["segments"].extend(item.get("segments", []))
        else:
            merged.append(dict(item))
    return sanitize_pause_plan(merged, video_dur)


def make_filter_script_for_pauses(temp_dir: str, pause_plan: list, video_dur: float,
                                  final_dur: float, keep_original: bool,
                                  orig_vol_pct: int, audio_stream_index: int | None = None,
                                  duck_original: bool = False, video_fps: float | None = None) -> str:
    """
    Создаёт filter_complex_script для ffmpeg.
    Видео расширяется стоп-кадрами, оригинальный фон — тишиной в местах стоп-кадров,
    новая голосовая дорожка уже заранее разложена по расширенному таймлайну.
    """
    pause_plan = sanitize_pause_plan(pause_plan, video_dur)
    original_audio = f"[0:{int(audio_stream_index)}]" if audio_stream_index is not None else "[0:a:0]"
    lines = []

    video_labels = []
    audio_labels = []
    cursor = 0.0
    chunk_index = 0
    leading_pause = 0.0

    def f(value: float) -> str:
        return f"{float(value):.6f}"

    try:
        frame_rate = float(video_fps) if video_fps is not None else 0.0
    except (TypeError, ValueError):
        frame_rate = 0.0
    if not (frame_rate > 0.0):
        frame_rate = 0.0

    def tpad_option(kind: str, duration: float) -> str:
        """Prefer exact frame counts when source FPS is known.

        This mirrors final-video validation and avoids FFmpeg builds where
        duration-based tpad on a trimmed branch fails to emit the requested clone
        frames.  The duration form remains a compatibility fallback for callers
        that do not know source FPS.
        """
        if frame_rate > 0.0:
            frames = max(1, int(float(duration) * frame_rate + 0.5))
            return f"{kind}={frames}"
        return f"{kind}_duration={f(duration)}"

    def fps_filter() -> str:
        # Recent FFmpeg releases can propagate a zero frame-rate through trim/setpts.
        # tpad then emits cloned frames with identical PTS and the encoder drops them.
        # Explicit FPS on Pause Sync branches gives tpad a stable frame duration and
        # matches the single-FPS quantization already used by final validation.
        return f"fps=fps={f(frame_rate)}:round=near," if frame_rate > 0.0 else ""

    for pause_index, pause in enumerate(pause_plan):
        at = max(cursor, min(float(video_dur), float(pause["at"])))
        pause_dur = max(0.0, float(pause["duration"]))
        if at == 0.0:
            # No preceding frames exist at zero: clone the first frame instead.
            leading_pause += pause_dur
            if keep_original:
                lines.append(
                    f"anullsrc=channel_layout=stereo:sample_rate={SAMPLE_RATE}:d={f(pause_dur)}[slead]"
                )
                audio_labels.append("[slead]")
        elif at > cursor:
            vlabel = f"v{chunk_index}"
            lines.append(
                f"[0:V:0]trim=start={f(cursor)}:end={f(at)},setpts=PTS-STARTPTS,"
                f"{fps_filter()}tpad=stop_mode=clone:{tpad_option('stop', pause_dur)}[{vlabel}]"
            )
            video_labels.append(f"[{vlabel}]")

            if keep_original:
                alabel = f"a{chunk_index}"
                slabel = f"s{chunk_index}"
                lines.append(
                    f"{original_audio}aresample={SAMPLE_RATE}:first_pts=0,atrim=start={f(cursor)}:end={f(at)},asetpts=PTS-STARTPTS,"
                    f"aresample={SAMPLE_RATE},aformat=sample_fmts=fltp:channel_layouts=stereo[{alabel}]"
                )
                lines.append(
                    f"anullsrc=channel_layout=stereo:sample_rate={SAMPLE_RATE}:d={f(pause_dur)}[{slabel}]"
                )
                audio_labels.extend([f"[{alabel}]", f"[{slabel}]"])
            chunk_index += 1
        cursor = at

    if float(video_dur) > cursor:
        vlabel = f"v{chunk_index}"
        # Keep ordinary tail chunks on their native timestamps.  Only a branch that
        # actually needs tpad is normalized to a known FPS; this avoids needlessly
        # converting the whole Pause Sync result to CFR for variable-frame-rate input.
        lines.append(
            f"[0:V:0]trim=start={f(cursor)}:end={f(video_dur)},setpts=PTS-STARTPTS[{vlabel}]"
        )
        video_labels.append(f"[{vlabel}]")
        if keep_original:
            alabel = f"a{chunk_index}"
            lines.append(
                f"{original_audio}aresample={SAMPLE_RATE}:first_pts=0,atrim=start={f(cursor)}:end={f(video_dur)},asetpts=PTS-STARTPTS,"
                f"aresample={SAMPLE_RATE},aformat=sample_fmts=fltp:channel_layouts=stereo[{alabel}]"
            )
            audio_labels.append(f"[{alabel}]")

    if not video_labels:
        # Теоретический аварийный случай: очень короткое/битое видео.
        lines.append("[0:V:0]setpts=PTS-STARTPTS[vbase]")
        video_labels = ["[vbase]"]

    if leading_pause:
        # Duration-based tpad can lose clone frames after trim/setpts on recent FFmpeg
        # when the branch carries no reliable frame rate. Normalize only this branch.
        leading_source = video_labels[0]
        if frame_rate > 0.0:
            lines.append(f"{leading_source}{fps_filter().rstrip(',')}[vlead_fps]")
            leading_source = "[vlead_fps]"
        lines.append(
            f"{leading_source}tpad=start_mode=clone:{tpad_option('start', leading_pause)}[vlead]"
        )
        video_labels[0] = "[vlead]"

    if len(video_labels) == 1:
        lines.append(f"{video_labels[0]}format=yuv420p[vout]")
    else:
        lines.append("".join(video_labels) + f"concat=n={len(video_labels)}:v=1:a=0,format=yuv420p[vout]")

    ru_chain = (
        f"[1:a]aresample={SAMPLE_RATE},aformat=sample_fmts=fltp:channel_layouts=stereo,"
        f"apad=whole_dur={f(final_dur)},atrim=duration={f(final_dur)},asetpts=PTS-STARTPTS,"
        f"alimiter=limit=0.95[ru]"
    )
    lines.append(ru_chain)

    if keep_original and audio_labels:
        if len(audio_labels) == 1:
            lines.append(f"{audio_labels[0]}volume={max(0.0, min(1.0, float(orig_vol_pct) / 100.0)):.4f}[orig_ext]")
        else:
            lines.append("".join(audio_labels) + f"concat=n={len(audio_labels)}:v=0:a=1,"
                         f"volume={max(0.0, min(1.0, float(orig_vol_pct) / 100.0)):.4f}[orig_ext]")
        lines.append(build_original_voice_mix_tail(
            "[orig_ext]", "[ru]", duck_original=duck_original, label_prefix="pause_duck"
        ))
    else:
        lines.append("[ru]anull[aout]")

    script_path = os.path.join(temp_dir, "pause_sync_filter.txt")
    with open(script_path, "w", encoding="utf-8") as file:
        file.write(";\n".join(lines))
    return script_path
