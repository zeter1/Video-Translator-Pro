"""Owner module for: make_unique_output_path, write_translation_report, write_translated_text_file."""

from __future__ import annotations

from pathlib import Path
from datetime import datetime
import os
from videotranslator.config import DEFAULT_TARGET_LANGUAGE, get_target_language
from videotranslator.core.diagnostics import safe_log_filename
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
        with open(report_path, "w", encoding="utf-8") as file:
            file.write("\n".join(lines))
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

        with open(path, "w", encoding="utf-8") as file:
            file.write("\n".join(lines))
        if log:
            log(f"   📝 Текст перевода → {path}")
        return str(path)
    except Exception as exc:
        if log:
            log(f"      ⚠️ Не удалось сохранить текст перевода: {exc}")
        return ""
