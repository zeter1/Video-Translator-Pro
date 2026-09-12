"""Owner module for: build_translation_batch, parse_translation_batch, split_translation_batches."""

from __future__ import annotations

import hashlib
import re
from videotranslator.config import TRANSLATION_BATCH_MAX_CHARS, TRANSLATION_BATCH_MAX_SEGMENTS


def split_translation_text(text: str) -> list[str]:
    """Bound a single long phrase without dropping any source characters."""
    limit = TRANSLATION_BATCH_MAX_CHARS
    parts = []
    offset = 0
    while len(text) - offset > limit:
        window = text[offset:offset + limit]
        # Prefer a word boundary near the end; unbroken text still progresses.
        boundaries = [match.end() for match in re.finditer(r"\s+", window)
                      if match.end() >= limit // 2]
        end = offset + (boundaries[-1] if boundaries else limit)
        parts.append(text[offset:end])
        offset = end
    if offset < len(text):
        parts.append(text[offset:])
    return parts


def build_translation_batch(items: list[tuple[int, str]]) -> tuple[str, list[tuple[int, str, str]]]:
    """Упаковывает сегменты в один запрос с уникальными парными разделителями."""
    digest_source = "\n".join(f"{index}:{text}" for index, text in items)
    nonce = hashlib.sha256(digest_source.encode("utf-8", errors="replace")).hexdigest()[:10].upper()
    markers = []
    blocks = []
    for order, (index, text) in enumerate(items, 1):
        start = f"__VTSEG_{nonce}_{order:04d}_START__"
        end = f"__VTSEG_{nonce}_{order:04d}_END__"
        markers.append((index, start, end))
        blocks.append(f"{start}\n{text.strip()}\n{end}")
    return "\n".join(blocks), markers


def parse_translation_batch(translated: str, markers: list[tuple[int, str, str]]) -> dict[int, str]:
    """Строго проверяет число, порядок и целостность разделителей пакетного ответа."""
    translated = translated or ""
    for index, start_marker, end_marker in markers:
        if translated.count(start_marker) != 1 or translated.count(end_marker) != 1:
            raise ValueError(f"разделители сегмента {index} отсутствуют или повторяются")
    cursor = 0
    result = {}
    for index, start_marker, end_marker in markers:
        start_pos = translated.find(start_marker, cursor)
        if start_pos < 0 or translated[cursor:start_pos].strip():
            raise ValueError(f"не найден или нарушен начальный разделитель сегмента {index}")
        content_start = start_pos + len(start_marker)
        end_pos = translated.find(end_marker, content_start)
        if end_pos < 0:
            raise ValueError(f"не найден конечный разделитель сегмента {index}")
        value = translated[content_start:end_pos].strip()
        if not value:
            raise ValueError(f"пустой перевод сегмента {index}")
        if start_marker in value or end_marker in value:
            raise ValueError(f"дублирован разделитель сегмента {index}")
        result[index] = value
        cursor = end_pos + len(end_marker)
    if translated[cursor:].strip():
        raise ValueError("после последнего разделителя найден посторонний текст")
    if len(result) != len(markers):
        raise ValueError("число переведённых сегментов не совпадает с запросом")
    return result


def split_translation_batches(records: list[tuple[int, dict]]) -> list[list[tuple[int, dict]]]:
    batches = []
    current = []
    current_chars = 0
    for index, record in records:
        text = str(record.get("source") or "")
        estimated = len(text) + 100
        if current and (len(current) >= TRANSLATION_BATCH_MAX_SEGMENTS
                        or current_chars + estimated > TRANSLATION_BATCH_MAX_CHARS):
            batches.append(current)
            current = []
            current_chars = 0
        current.append((index, record))
        current_chars += estimated
    if current:
        batches.append(current)
    return batches
