"""Owner module for: make_unique_output_path, write_translation_report, write_translated_text_file."""

from __future__ import annotations

from pathlib import Path
from datetime import datetime
import os
from videotranslator.config import DEFAULT_TARGET_LANGUAGE, get_target_language
from videotranslator.core.diagnostics import safe_log_filename
from videotranslator.core.io import atomic_write_text
from videotranslator.core.paths import get_translated_texts_dir
from videotranslator.core.timefmt import fmt_time


def make_unique_output_path(folder: str, base_name: str, suffix: str = "_TR", ext: str = ".mp4") -> str:
    """Не даёт случайно перезаписать готовый результат."""
    safe_base = base_name.strip() or "video"
    candidate = os.path.join(folder, f"{safe_base}{suffix}{ext}")
    if not os.path.exists(candidate):
        return candidate

    idx = 2
    while True:
        candidate = os.path.join(folder, f"{safe_base}{suffix}_{idx}{ext}")
        if not os.path.exists(candidate):
            return candidate
        idx += 1


def write_translation_report(output_path: str, segments: list, pause_plan: list,
                             original_dur: float, final_dur: float, log=None,
                             source_lang: str = "auto", target_info: dict | None = None) -> str:
    """Пишет рядом с MP4 проверочный отчёт: весь распознанный текст и выбранный перевод."""
    report_path = os.path.splitext(output_path)[0] + "_segments.txt"
    try:
        target_info = dict(target_info or get_target_language(DEFAULT_TARGET_LANGUAGE))
        target_code = str(target_info.get("code") or "?")
        target_name = str(target_info.get("name") or target_code)
        source_label = str(source_lang or "auto")
        lines = []
        lines.append("ОТЧЁТ ПО ПЕРЕВОДУ И СИНХРОНИЗАЦИИ")
        lines.append("=" * 72)
        lines.append(f"Итоговое видео: {os.path.basename(output_path)}")
        lines.append(f"Исходный язык (авто): {source_label}")
        lines.append(f"Язык перевода: {target_name} ({target_code})")
        lines.append(f"Оригинальная длительность: {fmt_time(original_dur)} ({original_dur:.2f}с)")
        lines.append(f"Итоговая длительность:     {fmt_time(final_dur)} ({final_dur:.2f}с)")
        total_pause = sum(float(p.get("duration", 0.0)) for p in (pause_plan or []))
        lines.append(f"Добавлено стоп-кадров: {len(pause_plan or [])}, всего пауз: {total_pause:.2f}с")
        lines.append("")
        if pause_plan:
            lines.append("СТОП-КАДРЫ / ПАУЗЫ ВИДЕО")
            lines.append("-" * 72)
            for idx, pause in enumerate(pause_plan, 1):
                segs = ", ".join(str(x) for x in pause.get("segments", [])) or "?"
                lines.append(
                    f"{idx:03d}. {fmt_time(pause.get('at', 0.0))} +{float(pause.get('duration', 0.0)):.2f}с "
                    f"для сегмента(ов): {segs}"
                )
            lines.append("")
        lines.append(f"СЕГМЕНТЫ: {source_label.upper()} → {target_name.upper()}")
        lines.append("-" * 72)
        for idx, seg in enumerate(segments or [], 1):
            start = float(seg.get("start", 0.0))
            end = float(seg.get("end", 0.0))
            lines.append(f"[{idx:03d}] {fmt_time(start)} → {fmt_time(end)}")
            lines.append(f"SRC ({source_label}): " + (seg.get("source") or "").strip())
            lines.append(f"{target_code.upper()}: " + (seg.get("translated") or "").strip())
            lines.append("")
        atomic_write_text(Path(report_path), "\n".join(lines))
        if log:
            log(f"   📝 Отчёт сегментов → {report_path}")
        return report_path
    except Exception as exc:
        if log:
            log(f"      ⚠️ Не удалось сохранить отчёт сегментов: {exc}")
        return ""


def write_translated_text_file(output_path: str, segments: list, source_lang: str,
                               target_info: dict | None = None, log=None) -> str:
    """Сохраняет чистый .txt с текстом перевода в папку translated_texts рядом с программой."""
    try:
        target_info = dict(target_info or get_target_language(DEFAULT_TARGET_LANGUAGE))
        target_code = str(target_info.get("code") or "target").replace("-", "_")
        target_name = str(target_info.get("name") or target_code)
        base_name = safe_log_filename(Path(output_path).stem or "translation")
        folder = get_translated_texts_dir()
        path = folder / f"{base_name}_{target_code}.txt"
        if path.exists():
            idx = 2
            while True:
                candidate = folder / f"{base_name}_{target_code}_{idx}.txt"
                if not candidate.exists():
                    path = candidate
                    break
                idx += 1

        translated_lines = [(seg.get("translated") or "").strip() for seg in (segments or [])]
        translated_lines = [line for line in translated_lines if line]

        lines = []
        lines.append("ТЕКСТ ПЕРЕВОДА")
        lines.append("=" * 72)
        lines.append(f"Видео: {os.path.basename(output_path)}")
        lines.append(f"Исходный язык (авто): {source_lang or 'auto'}")
        lines.append(f"Язык перевода: {target_name} ({target_info.get('code') or '?'})")
        lines.append(f"Создано: {datetime.now():%Y-%m-%d %H:%M:%S}")
        lines.append("")
        lines.append("ПОЛНЫЙ ТЕКСТ")
        lines.append("-" * 72)
        lines.append("\n".join(translated_lines))
        lines.append("")
        lines.append("")
        lines.append("СЕГМЕНТЫ С ТАЙМКОДАМИ")
        lines.append("-" * 72)
        for idx, seg in enumerate(segments or [], 1):
            start = float(seg.get("start", 0.0))
            end = float(seg.get("end", 0.0))
            lines.append(f"[{idx:03d}] {fmt_time(start)} → {fmt_time(end)}")
            lines.append((seg.get("translated") or "").strip())
            lines.append("")

        atomic_write_text(path, "\n".join(lines))
        if log:
            log(f"   📝 Текст перевода → {path}")
        return str(path)
    except Exception as exc:
        if log:
            log(f"      ⚠️ Не удалось сохранить текст перевода: {exc}")
        return ""


def _srt_timestamp(seconds: float) -> str:
    total_ms = max(0, int(round(float(seconds or 0.0) * 1000.0)))
    hours, remainder = divmod(total_ms, 3_600_000)
    minutes, remainder = divmod(remainder, 60_000)
    secs, millis = divmod(remainder, 1000)
    return f"{hours:02d}:{minutes:02d}:{secs:02d},{millis:03d}"


def _pause_shifted_time(seconds: float, pause_plan: list | None) -> float:
    """Map a source-media timestamp onto the Pause Sync output timeline."""
    value = max(0.0, float(seconds or 0.0))
    extra = sum(
        max(0.0, float(item.get("duration", 0.0)))
        for item in (pause_plan or [])
        if float(item.get("at", 0.0)) <= value + 1e-6
    )
    return value + extra


def _render_srt(segments: list, text_key: str, pause_plan: list | None) -> str:
    blocks = []
    sequence = 0
    for segment in segments or []:
        text = " ".join(str(segment.get(text_key) or "").split())
        if not text:
            continue
        start = _pause_shifted_time(float(segment.get("start", 0.0)), pause_plan)
        end = _pause_shifted_time(float(segment.get("end", start)), pause_plan)
        if end <= start:
            end = start + 0.08
        sequence += 1
        blocks.append(
            f"{sequence}\n{_srt_timestamp(start)} --> {_srt_timestamp(end)}\n{text}"
        )
    return "\n\n".join(blocks) + ("\n" if blocks else "")


def write_subtitle_files(output_path: str, segments: list, pause_plan: list | None = None,
                         source_lang: str = "auto", target_info: dict | None = None, log=None) -> dict:
    """Write source and translated SRT sidecars aligned to the final Pause Sync timeline."""
    paths = {}
    try:
        target_info = dict(target_info or get_target_language(DEFAULT_TARGET_LANGUAGE))
        stem = Path(output_path).with_suffix("")

        def safe_code(value: str, fallback: str) -> str:
            cleaned = "".join(ch if ch.isalnum() or ch in "-_" else "_" for ch in str(value or ""))
            return cleaned.strip("_-") or fallback

        source_code = safe_code(source_lang, "auto")
        target_code = safe_code(target_info.get("code"), "target")
        source_path = Path(f"{stem}_source_{source_code}.srt")
        translated_path = Path(f"{stem}_translated_{target_code}.srt")

        source_srt = _render_srt(segments, "source", pause_plan)
        translated_srt = _render_srt(segments, "translated", pause_plan)
        if source_srt:
            atomic_write_text(source_path, source_srt)
            paths["source"] = str(source_path)
        if translated_srt:
            atomic_write_text(translated_path, translated_srt)
            paths["translated"] = str(translated_path)

        if log and paths:
            log("   💬 Субтитры SRT → " + ", ".join(paths.values()))
        return paths
    except Exception as exc:
        if log:
            log(f"      ⚠️ Не удалось сохранить SRT-субтитры: {exc}")
        return paths
