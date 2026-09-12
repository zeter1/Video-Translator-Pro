"""Owner module for: safe_log_filename, compact_exception, redact_diagnostic_text, classify_exception, exception_chain...."""

from __future__ import annotations

from pathlib import Path
import re
import subprocess


def safe_log_filename(value: str, max_len: int = 80) -> str:
    """Делает безопасный фрагмент имени файла для Windows/macOS/Linux."""
    value = re.sub(r"[^0-9A-Za-zА-Яа-яЁё._-]+", "_", value or "")
    value = value.strip("._-")
    return (value[:max_len].strip("._-") or "session")


def compact_exception(exc: Exception, max_len: int = 360) -> str:
    """Сжимает сетевые ошибки для логов и прячет длинные URL с токенами."""
    text = str(exc) or type(exc).__name__
    text = re.sub(r"((?:https?|wss?)://[^\s'\"<>?)]+)\?[^\s'\"<>)]*", r"\1?...", text)
    text = re.sub(r"(url:\s+[^\s?]+)\?[^\s)]*", r"\1?...", text, flags=re.IGNORECASE)
    text = re.sub(r"(TrustedClientToken=)[^&\s)]+", r"\1...", text)
    text = re.sub(r"(ConnectionId=)[^&\s)]+", r"\1...", text)
    text = re.sub(r"\s+", " ", text).strip()
    if len(text) > max_len:
        text = text[: max_len - 3].rstrip() + "..."
    return text


def redact_diagnostic_text(value, max_len: int = 30000) -> str:
    """Скрывает токены/длинные URL, сохраняя переносы строк и полезный traceback."""
    text = str(value or "")
    text = re.sub(r"((?:https?|wss?)://[^\s'\"<>?)]+)\?[^\s'\"<>)]*", r"\1?...", text)
    text = re.sub(r"(url:\s+[^\s?]+)\?[^\s)]*", r"\1?...", text, flags=re.IGNORECASE)
    text = re.sub(r"(TrustedClientToken=)[^&\s)]+", r"\1...", text, flags=re.IGNORECASE)
    text = re.sub(r"(ConnectionId=)[^&\s)]+", r"\1...", text, flags=re.IGNORECASE)
    text = re.sub(
        r"(?i)(authorization|api[_-]?key|access[_-]?token|refresh[_-]?token)(\s*[:=]\s*)([^\s,;]+)",
        r"\1\2<скрыто>",
        text,
    )
    if len(text) > max_len:
        text = text[: max_len - 20].rstrip() + "\n...<обрезано>"
    return text


def classify_exception(exc: Exception | None) -> str:
    """Короткая категория ошибки для фильтрации JSONL без разбора длинного текста."""
    if exc is None:
        return "unknown"
    name = type(exc).__name__.lower()
    text = f"{name} {exc}".lower()
    if "timeout" in text or "timed out" in text:
        return "network_timeout" if any(x in text for x in ("http", "ssl", "connect")) else "timeout"
    if any(x in text for x in ("connection", "dns", "name resolution", "network")):
        return "network_connection"
    if any(x in text for x in ("429", "too many requests", "rate limit")):
        return "rate_limit"
    if isinstance(exc, FileNotFoundError):
        return "file_not_found"
    if isinstance(exc, PermissionError):
        return "permission_denied"
    if isinstance(exc, subprocess.CalledProcessError):
        return "subprocess_failed"
    return name or "error"


def exception_chain(exc: Exception | None) -> list[dict]:
    """Возвращает всю цепочку cause/context, а не только последнюю ошибку."""
    chain = []
    seen = set()
    current = exc
    while current is not None and id(current) not in seen and len(chain) < 12:
        seen.add(id(current))
        chain.append({
            "type": type(current).__name__,
            "category": classify_exception(current),
            "message": compact_exception(current, max_len=1000),
        })
        current = current.__cause__ or current.__context__
    return chain


def diagnostic_json_value(value, depth: int = 0):
    """Приводит детали события к безопасному JSON-совместимому виду."""
    if depth > 5:
        return "<слишком глубокая структура>"
    if value is None or isinstance(value, (bool, int, float)):
        return value
    if isinstance(value, (str, Path)):
        return redact_diagnostic_text(value, max_len=6000)
    if isinstance(value, dict):
        result = {}
        for key, item in value.items():
            key_text = str(key)
            if re.search(r"(?i)(password|secret|token|api.?key|authorization)", key_text):
                result[key_text] = "<скрыто>"
            else:
                result[key_text] = diagnostic_json_value(item, depth + 1)
        return result
    if isinstance(value, (list, tuple, set)):
        items = list(value)
        converted = [diagnostic_json_value(item, depth + 1) for item in items[:200]]
        if len(items) > 200:
            return {
                "items": converted,
                "total_count": len(items),
                "shown_count": len(converted),
                "truncated": True,
            }
        return converted
    return redact_diagnostic_text(repr(value), max_len=6000)


def diagnostic_signature_value(value):
    """Убирает нестабильные детали, чтобы одинаковые повторы имели одну сигнатуру."""
    if value is None or isinstance(value, (bool, int, float)):
        return value
    if isinstance(value, str):
        text = value.lower()
        text = re.sub(r"(?:https?|wss?)://[^\s]+", "<url>", text)
        text = re.sub(r"\b[0-9a-f]{16,}\b", "<hash>", text)
        text = re.sub(r"\b\d+(?:\.\d+)?\b", "<n>", text)
        return text[:500]
    if isinstance(value, dict):
        ignored = {
            "attempt", "elapsed_sec", "eta_sec", "first_timestamp", "last_timestamp",
            "next_probe_after_sec", "probe_number", "remaining_wait_sec", "sequence",
            "thread", "timestamp", "tts_segment_index", "segment_index",
        }
        return {
            str(key): diagnostic_signature_value(item)
            for key, item in sorted(value.items(), key=lambda pair: str(pair[0]))
            if str(key) not in ignored
        }
    if isinstance(value, (list, tuple)):
        return [diagnostic_signature_value(item) for item in list(value)[:20]]
    return diagnostic_signature_value(str(value))


def should_store_problem_occurrence(count: int) -> bool:
    """Хранит первые примеры и редкие контрольные точки длинной серии повторов."""
    count = max(1, int(count))
    return count <= 3 or count in {5, 10, 20, 50, 100} or count % 250 == 0
