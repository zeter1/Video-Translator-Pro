"""
Видео Переводчик PRO — Pause Sync Quality
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
Что улучшено по сравнению с исходной версией:
  • безопасные обновления Tkinter из рабочего потока;
  • временная папка на каждую задачу вместо мусора в папке результата;
  • гарантированное закрытие VideoFileClip / AudioFileClip даже при ошибках;
  • защита от перезаписи результатов с одинаковыми именами;
  • более подробная диагностика ffmpeg / перевода / TTS;
  • PAUSE SYNC Quality: адаптивное использование пауз между фразами;
  • сохранение pitch при подгонке tempo: Rubber Band при наличии, иначе atempo;
  • нативное ускорение Edge TTS до пост-обработки, чтобы меньше роботизации;
  • мягкий мастеринг голоса, лимитер и нормализация громкости;
  • более отзывчивая отмена между сетевыми попытками;
  • исправлено автозатирание настроек при запуске;
  • fallback через numpy стал стерео и логирует проблемные файлы;
  • FIX FINAL MP4: финальная сборка теперь только через ffmpeg, без MoviePy TEMP_MPY аудиофайлов.
  • PAUSE SYNC: если фраза требует ускорения выше выбранного предела, видео вставляет стоп-кадр/паузу вместо порчи голоса.

Установка:
    pip install -r requirements.txt

Важно:
    ffmpeg и ffprobe должны быть в PATH или лежать рядом с этим .py файлом.
"""

import asyncio
import hashlib
import importlib
import importlib.metadata as importlib_metadata
import json
import os
import platform
import re
import shutil
import subprocess
import sys
import tempfile
import threading
import time
import traceback
import wave
from collections import deque
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from datetime import datetime

import tkinter as tk
from tkinter import filedialog, messagebox, scrolledtext
from tkinter import ttk


# ─────────────────────────────────────────────────────────────────────────────
#  КОНФИГУРАЦИЯ
# ─────────────────────────────────────────────────────────────────────────────

MODELS_MAP = {
    "tiny  — быстро, низкая точность": "tiny",
    "base  — баланс (рекомендуется)": "base",
    "small — медленнее, лучше качество": "small",
    "medium — высокая точность (GPU желательно)": "medium",
}

TARGET_LANGUAGES = {
    "Английский (США)": {
        "code": "en",
        "gtts": "en",
        "suffix": "_EN",
        "name": "английский",
        "voices": {
            "Мужской — Guy": "en-US-GuyNeural",
            "Женский — Jenny": "en-US-JennyNeural",
            "Женский — Aria": "en-US-AriaNeural",
        },
    },
    "Английский (Великобритания)": {
        "code": "en",
        "gtts": "en",
        "suffix": "_EN_GB",
        "name": "английский (Великобритания)",
        "voices": {
            "Мужской — Ryan": "en-GB-RyanNeural",
            "Женский — Sonia": "en-GB-SoniaNeural",
            "Женский — Libby": "en-GB-LibbyNeural",
        },
    },
    "Испанский": {
        "code": "es",
        "gtts": "es",
        "suffix": "_ES",
        "name": "испанский",
        "voices": {
            "Мужской — Alvaro": "es-ES-AlvaroNeural",
            "Женский — Elvira": "es-ES-ElviraNeural",
        },
    },
    "Немецкий": {
        "code": "de",
        "gtts": "de",
        "suffix": "_DE",
        "name": "немецкий",
        "voices": {
            "Мужской — Conrad": "de-DE-ConradNeural",
            "Женский — Katja": "de-DE-KatjaNeural",
        },
    },
    "Французский": {
        "code": "fr",
        "gtts": "fr",
        "suffix": "_FR",
        "name": "французский",
        "voices": {
            "Мужской — Henri": "fr-FR-HenriNeural",
            "Женский — Denise": "fr-FR-DeniseNeural",
        },
    },
    "Итальянский": {
        "code": "it",
        "gtts": "it",
        "suffix": "_IT",
        "name": "итальянский",
        "voices": {
            "Мужской — Diego": "it-IT-DiegoNeural",
            "Женский — Elsa": "it-IT-ElsaNeural",
        },
    },
    "Польский": {
        "code": "pl",
        "gtts": "pl",
        "suffix": "_PL",
        "name": "польский",
        "voices": {
            "Мужской — Marek": "pl-PL-MarekNeural",
            "Женский — Zofia": "pl-PL-ZofiaNeural",
        },
    },
    "Украинский": {
        "code": "uk",
        "gtts": "uk",
        "suffix": "_UK",
        "name": "украинский",
        "voices": {
            "Мужской — Ostap": "uk-UA-OstapNeural",
            "Женский — Polina": "uk-UA-PolinaNeural",
        },
    },
    "Турецкий": {
        "code": "tr",
        "gtts": "tr",
        "suffix": "_TR",
        "name": "турецкий",
        "voices": {
            "Мужской — Ahmet": "tr-TR-AhmetNeural",
            "Женский — Emel": "tr-TR-EmelNeural",
        },
    },
    "Японский": {
        "code": "ja",
        "gtts": "ja",
        "suffix": "_JA",
        "name": "японский",
        "voices": {
            "Женский — Nanami": "ja-JP-NanamiNeural",
            "Мужской — Keita": "ja-JP-KeitaNeural",
        },
    },
    "Корейский": {
        "code": "ko",
        "gtts": "ko",
        "suffix": "_KO",
        "name": "корейский",
        "voices": {
            "Женский — SunHi": "ko-KR-SunHiNeural",
            "Мужской — InJoon": "ko-KR-InJoonNeural",
        },
    },
    "Китайский": {
        "code": "zh-CN",
        "gtts": "zh-CN",
        "suffix": "_ZH",
        "name": "китайский",
        "voices": {
            "Женский — Xiaoxiao": "zh-CN-XiaoxiaoNeural",
            "Мужской — Yunxi": "zh-CN-YunxiNeural",
        },
    },
    "Русский": {
        "code": "ru",
        "gtts": "ru",
        "suffix": "_RU",
        "name": "русский",
        "voices": {
            "Дмитрий (мужской)": "ru-RU-DmitryNeural",
            "Светлана (женский)": "ru-RU-SvetlanaNeural",
            "Дарья (живой женский)": "ru-RU-DariyaNeural",
        },
    },
}

DEFAULT_TARGET_LANGUAGE = "Русский"
SETTINGS_LANGUAGE_VERSION = 2
DEFAULT_AUDIO_SETTINGS = {
    "voice_volume_pct": 100,
    "highpass_hz": 55,
    "lowpass_hz": 0,
    "noise_reduction": False,
    "master_loudness_i": -16,
    "speech_speed_limit": 1.15,
}
SPEECH_SPEED_LIMITS = (1.15, 1.20, 1.25)


def get_target_language(label: str | None) -> dict:
    base = TARGET_LANGUAGES.get(label or "") or TARGET_LANGUAGES[DEFAULT_TARGET_LANGUAGE]
    info = dict(base)
    info["label"] = label if label in TARGET_LANGUAGES else DEFAULT_TARGET_LANGUAGE
    return info


def get_target_language_by_code(code: str | None) -> str:
    code = (code or "").strip().lower()
    for label, info in TARGET_LANGUAGES.items():
        if str(info.get("code", "")).lower() == code:
            return label
    return DEFAULT_TARGET_LANGUAGE


def get_voice_options(language_label: str | None) -> dict:
    return dict(get_target_language(language_label).get("voices") or {})


def get_language_labels() -> list[str]:
    labels = [label for label in TARGET_LANGUAGES.keys() if label != DEFAULT_TARGET_LANGUAGE]
    return [DEFAULT_TARGET_LANGUAGE] + labels


def normalize_audio_settings(settings: dict | None = None) -> dict:
    data = dict(DEFAULT_AUDIO_SETTINGS)
    if settings:
        data.update(settings)

    data["voice_volume_pct"] = max(50, min(150, int(data.get("voice_volume_pct", 100))))
    data["highpass_hz"] = max(0, min(220, int(data.get("highpass_hz", 55))))
    data["lowpass_hz"] = max(0, min(16000, int(data.get("lowpass_hz", 0))))
    if data["lowpass_hz"] and data["lowpass_hz"] < 2500:
        data["lowpass_hz"] = 0
    data["noise_reduction"] = bool(data.get("noise_reduction", False))
    data["master_loudness_i"] = max(-24, min(-12, int(data.get("master_loudness_i", -16))))
    try:
        requested_speed = float(data.get("speech_speed_limit", 1.15))
    except (TypeError, ValueError):
        requested_speed = 1.15
    data["speech_speed_limit"] = min(SPEECH_SPEED_LIMITS, key=lambda value: abs(value - requested_speed))
    return data

# Размер пачки ограничивает длину ffmpeg-команды на Windows.
BATCH_SIZE = 20

# Единый формат голоса во всём пайплайне.
SAMPLE_RATE = 44100

# PAUSE SYNC Quality: полный перевод важнее жёсткого тайминга.
# Если фраза требует ускорения выше 1.15x, мы не режем слова и не гоним голос,
# а вставляем стоп-кадр/паузу в видео до окончания переведённой фразы.
TARGET_HEADROOM = 0.990          # почти весь доступный слот можно занять голосом
MIN_SYNC_GAP = 0.045             # небольшой зазор перед следующей фразой
TOTAL_MAX_SPEECH_SPEED = 1.15    # значение по умолчанию; пользователь может выбрать 1.20/1.25
MAX_NATIVE_TTS_RATE = 0.25       # часть ускорения отдаём Edge TTS, звучит естественнее
MAX_NATURAL_TEMPO = 1.25         # верхний доступный предел; реальный берётся из настройки
MAX_EMERGENCY_TEMPO = 1.25       # жёсткий технический потолок
MIN_TEMPO = 0.75
MIN_INSERTED_PAUSE = 0.035       # не вставляем микропаузы меньше этого значения
PAUSE_FINISH_MARGIN = 0.055      # маленький запас, чтобы фраза не налезала на продолжение видео
VOICE_LIMIT = 0.96
MASTER_LOUDNESS_I = -16
MASTER_TRUE_PEAK = -1.5

# Финальная сборка длинных роликов с Pause Sync может идти дольше часа.
# Старый фиксированный timeout=3600 убивал ffmpeg уже на самом конце работы.
FINAL_ENCODE_MIN_TIMEOUT = 3600
FINAL_ENCODE_MAX_TIMEOUT = 8 * 3600
HEAVY_PAUSE_SYNC_COUNT = 200
NETWORK_CONNECT_TIMEOUT_SEC = 8
NETWORK_READ_TIMEOUT_SEC = 35
EDGE_TTS_MIN_TIMEOUT_SEC = 45
EDGE_TTS_MAX_TIMEOUT_SEC = 180
PROBLEM_LOG_RETENTION_DAYS = 120
PROBLEM_LOG_MAX_FILES = 80
PROBLEM_SESSION_STALE_SEC = 15 * 60
PROBLEM_LOG_SCHEMA_VERSION = 4
PROBLEM_MANIFEST_SCHEMA_VERSION = 1
PROBLEM_INDEX_SCHEMA_VERSION = 1
PROBLEM_REPORT_REFRESH_SEC = 10
PROBLEM_REPEAT_SAMPLE_EVENTS = {
    "activity_heartbeat",
    "manual_review_still_waiting",
    "runtime_error_log",
    "runtime_warning_log",
    "translation_retry",
    "translation_retry_recovered",
    "translation_segment_deferred",
    "translation_progress",
    "tts_edge_probe_failed",
    "tts_gtts_retry",
    "tts_retry",
}
TRANSLATION_CHECKPOINT_EVERY = 20
TRANSLATION_PROGRESS_EVERY = 50
TRANSLATION_BATCH_MAX_SEGMENTS = 8
TRANSLATION_BATCH_MAX_CHARS = 3900
NETWORK_STORM_WINDOW_SEC = 90
NETWORK_STORM_THRESHOLD = 6
NETWORK_STORM_RECOVERY_SUCCESSES = 3
EDGE_VOICE_PRESERVE_SEC = 120
EDGE_VOICE_PROBE_INTERVAL_SEC = 15
TTS_PREPARE_WORKERS = 3
TTS_CACHE_SCHEMA_VERSION = 1
TTS_PREPARED_CACHE_SCHEMA_VERSION = 2
TTS_CACHE_RECOVERY_RETENTION_DAYS = 7
TTS_CACHE_PREPARED_RETENTION_HOURS = 24
TTS_CACHE_ORPHAN_RETENTION_HOURS = 24
TTS_CACHE_MAX_BYTES = 2 * 1024 ** 3
TTS_CACHE_TARGET_BYTES = 1536 * 1024 ** 2
TTS_CACHE_SIZE_CHECK_EVERY = 25
APP_DIAGNOSTIC_VERSION = "2026.08.12.3"
BATCH_RECOVERY_SCHEMA_VERSION = 1

_whisper_cache = {}
_whisper_lock = threading.Lock()


# ─────────────────────────────────────────────────────────────────────────────
#  УТИЛИТЫ
# ─────────────────────────────────────────────────────────────────────────────

class CancelledError(Exception):
    """Внутреннее исключение для мягкой отмены обработки."""


def get_program_dir() -> Path:
    """Папка, где лежит .py/.exe программы. Рядом с ней создаётся папка logs."""
    try:
        if getattr(sys, "frozen", False):
            return Path(sys.executable).resolve().parent
        return Path(__file__).resolve().parent
    except Exception:
        return Path.cwd()


def get_logs_dir() -> Path:
    """Возвращает папку logs рядом с программой и создаёт её при необходимости."""
    logs_dir = get_program_dir() / "logs"
    logs_dir.mkdir(parents=True, exist_ok=True)
    return logs_dir


def get_problem_logs_dir() -> Path:
    """Папка с подробными JSONL-журналами, рассчитанными на разбор в Codex."""
    logs_dir = get_program_dir() / "Логи проблем"
    logs_dir.mkdir(parents=True, exist_ok=True)
    return logs_dir


def get_translated_texts_dir() -> Path:
    """Папка с .txt-файлами готовых переводов рядом с программой."""
    texts_dir = get_program_dir() / "translated_texts"
    texts_dir.mkdir(parents=True, exist_ok=True)
    return texts_dir


def get_translation_checkpoints_dir() -> Path:
    """Контрольные точки перевода, чтобы сетевой сбой не обнулял долгую работу."""
    checkpoint_dir = get_translated_texts_dir() / "Контрольные точки"
    checkpoint_dir.mkdir(parents=True, exist_ok=True)
    return checkpoint_dir


def get_tts_cache_dir() -> Path:
    """Постоянный кэш озвучки, переживающий перезапуск программы."""
    cache_dir = get_program_dir() / "tts_cache"
    cache_dir.mkdir(parents=True, exist_ok=True)
    return cache_dir


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


def atomic_write_text(path: Path, text: str):
    """Атомарно записывает UTF-8 текст, не оставляя частично записанный отчёт."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temp_path = path.with_name(f".{path.name}.{os.getpid()}.{threading.get_ident()}.tmp")
    try:
        with open(temp_path, "w", encoding="utf-8", newline="\n") as file:
            file.write(text)
            file.flush()
            os.fsync(file.fileno())
        os.replace(temp_path, path)
    finally:
        try:
            if temp_path.exists():
                temp_path.unlink()
        except OSError:
            pass


def read_json_file(path: Path) -> dict:
    try:
        with open(path, "r", encoding="utf-8-sig") as file:
            value = json.load(file)
        return value if isinstance(value, dict) else {}
    except (OSError, ValueError, TypeError):
        return {}


def problem_log_relative_path(root: Path, path: Path) -> str:
    """Возвращает переносимый путь внутри папки журналов."""
    try:
        return path.resolve().relative_to(root.resolve()).as_posix()
    except (OSError, ValueError):
        return path.name


def find_previous_problem_summary(directory: Path) -> tuple[dict, Path | None]:
    """Находит сводку прошлой сессии в новой или старой структуре."""
    index = read_json_file(directory / "latest_session.json")
    files = index.get("files") if isinstance(index.get("files"), dict) else {}
    relative_summary = files.get("summary") or index.get("summary") or ""
    if relative_summary:
        try:
            candidate = (directory / str(relative_summary)).resolve()
            if candidate.is_relative_to(directory.resolve()):
                summary = read_json_file(candidate)
                if summary:
                    return summary, candidate
        except (OSError, ValueError):
            pass

    legacy_path = directory / "latest_problem_summary.json"
    legacy = read_json_file(legacy_path)
    if legacy:
        return legacy, legacy_path
    return {}, None


def archive_legacy_problem_logs(directory: Path) -> dict[str, Path]:
    """Переносит только известные старые журналы в legacy_flat_logs без удаления."""
    candidates = []
    for pattern in ("problems_*.jsonl", "session_summary_*.json", "latest_problem_summary.json"):
        candidates.extend(path for path in directory.glob(pattern) if path.is_file())
    if not candidates:
        return {}

    legacy_dir = directory / "legacy_flat_logs"
    legacy_dir.mkdir(parents=True, exist_ok=True)
    moved = {}
    for source in sorted(set(candidates), key=lambda path: path.name.lower()):
        target = legacy_dir / source.name
        if target.exists():
            stem, suffix = source.stem, source.suffix
            counter = 2
            while target.exists():
                target = legacy_dir / f"{stem}_{counter}{suffix}"
                counter += 1
        try:
            source_key = str(source.resolve())
            os.replace(source, target)
            moved[source_key] = target
        except OSError:
            continue
    return moved


def write_problem_logs_guide(directory: Path):
    """Создаёт короткую инструкцию, с которой Codex начинает анализ журналов."""
    guide = """# Логи проблем — инструкция для Codex

Каждый запуск программы хранится в отдельной папке `session_<ID>`.

## С чего начинать анализ

1. Откройте `latest_session.json` — это указатель на последнюю сессию.
2. В папке сессии сначала прочитайте `report.md` и `summary.json`.
3. Затем изучите `problems.jsonl`: там только предупреждения и ошибки.
4. При необходимости объедините `events.jsonl` и `problems.jsonl` по `sequence` — это полная сохранённая хронология.
5. `manifest.json` описывает назначение файлов, версию схемы и границы проверки.

## Правила интерпретации

- `summary.json` и `report.md` обновляются во время работы; статус `running` не является ошибкой.
- Сессия `interrupted` означает, что прошлый процесс не записал штатное завершение.
- Поле `codex_analysis` содержит подсказки, а не неподтверждённые факты.
- Одинаковые проблемы объединены по `signature`; `occurrences_total` показывает точное число повторов.
- В JSONL сохраняются первые 3 примера и редкие контрольные повторы; точная политика описана в `manifest.json`, а `stored_samples` показывает число строк на диске.
- Карточки `issue_cards` содержат доказательства, вероятную причину, место поиска, исправление и проверку.
- Секреты в URL и полях токенов маскируются. Большие списки сокращаются с сохранением общего количества.
- Нельзя считать GUI, сеть, реальный FFmpeg или итоговое видео проверенными только по статическому анализу кода.

Старые журналы прежнего плоского формата сохранены в `legacy_flat_logs`.
"""
    atomic_write_text(directory / "README_FOR_CODEX.md", guide)


def _is_owned_problem_session(path: Path) -> bool:
    if not path.is_dir() or not re.fullmatch(r"session_[0-9]{8}_[0-9]{6}_[0-9]{6}_[0-9]+", path.name):
        return False
    manifest = read_json_file(path / "manifest.json")
    return (
        int(manifest.get("schema_version") or 0) == PROBLEM_MANIFEST_SCHEMA_VERSION
        and manifest.get("artifact_type") == "video_translator_problem_session"
    )


def cleanup_old_problem_logs(directory: Path):
    """Ограничивает хранение только помеченных папок сессий и старых журналов приложения."""
    try:
        cutoff = time.time() - PROBLEM_LOG_RETENTION_DAYS * 24 * 60 * 60
        sessions = []
        for path in directory.glob("session_*"):
            try:
                if _is_owned_problem_session(path):
                    sessions.append(path)
            except OSError:
                continue
        sessions.sort(key=lambda path: path.stat().st_mtime)
        expired = {path for path in sessions if path.stat().st_mtime < cutoff}
        expired.update(sessions[:-PROBLEM_LOG_MAX_FILES])
        for path in sorted(expired, key=lambda item: item.name):
            try:
                shutil.rmtree(path)
            except OSError:
                pass

        legacy_dir = directory / "legacy_flat_logs"
        if legacy_dir.is_dir():
            for pattern in ("problems_*.jsonl", "session_summary_*.json"):
                for path in legacy_dir.glob(pattern):
                    try:
                        if path.is_file() and path.stat().st_mtime < cutoff:
                            path.unlink()
                    except OSError:
                        pass
    except OSError:
        pass


def installed_package_versions() -> dict:
    versions = {}
    for package in ("openai-whisper", "torch", "edge-tts", "gTTS", "deep-translator", "moviepy", "numpy", "requests"):
        try:
            versions[package] = importlib_metadata.version(package)
        except importlib_metadata.PackageNotFoundError:
            versions[package] = "not-installed"
        except Exception as exc:
            versions[package] = f"unknown ({type(exc).__name__})"
    return versions


class ProblemLogger:
    """Потокобезопасный JSONL-журнал с контекстом для диагностики в Codex."""

    @staticmethod
    def _recover_previous_summary(
        previous: dict,
        summary_path: Path | None,
        moved_paths: dict[str, Path] | None = None,
    ) -> dict:
        if not previous:
            return {}
        previous = dict(previous)
        moved_paths = moved_paths or {}
        old_problem_log = str(previous.get("problem_log") or previous.get("events_log") or "")
        if old_problem_log:
            try:
                moved_problem_log = moved_paths.get(str(Path(old_problem_log).resolve()))
            except OSError:
                moved_problem_log = None
            if moved_problem_log:
                previous["problem_log"] = str(moved_problem_log)
                previous["events_log"] = str(moved_problem_log)
        previous_status = str(previous.get("session_status") or previous.get("status") or "")
        if previous_status != "running":
            return {}

        updated_at = str(previous.get("updated_at") or "")
        age_sec = None
        try:
            age_sec = max(0, int((datetime.now().astimezone() - datetime.fromisoformat(updated_at)).total_seconds()))
        except (TypeError, ValueError):
            pass
        stale = age_sec is None or age_sec >= PROBLEM_SESSION_STALE_SEC
        session_id = safe_log_filename(str(previous.get("session_id") or "unknown"), max_len=96)
        recovered = {
            "session_id": previous.get("session_id") or "",
            "original_status": previous_status,
            "status": "interrupted" if stale else "possibly_active",
            "updated_at": updated_at,
            "detected_at": datetime.now().astimezone().isoformat(timespec="seconds"),
            "age_sec": age_sec,
            "current_stage": previous.get("current_stage") or "",
            "current_input_path": previous.get("current_input_path") or "",
            "problem_log": previous.get("problem_log") or previous.get("events_log") or "",
            "summary": str(summary_path) if summary_path else "",
        }
        if stale and summary_path and summary_path.exists():
            archived = dict(previous)
            archived["status"] = recovered["status"]
            archived["session_status"] = recovered["status"]
            archived["interruption_detection"] = recovered
            try:
                atomic_write_json(summary_path, archived)
            except OSError:
                pass
        return recovered

    def __init__(self):
        self.lock = threading.Lock()
        self.sequence = 0
        self.session_id = f"{datetime.now():%Y%m%d_%H%M%S_%f}_{os.getpid()}"
        self.root_dir = get_problem_logs_dir()
        previous_summary, previous_summary_path = find_previous_problem_summary(self.root_dir)
        moved_paths = archive_legacy_problem_logs(self.root_dir)
        if previous_summary_path:
            try:
                previous_summary_path = moved_paths.get(
                    str(previous_summary_path.resolve()), previous_summary_path
                )
            except OSError:
                pass
        try:
            write_problem_logs_guide(self.root_dir)
        except OSError:
            pass
        cleanup_old_problem_logs(self.root_dir)

        self.session_dir = self.root_dir / f"session_{self.session_id}"
        self.session_dir.mkdir(parents=True, exist_ok=False)
        self.path = self.session_dir / "events.jsonl"
        self.problem_path = self.session_dir / "problems.jsonl"
        self.summary_path = self.session_dir / "summary.json"
        self.report_path = self.session_dir / "report.md"
        self.manifest_path = self.session_dir / "manifest.json"
        self.latest_index_path = self.root_dir / "latest_session.json"
        previous_session = self._recover_previous_summary(
            previous_summary, previous_summary_path, moved_paths
        )
        self._last_summary_write = 0.0
        self._last_report_write = 0.0
        self._occurrence_counts = {}
        self._problem_signatures_seen = set()
        self._stored_event_counts = {"timeline": 0, "problems": 0}
        self._summary = {
            "schema_version": PROBLEM_LOG_SCHEMA_VERSION,
            "artifact_type": "video_translator_problem_summary",
            "updated_at": "",
            "session_id": self.session_id,
            "session_dir": self.session_dir.name,
            "status": "running",
            "session_status": "running",
            "stale_after_sec": PROBLEM_SESSION_STALE_SEC,
            "previous_session": previous_session,
            "current_stage": "startup",
            "current_input_path": "",
            "current_file": {
                "status": "idle",
                "stage": "startup",
                "input_path": "",
                "output_path": "",
            },
            "batch": {
                "status": "idle",
                "total": 0,
                "started": 0,
                "successful": 0,
                "failed": 0,
                "pending": 0,
                "current_index": 0,
                "files": [],
            },
            "last_event": {},
            "selected_video_encoder": {},
            "tts_voice": {
                "status": "idle",
                "selected_voice": "",
                "fallback_provider": "",
                "voice_may_differ": False,
            },
            "tts_cache": {},
            "network": {
                "incidents_started": 0,
                "incidents_finished": 0,
                "active_services": {},
            },
            "counters": {"info": 0, "warning": 0, "error": 0},
            "event_counts_by_event": {},
            "problem_counts_by_event": {},
            "problem_counts_by_category": {},
            "recent_problems": [],
            "problem_log": "events.jsonl",
            "events_log": "events.jsonl",
            "problems_log": "problems.jsonl",
            "artifacts": {
                "manifest": "manifest.json",
                "report": "report.md",
                "summary": "summary.json",
                "problems": "problems.jsonl",
                "events": "events.jsonl",
            },
            "diagnostics_quality": {
                "events_total": 0,
                "records_stored_total": 0,
                "timeline_events_stored": 0,
                "problem_events_total": 0,
                "problem_events_stored": 0,
                "unique_problem_signatures_total": 0,
                "problem_signatures_outside_recent_window": 0,
                "problem_timeline_duplicates_avoided": 0,
                "repeat_events_compacted": 0,
                "estimated_bytes_saved": 0,
                "problems_with_stage": 0,
                "problems_with_input_path": 0,
                "error_events_total": 0,
                "errors_without_traceback": 0,
                "exceptions_with_traceback": 0,
                "problems_missing_stage_and_input": 0,
                "artifact_write_errors": [],
            },
            "codex_analysis": {},
        }
        self.file = open(self.path, "a", encoding="utf-8", buffering=1)
        try:
            self.problem_file = open(self.problem_path, "a", encoding="utf-8", buffering=1)
        except OSError as exc:
            self.problem_file = None
            self._summary["diagnostics_quality"]["artifact_write_errors"].append({
                "artifact": "problems.jsonl",
                "error": compact_exception(exc),
            })
        try:
            self._write_manifest_locked()
        except Exception as exc:
            self._record_artifact_error_locked("manifest.json", exc)
        cleanup_old_problem_logs(self.root_dir)
        self.event(
            "app_session_started",
            message="Программа запущена; диагностический журнал готов.",
            app_version=APP_DIAGNOSTIC_VERSION,
            python={
                "version": sys.version,
                "executable": sys.executable,
                "frozen": bool(getattr(sys, "frozen", False)),
            },
            system={
                "platform": platform.platform(),
                "machine": platform.machine(),
                "processor": platform.processor(),
            },
            paths={
                "program_dir": str(get_program_dir()),
                "working_dir": str(Path.cwd()),
                "session_dir": str(self.session_dir),
                "events_log": str(self.path),
                "problems_log": str(self.problem_path),
                "problem_summary": str(self.summary_path),
                "problem_report": str(self.report_path),
                "manifest": str(self.manifest_path),
            },
            packages=installed_package_versions(),
            retention={"days": PROBLEM_LOG_RETENTION_DAYS, "max_sessions": PROBLEM_LOG_MAX_FILES},
        )
        if previous_session:
            stale = previous_session.get("status") == "interrupted"
            self.event(
                "previous_session_interrupted" if stale else "previous_session_was_running",
                level="warning",
                message=(
                    "Предыдущая сессия давно не обновлялась и помечена как прерванная."
                    if stale else
                    "Обнаружена другая недавно активная сессия; её сводка сохранена отдельно."
                ),
                previous_session=previous_session,
            )

    @staticmethod
    def _problem_family(event: str, details: dict) -> str:
        exception = details.get("exception") if isinstance(details.get("exception"), dict) else {}
        category = str(exception.get("category") or "")
        if category:
            return category
        event_lower = event.lower()
        command = details.get("command")
        if isinstance(command, dict):
            command = command.get("items") or []
        command_text = " ".join(str(item) for item in command) if isinstance(command, list) else str(command or "")
        if any(word in event_lower for word in ("network", "timeout", "connection", "http")):
            return "network"
        if (
            any(word in event_lower for word in ("ffmpeg", "ffprobe", "subprocess", "encoder", "mux"))
            or re.search(r"(?i)(^|[\\/ ])ff(?:mpeg|probe)(?:\.exe)?(?:$| )", command_text)
        ):
            return "external_process"
        if "translation" in event_lower or "translator" in event_lower:
            return "translation"
        if "tts" in event_lower or "voice" in event_lower or "audio" in event_lower:
            return "text_to_speech"
        if "cache" in event_lower or "checkpoint" in event_lower or "recovery" in event_lower:
            return "recovery_and_cache"
        if any(word in event_lower for word in ("file", "path", "batch", "input", "output")):
            return "file_pipeline"
        if any(word in event_lower for word in ("worker", "application", "app_", "session")):
            return "application"
        return "runtime"

    @staticmethod
    def _event_signature(event: str, level: str, family: str, message: str, details: dict) -> str:
        exception = details.get("exception") if isinstance(details.get("exception"), dict) else {}
        basis = {
            "event": event,
            "level": level,
            "family": family,
            "message": diagnostic_signature_value(message),
            "stage": details.get("stage") or "",
            "service": details.get("service") or details.get("component") or "",
            "provider": details.get("provider") or "",
            "exception": diagnostic_signature_value({
                "type": exception.get("type") or "",
                "category": exception.get("category") or "",
                "message": exception.get("message") or "",
            }),
            "command": diagnostic_signature_value(details.get("command") or []),
            "returncode": details.get("returncode"),
        }
        encoded = json.dumps(basis, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        return hashlib.sha256(encoded.encode("utf-8", errors="replace")).hexdigest()[:20]

    def _record_artifact_error_locked(self, artifact: str, exc: Exception):
        errors = self._summary["diagnostics_quality"]["artifact_write_errors"]
        item = {"artifact": artifact, "error": compact_exception(exc)}
        if not errors or errors[-1] != item:
            errors.append(item)
            del errors[:-10]

    @staticmethod
    def _compact_exception_evidence(exception) -> dict:
        if not isinstance(exception, dict):
            return {}
        chain = exception.get("chain") if isinstance(exception.get("chain"), list) else []
        return {
            "type": exception.get("type") or "",
            "category": exception.get("category") or "",
            "message": redact_diagnostic_text(exception.get("message") or "", max_len=1000),
            "chain": [
                {
                    "type": item.get("type") or "",
                    "category": item.get("category") or "",
                    "message": redact_diagnostic_text(item.get("message") or "", max_len=500),
                }
                for item in chain[:5]
                if isinstance(item, dict)
            ],
            "traceback_available_in_problems_jsonl": bool(exception.get("traceback")),
        }

    @staticmethod
    def _compact_problem_context(details: dict) -> dict:
        context = {
            key: details.get(key)
            for key in (
                "service", "operation", "provider", "returncode", "file_index", "files_total",
                "segment_index", "tts_segment_index", "attempt", "attempts_total", "max_attempts",
                "elapsed_sec", "timeout_sec",
            )
            if details.get(key) not in (None, "", [], {})
        }
        command = details.get("command")
        if isinstance(command, list) and command:
            context["command_preview"] = command[:16]
            context["command_items_total"] = len(command)
        stderr = details.get("stderr")
        if stderr:
            context["stderr_tail"] = redact_diagnostic_text(str(stderr)[-1000:], max_len=1000)
        return context

    @staticmethod
    def _remediation_playbooks() -> dict:
        return {
            "network_retry": {
                "likely_cause": "Нестабильный сетевой маршрут/VPN, тайм-аут или ограничение внешнего сервиса.",
                "code_search": ["NetworkStormGuard", "VideoTranslator.translate_segment", "VideoTranslator.generate_tts"],
                "recommended_fix": "Проверить ограниченные повторы, тайм-ауты, паузу шторма и продолжение из контрольной точки; не ослаблять проверку успешного ответа.",
                "verification": "Повторить тот же файл: серия должна либо восстановиться, либо завершиться одной ясной terminal error с сохранённым прогрессом.",
            },
            "user_cancel": {
                "likely_cause": "Пользователь запросил остановку во время обработки; это не обязательно баг программы.",
                "code_search": ["VideoTranslator.process", "VideoTranslatorApp._on_close", "save_batch_recovery_state"],
                "recommended_fix": "Не исправлять как ошибку без признаков самопроизвольной отмены; проверить контрольную точку и отсутствие принятого неполного результата.",
                "verification": "Отменить обработку вручную и убедиться, что исходник сохранён, а незавершённый пакет предлагается продолжить.",
            },
            "translation_tts": {
                "likely_cause": "Сбой внешнего сервиса перевода/озвучки либо некорректный ответ для конкретного сегмента.",
                "code_search": ["VideoTranslator.translate_segment", "VideoTranslator.generate_tts", "translation_segments_failed", "tts_summary"],
                "recommended_fix": "Сопоставить сегмент, провайдера и последнюю exception; сохранять готовые сегменты и не собирать неполное видео.",
                "verification": "Проверить повторный запуск, восстановление кэша/контрольной точки и полный итоговый счётчик сегментов.",
            },
            "ffmpeg_process": {
                "likely_cause": "FFmpeg/ffprobe завершился ошибкой, не поддержал параметр или создал неполный результат.",
                "code_search": ["run_subprocess", "run_subprocess_streaming", "output_has_video_and_audio", "file_pipeline_failed"],
                "recommended_fix": "Воспроизвести command, проверить returncode и конец stderr, затем сохранить строгую проверку видео+аудио.",
                "verification": "Запустить команду на проблемном входе и проверить ffprobe итогового MP4; исходник не удалять.",
            },
            "files_paths": {
                "likely_cause": "Файл отсутствует, занят, изменился, недоступен или путь не поддержан внешней программой.",
                "code_search": ["VideoTranslator.process", "VideoTranslatorApp._worker", "make_unique_output_path", "input_file_signature"],
                "recommended_fix": "Проверить существование/доступ/длину пути и конфликт результата; не удалять исходник при сбое.",
                "verification": "Повторить с тем же путём, с кириллицей и пробелами; подтвердить сохранность исходника и проверенный выход.",
            },
            "recovery_cache": {
                "likely_cause": "Контрольная точка или кэш не записались, устарели либо не соответствуют текущему входу.",
                "code_search": ["save_batch_recovery_state", "load_batch_recovery_state", "TTSCache", "input_file_signature"],
                "recommended_fix": "Проверить атомарную запись, сигнатуру входа и очистку только собственных записей.",
                "verification": "Имитировать прерывание и убедиться, что продолжились только незавершённые элементы без перезаписи готовых.",
            },
            "application_exception": {
                "likely_cause": "Причина определяется по первой exception/traceback и предшествующему этапу.",
                "code_search": ["ProblemLogger.write_exception", "VideoTranslator.process", "VideoTranslatorApp._worker"],
                "recommended_fix": "Найти первое место возникновения исключения, исправить первопричину и не заменять её пустым try/except.",
                "verification": "Добавить минимальный воспроизводящий тест и повторить исходный пользовательский сценарий.",
            },
        }

    @staticmethod
    def _compact_repeat_record(record: dict) -> dict:
        """Оставляет в контрольном повторе только изменяемый и поисковый контекст."""
        details = record.get("details") if isinstance(record.get("details"), dict) else {}
        compact_details = ProblemLogger._compact_problem_context(details)
        if details.get("stage"):
            compact_details["stage"] = details.get("stage")
        if details.get("input_path"):
            compact_details["input_path"] = details.get("input_path")
        exception = ProblemLogger._compact_exception_evidence(details.get("exception"))
        if exception:
            exception.pop("chain", None)
            compact_details["exception"] = exception
        result = dict(record)
        result["details"] = compact_details
        result["diagnostic"] = dict(record.get("diagnostic") or {})
        result["diagnostic"]["sample_kind"] = "repeat_checkpoint"
        return result

    @staticmethod
    def _issue_card(problem: dict) -> dict:
        event = str(problem.get("event") or "unknown")
        family = str(problem.get("category") or problem.get("family") or "runtime")
        level = str(problem.get("level") or "warning")
        occurrences = int(problem.get("occurrences_total") or problem.get("count") or 1)
        is_terminal = level == "error" or event.endswith(("_failed", "_crashed"))
        priority = "critical" if event in {"worker_thread_crashed", "application_startup_failed"} else (
            "high" if is_terminal else ("medium" if occurrences >= 3 else "low")
        )
        card = {
            "signature": problem.get("signature") or "",
            "priority": priority,
            "event": event,
            "family": family,
            "state": "terminal_failure" if is_terminal else "retry_or_warning",
            "occurrences_total": occurrences,
            "stored_samples": int(problem.get("stored_samples") or min(occurrences, 1)),
            "confirmed_evidence": {
                "message": problem.get("message") or "",
                "stage": problem.get("stage") or "",
                "input_path": problem.get("input_path") or "",
                "exception": ProblemLogger._compact_exception_evidence(problem.get("exception")),
                "first_sequence": problem.get("first_sequence"),
                "last_sequence": problem.get("last_sequence"),
                "first_timestamp": problem.get("first_timestamp") or "",
                "last_timestamp": problem.get("last_timestamp") or "",
            },
            "cause_status": "likely_not_proven",
            "do_not_assume": "Предположение становится подтверждённой причиной только после проверки evidence и соседних событий.",
        }
        if family in {"network", "network_timeout", "network_connection", "rate_limit", "timeout"}:
            card["playbook"] = "network_retry"
        elif event == "file_pipeline_cancelled":
            card["playbook"] = "user_cancel"
            card["cause_status"] = "confirmed_user_action_if_app_close_requested_precedes"
        elif family in {"text_to_speech", "translation"}:
            card["playbook"] = "translation_tts"
        elif family in {"external_process", "subprocess_failed", "ffmpeg"}:
            card["playbook"] = "ffmpeg_process"
        elif family in {"file_pipeline", "file_not_found", "permission_denied", "file_access", "path"}:
            card["playbook"] = "files_paths"
        elif family in {"recovery_and_cache", "cache"}:
            card["playbook"] = "recovery_cache"
        else:
            card["playbook"] = "application_exception"
        return card

    def _build_codex_analysis_locked(self) -> dict:
        counters = self._summary.get("counters") or {}
        warnings = int(counters.get("warning") or 0)
        errors = int(counters.get("error") or 0)
        categories = {
            key for key, count in (self._summary.get("problem_counts_by_category") or {}).items()
            if int(count or 0) > 0
        }
        problems = self._summary.get("recent_problems") or []
        repeated = sum(
            max(0, int(item.get("occurrences_total") or item.get("count") or 1) - 1)
            for item in problems
        )
        quality = self._summary.get("diagnostics_quality") or {}
        all_issue_cards = sorted(
            (self._issue_card(item) for item in problems),
            key=lambda item: (
                {"critical": 0, "high": 1, "medium": 2, "low": 3}.get(item.get("priority"), 4),
                -int(item.get("occurrences_total") or 0),
            ),
        )
        issue_cards = all_issue_cards[:10]

        observations = []
        if errors:
            observations.append(f"Записано ошибок уровня error: {errors}.")
        if warnings:
            observations.append(f"Записано предупреждений: {warnings}.")
        if repeated:
            observations.append(
                f"Повторных срабатываний уже известных сигнатур: {repeated}; "
                f"компактно не записано строк: {int(quality.get('repeat_events_compacted') or 0)}."
            )
        previous = self._summary.get("previous_session") or {}
        if previous.get("status") == "interrupted":
            observations.append("Подтверждено незавершённое закрытие предыдущей сессии.")
        if not observations:
            observations.append("На текущем этапе предупреждения и ошибки не зарегистрированы.")

        program_improvements = []
        if categories & {"network", "network_timeout", "connection", "text_to_speech"}:
            program_improvements.append({
                "priority": "high" if errors else "medium",
                "area": "network_and_tts",
                "proposal": "Проверить тайм-ауты, ограничение повторов, восстановление после сети и сохранение выбранного голоса.",
                "basis": "В сессии есть сетевые или TTS-события; точную причину подтвердить по problems.jsonl.",
            })
        if categories & {"external_process", "subprocess", "ffmpeg"}:
            program_improvements.append({
                "priority": "high",
                "area": "ffmpeg_pipeline",
                "proposal": "Проверить команду, код завершения, stderr и валидацию созданного медиафайла до принятия результата.",
                "basis": "Зарегистрирована проблема внешнего процесса или кодека.",
            })
        if categories & {"file_pipeline", "file_access", "permission", "path"}:
            program_improvements.append({
                "priority": "high" if errors else "medium",
                "area": "files_and_paths",
                "proposal": "Проверить существование, доступ, занятость, длинные пути и запрет удаления исходника при сбое.",
                "basis": "Есть проблема файлового конвейера или доступа к пути.",
            })
        if categories & {"recovery_and_cache", "cache"} or previous.get("status") == "interrupted":
            program_improvements.append({
                "priority": "medium",
                "area": "recovery",
                "proposal": "Проверить атомарность контрольных точек и безопасное продолжение только незавершённых элементов.",
                "basis": "Есть событие восстановления/кэша или прерванная прошлая сессия.",
            })
        if categories & {"application", "runtime", "unknown"} and errors:
            program_improvements.append({
                "priority": "high",
                "area": "application_code",
                "proposal": "Начать исправление с первого traceback и его цепочки exception; не подавлять исключение.",
                "basis": "Есть ошибка приложения, не отнесённая к более узкой подсистеме.",
            })
        if not program_improvements:
            program_improvements.append({
                "priority": "low",
                "area": "no_confirmed_change",
                "proposal": "Не менять рабочее поведение без подтверждённой проблемы; дождаться полного сценария или изучить events.jsonl.",
                "basis": "Текущая сессия пока не содержит диагностических оснований для исправления программы.",
            })

        logging_improvements = []
        missing_context = int(quality.get("problems_missing_stage_and_input") or 0)
        if missing_context:
            logging_improvements.append({
                "priority": "high",
                "proposal": "Добавить stage и input_path в события, где сейчас отсутствуют оба поля.",
                "basis": f"Проблем без этапа и входного объекта: {missing_context}.",
            })
        errors_without_traceback = int(quality.get("errors_without_traceback") or 0)
        if errors_without_traceback:
            logging_improvements.append({
                "priority": "high",
                "proposal": "Для исключений использовать write_exception, чтобы сохранялась цепочка и traceback.",
                "basis": f"Error-событий без traceback: {errors_without_traceback}.",
            })
        artifact_errors = quality.get("artifact_write_errors") or []
        if artifact_errors:
            logging_improvements.append({
                "priority": "high",
                "proposal": "Проверить доступ и свободное место для дополнительных диагностических артефактов.",
                "basis": f"Сбоев записи артефактов: {len(artifact_errors)}.",
            })
        if not logging_improvements:
            logging_improvements.append({
                "priority": "low",
                "proposal": "Сохранять текущую структуру; расширять поля только для нового подтверждённого класса проблем.",
                "basis": "Обязательный контекст присутствует, явных пробелов самопроверка не нашла.",
            })

        return {
            "generated_at": self._summary.get("updated_at") or "",
            "nature": "Автоматические подтверждённые счётчики и эвристические кандидаты; предложения требуют проверки по исходному коду и полной хронологии.",
            "result": "errors_detected" if errors else ("warnings_detected" if warnings else "no_problems_detected_yet"),
            "confirmed_observations": observations,
            "problem_categories": sorted(categories),
            "primary_issue_signature": issue_cards[0]["signature"] if issue_cards else "",
            "issue_cards": issue_cards,
            "issue_cards_omitted": max(0, len(all_issue_cards) - len(issue_cards)),
            "remediation_playbooks": self._remediation_playbooks(),
            "program_improvement_candidates": program_improvements,
            "logging_improvement_candidates": logging_improvements,
            "recommended_reading_order": [
                "report.md", "summary.json", "problems.jsonl", "events.jsonl"
            ],
            "next_checks": [
                "Сопоставить первую проблему с предыдущими 10–30 событиями в events.jsonl.",
                "Проверить traceback, команду, returncode и stderr, если эти поля применимы.",
                "Найти место записи event в коде и проверить первопричину, а не подавлять исключение.",
                "После исправления воспроизвести сценарий и сравнить новую отдельную сессию с проблемной.",
            ],
        }

    def _manifest_payload_locked(self) -> dict:
        return {
            "schema_version": PROBLEM_MANIFEST_SCHEMA_VERSION,
            "artifact_type": "video_translator_problem_session",
            "diagnostic_schema_version": PROBLEM_LOG_SCHEMA_VERSION,
            "app_version": APP_DIAGNOSTIC_VERSION,
            "session_id": self.session_id,
            "status": self._summary.get("session_status") or self._summary.get("status"),
            "created_from_process": os.getpid(),
            "updated_at": self._summary.get("updated_at") or datetime.now().astimezone().isoformat(timespec="seconds"),
            "encoding": "UTF-8",
            "files": {
                "report.md": "Краткий читаемый отчёт и порядок анализа.",
                "summary.json": "Машиночитаемое текущее состояние, группы проблем и подсказки.",
                "problems.jsonl": "Warning/error; первые примеры и контрольные повторы, одна JSON-запись на строку.",
                "events.jsonl": "Информационная временная шкала без дублирования warning/error; объединяется с problems.jsonl по sequence.",
                "manifest.json": "Описание этой папки и схемы артефактов.",
            },
            "repeat_compaction": {
                "exact_totals_in": "summary.json -> recent_problems[].occurrences_total",
                "stored_samples_in": "summary.json -> recent_problems[].stored_samples",
                "policy": "Первые 3, затем 5/10/20/50/100, каждый 250-й и последний итог в summary/report.",
                "loss_boundary": "Промежуточные одинаковые повторы не имеют отдельных JSONL-строк; первый/последний контекст и точный счётчик сохранены в summary.json.",
            },
            "retention": {
                "days": PROBLEM_LOG_RETENTION_DAYS,
                "max_sessions": PROBLEM_LOG_MAX_FILES,
                "scope": "Удаляются только папки с marker artifact_type=video_translator_problem_session.",
            },
            "validation_boundaries": [
                "Отчёт не доказывает визуальную корректность GUI.",
                "Успешный subprocess не доказывает качество итогового перевода или озвучки без проверки медиа.",
                "Сетевые причины являются подтверждёнными только при наличии соответствующей ошибки в событии.",
            ],
        }

    def _write_manifest_locked(self):
        atomic_write_json(self.manifest_path, self._manifest_payload_locked())

    def _write_latest_index_locked(self):
        files = {
            "report": problem_log_relative_path(self.root_dir, self.report_path),
            "summary": problem_log_relative_path(self.root_dir, self.summary_path),
            "problems": problem_log_relative_path(self.root_dir, self.problem_path),
            "events": problem_log_relative_path(self.root_dir, self.path),
            "manifest": problem_log_relative_path(self.root_dir, self.manifest_path),
        }
        index = {
            "schema_version": PROBLEM_INDEX_SCHEMA_VERSION,
            "artifact_type": "video_translator_latest_session_pointer",
            "updated_at": self._summary.get("updated_at") or "",
            "session_id": self.session_id,
            "status": self._summary.get("session_status") or self._summary.get("status"),
            "session_dir": problem_log_relative_path(self.root_dir, self.session_dir),
            "files": files,
            "start_here": files["report"],
        }
        atomic_write_json(self.latest_index_path, index)

    @staticmethod
    def _markdown_cell(value) -> str:
        return str(value if value is not None else "").replace("|", "\\|").replace("\r", " ").replace("\n", " ")

    def _render_report_locked(self) -> str:
        summary = self._summary
        counters = summary.get("counters") or {}
        batch = summary.get("batch") or {}
        current = summary.get("current_file") or {}
        analysis = summary.get("codex_analysis") or {}
        quality = summary.get("diagnostics_quality") or {}
        lines = [
            "# Диагностический отчёт сессии",
            "",
            f"- Сессия: `{self.session_id}`",
            f"- Статус: `{summary.get('session_status') or summary.get('status')}`",
            f"- Обновлено: `{summary.get('updated_at') or 'ещё нет событий'}`",
            f"- События: {sum(int(value or 0) for value in counters.values())}; предупреждения: {int(counters.get('warning') or 0)}; ошибки: {int(counters.get('error') or 0)}",
            f"- Строк сохранено: {int(quality.get('records_stored_total') or 0)}; компактно объединено повторов: {int(quality.get('repeat_events_compacted') or 0)}; приблизительно сэкономлено: {int(quality.get('estimated_bytes_saved') or 0)} байт",
            f"- Текущий этап: `{summary.get('current_stage') or 'не указан'}`",
            f"- Текущий файл: `{summary.get('current_input_path') or 'не выбран'}`",
            "",
            "## Быстрый вывод",
            "",
        ]
        lines.extend(f"- {item}" for item in analysis.get("confirmed_observations") or [])
        lines.extend([
            "",
            "## Пакет и текущий файл",
            "",
            f"- Пакет: статус `{batch.get('status') or 'idle'}`, всего {int(batch.get('total') or 0)}, успешно {int(batch.get('successful') or 0)}, ошибок {int(batch.get('failed') or 0)}, ожидают {int(batch.get('pending') or 0)}.",
            f"- Файл: статус `{current.get('status') or 'idle'}`, этап `{current.get('stage') or 'не указан'}`, результат `{current.get('output_path') or 'не создан'}`.",
            "",
            "## Сгруппированные проблемы",
            "",
        ])
        problems = summary.get("recent_problems") or []
        if problems:
            lines.extend([
                "| Уровень | Событие | Категория | Всего | Строк | Этап | Сообщение |",
                "|---|---|---|---:|---:|---|---|",
            ])
            for item in problems:
                lines.append(
                    "| {level} | {event} | {category} | {count} | {stored} | {stage} | {message} |".format(
                        level=self._markdown_cell(item.get("level")),
                        event=self._markdown_cell(item.get("event")),
                        category=self._markdown_cell(item.get("category") or item.get("family")),
                        count=int(item.get("occurrences_total") or item.get("count") or 1),
                        stored=int(item.get("stored_samples") or 1),
                        stage=self._markdown_cell(item.get("stage")),
                        message=self._markdown_cell(item.get("message")),
                    )
                )
        else:
            lines.append("Пока предупреждений и ошибок нет.")

        lines.extend(["", "## Карточки исправления проблем", ""])
        issue_cards = analysis.get("issue_cards") or []
        if issue_cards:
            playbooks = analysis.get("remediation_playbooks") or {}
            for index, card in enumerate(issue_cards, 1):
                evidence = card.get("confirmed_evidence") or {}
                playbook_name = card.get("playbook") or "application_exception"
                playbook = playbooks.get(playbook_name) or {}
                lines.extend([
                    f"### {index}. {card.get('event', 'unknown')} — {card.get('priority', 'medium')}",
                    "",
                    f"- Сигнатура: `{card.get('signature') or 'нет'}`; всего: {int(card.get('occurrences_total') or 0)}; сохранено строк: {int(card.get('stored_samples') or 0)}.",
                    f"- Подтверждено логом: {evidence.get('message') or 'сообщение отсутствует'} Этап: `{evidence.get('stage') or 'не указан'}`.",
                    f"- Сценарий исправления: `{playbook_name}`.",
                    f"- Вероятная причина: {playbook.get('likely_cause') or 'требует анализа'}",
                    f"- Где искать в коде: `{', '.join(playbook.get('code_search') or [])}`.",
                    f"- Как исправлять: {playbook.get('recommended_fix') or ''}",
                    f"- Как проверить: {playbook.get('verification') or ''}",
                    f"- Ограничение вывода: {card.get('do_not_assume') or ''}",
                    "",
                ])
        else:
            lines.append("Карточек нет: в сессии не зарегистрированы warning/error.")
        omitted_cards = int(analysis.get("issue_cards_omitted") or 0)
        if omitted_cards:
            lines.append(
                f"\nЕщё карточек вне компактного отчёта: {omitted_cards}. Их счётчики доступны в "
                "`problem_counts_by_event`/`problem_counts_by_category`, а примеры — в `problems.jsonl`."
            )

        lines.extend(["", "## Что можно улучшить в программе", ""])
        for item in analysis.get("program_improvement_candidates") or []:
            lines.append(f"- **{item.get('priority', 'medium')} / {item.get('area', 'general')}** — {item.get('proposal', '')} Основание: {item.get('basis', '')}")
        lines.extend(["", "## Что можно улучшить в логах", ""])
        for item in analysis.get("logging_improvement_candidates") or []:
            lines.append(f"- **{item.get('priority', 'medium')}** — {item.get('proposal', '')} Основание: {item.get('basis', '')}")
        lines.extend(["", "## Самые частые события", ""])
        event_counts = sorted(
            (summary.get("event_counts_by_event") or {}).items(),
            key=lambda pair: (-int(pair[1] or 0), pair[0]),
        )[:12]
        if event_counts:
            lines.extend(f"- `{event}` — {int(count or 0)}" for event, count in event_counts)
        else:
            lines.append("События ещё не зарегистрированы.")
        lines.extend([
            "",
            "## Порядок дальнейшего анализа",
            "",
        ])
        lines.extend(f"{index}. {item}" for index, item in enumerate(analysis.get("next_checks") or [], 1))
        lines.extend([
            "",
            "## Файлы этой сессии",
            "",
            "- `summary.json` — полная компактная сводка.",
            "- `problems.jsonl` — выборка предупреждений/ошибок; точные итоги повторов находятся в `summary.json`.",
            "- `events.jsonl` — информационная временная шкала без дублей warning/error; обе JSONL объединяются по `sequence`.",
            "- `manifest.json` — схема и границы достоверности.",
            "",
            "> Автоматические предложения — кандидаты для проверки, а не доказанная первопричина.",
            "",
        ])
        return "\n".join(lines)

    def _update_summary_locked(self, record: dict):
        level = str(record.get("level") or "info")
        event = str(record.get("event") or "")
        details = record.get("details") or {}
        timestamp = record.get("timestamp") or ""
        self._summary["updated_at"] = record.get("timestamp") or ""
        self._summary["last_event"] = {
            "event": event,
            "level": level,
            "message": record.get("message") or "",
            "sequence": record.get("sequence"),
        }
        self._summary["counters"][level] = int(self._summary["counters"].get(level, 0)) + 1
        event_counts = self._summary["event_counts_by_event"]
        event_counts[event] = int(event_counts.get(event, 0)) + 1
        quality = self._summary["diagnostics_quality"]
        quality["events_total"] = int(quality.get("events_total") or 0) + 1
        if details.get("stage"):
            self._summary["current_stage"] = details.get("stage")
            self._summary["current_file"]["stage"] = details.get("stage")
        if details.get("input_path"):
            self._summary["current_input_path"] = details.get("input_path")
            self._summary["current_file"]["input_path"] = details.get("input_path")

        if event == "batch_started":
            raw_files = details.get("files") or []
            if isinstance(raw_files, dict):
                raw_files = raw_files.get("items") or []
            raw_states = details.get("file_states") or []
            if isinstance(raw_states, dict):
                raw_states = raw_states.get("items") or []
            if raw_states:
                files = [dict(item) for item in raw_states]
            else:
                files = [
                    {"file_index": index, "input_path": path, "status": "pending"}
                    for index, path in enumerate(raw_files, 1)
                ]
            total = int(details.get("files_count") or len(files))
            statuses = [item.get("status") for item in files]
            self._summary["batch"] = {
                "status": "running",
                "batch_id": details.get("batch_id") or "",
                "resumed": bool(details.get("resumed")),
                "total": total,
                "started": 0,
                "successful": statuses.count("succeeded"),
                "failed": statuses.count("failed"),
                "pending": sum(status != "succeeded" for status in statuses),
                "current_index": 0,
                "output_dir": details.get("output_dir") or "",
                "files": files,
            }
        elif event == "batch_file_started":
            batch = self._summary["batch"]
            index = int(details.get("file_index") or 0)
            batch["status"] = "running"
            batch["current_index"] = index
            batch["started"] = max(int(batch.get("started") or 0), index)
            for item in batch.get("files", []):
                if int(item.get("file_index") or 0) == index:
                    item.update({
                        "input_path": details.get("input_path") or item.get("input_path") or "",
                        "output_path": details.get("output_path") or "",
                        "status": "processing",
                    })
                    break
        elif event == "batch_file_finished":
            batch = self._summary["batch"]
            index = int(details.get("file_index") or 0)
            success = bool(details.get("success"))
            for item in batch.get("files", []):
                if int(item.get("file_index") or 0) == index:
                    item.update({
                        "input_path": details.get("input_path") or item.get("input_path") or "",
                        "output_path": details.get("output_path") or item.get("output_path") or "",
                        "status": "succeeded" if success else "failed",
                        "elapsed_sec": details.get("elapsed_sec"),
                    })
                    break
            statuses = [item.get("status") for item in batch.get("files", [])]
            batch["successful"] = statuses.count("succeeded")
            batch["failed"] = sum(status in {"failed", "missing"} for status in statuses)
            batch["pending"] = sum(status != "succeeded" for status in statuses)
        elif event == "batch_finished":
            batch = self._summary["batch"]
            successful = int(details.get("successful") or 0)
            total = int(details.get("total") or batch.get("total") or 0)
            cancelled = bool(details.get("cancelled"))
            batch.update({
                "status": "cancelled" if cancelled else ("completed" if successful == total else "completed_with_errors"),
                "successful": successful,
                "failed": int(details.get("failed") or 0),
                "pending": int(details.get("pending") or 0),
                "elapsed_sec": details.get("elapsed_sec"),
            })

        if event == "file_pipeline_started":
            settings = details.get("settings") or {}
            self._summary["current_file"] = {
                "status": "running",
                "stage": details.get("stage") or "initialization",
                "input_path": details.get("input_path") or self._summary.get("current_input_path") or "",
                "output_path": details.get("output_path") or "",
            }
            self._summary["selected_video_encoder"] = {}
            self._summary["tts_cache"] = {}
            self._summary["tts_voice"] = {
                "status": "idle",
                "selected_voice": settings.get("voice") or "",
                "fallback_provider": "",
                "voice_may_differ": False,
            }
        elif event in {"file_pipeline_finished", "file_pipeline_failed", "file_pipeline_cancelled"}:
            self._summary["current_file"]["status"] = event.removeprefix("file_pipeline_")
            self._summary["current_file"]["elapsed_sec"] = details.get("elapsed_sec")
            if details.get("final_path"):
                self._summary["current_file"]["output_path"] = details.get("final_path")
        elif event == "app_session_finished":
            self._summary["status"] = "closed"
            self._summary["session_status"] = "closed"
            self._summary["current_stage"] = "closed"
            self._summary["current_file"]["stage"] = "closed"
        elif event == "app_close_requested" and details.get("processing"):
            self._summary["batch"]["status"] = "cancellation_requested"
        if event == "video_encoder_selected":
            self._summary["selected_video_encoder"] = {
                "input_path": details.get("input_path") or self._summary.get("current_input_path") or "",
                "name": details.get("encoder_name") or "",
                "processing_speed_x": details.get("processing_speed_x"),
                "elapsed_sec": details.get("elapsed_sec"),
                "command": details.get("command") or [],
            }
        if event == "tts_voice_preservation_started":
            self._summary["tts_voice"] = {
                "status": "waiting_for_edge_tts",
                "selected_voice": details.get("voice") or "",
                "fallback_provider": "",
                "voice_may_differ": False,
                "wait_sec": details.get("wait_sec"),
                "incident": details.get("incident"),
            }
        elif event == "tts_edge_probe_started":
            self._summary["tts_voice"]["status"] = "checking_edge_tts"
        elif event in {"tts_voice_preservation_recovered", "network_storm_finished"}:
            if event != "network_storm_finished" or details.get("service") == "edge_tts":
                self._summary["tts_voice"]["status"] = "selected_voice_restored"
        elif event == "tts_gtts_fallback_enabled":
            self._summary["tts_voice"] = {
                "status": "gtts_fallback",
                "selected_voice": details.get("voice") or self._summary["tts_voice"].get("selected_voice", ""),
                "fallback_provider": "gtts",
                "voice_may_differ": True,
                "reason": details.get("reason") or "",
                "incident": details.get("incident"),
            }
        elif event == "tts_summary":
            self._summary["tts_voice"].update({
                "status": details.get("voice_consistency") or "finished",
                "selected_voice": details.get("selected_voice") or self._summary["tts_voice"].get("selected_voice", ""),
                "fallback_provider": "gtts" if details.get("gtts_fallback", 0) else "",
                "voice_may_differ": bool(details.get("gtts_fallback", 0)),
                "gtts_segments": details.get("gtts_fallback", 0),
            })
        if event in {
            "tts_cache_maintenance_finished",
            "tts_cache_released_after_success",
            "tts_prepared_cache_released_after_stop",
        }:
            self._summary["tts_cache"] = {
                "event": event,
                "input_path": details.get("input_path") or self._summary.get("current_input_path") or "",
                "deleted_entries": details.get("deleted_entries", 0),
                "deleted_files": details.get("deleted_files", 0),
                "freed_bytes": details.get("freed_bytes", 0),
                "remaining_bytes": details.get("remaining_bytes"),
                "errors": details.get("errors") or [],
            }

        if event == "network_storm_started":
            network = self._summary["network"]
            service = str(details.get("service") or details.get("component") or "network")
            network["incidents_started"] = int(network.get("incidents_started") or 0) + 1
            network["active_services"][service] = {
                "active": True,
                "started_at": timestamp,
                "failures_in_window": details.get("failures_in_window"),
                "pause_sec": details.get("pause_sec"),
            }
        elif event == "network_storm_finished":
            network = self._summary["network"]
            service = str(details.get("service") or details.get("component") or "network")
            network["incidents_finished"] = int(network.get("incidents_finished") or 0) + 1
            network["active_services"][service] = {
                "active": False,
                "finished_at": timestamp,
                "duration_sec": details.get("duration_sec"),
                "successful_requests": details.get("successful_requests"),
            }

        if level in {"warning", "error"}:
            quality["problem_events_total"] = int(quality.get("problem_events_total") or 0) + 1
            counts = self._summary["problem_counts_by_event"]
            counts[event] = int(counts.get(event, 0)) + 1
            recent = self._summary["recent_problems"]
            exception = details.get("exception") or {}
            diagnostic = record.get("diagnostic") or {}
            category = exception.get("category") or diagnostic.get("family") or "runtime"
            category_counts = self._summary["problem_counts_by_category"]
            category_counts[category] = int(category_counts.get(category, 0)) + 1
            message = record.get("message") or ""
            signature = str(diagnostic.get("signature") or self._event_signature(
                event, level, str(diagnostic.get("family") or category), message, details
            ))
            if signature not in self._problem_signatures_seen:
                self._problem_signatures_seen.add(signature)
                quality["unique_problem_signatures_total"] = len(self._problem_signatures_seen)
            occurrence = int(diagnostic.get("occurrence") or 1)
            stage = details.get("stage") or self._summary.get("current_stage") or ""
            input_path = details.get("input_path") or self._summary.get("current_input_path") or ""
            if stage:
                quality["problems_with_stage"] = int(quality.get("problems_with_stage") or 0) + 1
            if input_path:
                quality["problems_with_input_path"] = int(quality.get("problems_with_input_path") or 0) + 1
            if not stage and not input_path:
                quality["problems_missing_stage_and_input"] = int(
                    quality.get("problems_missing_stage_and_input") or 0
                ) + 1
            if isinstance(exception, dict) and exception.get("traceback"):
                quality["exceptions_with_traceback"] = int(
                    quality.get("exceptions_with_traceback") or 0
                ) + 1
            if level == "error":
                quality["error_events_total"] = int(quality.get("error_events_total") or 0) + 1
                if not (isinstance(exception, dict) and exception.get("traceback")):
                    quality["errors_without_traceback"] = int(
                        quality.get("errors_without_traceback") or 0
                    ) + 1
            existing = next((item for item in recent if item.get("signature") == signature), None)
            if existing is None and occurrence > 1:
                quality["problem_signatures_outside_recent_window"] = int(
                    quality.get("problem_signatures_outside_recent_window") or 0
                ) + 1
            segment_index = details.get("segment_index")
            if segment_index is None:
                segment_index = details.get("tts_segment_index")
            context = self._compact_problem_context(details)
            compact_exception = self._compact_exception_evidence(exception)
            if existing:
                existing.update({
                    "last_timestamp": timestamp,
                    "last_sequence": record.get("sequence"),
                    "count": int(existing.get("occurrences_total") or existing.get("count") or 1) + 1,
                    "occurrences_total": int(
                        existing.get("occurrences_total") or existing.get("count") or 1
                    ) + 1,
                    "stored_samples": int(existing.get("stored_samples") or 0),
                    "last_segment_index": segment_index,
                    "last_exception_message": compact_exception.get("message") or "",
                    "last_context": context,
                })
                recent.remove(existing)
                recent.append(existing)
            else:
                recent.append({
                    "signature": signature,
                    "first_timestamp": timestamp,
                    "last_timestamp": timestamp,
                    "first_sequence": record.get("sequence"),
                    "last_sequence": record.get("sequence"),
                    "count": occurrence,
                    "occurrences_total": occurrence,
                    "stored_samples": 0,
                    "event": event,
                    "level": level,
                    "message": message,
                    "category": category,
                    "family": diagnostic.get("family") or category,
                    "stage": stage,
                    "input_path": input_path,
                    "last_segment_index": segment_index,
                    "exception": compact_exception,
                    "context": context,
                })
            del recent[:-20]

    def _sync_stored_sample_locked(self, record: dict):
        diagnostic = record.get("diagnostic") or {}
        signature = diagnostic.get("signature") or ""
        if not signature:
            return
        item = next(
            (problem for problem in self._summary.get("recent_problems", []) if problem.get("signature") == signature),
            None,
        )
        if item:
            item["stored_samples"] = int(item.get("stored_samples") or 0) + 1

    def _write_summary_locked(self, force: bool = False):
        now = time.monotonic()
        if not force and now - self._last_summary_write < 1.0:
            return
        self._summary["codex_analysis"] = self._build_codex_analysis_locked()
        atomic_write_json(self.summary_path, self._summary)
        artifact_errors_before = len(
            self._summary["diagnostics_quality"].get("artifact_write_errors") or []
        )
        session_terminal = self._summary.get("session_status") != "running"
        refresh_readable_artifacts = (
            session_terminal
            or not self.report_path.exists()
            or now - self._last_report_write >= PROBLEM_REPORT_REFRESH_SEC
        )
        if refresh_readable_artifacts:
            try:
                atomic_write_text(self.report_path, self._render_report_locked())
                self._last_report_write = now
            except Exception as exc:
                self._record_artifact_error_locked("report.md", exc)
            try:
                self._write_manifest_locked()
            except Exception as exc:
                self._record_artifact_error_locked("manifest.json", exc)
        try:
            self._write_latest_index_locked()
        except Exception as exc:
            self._record_artifact_error_locked("latest_session.json", exc)
        if len(self._summary["diagnostics_quality"].get("artifact_write_errors") or []) > artifact_errors_before:
            self._summary["codex_analysis"] = self._build_codex_analysis_locked()
            try:
                atomic_write_json(self.summary_path, self._summary)
            except Exception:
                pass
        self._last_summary_write = now

    def event(self, event: str, level: str = "info", message: str = "", **details):
        with self.lock:
            if not hasattr(self, "file") or self.file.closed:
                return
            self.sequence += 1
            safe_details = diagnostic_json_value(details)
            level = str(level)
            event = str(event)
            family = self._problem_family(event, safe_details)
            safe_message = redact_diagnostic_text(message, max_len=6000)
            signature = self._event_signature(event, level, family, safe_message, safe_details)
            occurrence = int(self._occurrence_counts.get(signature, 0)) + 1
            self._occurrence_counts[signature] = occurrence
            compact_repeat = event in PROBLEM_REPEAT_SAMPLE_EVENTS and occurrence > 1
            store_record = not compact_repeat or should_store_problem_occurrence(occurrence)
            record = {
                "schema_version": PROBLEM_LOG_SCHEMA_VERSION,
                "timestamp": datetime.now().astimezone().isoformat(timespec="milliseconds"),
                "sequence": self.sequence,
                "level": level,
                "event": event,
                "message": safe_message,
                "thread": threading.current_thread().name,
                "diagnostic": {
                    "is_problem": level in {"warning", "error"},
                    "family": family,
                    "actionability": "investigate" if level == "error" else ("review" if level == "warning" else "context"),
                    "signature": signature,
                    "occurrence": occurrence,
                    "stored_occurrence": occurrence if store_record else 0,
                },
                "details": safe_details,
            }
            self._update_summary_locked(record)
            quality = self._summary["diagnostics_quality"]
            if store_record:
                stored_record = self._compact_repeat_record(record) if compact_repeat else record
                line = json.dumps(stored_record, ensure_ascii=False, separators=(",", ":")) + "\n"
                if level in {"warning", "error"} and self.problem_file is not None:
                    try:
                        self.problem_file.write(line)
                        self.problem_file.flush()
                        self._stored_event_counts["problems"] += 1
                        quality["problem_events_stored"] = self._stored_event_counts["problems"]
                        self._sync_stored_sample_locked(record)
                    except OSError as exc:
                        self._record_artifact_error_locked("problems.jsonl", exc)
                    quality["problem_timeline_duplicates_avoided"] = int(
                        quality.get("problem_timeline_duplicates_avoided") or 0
                    ) + 1
                else:
                    self.file.write(line)
                    self.file.flush()
                    self._stored_event_counts["timeline"] += 1
                    quality["timeline_events_stored"] = self._stored_event_counts["timeline"]
                quality["records_stored_total"] = (
                    self._stored_event_counts["timeline"] + self._stored_event_counts["problems"]
                )
            else:
                estimated_line = json.dumps(record, ensure_ascii=False, separators=(",", ":")) + "\n"
                quality["repeat_events_compacted"] = int(quality.get("repeat_events_compacted") or 0) + 1
                quality["estimated_bytes_saved"] = int(quality.get("estimated_bytes_saved") or 0) + len(
                    estimated_line.encode("utf-8", errors="replace")
                )
            force_summary = (
                str(level) == "error"
                or (str(level) == "warning" and store_record)
                or str(event) in {
                    "app_session_started", "app_session_finished", "batch_started", "batch_finished",
                    "batch_file_started", "batch_file_finished", "app_close_requested",
                    "file_pipeline_started", "file_pipeline_finished", "file_pipeline_failed",
                     "file_pipeline_cancelled", "stage_started", "stage_finished",
                     "network_storm_started", "network_storm_finished", "tts_summary",
                     "video_encoder_selected", "tts_voice_preservation_started",
                     "tts_voice_preservation_recovered", "tts_gtts_fallback_enabled",
                     "tts_cache_maintenance_finished", "tts_cache_released_after_success",
                     "tts_prepared_cache_released_after_stop",
                 }
            )
            try:
                self._write_summary_locked(force=force_summary)
            except Exception:
                # Сбой краткой сводки не должен ломать основной JSONL-журнал.
                pass

    def write_exception(self, event: str, exc: Exception, message: str = "", **details):
        trace = "".join(traceback.format_exception(type(exc), exc, exc.__traceback__))
        self.event(
            event,
            level="error",
            message=message or compact_exception(exc),
            exception={
                "type": type(exc).__name__,
                "category": classify_exception(exc),
                "message": compact_exception(exc, max_len=2000),
                "chain": exception_chain(exc),
                "traceback": redact_diagnostic_text(trace),
            },
            **details,
        )

    def close(self):
        try:
            self.event("app_session_finished", message="Программа закрывает диагностический журнал.")
            with self.lock:
                try:
                    self._write_summary_locked(force=True)
                except Exception:
                    pass
                if hasattr(self, "file") and not self.file.closed:
                    self.file.close()
                if getattr(self, "problem_file", None) is not None and not self.problem_file.closed:
                    self.problem_file.close()
        except Exception:
            pass


class ActivityHeartbeat:
    """Пишет редкий признак жизни во время долгой блокирующей операции."""

    def __init__(self, log, label: str, interval: int = 60, event_cb=None, **event_details):
        self.log = log
        self.label = label
        self.interval = max(15, int(interval))
        self.event_cb = event_cb
        self.event_details = dict(event_details or {})
        self.started_at = 0.0
        self.stop_event = threading.Event()
        self.thread = None

    def __enter__(self):
        self.started_at = time.monotonic()
        self.thread = threading.Thread(target=self._run, name=f"heartbeat-{self.label}", daemon=True)
        self.thread.start()
        return self

    def _run(self):
        while not self.stop_event.wait(self.interval):
            elapsed = int(time.monotonic() - self.started_at)
            try:
                self.log(f"   ⏳ {self.label}: работа продолжается, прошло {fmt_time(elapsed)}...")
                if self.event_cb:
                    self.event_cb(
                        "activity_heartbeat",
                        message="Долгая операция продолжает выполняться.",
                        operation=self.label,
                        elapsed_sec=elapsed,
                        **self.event_details,
                    )
            except Exception:
                return

    def __exit__(self, exc_type, exc_value, exc_tb):
        self.stop_event.set()
        return False


def atomic_write_json(path: Path, data: dict):
    """Атомарно записывает небольшой служебный JSON рядом с программой."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temp_path = path.with_name(f".{path.name}.{os.getpid()}.{threading.get_ident()}.tmp")
    try:
        with open(temp_path, "w", encoding="utf-8", newline="\n") as file:
            json.dump(data, file, ensure_ascii=False, indent=2)
            file.flush()
            os.fsync(file.fileno())
        os.replace(temp_path, path)
    finally:
        try:
            if temp_path.exists():
                temp_path.unlink()
        except OSError:
            pass


def input_file_signature(path: str) -> dict:
    """Минимальная сигнатура входа для безопасного продолжения пакетной задачи."""
    result = {"path": os.path.abspath(path)}
    try:
        stat = os.stat(path)
        result.update({"size": int(stat.st_size), "modified_ns": int(stat.st_mtime_ns)})
    except OSError:
        pass
    return result


def get_batch_recovery_state_path() -> Path:
    return get_translation_checkpoints_dir() / "latest_batch_recovery.json"


def create_batch_recovery_state(files: list[str], output_dir: str, settings: dict) -> dict:
    now = datetime.now().astimezone().isoformat(timespec="seconds")
    return {
        "schema_version": BATCH_RECOVERY_SCHEMA_VERSION,
        "batch_id": f"{datetime.now():%Y%m%d_%H%M%S_%f}_{os.getpid()}",
        "status": "running",
        "created_at": now,
        "updated_at": now,
        "output_dir": os.path.abspath(output_dir),
        "settings": diagnostic_json_value(dict(settings or {})),
        "files": [
            {
                "file_index": index,
                "input": input_file_signature(path),
                "status": "pending",
                "output_path": "",
                "elapsed_sec": 0.0,
                "last_error": "",
            }
            for index, path in enumerate(files, 1)
        ],
    }


def save_batch_recovery_state(state: dict, path: Path | None = None):
    state["updated_at"] = datetime.now().astimezone().isoformat(timespec="seconds")
    atomic_write_json(path or get_batch_recovery_state_path(), state)


def load_batch_recovery_state(path: Path | None = None) -> dict:
    path = path or get_batch_recovery_state_path()
    if not path.exists():
        return {}
    try:
        with open(path, "r", encoding="utf-8-sig") as file:
            data = json.load(file)
        if int(data.get("schema_version", 0)) != BATCH_RECOVERY_SCHEMA_VERSION:
            return {}
        if not isinstance(data.get("files"), list) or not data.get("output_dir"):
            return {}
        return data
    except (OSError, ValueError, TypeError):
        return {}


def batch_recovery_pending_entries(state: dict) -> list[dict]:
    return [
        entry for entry in state.get("files", [])
        if str(entry.get("status") or "pending") != "succeeded"
    ]


def reconstruct_batch_recovery_state_from_problem_log(path: str | Path) -> dict:
    """Восстанавливает пакет старой версии по её JSONL, не изменяя исходные видео."""
    path = Path(path)
    if not path.exists():
        return {}

    batch_started = None
    records = []
    sibling_problem_log = path.with_name("problems.jsonl")
    source_paths = [path]
    if path.name == "events.jsonl" and sibling_problem_log.exists():
        source_paths.append(sibling_problem_log)
    try:
        for source_path in source_paths:
            with open(source_path, "r", encoding="utf-8-sig") as file:
                for line in file:
                    try:
                        record = json.loads(line)
                    except (ValueError, TypeError):
                        continue
                    records.append(record)
                    if record.get("event") == "batch_started":
                        batch_started = record
    except OSError:
        return {}
    records.sort(key=lambda record: int(record.get("sequence") or 0))
    if not batch_started:
        return {}

    details = batch_started.get("details") or {}
    files = details.get("files") or []
    if isinstance(files, dict):
        files = files.get("items") or []
    files = [str(item or "") for item in files if str(item or "")]
    output_dir = str(details.get("output_dir") or "")
    if not files or not output_dir:
        return {}

    state = create_batch_recovery_state(files, output_dir, details.get("settings") or {})
    state["batch_id"] = f"recovered_{safe_log_filename(str(batch_started.get('session_id') or path.stem), 96)}"
    state["status"] = "interrupted"
    state["source_problem_log"] = str(path.resolve())
    by_path = {
        os.path.normcase(os.path.abspath(str((entry.get("input") or {}).get("path") or ""))): entry
        for entry in state.get("files", [])
    }

    for record in records:
        event = str(record.get("event") or "")
        event_details = record.get("details") or {}
        input_path = str(event_details.get("input_path") or "")
        entry = by_path.get(os.path.normcase(os.path.abspath(input_path))) if input_path else None
        if not entry:
            continue
        if event == "batch_file_started":
            entry["status"] = "pending"
            entry["output_path"] = str(event_details.get("output_path") or entry.get("output_path") or "")
        elif event == "file_pipeline_finished":
            entry["output_path"] = str(event_details.get("final_path") or entry.get("output_path") or "")
        elif event == "batch_file_finished":
            success = bool(event_details.get("success"))
            entry["status"] = "succeeded" if success else "failed"
            entry["elapsed_sec"] = float(event_details.get("elapsed_sec") or 0.0)
            if not success:
                entry["last_error"] = "Файл не был завершён в прежней сессии."

    batch_finished = next(
        (record for record in reversed(records) if record.get("event") == "batch_finished"),
        None,
    )
    if batch_finished:
        final_details = batch_finished.get("details") or {}
        if int(final_details.get("successful") or 0) == int(final_details.get("total") or len(files)):
            state["status"] = "completed"
    return state


def translation_segment_key(start: float, end: float, source_text: str) -> str:
    payload = f"{float(start):.3f}\n{float(end):.3f}\n{(source_text or '').strip()}"
    return hashlib.sha256(payload.encode("utf-8", errors="replace")).hexdigest()


def translation_checkpoint_path(input_path: str, target_code: str) -> Path:
    absolute = os.path.normcase(os.path.abspath(input_path))
    try:
        stat = os.stat(input_path)
        signature = f"{absolute}\n{stat.st_size}\n{stat.st_mtime_ns}\n{target_code}"
    except OSError:
        signature = f"{absolute}\n{target_code}"
    digest = hashlib.sha256(signature.encode("utf-8", errors="replace")).hexdigest()[:16]
    base = safe_log_filename(Path(input_path).stem, max_len=48)
    target = safe_log_filename(str(target_code).replace("-", "_"), max_len=16)
    return get_translation_checkpoints_dir() / f"{base}_{target}_{digest}.json"


def load_translation_checkpoint(path: Path) -> dict[str, str]:
    if not path.exists():
        return {}
    with open(path, "r", encoding="utf-8-sig") as file:
        data = json.load(file)
    if int(data.get("schema_version", 0)) != 1:
        return {}
    result = {}
    for item in data.get("segments", []):
        key = str(item.get("key") or "")
        translated = str(item.get("translated") or "").strip()
        if key and translated:
            result[key] = translated
    return result


def save_translation_checkpoint(path: Path, input_path: str, source_lang: str,
                                target_code: str, segments: list):
    """Атомарно сохраняет уже готовые сегменты; исходное видео не затрагивается."""
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        stat = os.stat(input_path)
        input_info = {
            "path": os.path.abspath(input_path),
            "size": stat.st_size,
            "modified_ns": stat.st_mtime_ns,
        }
    except OSError:
        input_info = {"path": os.path.abspath(input_path)}

    items = []
    for seg in segments or []:
        source = (seg.get("source") or "").strip()
        start = float(seg.get("start", 0.0))
        end = float(seg.get("end", 0.0))
        items.append({
            "key": translation_segment_key(start, end, source),
            "start": start,
            "end": end,
            "source": source,
            "translated": (seg.get("translated") or "").strip(),
        })

    data = {
        "schema_version": 1,
        "updated_at": datetime.now().astimezone().isoformat(timespec="seconds"),
        "input": input_info,
        "source_language": source_lang,
        "target_language": target_code,
        "segments": items,
    }
    atomic_write_json(path, data)


def install_requests_default_timeout():
    """
    deep-translator/gTTS внутри используют requests и иногда не задают timeout.
    Добавляем общий разумный лимит, чтобы перевод не зависал бесконечно.
    """
    try:
        import requests
    except Exception:
        return

    session_cls = requests.sessions.Session
    if getattr(session_cls.request, "_video_translator_timeout_patch", False):
        return

    original_request = session_cls.request

    def request_with_default_timeout(self, method, url, **kwargs):
        if kwargs.get("timeout") is None:
            kwargs["timeout"] = (NETWORK_CONNECT_TIMEOUT_SEC, NETWORK_READ_TIMEOUT_SEC)
        return original_request(self, method, url, **kwargs)

    request_with_default_timeout._video_translator_timeout_patch = True
    request_with_default_timeout._video_translator_original = original_request
    session_cls.request = request_with_default_timeout


def edge_tts_timeout_for_text(text: str) -> int:
    """Динамический лимит EdgeTTS: короткие фразы не висят, длинным даём запас."""
    seconds = EDGE_TTS_MIN_TIMEOUT_SEC + int(len(text or "") * 0.12)
    return max(EDGE_TTS_MIN_TIMEOUT_SEC, min(EDGE_TTS_MAX_TIMEOUT_SEC, seconds))


def progressive_retry_delay(attempt: int, base: float = 1.5, maximum: float = 20.0) -> float:
    """Ограниченная экспоненциальная пауза: 1.5, 3, 6, 12... секунд."""
    attempt = max(1, int(attempt or 1))
    maximum = max(0.0, float(maximum))
    delay = min(maximum, max(0.0, float(base)))
    # Не вычисляем 2 ** attempt напрямую: на тысячах сегментов это раньше
    # приводило к OverflowError ещё до применения верхнего ограничения.
    remaining_steps = attempt - 1
    while remaining_steps > 0 and delay < maximum:
        delay = min(maximum, delay * 2.0)
        remaining_steps -= 1
    return delay


class NetworkStormGuard:
    """Останавливает новые сетевые запросы, когда за короткое время накопился шквал ошибок."""

    def __init__(self, log=None, event_cb=None, clock=None, *, service: str = "network",
                 threshold: int = NETWORK_STORM_THRESHOLD,
                 window_sec: int = NETWORK_STORM_WINDOW_SEC,
                 initial_pause_sec: int = 30, recovery_successes: int = 3,
                 recovery_min_sec: int = 0, probe_interval_sec: int = 0):
        self.log = log
        self.event_cb = event_cb
        self.clock = clock or time.monotonic
        self.service = service
        self.threshold = max(2, int(threshold))
        self.window_sec = max(10, int(window_sec))
        self.initial_pause_sec = max(1, min(60, int(initial_pause_sec)))
        self.required_recovery_successes = max(1, int(recovery_successes))
        self.recovery_min_sec = max(0, int(recovery_min_sec))
        self.probe_interval_sec = max(0, int(probe_interval_sec))
        self.lock = threading.Lock()
        self.failures = deque()
        self.active = False
        self.cooldown_until = 0.0
        self.storm_number = 0
        self.recovery_successes = 0
        self.recovery_started_at = 0.0
        self.suppressed_failures = 0
        self.started_at = 0.0
        self.component = service
        self.cooldown_rounds = 0
        self.probe_in_flight = False

    def _event(self, event: str, message: str, **details):
        if self.event_cb:
            try:
                self.event_cb(event, level="warning" if event.endswith("started") else "info",
                              message=message, **details)
            except Exception:
                pass

    def before_request(self, sleep_or_cancel):
        while True:
            with self.lock:
                remaining = self.cooldown_until - self.clock() if self.active else 0.0
            if remaining <= 0:
                return
            sleep_or_cancel(remaining)

    def is_active(self) -> bool:
        with self.lock:
            return bool(self.active)

    def seconds_until_probe(self) -> float:
        with self.lock:
            if not self.active:
                return 0.0
            return max(0.0, self.cooldown_until - self.clock())

    def request_probe_now(self) -> bool:
        """Разрешает ближайшему ожидающему потоку немедленно проверить новый VPN-маршрут."""
        with self.lock:
            if not self.active:
                return False
            self.cooldown_until = self.clock()
            self.probe_in_flight = False
            return True

    def claim_probe_or_bypass(self) -> str:
        """
        Для резервируемого сервиса возвращает normal/probe/bypass.
        Во время шторма только один редкий запрос проверяет восстановление,
        остальные сразу используют резервный сервис и не ждут всей паузы.
        """
        now = self.clock()
        with self.lock:
            if not self.active:
                return "normal"
            if now < self.cooldown_until or self.probe_in_flight:
                return "bypass"
            self.probe_in_flight = True
            return "probe"

    def record_failure(self, component: str, exc: Exception) -> bool:
        """Возвращает True, если подробность этой ошибки ещё следует записать."""
        now = self.clock()
        started = False
        pause_sec = 0
        failure_count = 0
        with self.lock:
            self.failures.append(now)
            cutoff = now - self.window_sec
            while self.failures and self.failures[0] < cutoff:
                self.failures.popleft()

            if not self.active and len(self.failures) >= self.threshold:
                self.active = True
                self.storm_number += 1
                pause_sec = min(60, self.initial_pause_sec + (self.storm_number - 1) * 15)
                self.cooldown_until = now + pause_sec
                self.started_at = now
                self.recovery_successes = 0
                self.recovery_started_at = 0.0
                self.suppressed_failures = 0
                self.component = component
                self.cooldown_rounds = 1
                self.probe_in_flight = False
                started = True
            elif self.active:
                self.suppressed_failures += 1
                self.recovery_successes = 0
                self.recovery_started_at = 0.0
                self.probe_in_flight = False
                if now >= self.cooldown_until:
                    self.cooldown_rounds += 1
                    pause_sec = min(60, self.initial_pause_sec + (self.cooldown_rounds - 1) * 15)
                    self.cooldown_until = now + pause_sec
            failure_count = len(self.failures)

        if started:
            if self.log:
                self.log(
                    f"      🌩️ Сетевой шторм начался: {failure_count} ошибок за "
                    f"{self.window_sec}с; сервис {self.service}; передышка {pause_sec}с"
                )
            self._event(
                "network_storm_started",
                "Накопилось много сетевых ошибок; новые запросы временно приостановлены.",
                component=component,
                service=self.service,
                failures_in_window=failure_count,
                window_sec=self.window_sec,
                pause_sec=pause_sec,
                exception_category=classify_exception(exc),
            )
        return not self.active or started

    def record_success(self, component: str):
        finished = None
        now = self.clock()
        with self.lock:
            if not self.active:
                cutoff = now - self.window_sec
                while self.failures and self.failures[0] < cutoff:
                    self.failures.popleft()
                return
            self.probe_in_flight = False
            if now < self.cooldown_until:
                return
            if self.recovery_started_at <= 0:
                self.recovery_started_at = now
            self.recovery_successes += 1
            stable_for = now - self.recovery_started_at
            recovered = (
                self.recovery_successes >= self.required_recovery_successes
                and stable_for >= self.recovery_min_sec
            )
            if recovered:
                finished = {
                    "component": component or self.component,
                    "service": self.service,
                    "duration_sec": round(now - self.started_at, 3),
                    "suppressed_failures": self.suppressed_failures,
                    "successful_requests": self.recovery_successes,
                    "stable_for_sec": round(stable_for, 3),
                    "cooldown_rounds": self.cooldown_rounds,
                }
                self.active = False
                self.cooldown_until = 0.0
                self.failures.clear()
                self.recovery_successes = 0
                self.recovery_started_at = 0.0
                self.suppressed_failures = 0
                self.cooldown_rounds = 0
            elif self.probe_interval_sec > 0:
                self.cooldown_until = now + self.probe_interval_sec

        if finished:
            if self.log:
                self.log(
                    "      🌤️ Сетевой шторм закончился: сервис снова отвечает "
                    f"({finished['service']}, {finished['successful_requests']} успешных проверок, "
                    f"стабильно {finished['stable_for_sec']:.0f}с)"
                )
            self._event(
                "network_storm_finished",
                "Сетевой сервис снова стабильно отвечает.",
                **finished,
            )

    def force_recovery(self, component: str, reason: str = "manual_probe") -> bool:
        """
        Завершает шторм после успешной целевой проверки.

        Используется только тогда, когда программа специально дождалась смены VPN
        и успешно создала реальный TTS-фрагмент выбранным голосом.
        """
        now = self.clock()
        with self.lock:
            if not self.active:
                return False
            finished = {
                "component": component or self.component,
                "service": self.service,
                "duration_sec": round(now - self.started_at, 3),
                "suppressed_failures": self.suppressed_failures,
                "successful_requests": max(1, self.recovery_successes),
                "stable_for_sec": round(max(0.0, now - self.recovery_started_at), 3)
                if self.recovery_started_at > 0 else 0.0,
                "cooldown_rounds": self.cooldown_rounds,
                "recovery_reason": reason,
            }
            self.active = False
            self.cooldown_until = 0.0
            self.failures.clear()
            self.recovery_successes = 0
            self.recovery_started_at = 0.0
            self.suppressed_failures = 0
            self.cooldown_rounds = 0
            self.probe_in_flight = False

        if self.log:
            self.log(
                "      🌤️ Edge TTS снова отвечает: успешна проверка после смены VPN; "
                "возвращаю выбранный голос"
            )
        self._event(
            "network_storm_finished",
            "Сетевой сервис успешно проверен после ожидания или смены VPN.",
            **finished,
        )
        return True


class _SessionRequestsProxy:
    """Минимальный requests-совместимый объект для deep-translator."""

    def __init__(self, requests_module, session):
        self._requests = requests_module
        self._session = session

    def get(self, url, **kwargs):
        if kwargs.get("timeout") is None:
            kwargs["timeout"] = (NETWORK_CONNECT_TIMEOUT_SEC, NETWORK_READ_TIMEOUT_SEC)
        return self._session.get(url, **kwargs)

    def __getattr__(self, name):
        return getattr(self._requests, name)


class ReusableGoogleTranslator:
    """GoogleTranslator с одним HTTP-сеансом и повторным использованием TLS-соединения."""

    def __init__(self, target: str):
        from deep_translator import GoogleTranslator

        try:
            import requests
        except ModuleNotFoundError:
            # Нужен для изолированных тестов с подменённым deep-translator.
            self.requests_module = None
            self.session = None
            self.translator = GoogleTranslator(source="auto", target=target)
            self.google_module = None
            self.original_requests = None
            self.proxy = None
            return

        self.requests_module = requests
        self.session = requests.Session()
        adapter = requests.adapters.HTTPAdapter(pool_connections=2, pool_maxsize=4, max_retries=0)
        self.session.mount("https://", adapter)
        self.session.mount("http://", adapter)
        self.translator = GoogleTranslator(source="auto", target=target)
        self.google_module = None
        self.original_requests = None
        self.proxy = _SessionRequestsProxy(requests, self.session)
        try:
            self.google_module = importlib.import_module("deep_translator.google")
            self.original_requests = getattr(self.google_module, "requests", None)
            self.google_module.requests = self.proxy
        except Exception:
            # Совместимость с тестовыми/старыми сборками deep-translator.
            self.google_module = None

    def translate(self, text: str) -> str:
        return self.translator.translate(text)

    def close(self):
        try:
            if self.google_module is not None and getattr(self.google_module, "requests", None) is self.proxy:
                self.google_module.requests = self.original_requests
        finally:
            if self.session is not None:
                self.session.close()


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


class TTSCache:
    """Постоянный потокобезопасный кэш TTS по тексту, голосу, скорости и провайдеру."""

    _maintenance_lock = threading.Lock()

    def __init__(self, directory: Path | None = None, max_bytes: int = TTS_CACHE_MAX_BYTES):
        self.directory = Path(directory or get_tts_cache_dir())
        self.directory.mkdir(parents=True, exist_ok=True)
        self.max_bytes = max(0, int(max_bytes))
        self._locks_guard = threading.Lock()
        self._locks = {}
        self._used_entries_lock = threading.Lock()
        self._used_entries = set()
        self._prepared_size_lock = threading.Lock()
        self._prepared_stores_since_size_check = max(0, TTS_CACHE_SIZE_CHECK_EVERY - 1)
        self._prepared_cache_full = False

    @staticmethod
    def make_key(text: str, voice: str, rate_pct: int, provider: str, language: str,
                 extra: dict | None = None) -> str:
        payload = {
            "schema": TTS_CACHE_SCHEMA_VERSION,
            "provider": provider,
            "language": language,
            "voice": voice,
            "rate_pct": int(rate_pct),
            "text": normalize_tts_text(text),
            "extra": diagnostic_json_value(extra or {}),
        }
        packed = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        return hashlib.sha256(packed.encode("utf-8", errors="replace")).hexdigest()

    def key_lock(self, key: str):
        with self._locks_guard:
            return self._locks.setdefault(key, threading.Lock())

    def _audio_path(self, key: str, suffix: str = ".mp3") -> Path:
        return self.directory / f"{key}{suffix}"

    def _mark_used(self, key: str, suffix: str):
        with self._used_entries_lock:
            self._used_entries.add((str(key), str(suffix)))

    def _touch_entry(self, key: str, suffix: str):
        for path in (self._audio_path(key, suffix=suffix), self.directory / f"{key}.json"):
            try:
                if path.exists():
                    os.utime(path, None)
            except OSError:
                pass

    @staticmethod
    def _safe_unlink(path: Path) -> tuple[int, str]:
        try:
            size = path.stat().st_size if path.exists() else 0
            if path.exists():
                path.unlink()
            return int(size), ""
        except (OSError, TypeError, ValueError) as exc:
            return 0, f"{path.name}: {compact_exception(exc, max_len=500)}"

    def _directory_size(self) -> int:
        total = 0
        try:
            for path in self.directory.iterdir():
                try:
                    if path.is_file():
                        total += path.stat().st_size
                except OSError:
                    continue
        except OSError:
            return 0
        return int(total)

    def _prepared_cache_has_room(self, incoming_bytes: int) -> bool:
        """Редко проверяет размер папки и не даёт производным WAV перейти жёсткий предел."""
        if not self.max_bytes:
            return True
        with self._prepared_size_lock:
            if self._prepared_cache_full:
                return False
            self._prepared_stores_since_size_check += 1
            if self._prepared_stores_since_size_check < TTS_CACHE_SIZE_CHECK_EVERY:
                return True
            self._prepared_stores_since_size_check = 0

        has_room = self._directory_size() + max(0, int(incoming_bytes)) <= self.max_bytes
        if not has_room:
            with self._prepared_size_lock:
                self._prepared_cache_full = True
        return has_room

    def restore(self, key: str, destination: str, suffix: str = ".mp3") -> bool:
        source = self._audio_path(key, suffix=suffix)
        try:
            if not source.exists() or source.stat().st_size <= 200:
                return False
            shutil.copyfile(source, destination)
            restored = ffmpeg_audio_ok(destination)
            if restored:
                self._mark_used(key, suffix)
                self._touch_entry(key, suffix)
            return restored
        except OSError:
            return False

    def store(self, key: str, source: str, metadata: dict, suffix: str = ".mp3"):
        if not ffmpeg_audio_ok(source):
            return False
        source_size = os.path.getsize(source)
        if suffix.lower() == ".wav" and not self._prepared_cache_has_room(source_size):
            return False
        audio_path = self._audio_path(key, suffix=suffix)
        meta_path = self.directory / f"{key}.json"
        audio_temp = self.directory / f".{key}.{os.getpid()}.{threading.get_ident()}{suffix}.tmp"
        meta_temp = self.directory / f".{key}.{os.getpid()}.{threading.get_ident()}.json.tmp"
        stored = False
        try:
            shutil.copyfile(source, audio_temp)
            os.replace(audio_temp, audio_path)
            data = {
                "schema_version": TTS_CACHE_SCHEMA_VERSION,
                "created_at": datetime.now().astimezone().isoformat(timespec="seconds"),
                **diagnostic_json_value(metadata),
            }
            with open(meta_temp, "w", encoding="utf-8", newline="\n") as file:
                json.dump(data, file, ensure_ascii=False, indent=2)
                file.flush()
                os.fsync(file.fileno())
            os.replace(meta_temp, meta_path)
            stored = True
        finally:
            for path in (audio_temp, meta_temp):
                try:
                    if path.exists():
                        path.unlink()
                except OSError:
                    pass
        if stored:
            self._mark_used(key, suffix)
        return stored

    def release_used_entries(self, suffixes: set[str] | None = None) -> dict:
        """
        Удаляет только те записи, которые использовал текущий экземпляр переводчика.

        Метод вызывается после успешной проверки итогового MP4. При ошибке или
        отмене можно удалить только производные WAV, сохранив компактные MP3 для
        продолжения после перезапуска.
        """
        selected_suffixes = None if suffixes is None else {str(suffix).lower() for suffix in suffixes}
        with self._used_entries_lock:
            entries = sorted(
                entry for entry in self._used_entries
                if selected_suffixes is None or entry[1].lower() in selected_suffixes
            )

        deleted_entries = 0
        deleted_files = 0
        freed_bytes = 0
        errors = []
        released = set()
        for key, suffix in entries:
            entry_errors = []
            with self.key_lock(key):
                for path in (self._audio_path(key, suffix=suffix), self.directory / f"{key}.json"):
                    existed = path.exists()
                    size, error = self._safe_unlink(path)
                    if existed and not error:
                        deleted_files += 1
                        freed_bytes += size
                    if error:
                        entry_errors.append(error)
            if not entry_errors:
                deleted_entries += 1
                released.add((key, suffix))
            else:
                errors.extend(entry_errors)

        if released:
            with self._used_entries_lock:
                self._used_entries.difference_update(released)

        return {
            "deleted_entries": deleted_entries,
            "deleted_files": deleted_files,
            "freed_bytes": int(freed_bytes),
            "remaining_bytes": self._directory_size(),
            "errors": errors[:20],
        }

    def cleanup_stale_entries(self, retention_days: int = TTS_CACHE_RECOVERY_RETENTION_DAYS,
                              orphan_hours: int = TTS_CACHE_ORPHAN_RETENTION_HOURS,
                              max_bytes: int = TTS_CACHE_MAX_BYTES,
                              target_bytes: int = TTS_CACHE_TARGET_BYTES,
                              prepared_retention_hours: int = TTS_CACHE_PREPARED_RETENTION_HOURS) -> dict:
        """Чистит производные WAV, старые recovery-записи, сироты и забытые *.tmp."""
        now = time.time()
        retention_sec = max(1, int(retention_days)) * 86400
        prepared_retention_sec = max(1, int(prepared_retention_hours)) * 3600
        orphan_sec = max(1, int(orphan_hours)) * 3600
        max_bytes = max(0, int(max_bytes))
        target_bytes = max(0, min(int(target_bytes), max_bytes)) if max_bytes else 0
        result = {
            "deleted_entries": 0,
            "deleted_files": 0,
            "freed_bytes": 0,
            "orphan_entries": 0,
            "prepared_entries": 0,
            "stale_entries": 0,
            "size_limited_entries": 0,
            "errors": [],
        }

        with self._maintenance_lock:
            try:
                files = [path for path in self.directory.iterdir() if path.is_file()]
            except (OSError, TypeError, ValueError) as exc:
                result["errors"].append(compact_exception(exc, max_len=500))
                result["remaining_bytes"] = 0
                return result

            groups = {}
            for path in files:
                if (
                    path.name.startswith(".")
                    and path.name.endswith(".tmp")
                    and re.match(r"^\.[0-9a-f]{64}\.", path.name)
                ):
                    try:
                        age = now - path.stat().st_mtime
                    except OSError:
                        age = 0
                    if age >= orphan_sec:
                        size, error = self._safe_unlink(path)
                        if error:
                            result["errors"].append(error)
                        else:
                            result["deleted_files"] += 1
                            result["freed_bytes"] += size
                    continue
                if path.suffix.lower() not in {".mp3", ".wav", ".json"}:
                    continue
                if not re.fullmatch(r"[0-9a-f]{64}", path.stem):
                    continue
                groups.setdefault(path.stem, []).append(path)

            with self._used_entries_lock:
                active_keys = {key for key, _suffix in self._used_entries}

            candidates = []
            deleted_keys = set()

            def delete_group(key: str, paths: list[Path], reason: str):
                if key in active_keys or key in deleted_keys:
                    return
                group_deleted = False
                for path in paths:
                    existed = path.exists()
                    size, error = self._safe_unlink(path)
                    if error:
                        result["errors"].append(error)
                    elif existed:
                        group_deleted = True
                        result["deleted_files"] += 1
                        result["freed_bytes"] += size
                if group_deleted:
                    deleted_keys.add(key)
                    result["deleted_entries"] += 1
                    result[f"{reason}_entries"] += 1

            for key, paths in groups.items():
                audio = [path for path in paths if path.suffix.lower() in {".mp3", ".wav"}]
                metadata = [path for path in paths if path.suffix.lower() == ".json"]
                try:
                    last_used = max(path.stat().st_mtime for path in paths)
                except (OSError, ValueError):
                    last_used = now
                age = max(0.0, now - last_used)
                if not audio or not metadata:
                    if age >= orphan_sec:
                        delete_group(key, paths, "orphan")
                    continue
                if age >= retention_sec:
                    delete_group(key, paths, "stale")
                    continue

                is_prepared = False
                for metadata_path in metadata:
                    try:
                        with open(metadata_path, "r", encoding="utf-8") as file:
                            provider = str((json.load(file) or {}).get("provider") or "")
                        if provider.startswith("prepared_wav_"):
                            is_prepared = True
                            break
                    except (OSError, ValueError, TypeError, AttributeError):
                        continue

                if is_prepared and age >= prepared_retention_sec:
                    delete_group(key, paths, "prepared")
                    continue
                size = 0
                for path in paths:
                    try:
                        size += path.stat().st_size
                    except OSError:
                        pass
                # При нехватке места сначала удаляем тяжёлые производные WAV.
                # Сжатые MP3 полезнее: по ним озвучку можно восстановить без сети.
                candidates.append((0 if is_prepared else 1, last_used, key, paths, size))

            remaining_bytes = self._directory_size()
            if max_bytes and remaining_bytes > max_bytes:
                for _priority, _last_used, key, paths, size in sorted(candidates):
                    if remaining_bytes <= target_bytes:
                        break
                    before = result["freed_bytes"]
                    delete_group(key, paths, "size_limited")
                    remaining_bytes -= max(0, result["freed_bytes"] - before)

            result["remaining_bytes"] = self._directory_size()
            result["errors"] = result["errors"][:20]
            return result


class FileLogger:
    """Дублирует журнал программы в .txt файл, чтобы его можно было отправить на разбор."""

    def __init__(self, prefix: str = "video_translator"):
        self.lock = threading.Lock()
        self.path = get_logs_dir() / f"{safe_log_filename(prefix)}_{datetime.now():%Y-%m-%d_%H-%M-%S}.txt"
        self.file = open(self.path, "a", encoding="utf-8", buffering=1)
        self.write("=" * 72)
        self.write("ЛОГ ПРОГРАММЫ: Видео Переводчик PRO")
        self.write(f"Дата запуска: {datetime.now():%Y-%m-%d %H:%M:%S}")
        self.write(f"Папка программы: {get_program_dir()}")
        self.write(f"Файл лога: {self.path}")
        self.write("=" * 72)

    def write(self, msg: str):
        if not hasattr(self, "file") or self.file.closed:
            return
        text = str(msg)
        stamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        with self.lock:
            for line in text.splitlines() or [""]:
                self.file.write(f"[{stamp}] {line}\n")
            self.file.flush()

    def write_exception(self, title: str, exc: Exception | None = None):
        self.write("\n" + "!" * 72)
        self.write(title)
        if exc is not None:
            self.write(f"{type(exc).__name__}: {exc}")
        self.write(traceback.format_exc())
        self.write("!" * 72)

    def close(self):
        try:
            self.write("\nЛог закрыт.")
            self.file.close()
        except Exception:
            pass


def fmt_time(sec: float) -> str:
    sec = max(0, float(sec or 0))
    return f"{int(sec) // 60:02d}:{int(sec) % 60:02d}"


def is_cuda_available() -> bool:
    try:
        import torch
        return bool(torch.cuda.is_available())
    except Exception:
        return False


def get_whisper_model(size: str):
    """Загружает и кэширует Whisper-модель. GPU используется автоматически, если доступен."""
    device = "cuda" if is_cuda_available() else "cpu"
    key = (size, device)
    with _whisper_lock:
        if key not in _whisper_cache:
            import whisper
            _whisper_cache[key] = whisper.load_model(size, device=device)
        return _whisper_cache[key]


def find_ffmpeg() -> str:
    for candidate in ("ffmpeg", "ffmpeg.exe"):
        path = shutil.which(candidate)
        if path:
            return path

    script_dir = Path(__file__).resolve().parent
    for name in ("ffmpeg.exe", "ffmpeg"):
        path = script_dir / name
        if path.exists():
            return str(path)

    raise RuntimeError(
        "ffmpeg не найден!\n"
        "Положите ffmpeg.exe рядом с программой или добавьте ffmpeg в PATH."
    )


def find_ffprobe(ffmpeg_path: str) -> str:
    """Ищет ffprobe рядом с ffmpeg или в PATH."""
    ffmpeg_dir = os.path.dirname(ffmpeg_path)
    for name in ("ffprobe.exe", "ffprobe"):
        local = os.path.join(ffmpeg_dir, name) if ffmpeg_dir else name
        if os.path.exists(local):
            return local

    path = shutil.which("ffprobe") or shutil.which("ffprobe.exe")
    if path:
        return path

    raise RuntimeError(
        "ffprobe не найден!\n"
        "Положите ffprobe.exe рядом с ffmpeg.exe или добавьте ffprobe в PATH."
    )


def format_subprocess_command(cmd: list, max_len: int = 4000) -> str:
    try:
        rendered = subprocess.list2cmdline([str(part) for part in (cmd or [])])
    except Exception:
        rendered = " ".join(str(part) for part in (cmd or []))
    return redact_diagnostic_text(rendered, max_len=max_len)


def log_subprocess_result(result: subprocess.CompletedProcess, cmd: list, log=None):
    if not log or result.returncode == 0:
        return
    log(f"      ⚠️ Внешний процесс завершился с кодом {result.returncode}")
    log(f"      ⚠️ Команда внешнего процесса: {format_subprocess_command(cmd)}")
    stderr = redact_diagnostic_text((result.stderr or "")[-4000:], max_len=4000).strip()
    stdout = redact_diagnostic_text((result.stdout or "")[-2000:], max_len=2000).strip()
    if stderr:
        log(f"      stderr (хвост): {stderr}")
    elif stdout:
        log(f"      stdout (хвост): {stdout}")


def run_subprocess(cmd: list, timeout: int, log=None, cancel_event=None,
                   heartbeat_cb=None) -> subprocess.CompletedProcess:
    try:
        if cancel_event is None:
            result = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                errors="replace",
                timeout=timeout,
            )
            log_subprocess_result(result, cmd, log=log)
            return result

        started_at = time.monotonic()
        process = subprocess.Popen(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            errors="replace",
        )
        last_heartbeat_at = started_at

        while True:
            if cancel_event.is_set():
                process.kill()
                stdout, stderr = process.communicate()
                raise CancelledError()

            if timeout is not None:
                remaining = timeout - (time.monotonic() - started_at)
                if remaining <= 0:
                    process.kill()
                    stdout, stderr = process.communicate()
                    raise subprocess.TimeoutExpired(cmd, timeout, output=stdout, stderr=stderr)
                wait_time = min(0.25, remaining)
            else:
                wait_time = 0.25

            now = time.monotonic()
            if (log or heartbeat_cb) and now - last_heartbeat_at >= 60:
                executable = os.path.basename(str(cmd[0])) if cmd else "процесс"
                elapsed = int(now - started_at)
                timeout_text = f", лимит {timeout}с" if timeout is not None else ""
                if log:
                    log(f"      ⏳ {executable} продолжает работать: прошло {fmt_time(elapsed)}{timeout_text}")
                if heartbeat_cb:
                    heartbeat_cb(
                        "activity_heartbeat",
                        message="Внешний процесс продолжает выполняться.",
                        operation=executable,
                        elapsed_sec=elapsed,
                        timeout_sec=timeout,
                    )
                last_heartbeat_at = now

            try:
                stdout, stderr = process.communicate(timeout=wait_time)
                result = subprocess.CompletedProcess(cmd, process.returncode, stdout, stderr)
                log_subprocess_result(result, cmd, log=log)
                return result
            except subprocess.TimeoutExpired:
                continue
    except subprocess.TimeoutExpired as exc:
        if log:
            log(f"      ⚠️ ffmpeg timeout: {timeout}с")
            log(f"      ⚠️ Команда внешнего процесса: {format_subprocess_command(cmd)}")
            stderr = redact_diagnostic_text((getattr(exc, "stderr", "") or "")[-4000:], max_len=4000).strip()
            if stderr:
                log(f"      stderr до timeout (хвост): {stderr}")
        raise exc


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




def ffmpeg_has_encoder(ffmpeg: str, encoder_name: str, log=None) -> bool:
    """Проверяет, есть ли в сборке ffmpeg нужный видеокодер."""
    cache = getattr(ffmpeg_has_encoder, "_cache", {})
    key = (ffmpeg, encoder_name)
    if key in cache:
        return cache[key]

    try:
        result = run_subprocess([ffmpeg, "-hide_banner", "-encoders"], timeout=20, log=log)
        ok = result.returncode == 0 and re.search(rf"\b{re.escape(encoder_name)}\b", result.stdout or "") is not None
    except Exception:
        ok = False

    cache[key] = ok
    setattr(ffmpeg_has_encoder, "_cache", cache)
    return ok


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


def extract_audio_for_whisper(ffmpeg: str, input_path: str, wav_path: str, log=None,
                              cancel_event=None, timeout: int = 600) -> bool:
    """
    Извлекает аудио для Whisper напрямую через ffmpeg.
    Это надёжнее, чем MoviePy, и не создаёт TEMP_MPY_* файлы рядом с программой.
    """
    cmd = [
        ffmpeg, "-y",
        "-i", input_path,
        "-vn",
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


def output_has_video_and_audio(ffprobe: str, path: str, log=None) -> bool:
    """Проверяет, что итоговый файл — именно видео с аудиодорожкой, а не временный аудиофайл."""
    if not ffprobe or not os.path.exists(path) or os.path.getsize(path) < 1000:
        return False
    try:
        cmd = [
            ffprobe, "-v", "error",
            "-show_entries", "stream=codec_type",
            "-of", "json",
            path,
        ]
        result = run_subprocess(cmd, timeout=20, log=log)
        if result.returncode != 0:
            if log:
                log(f"      ⚠️ ffprobe проверка результата: {(result.stderr or '')[-400:].strip()}")
            return False
        data = json.loads(result.stdout or "{}")
        types = {stream.get("codec_type") for stream in data.get("streams", [])}
        return "video" in types and "audio" in types
    except Exception as exc:
        if log:
            log(f"      ⚠️ Не удалось проверить итоговый MP4: {exc}")
        return False


def assemble_final_video(ffmpeg: str, ffprobe: str, input_video: str, russian_audio: str,
                         output_path: str, temp_dir: str, video_dur: float,
                         keep_original: bool, orig_vol_pct: int, log=None,
                         pause_plan: list | None = None, final_dur: float | None = None,
                         cancel_event=None, diagnostic=None) -> str:
    """
    Финальная сборка только через ffmpeg.
    Важно: MoviePy write_videofile здесь не используется, поэтому не появляются
    *_TEMP_MPY_wvf_snd.mp4 аудио-файлы, которые пользователь мог принять за результат.

    Исправление: для длинных роликов и сотен Pause Sync-вставок больше не используется
    жёсткий timeout=3600. Программа выбирает быстрый/аппаратный H.264-кодер,
    считает динамический лимит и не тратит ещё один час на заведомо медленный fallback.
    """
    pause_plan = sanitize_pause_plan(pause_plan or [], video_dur)
    final_dur = float(final_dur or (video_dur + sum(float(p.get("duration", 0.0)) for p in pause_plan)))
    encode_timeout = calc_final_encode_timeout(final_dur, len(pause_plan))

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

    def move_checked_result(build_path: str) -> str:
        if not output_has_video_and_audio(ffprobe, build_path, log=log):
            raise RuntimeError("Итоговый файл не прошёл проверку: в нём нет видеодорожки или аудиодорожки.")

        os.makedirs(os.path.dirname(output_path) or ".", exist_ok=True)
        if os.path.exists(output_path):
            raise FileExistsError(
                f"Итоговый путь появился во время обработки и не будет перезаписан: {output_path}"
            )
        shutil.move(build_path, output_path)

        if not output_has_video_and_audio(ffprobe, output_path, log=log):
            raise RuntimeError("Файл перенесён, но проверка video+audio не прошла.")
        return output_path

    # Если есть вставленные паузы, собираем расширенное видео через filter_complex_script.
    # Это предотвращает длинную командную строку на Windows и гарантирует, что на выходе будет именно видео.
    if pause_plan:
        total_pause = sum(float(p.get("duration", 0.0)) for p in pause_plan)
        if log:
            log(f"   ⏸️ Pause Sync: вставляем {len(pause_plan)} стоп-кадр(ов), +{total_pause:.2f}с к видео")
            if len(pause_plan) >= HEAVY_PAUSE_SYNC_COUNT:
                log("      ℹ️ Много стоп-кадров: включён ускоренный режим финального кодирования")

        filter_script = make_filter_script_for_pauses(temp_dir, pause_plan, video_dur, final_dur, keep_original, orig_vol_pct)
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
                if result.returncode == 0 and output_has_video_and_audio(ffprobe, build_path, log=log):
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
                last_error = f"{encoder_name}: {(result.stderr or '')[-1200:].strip()}"
                diag(
                    "video_encoder_attempt_failed",
                    level="warning",
                    message="Попытка видеокодирования завершилась ошибкой.",
                    encoder_name=encoder_name,
                    command=cmd,
                    elapsed_sec=round(time.monotonic() - attempt_started_at, 3),
                    return_code=result.returncode,
                    stderr_tail=(result.stderr or "")[-2000:],
                )
                if log:
                    log(f"      ⚠️ {encoder_name} не сработал: {last_error[-700:]}")
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
            except CancelledError:
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

    if keep_original:
        original_volume = max(0.0, min(1.0, float(orig_vol_pct) / 100.0))
        filter_complex = (
            f"[0:a]volume={original_volume:.4f},"
            f"aresample={SAMPLE_RATE},aformat=sample_fmts=fltp:channel_layouts=stereo[orig];"
            f"[1:a]aresample={SAMPLE_RATE},aformat=sample_fmts=fltp:channel_layouts=stereo,"
            f"alimiter=limit=0.95[ru];"
            f"[orig][ru]amix=inputs=2:duration=first:dropout_transition=0:normalize=0,"
            f"alimiter=limit=0.95[aout]"
        )
    else:
        filter_complex = (
            f"[1:a]aresample={SAMPLE_RATE},aformat=sample_fmts=fltp:channel_layouts=stereo,"
            f"alimiter=limit=0.95[aout]"
        )

    # Без stop-frame пауз видеоряд не меняется, поэтому сначала пробуем быстрый stream copy.
    copy_path = os.path.join(temp_dir, "final_build_copy.mp4")
    remove_if_exists(copy_path)
    copy_cmd = [
        ffmpeg, "-y",
        "-i", input_video,
        "-i", russian_audio,
        "-filter_complex", filter_complex,
        "-map", "0:v:0",
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
        if result.returncode == 0 and output_has_video_and_audio(ffprobe, copy_path, log=log):
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
        diag(
            "video_encoder_attempt_failed",
            level="warning",
            message="Копирование видеопотока не удалось; будет перекодирование.",
            encoder_name="stream copy",
            command=copy_cmd,
            elapsed_sec=round(time.monotonic() - copy_started_at, 3),
            return_code=result.returncode,
            stderr_tail=(result.stderr or "")[-2000:],
        )
        if log:
            log(f"      ⚠️ stream copy не сработал, будет перекодирование: {(result.stderr or '')[-600:].strip()}")
    except CancelledError:
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

    base_cmd = [
        ffmpeg, "-y",
        "-i", input_video,
        "-i", russian_audio,
        "-filter_complex", filter_complex,
        "-map", "0:v:0",
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
            if result.returncode == 0 and output_has_video_and_audio(ffprobe, build_path, log=log):
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
            last_error = f"{encoder_name}: {(result.stderr or '')[-1200:].strip()}"
            diag(
                "video_encoder_attempt_failed",
                level="warning",
                message="Попытка видеокодирования завершилась ошибкой.",
                encoder_name=encoder_name,
                command=cmd,
                elapsed_sec=round(time.monotonic() - attempt_started_at, 3),
                return_code=result.returncode,
                stderr_tail=(result.stderr or "")[-2000:],
            )
            if log:
                log(f"      ⚠️ {encoder_name} не сработал: {last_error[-700:]}")
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
        except CancelledError:
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
        combined_duration = max(0.0, next_end - cur_start)

        should_merge = (
            current_duration < min_dur
            and gap <= max_gap
            and combined_duration <= max_merged_dur
        )

        if should_merge:
            current["text"] = (current.get("text", "").rstrip() + " " + seg.get("text", "").lstrip()).strip()
            current["end"] = seg.get("end", current.get("end"))
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
        available_until = max(end, next_start - MIN_SYNC_GAP)
    else:
        available_until = max(end, float(video_dur or end))

    hard_slot = max(spoken_slot, available_until - start)
    hard_slot = min(max(0.10, float(video_dur or end) - start), hard_slot)
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
                                  orig_vol_pct: int) -> str:
    """
    Создаёт filter_complex_script для ffmpeg.
    Видео расширяется стоп-кадрами, оригинальный фон — тишиной в местах стоп-кадров,
    новая голосовая дорожка уже заранее разложена по расширенному таймлайну.
    """
    pause_plan = sanitize_pause_plan(pause_plan, video_dur)
    lines = []

    video_labels = []
    audio_labels = []
    cursor = 0.0
    chunk_index = 0

    def f(value: float) -> str:
        return f"{float(value):.6f}"

    for pause_index, pause in enumerate(pause_plan):
        at = max(cursor, min(float(video_dur), float(pause["at"])))
        pause_dur = max(0.0, float(pause["duration"]))
        if at - cursor > 0.020:
            vlabel = f"v{chunk_index}"
            lines.append(
                f"[0:v]trim=start={f(cursor)}:end={f(at)},setpts=PTS-STARTPTS,"
                f"tpad=stop_mode=clone:stop_duration={f(pause_dur)}[{vlabel}]"
            )
            video_labels.append(f"[{vlabel}]")

            if keep_original:
                alabel = f"a{chunk_index}"
                slabel = f"s{chunk_index}"
                lines.append(
                    f"[0:a]atrim=start={f(cursor)}:end={f(at)},asetpts=PTS-STARTPTS,"
                    f"aresample={SAMPLE_RATE},aformat=sample_fmts=fltp:channel_layouts=stereo[{alabel}]"
                )
                lines.append(
                    f"anullsrc=channel_layout=stereo:sample_rate={SAMPLE_RATE}:d={f(pause_dur)}[{slabel}]"
                )
                audio_labels.extend([f"[{alabel}]", f"[{slabel}]"])
            chunk_index += 1
        elif video_labels:
            # Если пауза попала почти в тот же таймкод, добавляем её к предыдущему видео-куску через ещё один tpad.
            # На практике sanitize_pause_plan почти всегда предотвращает этот случай.
            pass
        cursor = at

    if float(video_dur) - cursor > 0.020:
        vlabel = f"v{chunk_index}"
        lines.append(
            f"[0:v]trim=start={f(cursor)}:end={f(video_dur)},setpts=PTS-STARTPTS[{vlabel}]"
        )
        video_labels.append(f"[{vlabel}]")
        if keep_original:
            alabel = f"a{chunk_index}"
            lines.append(
                f"[0:a]atrim=start={f(cursor)}:end={f(video_dur)},asetpts=PTS-STARTPTS,"
                f"aresample={SAMPLE_RATE},aformat=sample_fmts=fltp:channel_layouts=stereo[{alabel}]"
            )
            audio_labels.append(f"[{alabel}]")

    if not video_labels:
        # Теоретический аварийный случай: очень короткое/битое видео.
        lines.append("[0:v]setpts=PTS-STARTPTS[vbase]")
        video_labels = ["[vbase]"]

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
        lines.append("[orig_ext][ru]amix=inputs=2:duration=longest:dropout_transition=0:normalize=0,alimiter=limit=0.95[aout]")
    else:
        lines.append("[ru]anull[aout]")

    script_path = os.path.join(temp_dir, "pause_sync_filter.txt")
    with open(script_path, "w", encoding="utf-8") as file:
        file.write(";\n".join(lines))
    return script_path

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


# ─────────────────────────────────────────────────────────────────────────────
#  ОСНОВНАЯ ЛОГИКА
# ─────────────────────────────────────────────────────────────────────────────

class VideoTranslator:
    def __init__(self, log_cb, progress_cb=None, cancel_event=None, review_callback=None,
                 problem_cb=None, network_notice_cb=None):
        self.log = log_cb
        self.set_progress = progress_cb or (lambda v, t="": None)
        self.cancel = cancel_event or threading.Event()
        self.ffmpeg = ""
        self.ffprobe = ""
        self.temp_dir = ""
        self.translator = None
        self.translation_client = None
        self.target_info = get_target_language(DEFAULT_TARGET_LANGUAGE)
        self.audio_settings = normalize_audio_settings()
        self.speech_speed_limit = TOTAL_MAX_SPEECH_SPEED
        self.review_callback = review_callback
        self.problem_cb = problem_cb
        self.network_notice_cb = network_notice_cb
        self.current_input_path = ""
        self.current_stage = "idle"
        self.selected_voice = ""
        self.edge_voice_lock = threading.Lock()
        self.edge_voice_incident = 0
        self.edge_voice_preservation_active = False
        self.edge_voice_deadline = 0.0
        self.edge_voice_probe_requested = threading.Event()
        self.edge_voice_fallback_requested = threading.Event()
        self.edge_voice_fallback_request_reason = ""
        self._tts_allow_gtts_fallback = False
        self._tts_fallback_reason = ""
        self.translation_network_guard = NetworkStormGuard(
            log=self.log,
            event_cb=self._problem,
            service="google_translate",
            threshold=6,
            window_sec=90,
            initial_pause_sec=30,
            recovery_successes=3,
            recovery_min_sec=10,
        )
        self.edge_network_guard = NetworkStormGuard(
            log=self.log,
            event_cb=self._problem,
            service="edge_tts",
            threshold=3,
            window_sec=120,
            initial_pause_sec=30,
            recovery_successes=3,
            recovery_min_sec=30,
            probe_interval_sec=EDGE_VOICE_PROBE_INTERVAL_SEC,
        )
        self.gtts_network_guard = NetworkStormGuard(
            log=self.log,
            event_cb=self._problem,
            service="gtts",
            threshold=4,
            window_sec=120,
            initial_pause_sec=30,
            recovery_successes=2,
            recovery_min_sec=10,
        )
        # Совместимость для внешних расширений; перевод использует отдельную защиту.
        self.network_guard = self.translation_network_guard
        self.edge_parallel_gate = threading.BoundedSemaphore(2)
        self.edge_probe_gate = threading.Lock()
        self.gtts_parallel_gate = threading.BoundedSemaphore(2)
        self.tts_cache = TTSCache()
        self.tts_stats_lock = threading.Lock()
        self.tts_stats = {}
        self.tts_gtts_segments = set()

    def _emit_problem(self, event: str, level: str = "info", message: str = "", **details):
        if not self.problem_cb:
            return
        try:
            self.problem_cb(
                event,
                level=level,
                message=message,
                input_path=self.current_input_path,
                stage=self.current_stage,
                **details,
            )
        except Exception:
            pass

    def _problem(self, event: str, level: str = "info", message: str = "", **details):
        self._emit_problem(event, level=level, message=message, **details)
        service = str(details.get("service") or "")
        if event == "network_storm_started" and service == "edge_tts":
            self._begin_edge_voice_preservation(reason="edge_network_storm")
        elif event == "network_storm_finished" and service == "edge_tts":
            self._finish_edge_voice_preservation(reason="edge_recovered", notify_action="recovered")

    def _notify_network_notice(self, action: str, **details):
        if not self.network_notice_cb:
            return
        try:
            self.network_notice_cb(action, {
                "input_path": self.current_input_path,
                "stage": self.current_stage,
                "voice": self.selected_voice,
                **details,
            })
        except Exception:
            pass

    def _begin_edge_voice_preservation(self, reason: str) -> int:
        now = time.monotonic()
        with self.edge_voice_lock:
            if self.edge_voice_preservation_active:
                return self.edge_voice_incident
            self.edge_voice_incident += 1
            incident = self.edge_voice_incident
            self.edge_voice_preservation_active = True
            self.edge_voice_deadline = now + EDGE_VOICE_PRESERVE_SEC
            self.edge_voice_probe_requested.clear()
            self.edge_voice_fallback_requested.clear()
            self.edge_voice_fallback_request_reason = ""

        self.log(
            "      🔔 Edge TTS нестабилен через текущий VPN. "
            f"До {EDGE_VOICE_PRESERVE_SEC}с сохраняю выбранный голос и жду смены VPN-сервера."
        )
        self._emit_problem(
            "tts_voice_preservation_started",
            level="warning",
            message="Edge TTS нестабилен; программа временно не использует голос gTTS и ждёт смены VPN.",
            voice=self.selected_voice,
            incident=incident,
            reason=reason,
            wait_sec=EDGE_VOICE_PRESERVE_SEC,
            automatic_fallback_after_sec=EDGE_VOICE_PRESERVE_SEC,
        )
        self._notify_network_notice(
            "unstable",
            incident=incident,
            reason=reason,
            wait_sec=EDGE_VOICE_PRESERVE_SEC,
        )
        return incident

    def _finish_edge_voice_preservation(self, reason: str, notify_action: str = ""):
        with self.edge_voice_lock:
            if not self.edge_voice_preservation_active:
                return False
            incident = self.edge_voice_incident
            self.edge_voice_preservation_active = False
            self.edge_voice_deadline = 0.0
            self.edge_voice_probe_requested.clear()
            self.edge_voice_fallback_requested.clear()
            self.edge_voice_fallback_request_reason = ""

        self._emit_problem(
            "tts_voice_preservation_finished",
            message="Ожидание выбранного голоса завершено.",
            voice=self.selected_voice,
            incident=incident,
            reason=reason,
            gtts_enabled=bool(self._tts_allow_gtts_fallback),
        )
        if notify_action:
            self._notify_network_notice(notify_action, incident=incident, reason=reason)
        return True

    def request_edge_probe_now(self):
        """Вызывается кнопкой после того, как пользователь сменил VPN-сервер."""
        with self.edge_voice_lock:
            if not self.edge_voice_preservation_active:
                return False
            incident = self.edge_voice_incident
        self.edge_network_guard.request_probe_now()
        self.edge_voice_probe_requested.set()
        self._emit_problem(
            "vpn_server_change_probe_requested",
            message="Пользователь сообщил о смене VPN-сервера; Edge TTS будет проверен немедленно.",
            voice=self.selected_voice,
            incident=incident,
        )
        return True

    def allow_gtts_fallback_now(self, reason: str = "user_requested"):
        """Разрешает пользователю не ждать окончания защитного окна."""
        with self.edge_voice_lock:
            if not self.edge_voice_preservation_active:
                return False
            incident = self.edge_voice_incident
            self.edge_voice_fallback_request_reason = str(reason or "user_requested")
        self.edge_voice_fallback_requested.set()
        self._emit_problem(
            "tts_gtts_fallback_requested",
            message="Пользователь разрешил перейти на резервный gTTS до окончания ожидания Edge TTS.",
            voice=self.selected_voice,
            incident=incident,
            reason=reason,
            voice_may_differ=True,
        )
        return True

    def _enable_gtts_fallback(self, reason: str):
        with self.edge_voice_lock:
            if self._tts_allow_gtts_fallback:
                return False
            self._tts_allow_gtts_fallback = True
            self._tts_fallback_reason = str(reason or "edge_unavailable")
            incident = self.edge_voice_incident

        self.log(
            "      ↩️ Edge TTS не восстановился. Включён резервный gTTS только для "
            "оставшихся сегментов; тембр голоса может отличаться."
        )
        self._emit_problem(
            "tts_gtts_fallback_enabled",
            level="warning",
            message="Edge TTS не восстановился; для оставшихся сегментов разрешён резервный gTTS.",
            voice=self.selected_voice,
            incident=incident,
            reason=self._tts_fallback_reason,
            voice_may_differ=True,
        )
        self._notify_network_notice(
            "fallback",
            incident=incident,
            reason=self._tts_fallback_reason,
            voice_may_differ=True,
        )
        return True

    # ── отмена ───────────────────────────────────────────────────────────────

    def _check_cancel(self):
        if self.cancel.is_set():
            raise CancelledError()

    def _sleep_or_cancel(self, seconds: float):
        if self.cancel.wait(seconds):
            raise CancelledError()

    def _increment_tts_stat(self, name: str, amount: int = 1):
        with self.tts_stats_lock:
            self.tts_stats[name] = int(self.tts_stats.get(name, 0)) + int(amount)
            return self.tts_stats[name]

    def _mark_gtts_fallback(self, segment_index: int) -> int:
        with self.tts_stats_lock:
            self.tts_gtts_segments.add(int(segment_index or 0))
            self.tts_stats["gtts_fallback"] = len(self.tts_gtts_segments)
            return self.tts_stats["gtts_fallback"]

    def _close_translation_client(self):
        client = self.translation_client
        self.translation_client = None
        self.translator = None
        if client is not None:
            try:
                client.close()
            except Exception:
                pass

    def _get_translation_client(self):
        if self.translation_client is None:
            install_requests_default_timeout()
            self.translation_client = ReusableGoogleTranslator(self.target_info["code"])
            # Старое поле оставлено как совместимое представление для тестов/расширений.
            self.translator = self.translation_client
            self._problem(
                "translation_http_session_started",
                message="Создан общий HTTP-сеанс перевода с повторным использованием TLS-соединения.",
                target_language=self.target_info.get("code"),
            )
        return self.translation_client

    def _save_checkpoint(self, path: Path, input_path: str, source_lang: str, segments: list):
        try:
            save_translation_checkpoint(
                path,
                input_path,
                source_lang,
                self.target_info.get("code") or "target",
                segments,
            )
            return True
        except Exception as exc:
            self.log(f"   ⚠️ Не удалось сохранить контрольную точку перевода: {compact_exception(exc)}")
            self._problem(
                "translation_checkpoint_save_failed",
                level="warning",
                message="Перевод продолжается, но контрольную точку записать не удалось.",
                checkpoint_path=str(path),
                exception={
                    "type": type(exc).__name__,
                    "category": classify_exception(exc),
                    "message": compact_exception(exc, max_len=1000),
                },
            )
            return False

    # ── TTS ──────────────────────────────────────────────────────────────────

    async def _edge_tts_async(self, text: str, path: str, voice: str, rate: str = "+0%"):
        import edge_tts
        # Pitch специально оставляем 0 Hz: ускоряем темп, а не высоту голоса.
        await asyncio.wait_for(
            edge_tts.Communicate(text, voice, rate=rate, pitch="+0Hz").save(path),
            timeout=edge_tts_timeout_for_text(text),
        )

    def _run_edge_tts(self, text: str, path: str, voice: str, rate: str = "+0%"):
        asyncio.run(self._edge_tts_async(text, path, voice, rate=rate))

    def _acquire_network_gate(self, gate):
        while not gate.acquire(timeout=0.25):
            self._check_cancel()

    def _await_selected_voice_or_fallback(self, text: str, voice: str,
                                           segment_index: int) -> str:
        """
        Даёт пользователю время сменить VPN и проверяет Edge реальным TTS-запросом.

        Возвращает ``edge_recovered`` либо ``gtts_allowed``. Ожидание ограничено,
        поэтому полностью недоступный Edge не может навсегда остановить программу.
        """
        incident = self._begin_edge_voice_preservation(reason="edge_segments_deferred")
        with self.edge_voice_lock:
            deadline = self.edge_voice_deadline

        next_probe_at = time.monotonic() + max(
            1.0,
            min(30.0, self.edge_network_guard.seconds_until_probe() or 15.0),
        )
        probe_number = 0
        probe_path = os.path.join(self.temp_dir, f"edge_vpn_probe_{segment_index:05d}.mp3")

        while True:
            self._check_cancel()
            now = time.monotonic()

            if self.edge_voice_fallback_requested.is_set():
                with self.edge_voice_lock:
                    reason = self.edge_voice_fallback_request_reason or "user_requested"
                self._enable_gtts_fallback(reason)
                return "gtts_allowed"

            remaining = max(0.0, deadline - now)
            if remaining <= 0:
                self._enable_gtts_fallback("edge_wait_timeout")
                return "gtts_allowed"

            manual_probe = self.edge_voice_probe_requested.is_set()
            if manual_probe:
                self.edge_voice_probe_requested.clear()
                self.edge_network_guard.request_probe_now()

            automatic_probe = (
                now >= next_probe_at
                and self.edge_network_guard.seconds_until_probe() <= 0
            )
            if manual_probe or automatic_probe:
                probe_number += 1
                self.set_progress(
                    81,
                    f"Проверка Edge TTS после смены VPN (попытка {probe_number})...",
                )
                self._emit_problem(
                    "tts_edge_probe_started",
                    message="Проверяется доступность выбранного голоса Edge TTS.",
                    tts_segment_index=segment_index,
                    voice=voice,
                    incident=incident,
                    probe_number=probe_number,
                    manual=manual_probe,
                    remaining_wait_sec=round(remaining, 1),
                )
                self._notify_network_notice(
                    "checking",
                    incident=incident,
                    probe_number=probe_number,
                    manual=manual_probe,
                )
                provider = self.generate_tts(
                    text,
                    probe_path,
                    voice,
                    rate_pct=0,
                    segment_index=segment_index,
                    allow_gtts=False,
                )
                if provider == "edge_tts":
                    forced = self.edge_network_guard.force_recovery(
                        "edge_tts",
                        reason="successful_vpn_probe",
                    )
                    if not forced:
                        self._finish_edge_voice_preservation(
                            reason="successful_vpn_probe",
                            notify_action="recovered",
                        )
                    self.log("      ✅ Новый VPN-маршрут подходит: выбранный голос Edge TTS восстановлен.")
                    self._emit_problem(
                        "tts_voice_preservation_recovered",
                        message="Проверка Edge TTS успешна; озвучка продолжится выбранным голосом.",
                        tts_segment_index=segment_index,
                        voice=voice,
                        incident=incident,
                        probe_number=probe_number,
                    )
                    return "edge_recovered"

                wait_for_probe = self.edge_network_guard.seconds_until_probe()
                next_probe_at = time.monotonic() + max(
                    float(EDGE_VOICE_PROBE_INTERVAL_SEC),
                    wait_for_probe,
                )
                self._emit_problem(
                    "tts_edge_probe_failed",
                    level="warning",
                    message="Edge TTS всё ещё недоступен; ожидание смены VPN продолжается.",
                    tts_segment_index=segment_index,
                    voice=voice,
                    incident=incident,
                    probe_number=probe_number,
                    next_probe_after_sec=round(max(0.0, next_probe_at - time.monotonic()), 1),
                    remaining_wait_sec=round(max(0.0, deadline - time.monotonic()), 1),
                )

            self.set_progress(
                80,
                f"VPN нестабилен: смените сервер и нажмите «Проверить» "
                f"(ожидание ещё {int(remaining) + 1}с)...",
            )
            self._sleep_or_cancel(min(0.5, remaining))

    def generate_tts(self, text: str, path: str, voice: str, rate_pct: int = 0,
                     segment_index: int = 0, allow_gtts: bool | None = None) -> str | None:
        self._check_cancel()
        text = normalize_tts_text(text)
        if not text:
            return None

        if allow_gtts is None:
            allow_gtts = bool(self._tts_allow_gtts_fallback)

        rate_pct = int(max(-20, min(35, rate_pct)))
        rate = f"{rate_pct:+d}%"
        target_code = self.target_info.get("code") or "target"
        text_hash = hashlib.sha256(text.encode("utf-8", errors="replace")).hexdigest()
        edge_key = self.tts_cache.make_key(text, voice, rate_pct, "edge_tts", target_code)

        with self.tts_cache.key_lock(edge_key):
            if self.tts_cache.restore(edge_key, path):
                self._increment_tts_stat("cache_hits")
                self._increment_tts_stat("edge_cache_hits")
                return "edge_tts"

            edge_mode = self.edge_network_guard.claim_probe_or_bypass()
            if edge_mode == "bypass":
                self._increment_tts_stat("edge_bypassed")
            else:
                edge_attempts = 1 if edge_mode == "probe" else 3
                for attempt in range(1, edge_attempts + 1):
                    self._check_cancel()
                    parallel_acquired = False
                    probe_acquired = False
                    try:
                        self._acquire_network_gate(self.edge_parallel_gate)
                        parallel_acquired = True
                        if edge_mode == "probe":
                            self._acquire_network_gate(self.edge_probe_gate)
                            probe_acquired = True
                        if os.path.exists(path):
                            os.remove(path)
                        self._run_edge_tts(text, path, voice, rate=rate)
                        if os.path.exists(path) and os.path.getsize(path) > 200:
                            self.edge_network_guard.record_success("edge_tts")
                            self.tts_cache.store(
                                edge_key,
                                path,
                                {
                                    "provider": "edge_tts",
                                    "target_language": target_code,
                                    "voice": voice,
                                    "rate_pct": rate_pct,
                                    "text_length": len(text),
                                    "text_sha256": text_hash,
                                },
                            )
                            self._increment_tts_stat("edge_created")
                            return "edge_tts"
                        raise RuntimeError("TTS создал пустой файл")
                    except CancelledError:
                        raise
                    except Exception as exc:
                        report_detail = self.edge_network_guard.record_failure("edge_tts", exc)
                        if report_detail:
                            self.log(
                                f"        ⚠️ EdgeTTS сегмент {segment_index}, {rate}, "
                                f"попытка {attempt}/{edge_attempts}: {compact_exception(exc)}"
                            )
                            self._problem(
                                "tts_retry",
                                level="warning",
                                message=(
                                    "Сетевая генерация Edge TTS не удалась; будет повтор. "
                                    "gTTS разрешается только после ожидания смены VPN."
                                ),
                                tts_segment_index=segment_index,
                                attempt=attempt,
                                attempts_total=edge_attempts,
                                request_mode=edge_mode,
                                rate=rate,
                                voice=voice,
                                text_length=len(text),
                                text_sha256=text_hash,
                                exception={
                                    "type": type(exc).__name__,
                                    "category": classify_exception(exc),
                                    "message": compact_exception(exc, max_len=1000),
                                    "chain": exception_chain(exc),
                                },
                            )
                        # После начала шторма не держим VPN тремя повторными WebSocket-запросами.
                        if self.edge_network_guard.is_active():
                            break
                        if attempt < edge_attempts:
                            if probe_acquired:
                                self.edge_probe_gate.release()
                                probe_acquired = False
                            if parallel_acquired:
                                self.edge_parallel_gate.release()
                                parallel_acquired = False
                            self._sleep_or_cancel(progressive_retry_delay(attempt, base=2.0, maximum=12.0))
                    finally:
                        if probe_acquired:
                            self.edge_probe_gate.release()
                        if parallel_acquired:
                            self.edge_parallel_gate.release()

        # gTTS не поддерживает Edge rate. Для ускоренной версии достаточно
        # обычной фразы: дальнейшую подгонку безопасно сделает atempo/rubberband.
        if rate_pct != 0:
            return None

        if not allow_gtts:
            self._increment_tts_stat("edge_deferred")
            self._begin_edge_voice_preservation(reason="edge_base_voice_unavailable")
            return None

        # gTTS не умеет такой же аккуратный rate/pitch, поэтому используем его только как резерв.
        gtts_lang = self.target_info.get("gtts") or self.target_info.get("code") or "en"
        gtts_key = self.tts_cache.make_key(text, voice, 0, "gtts", gtts_lang)
        last_gtts_error = None
        with self.tts_cache.key_lock(gtts_key):
            if self.tts_cache.restore(gtts_key, path):
                self._increment_tts_stat("cache_hits")
                self._mark_gtts_fallback(segment_index)
                return "gtts"

            install_requests_default_timeout()
            from gtts import gTTS
            for attempt in range(1, 4):
                gtts_acquired = False
                try:
                    self._check_cancel()
                    self.gtts_network_guard.before_request(self._sleep_or_cancel)
                    self._acquire_network_gate(self.gtts_parallel_gate)
                    gtts_acquired = True
                    if os.path.exists(path):
                        os.remove(path)
                    gTTS(text=text, lang=gtts_lang, slow=False).save(path)
                    if os.path.exists(path) and os.path.getsize(path) > 200:
                        self.gtts_network_guard.record_success("gtts")
                        self.tts_cache.store(
                            gtts_key,
                            path,
                            {
                                "provider": "gtts",
                                "target_language": gtts_lang,
                                "requested_voice": voice,
                                "voice_selection_supported": False,
                                "rate_pct": 0,
                                "text_length": len(text),
                                "text_sha256": text_hash,
                            },
                        )
                        fallback_count = self._mark_gtts_fallback(segment_index)
                        if fallback_count <= 3 or fallback_count % 50 == 0:
                            self.log(
                                f"        ↩️ [{segment_index}] Google TTS (резерв); "
                                f"всего резервных сегментов: {fallback_count}"
                            )
                        return "gtts"
                    raise RuntimeError("Google TTS создал пустой файл")
                except CancelledError:
                    raise
                except Exception as exc:
                    last_gtts_error = exc
                    self._increment_tts_stat("gtts_retries")
                    report_detail = self.gtts_network_guard.record_failure("gtts", exc)
                    if report_detail and attempt < 3:
                        self.log(
                            f"        ⚠️ Google TTS сегмент {segment_index}, "
                            f"попытка {attempt}/3: {compact_exception(exc)}"
                        )
                        self._problem(
                            "tts_gtts_retry",
                            level="warning",
                            message="Резервный Google TTS временно недоступен; будет повторная попытка.",
                            tts_segment_index=segment_index,
                            attempt=attempt,
                            attempts_total=3,
                            text_length=len(text),
                            text_sha256=text_hash,
                            exception={
                                "type": type(exc).__name__,
                                "category": classify_exception(exc),
                                "message": compact_exception(exc, max_len=1000),
                                "chain": exception_chain(exc),
                            },
                        )
                    if gtts_acquired:
                        self.gtts_parallel_gate.release()
                        gtts_acquired = False
                    if attempt < 3:
                        self._sleep_or_cancel(progressive_retry_delay(attempt, base=3.0, maximum=15.0))
                finally:
                    if gtts_acquired:
                        self.gtts_parallel_gate.release()

        if last_gtts_error is not None:
            self.log(
                f"        ⚠️ Google TTS сегмент {segment_index} не сработал после 3 попыток: "
                f"{compact_exception(last_gtts_error)}"
            )
            self._problem(
                "tts_generation_attempts_exhausted",
                level="warning",
                message="Текущий цикл Edge TTS и разрешённого gTTS исчерпан; сегмент будет повторён.",
                tts_segment_index=segment_index,
                voice=voice,
                rate=rate,
                text_length=len(text),
                text_sha256=text_hash,
                exception={
                    "type": type(last_gtts_error).__name__,
                    "category": classify_exception(last_gtts_error),
                    "message": compact_exception(last_gtts_error, max_len=1000),
                    "chain": exception_chain(last_gtts_error),
                },
            )

        return None

    # ── перевод ──────────────────────────────────────────────────────────────

    def translate_segment(self, text: str, index: int = 0, attempts: int = 3) -> str:
        text = (text or "").strip()
        if not text:
            return ""

        last_error = None
        attempts = max(1, int(attempts))
        text_hash = hashlib.sha256(text.encode("utf-8", errors="replace")).hexdigest()
        for attempt in range(1, attempts + 1):
            self._check_cancel()
            self.translation_network_guard.before_request(self._sleep_or_cancel)
            started_at = time.monotonic()
            try:
                translated = self._get_translation_client().translate(text)
                if translated and translated.strip():
                    self.translation_network_guard.record_success("translation")
                    if attempt > 1:
                        self._problem(
                            "translation_retry_recovered",
                            message="Повторная попытка перевода успешно завершилась.",
                            segment_index=index,
                            attempt=attempt,
                            elapsed_sec=round(time.monotonic() - started_at, 3),
                            source_length=len(text),
                            source_sha256=text_hash,
                        )
                    return translated.strip()
                raise RuntimeError("переводчик вернул пустой ответ")
            except CancelledError:
                raise
            except Exception as exc:
                last_error = exc
                report_detail = self.translation_network_guard.record_failure("translation", exc)
                if report_detail:
                    self.log(
                        f"      ⚠️ Перевод сегмента {index}, попытка {attempt}/{attempts}: "
                        f"{compact_exception(exc)}"
                    )
                    self._problem(
                        "translation_retry",
                        level="warning",
                        message="Запрос перевода не удался; попытка ограничена тайм-аутом.",
                        segment_index=index,
                        attempt=attempt,
                        attempts_total=attempts,
                        elapsed_sec=round(time.monotonic() - started_at, 3),
                        source_length=len(text),
                        source_sha256=text_hash,
                        target_language=self.target_info.get("code"),
                        exception={
                            "type": type(exc).__name__,
                            "category": classify_exception(exc),
                            "message": compact_exception(exc, max_len=1000),
                            "chain": exception_chain(exc),
                        },
                    )
                if attempt < attempts:
                    self._sleep_or_cancel(progressive_retry_delay(attempt))

        raise RuntimeError(f"Не удалось перевести сегмент {index}: {compact_exception(last_error)}") from last_error

    def translate_records_batch(self, records: list[tuple[int, dict]], attempts: int = 2
                                ) -> tuple[dict[int, str], list[tuple[int, dict, Exception]]]:
        """Переводит пачку одним запросом; при любой неоднозначности безопасно откатывается на поштучный режим."""
        if not records:
            return {}, []
        if len(records) == 1:
            index, record = records[0]
            try:
                return {index: self.translate_segment(record["source"], index, attempts=attempts)}, []
            except CancelledError:
                raise
            except Exception as exc:
                return {}, [(index, record, exc)]

        items = [(index, str(record.get("source") or "")) for index, record in records]
        payload, markers = build_translation_batch(items)
        first_index = records[0][0]
        last_index = records[-1][0]
        batch_label = f"{first_index}-{last_index}"
        fallback_reason = ""
        try:
            translated_payload = self.translate_segment(payload, index=batch_label, attempts=attempts)
            parsed = parse_translation_batch(translated_payload, markers)
            return parsed, []
        except CancelledError:
            raise
        except ValueError as exc:
            fallback_reason = compact_exception(exc)
            self._problem(
                "translation_batch_validation_failed",
                level="warning",
                message="Пакетный ответ отклонён: разделители не прошли строгую проверку; сегменты будут переведены по одному.",
                first_segment=first_index,
                last_segment=last_index,
                segments_count=len(records),
                reason=fallback_reason,
            )
        except Exception as exc:
            fallback_reason = compact_exception(exc)
            self._problem(
                "translation_batch_request_failed",
                level="warning",
                message="Пакетный запрос не удался; сегменты будут переведены по одному.",
                first_segment=first_index,
                last_segment=last_index,
                segments_count=len(records),
                exception={
                    "type": type(exc).__name__,
                    "category": classify_exception(exc),
                    "message": fallback_reason,
                },
            )

        self.log(
            f"      ↩️ Пачка {batch_label} не принята ({fallback_reason}); "
            "безопасный перевод по одному"
        )
        results = {}
        failures = []
        for index, record in records:
            try:
                results[index] = self.translate_segment(record["source"], index, attempts=attempts)
            except CancelledError:
                raise
            except Exception as exc:
                failures.append((index, record, exc))
        return results, failures

    # ── построение таймлайна ─────────────────────────────────────────────────

    def _tts_segment_log(self, index: int, message: str):
        text = str(message).strip()
        self.log(f"      [TTS-сегмент {index}] {text}")
        lowered = text.lower()
        if any(marker in lowered for marker in ("⚠", "ошибка", "timeout", "stderr")):
            self._problem(
                "tts_processing_warning",
                level="warning",
                message="Ошибка локальной подготовки TTS-аудио.",
                tts_segment_index=index,
                detail=redact_diagnostic_text(text, max_len=3000),
            )

    def _get_prepared_tts_audio(self, text: str, voice: str, index: int,
                                rate_pct: int, tag: str) -> str | None:
        """Восстанавливает готовый WAV или создаёт и атомарно сохраняет его в постоянный кэш."""
        target_code = self.target_info.get("code") or "target"
        prepared_path = os.path.join(self.temp_dir, f"seg_{index:05d}_{tag}.wav")
        prepared_extra = {
            "audio_settings": self.audio_settings,
            "sample_rate": SAMPLE_RATE,
            "prepared_cache_schema": TTS_PREPARED_CACHE_SCHEMA_VERSION,
        }

        # Кэш Edge и кэш gTTS разделены. Иначе WAV, когда-то созданный женским
        # резервным gTTS, мог выдаваться за выбранный мужской Edge-голос.
        cache_providers = ["edge_tts"]
        if self._tts_allow_gtts_fallback and rate_pct == 0:
            cache_providers.append("gtts")

        for source_provider in cache_providers:
            cache_key = self.tts_cache.make_key(
                text,
                voice,
                rate_pct,
                f"prepared_wav_{source_provider}",
                target_code,
                extra={**prepared_extra, "source_provider": source_provider},
            )
            with self.tts_cache.key_lock(cache_key):
                if self.tts_cache.restore(cache_key, prepared_path, suffix=".wav"):
                    self._increment_tts_stat("cache_hits")
                    self._increment_tts_stat("prepared_cache_hits")
                    if source_provider == "gtts":
                        self._increment_tts_stat("gtts_prepared_cache_hits")
                        self._mark_gtts_fallback(index)
                    return prepared_path

        raw_path = os.path.join(self.temp_dir, f"seg_{index:05d}_{tag}.mp3")
        source_provider = self.generate_tts(
            text,
            raw_path,
            voice,
            rate_pct=rate_pct,
            segment_index=index,
        )
        if not source_provider:
            return None

        cache_key = self.tts_cache.make_key(
            text,
            voice,
            rate_pct,
            f"prepared_wav_{source_provider}",
            target_code,
            extra={**prepared_extra, "source_provider": source_provider},
        )
        with self.tts_cache.key_lock(cache_key):
            # Пока ожидали блокировку, другой поток мог уже подготовить тот же WAV.
            if self.tts_cache.restore(cache_key, prepared_path, suffix=".wav"):
                self._increment_tts_stat("cache_hits")
                self._increment_tts_stat("prepared_cache_hits")
                if source_provider == "gtts":
                    self._increment_tts_stat("gtts_prepared_cache_hits")
                    self._mark_gtts_fallback(index)
                return prepared_path

            if polish_tts_audio(
                self.ffmpeg,
                raw_path,
                prepared_path,
                lambda message: self._tts_segment_log(index, message),
                cancel_event=self.cancel,
                audio_settings=self.audio_settings,
            ):
                cache_stored = self.tts_cache.store(
                    cache_key,
                    prepared_path,
                    {
                        "provider": f"prepared_wav_{source_provider}",
                        "source_provider": source_provider,
                        "target_language": target_code,
                        "requested_voice": voice,
                        "voice_selection_supported": source_provider == "edge_tts",
                        "rate_pct": rate_pct,
                        "audio_settings": self.audio_settings,
                        "sample_rate": SAMPLE_RATE,
                        "text_length": len(text),
                        "text_sha256": hashlib.sha256(text.encode("utf-8", errors="replace")).hexdigest(),
                    },
                    suffix=".wav",
                )
                if not cache_stored:
                    self._increment_tts_stat("prepared_cache_skipped_size_limit")
                return prepared_path
            return raw_path

    def _prepare_tts_segment(self, text: str, voice: str, index: int,
                             target_slot: float, hard_slot: float) -> tuple[str, float, dict] | tuple[None, float, dict]:
        """
        Готовит один сегмент без обрезки слов:
          1) Edge TTS обычным темпом;
          2) чистка/ресэмпл в WAV;
          3) если длинно — повторная генерация Edge TTS с нативным rate;
          4) если всё ещё длинно — tempo stretch без изменения pitch;
          5) если для попадания нужен темп выше выбранного лимита — не режем речь, а вставляем паузу видео.
        """
        stats = {
            "native_rate": 0,
            "tempo": 1.0,
            "tempo_method": "",
            "video_pause_needed": False,
            "total_speed": 1.0,
        }

        current_path = self._get_prepared_tts_audio(text, voice, index, 0, "edge_0")
        if not current_path:
            return None, 0.0, stats
        segment_log = lambda message: self._tts_segment_log(index, message)
        current_dur = get_audio_duration(self.ffprobe, current_path, segment_log)
        if current_dur <= 0:
            return None, 0.0, stats

        base_dur = current_dur
        speed_needed_total = base_dur / target_slot if target_slot > 0 else 1.0

        # Часть ускорения отдаём самому Edge TTS, но общий темп не должен превышать выбранный лимит.
        # Даже если фраза сильно длинная, мы просим TTS ускориться только в пределах качественного лимита,
        # а остаток компенсируем паузой видео.
        native_rate = edge_rate_from_speed(
            min(speed_needed_total, self.speech_speed_limit),
            speed_limit=self.speech_speed_limit,
        )
        if native_rate > 0:
            self._check_cancel()
            candidate = self._get_prepared_tts_audio(
                text, voice, index, native_rate, f"edge_{native_rate}"
            )
            if candidate:
                candidate_dur = get_audio_duration(self.ffprobe, candidate, segment_log)
                if 0 < candidate_dur < current_dur * 0.995:
                    current_path = candidate
                    current_dur = candidate_dur
                    stats["native_rate"] = native_rate

        # Оцениваем достигнутое ускорение и разрешаем post-tempo только до выбранного общего лимита.
        effective_speed = max(1.0, base_dur / current_dur) if current_dur > 0 else 1.0
        remaining_tempo_cap = max(1.0, self.speech_speed_limit / effective_speed)
        speed_needed_now = current_dur / target_slot if target_slot > 0 else 1.0

        if speed_needed_now > 1.025 and remaining_tempo_cap > 1.025:
            self._check_cancel()
            tempo = min(speed_needed_now, remaining_tempo_cap, MAX_NATURAL_TEMPO)
            if tempo > 1.025:
                stretched = os.path.join(self.temp_dir, f"seg_{index:05d}_tempo.wav")
                ok, method = time_stretch_audio(
                    self.ffmpeg, current_path, stretched, tempo, log=segment_log, cancel_event=self.cancel
                )
                if ok:
                    current_path = stretched
                    current_dur = get_audio_duration(self.ffprobe, stretched, segment_log)
                    stats["tempo"] = tempo
                    stats["tempo_method"] = method

        stats["total_speed"] = max(1.0, base_dur / current_dur) if current_dur > 0 else 1.0
        if current_dur > hard_slot + MIN_INSERTED_PAUSE:
            stats["video_pause_needed"] = True

        return current_path, current_dur, stats

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

        self._problem(
            "tts_parallel_preparation_started",
            message="Начата параллельная подготовка сегментов озвучки.",
            segments=len(jobs),
            workers=TTS_PREPARE_WORKERS,
            cache_dir=str(self.tts_cache.directory),
            speed_limit=self.speech_speed_limit,
        )

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

        if preparation_failures:
            first_pass_failures = list(dict.fromkeys(preparation_failures))
            jobs_by_index = {job[1]: job for job in jobs}
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
                freeze_at_original = min(float(video_dur), max(end, start + hard_slot))
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

        pause_count_before = len(sanitize_pause_plan(pause_plan, video_dur))
        pause_plan = merge_nearby_pause_plan(pause_plan, video_dur, segments)
        self._problem(
            "pause_plan_optimized",
            message="Близкие стоп-кадры безопасно объединены внутри естественных промежутков.",
            before=pause_count_before,
            after=len(pause_plan),
            merged=max(0, pause_count_before - len(pause_plan)),
            total_pause_sec=round(sum(float(item.get("duration", 0.0)) for item in pause_plan), 3),
        )
        final_dur = float(video_dur) + sum(float(p.get("duration", 0.0)) for p in pause_plan)
        if pause_plan:
            self.log(f"   ⏸️ Будет добавлено стоп-кадров: {len(pause_plan)}, новая длительность: {fmt_time(final_dur)}")

        self.log(f"\n   🎛️ Смешивание {len(ready_segments)} сегментов...")
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

    def _mix_in_batches(self, segments: list, video_dur: float) -> str:
        if len(segments) <= BATCH_SIZE:
            return self._amix_batch(segments, video_dur, "final")

        batches = [segments[i:i + BATCH_SIZE] for i in range(0, len(segments), BATCH_SIZE)]
        self.log(f"      ℹ️ Разбито на {len(batches)} пачек по ≤{BATCH_SIZE} сегм.")

        batch_wavs = []
        for batch_index, batch in enumerate(batches, 1):
            self._check_cancel()
            self.log(f"      🔄 Пачка {batch_index}/{len(batches)}...")
            batch_wavs.append(self._amix_batch(batch, video_dur, f"batch_{batch_index:03d}"))

        if len(batch_wavs) == 1:
            return batch_wavs[0]

        round_index = 1
        while len(batch_wavs) > BATCH_SIZE:
            next_round = []
            groups = [batch_wavs[i:i + BATCH_SIZE] for i in range(0, len(batch_wavs), BATCH_SIZE)]
            self.log(f"      🔗 Слияние WAV, круг {round_index}: {len(groups)} пачек...")
            for group_index, group in enumerate(groups, 1):
                self._check_cancel()
                next_round.append(self._amix_wavs(group, video_dur, f"merge_{round_index:02d}_{group_index:03d}"))
            batch_wavs = next_round
            round_index += 1

        self.log(f"      🔗 Финальное слияние {len(batch_wavs)} промежуточных WAV...")
        return self._amix_wavs(batch_wavs, video_dur, "final")

    def _amix_batch(self, segments: list, video_dur: float, tag: str) -> str:
        out_path = os.path.join(self.temp_dir, f"mix_{tag}.wav")

        if not segments:
            self._create_silence(out_path, video_dur)
            return out_path

        inputs = [
            "-f", "lavfi",
            "-i", f"anullsrc=channel_layout=stereo:sample_rate={SAMPLE_RATE}:duration={video_dur:.3f}",
        ]
        filter_parts = ["[0:a]anull[base]"]
        delayed_labels = ["[base]"]

        for idx, (delay_ms, file_path) in enumerate(segments):
            input_index = idx + 1
            inputs += ["-i", file_path]
            label = f"d{idx}"
            filter_parts.append(
                f"[{input_index}:a]aresample={SAMPLE_RATE},"
                "aformat=sample_fmts=fltp:channel_layouts=stereo,"
                f"adelay={delay_ms}|{delay_ms}[{label}]"
            )
            delayed_labels.append(f"[{label}]")

        # Смешиваем пачку одним amix, а не цепочкой amix→amix→amix: меньше накопленных артефактов.
        filter_parts.append(
            "".join(delayed_labels)
            + f"amix=inputs={len(delayed_labels)}:duration=first:dropout_transition=0:normalize=0,"
            + f"alimiter=limit={VOICE_LIMIT:.2f}[out]"
        )

        filter_complex = ";".join(filter_parts)
        cmd = [self.ffmpeg, "-y"] + inputs + [
            "-filter_complex", filter_complex,
            "-map", "[out]",
            "-t", f"{video_dur:.3f}",
            "-ar", str(SAMPLE_RATE),
            "-ac", "2",
            out_path,
        ]

        try:
            mix_timeout = calc_audio_work_timeout(video_dur, min_timeout=360, factor=0.70, extra=180)
            result = run_subprocess(cmd, timeout=mix_timeout, log=self.log, cancel_event=self.cancel)
            if result.returncode == 0 and os.path.exists(out_path) and os.path.getsize(out_path) > 100:
                return out_path
            self.log(f"      ⚠️ ffmpeg amix ошибка: {(result.stderr or '')[-600:].strip()}")
        except CancelledError:
            raise
        except Exception as exc:
            self.log(f"      ⚠️ ffmpeg amix исключение: {exc}")

        self.log("      ↩️ Переключаемся на numpy-метод для этой пачки...")
        return self._mix_numpy(segments, video_dur, out_path)

    def _amix_wavs(self, wav_files: list, video_dur: float, tag: str = "final") -> str:
        out_path = os.path.join(self.temp_dir, f"final_voice_{tag}.wav")
        inputs = []
        for wav_file in wav_files:
            inputs += ["-i", wav_file]

        n = len(wav_files)
        filter_complex = "".join(f"[{i}:a]" for i in range(n)) + \
            f"amix=inputs={n}:duration=longest:dropout_transition=0:normalize=0,alimiter=limit={VOICE_LIMIT:.2f}[out]"

        cmd = [self.ffmpeg, "-y"] + inputs + [
            "-filter_complex", filter_complex,
            "-map", "[out]",
            "-t", f"{video_dur:.3f}",
            "-ar", str(SAMPLE_RATE),
            "-ac", "2",
            out_path,
        ]

        try:
            mix_timeout = calc_audio_work_timeout(video_dur, min_timeout=360, factor=0.70, extra=240)
            result = run_subprocess(cmd, timeout=mix_timeout, log=self.log, cancel_event=self.cancel)
            if result.returncode == 0 and os.path.exists(out_path) and os.path.getsize(out_path) > 100:
                return out_path
            self.log(f"      ⚠️ Финальный amix ошибка: {(result.stderr or '')[-600:].strip()}")
        except CancelledError:
            raise
        except Exception as exc:
            self.log(f"      ⚠️ Финальный amix исключение: {exc}")

        self.log("      ↩️ Финальное слияние через numpy...")
        return self._mix_numpy([], video_dur, out_path, extra_wavs=wav_files)

    def _mix_numpy(self, segments: list, video_dur: float, out_path: str, extra_wavs: list = None) -> str:
        import numpy as np

        fps = SAMPLE_RATE
        total_samples = max(1, int(video_dur * fps))
        timeline = np.zeros((total_samples, 2), dtype=np.float32)

        def add_file(file_path: str, offset_samples: int):
            clip = None
            try:
                self._check_cancel()
                from moviepy.editor import AudioFileClip
                if offset_samples >= total_samples:
                    self.log(f"      ⚠️ numpy: файл за пределами таймлайна: {os.path.basename(file_path)}")
                    return
                clip = AudioFileClip(file_path)
                arr = clip.to_soundarray(fps=fps)
                if arr.ndim == 1:
                    arr = np.stack([arr, arr], axis=1)
                elif arr.shape[1] == 1:
                    arr = np.repeat(arr, 2, axis=1)
                elif arr.shape[1] > 2:
                    arr = arr[:, :2]

                arr = arr.astype(np.float32)
                end = min(offset_samples + len(arr), total_samples)
                if end <= offset_samples:
                    return
                timeline[offset_samples:end] += arr[:end - offset_samples]
            except Exception as exc:
                self.log(f"      ⚠️ numpy не смог добавить {os.path.basename(file_path)}: {exc}")
            finally:
                if clip:
                    try:
                        clip.close()
                    except Exception:
                        pass

        for delay_ms, file_path in (segments or []):
            self._check_cancel()
            add_file(file_path, int(delay_ms / 1000 * fps))

        for wav_file in (extra_wavs or []):
            self._check_cancel()
            add_file(wav_file, 0)

        peak = float(np.abs(timeline).max())
        if peak > 0.95:
            timeline = timeline / peak * 0.93

        import wave
        with wave.open(out_path, "w") as wav:
            wav.setnchannels(2)
            wav.setsampwidth(2)
            wav.setframerate(fps)
            wav.writeframes((timeline * 32767).clip(-32768, 32767).astype(np.int16).tobytes())

        return out_path

    def _create_silence(self, out_path: str, duration: float):
        cmd = [
            self.ffmpeg, "-y",
            "-f", "lavfi",
            "-i", f"anullsrc=channel_layout=stereo:sample_rate={SAMPLE_RATE}",
            "-t", f"{duration:.3f}",
            out_path,
        ]
        result = run_subprocess(cmd, timeout=60, log=self.log, cancel_event=self.cancel)
        if result.returncode != 0:
            raise RuntimeError(f"Не удалось создать тишину: {(result.stderr or '')[-400:].strip()}")

    # ── главный метод ────────────────────────────────────────────────────────

    def process(self, input_path: str, output_path: str, voice: str, model_size: str,
                keep_original: bool, orig_vol_pct: int, target_info: dict | None = None,
                review_before_tts: bool = False, audio_settings: dict | None = None) -> bool:
        video = None
        tts_clip = None
        final_audio = None
        output_clip = None
        process_started_at = time.monotonic()
        checkpoint_path = None
        pipeline_succeeded = False

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
            if not extract_audio_for_whisper(
                self.ffmpeg,
                input_path,
                tmp_wav,
                self.log,
                cancel_event=self.cancel,
                timeout=extract_timeout,
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
            )

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
            self._close_translation_client()
            for clip in (output_clip, final_audio, tts_clip, video):
                if clip:
                    try:
                        clip.close()
                    except Exception:
                        pass
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


# ─────────────────────────────────────────────────────────────────────────────
#  GUI
# ─────────────────────────────────────────────────────────────────────────────

class App:
    def __init__(self, root: tk.Tk):
        self.root = root
        self.root.title("Видео Переводчик PRO — автоязык → выбранный язык")
        self.root.geometry("760x930")
        self.root.resizable(True, True)

        self.config_file = Path.home() / ".video_translator_max_quality_sync.json"
        self.video_files = []
        self._cancel_event = threading.Event()
        self._processing = False
        self._closing = False
        self._suspend_save = True
        self.file_logger = None
        self.last_log_path = ""
        self.problem_logger = None
        self.problem_logger_error = ""
        self._resume_batch_state = None
        self._active_translator = None
        self._vpn_notice_window = None
        self._vpn_notice_status = None
        self._vpn_notice_detail = None
        try:
            self.problem_logger = ProblemLogger()
        except Exception as exc:
            self.problem_logger_error = f"{type(exc).__name__}: {compact_exception(exc)}"

        self._build_ui()
        self.load_settings()
        self._suspend_save = False
        self._apply_keep_state(save=False)
        self.root.protocol("WM_DELETE_WINDOW", self._on_close)
        self._safe_after(250, self._offer_batch_recovery)

    # ── построение интерфейса ────────────────────────────────────────────────

    def _build_ui(self):
        style = ttk.Style()
        style.theme_use("clam")
        style.configure("Start.TButton", font=("Arial", 11, "bold"), foreground="white", background="#1b5e20")
        style.map("Start.TButton", background=[("disabled", "#888"), ("active", "#2e7d32")])
        style.configure("Cancel.TButton", foreground="white", background="#b71c1c")
        style.map("Cancel.TButton", background=[("disabled", "#888"), ("active", "#c62828")])

        pad = dict(padx=10, pady=4)

        files_frame = ttk.LabelFrame(self.root, text="1. Видеофайлы", padding=8)
        files_frame.pack(fill="both", expand=False, **pad)

        buttons_frame = ttk.Frame(files_frame)
        buttons_frame.pack(fill="x", pady=(0, 4))
        self.btn_add = ttk.Button(buttons_frame, text="➕ Добавить", command=self.add_files)
        self.btn_add.pack(side="left", padx=2)
        self.btn_remove = ttk.Button(buttons_frame, text="➖ Удалить", command=self.remove_selected)
        self.btn_remove.pack(side="left", padx=2)
        self.btn_clear = ttk.Button(buttons_frame, text="🗑️ Очистить", command=self.clear_all)
        self.btn_clear.pack(side="left", padx=2)
        self.lbl_count = ttk.Label(buttons_frame, text="Файлов: 0", foreground="#666")
        self.lbl_count.pack(side="right")

        list_frame = ttk.Frame(files_frame)
        list_frame.pack(fill="both")
        scrollbar = ttk.Scrollbar(list_frame)
        scrollbar.pack(side="right", fill="y")
        self.listbox = tk.Listbox(
            list_frame,
            height=5,
            yscrollcommand=scrollbar.set,
            selectmode=tk.EXTENDED,
            font=("Consolas", 9),
        )
        self.listbox.pack(side="left", fill="both", expand=True)
        scrollbar.config(command=self.listbox.yview)

        recog_frame = ttk.LabelFrame(self.root, text="2. Язык, распознавание и голос", padding=8)
        recog_frame.pack(fill="x", **pad)
        recog_frame.columnconfigure(1, weight=1)

        ttk.Label(recog_frame, text="Перевести на:").grid(row=0, column=0, sticky="w", pady=2)
        self.combo_language = ttk.Combobox(recog_frame, values=get_language_labels(), state="readonly")
        self.combo_language.set(DEFAULT_TARGET_LANGUAGE)
        self.combo_language.grid(row=0, column=1, sticky="ew", padx=8, pady=2)
        self.combo_language.bind("<<ComboboxSelected>>", self._on_language_changed)

        ttk.Label(recog_frame, text="Диктор:").grid(row=1, column=0, sticky="w", pady=2)
        self.combo_voice = ttk.Combobox(recog_frame, values=list(get_voice_options(DEFAULT_TARGET_LANGUAGE).keys()), state="readonly")
        self.combo_voice.current(0)
        self.combo_voice.grid(row=1, column=1, sticky="ew", padx=8, pady=2)
        self.combo_voice.bind("<<ComboboxSelected>>", lambda _event: self.save_settings())

        ttk.Label(recog_frame, text="Точность:").grid(row=2, column=0, sticky="w", pady=2)
        self.combo_model = ttk.Combobox(recog_frame, values=list(MODELS_MAP.keys()), state="readonly")
        self.combo_model.current(1)
        self.combo_model.grid(row=2, column=1, sticky="ew", padx=8, pady=2)
        self.combo_model.bind("<<ComboboxSelected>>", lambda _event: self.save_settings())

        self.var_review = tk.BooleanVar(value=True)
        self.chk_review = ttk.Checkbutton(
            recog_frame,
            text="Перед озвучкой подкорректировать перевод",
            variable=self.var_review,
            command=self.save_settings,
        )
        self.chk_review.grid(row=3, column=0, columnspan=2, sticky="w", pady=(5, 0))

        audio_frame = ttk.LabelFrame(self.root, text="3. Настройки аудио", padding=8)
        audio_frame.pack(fill="x", **pad)
        self.var_keep = tk.BooleanVar(value=False)
        self.chk_keep = ttk.Checkbutton(
            audio_frame,
            text="Оставить оригинальный звук фоном",
            variable=self.var_keep,
            command=lambda: self._apply_keep_state(save=True),
        )
        self.chk_keep.pack(anchor="w")

        volume_frame = ttk.Frame(audio_frame)
        volume_frame.pack(fill="x", pady=(4, 0))
        self.lbl_vol = ttk.Label(volume_frame, text="Громкость оригинала: 15%", width=26)
        self.lbl_vol.pack(side="left")
        self.var_volume = tk.IntVar(value=15)
        self.scale_vol = ttk.Scale(
            volume_frame,
            from_=0,
            to=100,
            orient="horizontal",
            variable=self.var_volume,
            command=self._on_vol_changed,
        )
        self.scale_vol.pack(side="left", fill="x", expand=True, padx=6)

        voice_volume_frame = ttk.Frame(audio_frame)
        voice_volume_frame.pack(fill="x", pady=(6, 0))
        self.lbl_voice_vol = ttk.Label(voice_volume_frame, text="Громкость новой озвучки: 100%", width=30)
        self.lbl_voice_vol.pack(side="left")
        self.var_voice_volume = tk.IntVar(value=100)
        self.scale_voice_volume = ttk.Scale(
            voice_volume_frame,
            from_=50,
            to=150,
            orient="horizontal",
            variable=self.var_voice_volume,
            command=self._on_voice_volume_changed,
        )
        self.scale_voice_volume.pack(side="left", fill="x", expand=True, padx=6)

        highpass_frame = ttk.Frame(audio_frame)
        highpass_frame.pack(fill="x", pady=(6, 0))
        self.lbl_highpass = ttk.Label(highpass_frame, text="Убрать гул ниже: 55 Гц", width=30)
        self.lbl_highpass.pack(side="left")
        self.var_highpass = tk.IntVar(value=55)
        self.scale_highpass = ttk.Scale(
            highpass_frame,
            from_=0,
            to=220,
            orient="horizontal",
            variable=self.var_highpass,
            command=self._on_highpass_changed,
        )
        self.scale_highpass.pack(side="left", fill="x", expand=True, padx=6)

        lowpass_frame = ttk.Frame(audio_frame)
        lowpass_frame.pack(fill="x", pady=(6, 0))
        self.lbl_lowpass = ttk.Label(lowpass_frame, text="Смягчить верх: выкл", width=30)
        self.lbl_lowpass.pack(side="left")
        self.var_lowpass = tk.IntVar(value=0)
        self.scale_lowpass = ttk.Scale(
            lowpass_frame,
            from_=0,
            to=16000,
            orient="horizontal",
            variable=self.var_lowpass,
            command=self._on_lowpass_changed,
        )
        self.scale_lowpass.pack(side="left", fill="x", expand=True, padx=6)

        loudness_frame = ttk.Frame(audio_frame)
        loudness_frame.pack(fill="x", pady=(6, 0))
        self.lbl_loudness = ttk.Label(loudness_frame, text="Итоговая громкость: -16 LUFS", width=30)
        self.lbl_loudness.pack(side="left")
        self.var_loudness = tk.IntVar(value=-16)
        self.scale_loudness = ttk.Scale(
            loudness_frame,
            from_=-24,
            to=-12,
            orient="horizontal",
            variable=self.var_loudness,
            command=self._on_loudness_changed,
        )
        self.scale_loudness.pack(side="left", fill="x", expand=True, padx=6)

        self.var_denoise = tk.BooleanVar(value=False)
        self.chk_denoise = ttk.Checkbutton(
            audio_frame,
            text="Лёгкое шумоподавление перед озвучкой",
            variable=self.var_denoise,
            command=self.save_settings,
        )
        self.chk_denoise.pack(anchor="w", pady=(6, 0))

        speed_frame = ttk.Frame(audio_frame)
        speed_frame.pack(fill="x", pady=(6, 0))
        ttk.Label(speed_frame, text="Максимальное ускорение речи:", width=30).pack(side="left")
        self.combo_speed = ttk.Combobox(
            speed_frame,
            values=[f"{value:.2f}" for value in SPEECH_SPEED_LIMITS],
            state="readonly",
            width=8,
        )
        self.combo_speed.set(f"{TOTAL_MAX_SPEECH_SPEED:.2f}")
        self.combo_speed.pack(side="left", padx=6)
        self.combo_speed.bind("<<ComboboxSelected>>", lambda _event: self.save_settings())
        ttk.Label(speed_frame, text="x (выше — меньше стоп-кадров)", foreground="#666").pack(side="left")

        ttk.Label(
            self.root,
            text="ℹ️ Фразы переводятся, озвучиваются и вставляются по таймкодам оригинала",
            foreground="#1565c0",
            font=("Arial", 8),
        ).pack(padx=10, anchor="w")

        progress_frame = ttk.LabelFrame(self.root, text="Прогресс", padding=6)
        progress_frame.pack(fill="x", **pad)
        self.var_prog = tk.IntVar(value=0)
        ttk.Progressbar(progress_frame, variable=self.var_prog, maximum=100).pack(fill="x", pady=(0, 3))
        self.lbl_status = ttk.Label(progress_frame, text="Ожидание...", foreground="#555", font=("Arial", 9))
        self.lbl_status.pack(anchor="w")

        log_frame = ttk.LabelFrame(self.root, text="Журнал", padding=6)
        log_frame.pack(fill="both", expand=True, **pad)
        self.txt_log = scrolledtext.ScrolledText(
            log_frame,
            height=11,
            state="disabled",
            font=("Consolas", 8),
            wrap="word",
        )
        self.txt_log.pack(fill="both", expand=True)
        self.btn_clear_log = ttk.Button(log_frame, text="Очистить журнал", command=self._clear_log)
        self.btn_clear_log.pack(anchor="e", pady=(2, 0))

        bottom_frame = ttk.Frame(self.root)
        bottom_frame.pack(fill="x", padx=10, pady=8)
        self.btn_start = ttk.Button(
            bottom_frame,
            text="▶  НАЧАТЬ ОБРАБОТКУ",
            style="Start.TButton",
            command=self.start,
        )
        self.btn_start.pack(side="left", fill="x", expand=True, ipady=10, padx=(0, 5))
        self.btn_cancel = ttk.Button(
            bottom_frame,
            text="⏹ Отмена",
            style="Cancel.TButton",
            command=self.cancel,
            state="disabled",
        )
        self.btn_cancel.pack(side="left", ipady=10, ipadx=14)

    # ── безопасные вызовы из потоков ─────────────────────────────────────────

    def _safe_after(self, delay_ms: int, func, *args):
        if self._closing:
            return False
        try:
            self.root.after(delay_ms, func, *args)
            return True
        except Exception as exc:
            self._problem(
                "ui_callback_schedule_failed",
                level="warning",
                message="Не удалось передать обновление в GUI-поток.",
                callback=getattr(func, "__name__", type(func).__name__),
                exception={
                    "type": type(exc).__name__,
                    "category": classify_exception(exc),
                    "message": compact_exception(exc, max_len=1000),
                },
            )
            return False

    def _threadsafe_progress(self, value: int, status: str = ""):
        self._safe_after(0, self._set_progress, value, status)

    def _threadsafe_network_notice(self, action: str, details: dict):
        self._safe_after(0, self._handle_network_notice, str(action), dict(details or {}))

    def _handle_network_notice(self, action: str, details: dict):
        if self._closing:
            return
        if action == "unstable":
            self._show_vpn_notice(details)
            return

        window = self._vpn_notice_window
        if not window or not window.winfo_exists():
            return

        if action == "recovered":
            self._vpn_notice_status.set(
                "✅ Edge TTS снова отвечает. Озвучка продолжится выбранным голосом."
            )
            self._vpn_notice_detail.set(
                "Новый VPN-маршрут успешно проверен. Резервный голос для ожидавших сегментов не нужен."
            )
            self._problem(
                "vpn_instability_notification_updated",
                message="Уведомление обновлено: Edge TTS восстановился.",
                action=action,
                **details,
            )
            self._safe_after(5000, self._close_vpn_notice)
        elif action == "checking":
            self._vpn_notice_status.set("🔎 Проверяю выбранный VPN-сервер через Edge TTS...")
            self._vpn_notice_detail.set(
                "Программа создаёт настоящий аудиофрагмент выбранным голосом. "
                "Результат проверки появится автоматически."
            )
        elif action == "fallback":
            self._vpn_notice_status.set(
                "⚠️ Edge TTS не восстановился. Программа продолжает через резервный gTTS."
            )
            self._vpn_notice_detail.set(
                "gTTS не умеет выбирать заданный Edge-голос, поэтому тембр только оставшихся фраз может отличаться."
            )
            self._problem(
                "vpn_instability_notification_updated",
                level="warning",
                message="Уведомление обновлено: включён резервный gTTS.",
                action=action,
                **details,
            )
        elif action == "stage_finished":
            self._close_vpn_notice()

    def _show_vpn_notice(self, details: dict):
        window = self._vpn_notice_window
        if window and window.winfo_exists():
            window.deiconify()
            window.lift()
            try:
                window.attributes("-topmost", True)
                window.after(3000, lambda: window.winfo_exists() and window.attributes("-topmost", False))
            except tk.TclError:
                pass
            return

        wait_sec = int(details.get("wait_sec") or EDGE_VOICE_PRESERVE_SEC)
        window = tk.Toplevel(self.root)
        self._vpn_notice_window = window
        window.title("VPN нестабилен — требуется внимание")
        window.resizable(False, False)
        window.transient(self.root)
        try:
            window.attributes("-topmost", True)
        except tk.TclError:
            pass

        body = ttk.Frame(window, padding=16)
        body.pack(fill="both", expand=True)
        ttk.Label(
            body,
            text="🔔 Edge TTS не отвечает через текущий VPN-сервер",
            font=("Arial", 11, "bold"),
            foreground="#b45309",
        ).pack(anchor="w")
        selected_voice = str(details.get("voice") or "выбранный в программе")
        ttk.Label(
            body,
            text=f"Выбранный голос: {selected_voice}",
            foreground="#444",
        ).pack(anchor="w", pady=(3, 0))

        self._vpn_notice_status = tk.StringVar(
            value=(
                "Программа пока НЕ переключается на другой голос и сохраняет "
                f"выбранный Edge-голос до {wait_sec} секунд."
            )
        )
        self._vpn_notice_detail = tk.StringVar(
            value=(
                "Переключите сервер в вашей VPN-программе, затем нажмите кнопку проверки. "
                "Окно не блокирует обработку и не мешает работать с VPN."
            )
        )
        ttk.Label(
            body,
            textvariable=self._vpn_notice_status,
            wraplength=540,
            justify="left",
        ).pack(fill="x", pady=(10, 5))
        ttk.Label(
            body,
            textvariable=self._vpn_notice_detail,
            wraplength=540,
            justify="left",
            foreground="#555",
        ).pack(fill="x", pady=(0, 12))

        buttons = ttk.Frame(body)
        buttons.pack(fill="x")
        ttk.Button(
            buttons,
            text="✅ VPN-сервер сменён — проверить",
            command=self._vpn_probe_now,
        ).pack(side="left", padx=(0, 6))
        ttk.Button(
            buttons,
            text="Использовать gTTS сейчас",
            command=self._vpn_allow_fallback_now,
        ).pack(side="left", padx=6)
        ttk.Button(
            buttons,
            text="Скрыть",
            command=self._close_vpn_notice,
        ).pack(side="right")

        def on_destroy(event=None):
            if event is not None and event.widget is not window:
                return
            if self._vpn_notice_window is window:
                self._vpn_notice_window = None
                self._vpn_notice_status = None
                self._vpn_notice_detail = None

        window.protocol("WM_DELETE_WINDOW", self._close_vpn_notice)
        window.bind("<Destroy>", on_destroy, add="+")
        window.update_idletasks()
        try:
            x = self.root.winfo_rootx() + max(20, (self.root.winfo_width() - window.winfo_width()) // 2)
            y = self.root.winfo_rooty() + 80
            window.geometry(f"+{x}+{y}")
            window.lift()
            window.bell()
            window.after(3500, lambda: window.winfo_exists() and window.attributes("-topmost", False))
        except tk.TclError:
            pass

        self._problem(
            "vpn_instability_notification_shown",
            level="warning",
            message="Пользователю показано неблокирующее уведомление о нестабильном VPN для Edge TTS.",
            **details,
        )

    def _vpn_probe_now(self):
        translator = self._active_translator
        if translator and translator.request_edge_probe_now():
            if self._vpn_notice_status is not None:
                self._vpn_notice_status.set("✅ Новый VPN-сервер отмечен; проверка поставлена в очередь.")
            if self._vpn_notice_detail is not None:
                self._vpn_notice_detail.set(
                    "Сначала безопасно завершатся уже начатые сетевые попытки, затем программа "
                    "создаст контрольный аудиофрагмент выбранным голосом."
                )
        elif self._vpn_notice_detail is not None:
            self._vpn_notice_detail.set("Проверка уже не требуется: этап ожидания завершён.")

    def _vpn_allow_fallback_now(self):
        window = self._vpn_notice_window
        if not messagebox.askyesno(
            "Перейти на резервный gTTS?",
            "gTTS поможет закончить обработку, но не умеет выбирать заданный Edge-голос.\n\n"
            "Тембр оставшихся фраз может отличаться. Продолжить?",
            parent=window if window and window.winfo_exists() else self.root,
        ):
            return
        translator = self._active_translator
        if translator and translator.allow_gtts_fallback_now(reason="user_confirmed_fallback"):
            if self._vpn_notice_status is not None:
                self._vpn_notice_status.set("↩️ Переход на резервный gTTS подтверждён...")

    def _close_vpn_notice(self):
        window = self._vpn_notice_window
        self._vpn_notice_window = None
        self._vpn_notice_status = None
        self._vpn_notice_detail = None
        if window and window.winfo_exists():
            try:
                window.destroy()
            except tk.TclError:
                pass

    def _problem(self, event: str, level: str = "info", message: str = "", **details):
        try:
            if self.problem_logger:
                self.problem_logger.event(event, level=level, message=message, **details)
        except Exception:
            pass

    def _log(self, msg: str):
        # Сначала пишем в .txt из любого потока, затем безопасно обновляем окно Tkinter.
        try:
            if self.file_logger:
                self.file_logger.write(msg)
        except Exception:
            pass

        text = str(msg)
        lowered = text.lower()
        error_markers = ("❌", "traceback", "критическая ошибка", "неожиданная ошибка")
        warning_markers = ("⚠", "timeout", "timed out", "ошибка", "stderr")
        # Эти сообщения уже получают отдельное структурированное событие с полным контекстом.
        # Не создаём второй runtime_* дубликат той же ошибки.
        structured_owner_markers = (
            "перевод сегмента",
            "edgetts сегмент",
            "google tts сегмент",
            "tts-сегмент",
            "сетевой шторм",
            "кодер ",
            "❌ ошибка:",
            "неожиданная ошибка файла",
            "файл не обработан",
            "traceback (most recent call last)",
        )
        already_structured = any(marker in lowered for marker in structured_owner_markers)
        if not already_structured and any(marker in lowered for marker in error_markers):
            self._problem(
                "runtime_error_log",
                level="error",
                message=redact_diagnostic_text(text),
            )
        elif not already_structured and any(marker in lowered for marker in warning_markers):
            self._problem(
                "runtime_warning_log",
                level="warning",
                message=redact_diagnostic_text(text),
            )

        def do_log():
            if self._closing:
                return
            self.txt_log.config(state="normal")
            self.txt_log.insert(tk.END, msg + "\n")
            self.txt_log.see(tk.END)
            self.txt_log.config(state="disabled")

        self._safe_after(0, do_log)

    # ── действия пользователя ────────────────────────────────────────────────

    def add_files(self):
        paths = filedialog.askopenfilenames(
            filetypes=[
                ("Видео", "*.mp4 *.mkv *.avi *.mov *.webm *.ts *.flv"),
                ("Все", "*.*"),
            ]
        )
        existing = {os.path.normcase(os.path.abspath(p)) for p in self.video_files}
        for path in paths:
            normalized = os.path.normcase(os.path.abspath(path))
            if path and normalized not in existing:
                self.video_files.append(path)
                existing.add(normalized)
                self.listbox.insert(tk.END, os.path.basename(path))
        if paths:
            self._resume_batch_state = None
        self._update_count()

    def remove_selected(self):
        selected = list(self.listbox.curselection())
        for index in reversed(selected):
            self.listbox.delete(index)
            del self.video_files[index]
        if selected:
            self._resume_batch_state = None
        self._update_count()

    def clear_all(self):
        self.listbox.delete(0, tk.END)
        self.video_files.clear()
        self._resume_batch_state = None
        self._update_count()

    def _restore_batch_settings(self, settings: dict):
        language_label = str(settings.get("language_label") or "")
        if language_label not in TARGET_LANGUAGES:
            language_label = get_target_language_by_code(settings.get("target_language"))
        self.combo_language.set(language_label)
        self._refresh_voice_list(language_label, preferred_voice_code=settings.get("voice"))

        model_code = str(settings.get("model") or "")
        for index, code in enumerate(MODELS_MAP.values()):
            if code == model_code:
                self.combo_model.current(index)
                break

        self.var_keep.set(bool(settings.get("keep_original_audio")))
        self.var_volume.set(max(0, min(100, int(settings.get("original_volume_pct", 15)))))
        self.var_review.set(bool(settings.get("review_before_tts")))
        audio = normalize_audio_settings(settings.get("audio") or {})
        self.var_voice_volume.set(audio["voice_volume_pct"])
        self.var_highpass.set(audio["highpass_hz"])
        self.var_lowpass.set(audio["lowpass_hz"])
        self.var_denoise.set(audio["noise_reduction"])
        self.var_loudness.set(audio["master_loudness_i"])
        self.combo_speed.set(f"{audio['speech_speed_limit']:.2f}")
        self._apply_keep_state(save=False)

    def _offer_batch_recovery(self):
        if self._closing or self._processing:
            return
        state = load_batch_recovery_state()
        if not state and self.problem_logger:
            previous = self.problem_logger._summary.get("previous_session") or {}
            state = reconstruct_batch_recovery_state_from_problem_log(previous.get("problem_log") or "")
            if state:
                try:
                    save_batch_recovery_state(state)
                    self._problem(
                        "batch_recovery_reconstructed_from_problem_log",
                        level="warning",
                        message="Пакет старой версии восстановлен по сохранённому JSONL-журналу.",
                        batch_id=state.get("batch_id") or "",
                        source_problem_log=state.get("source_problem_log") or "",
                        files_count=len(state.get("files") or []),
                        successful=sum(
                            str(item.get("status") or "") == "succeeded"
                            for item in state.get("files", [])
                        ),
                    )
                except (OSError, TypeError, ValueError) as exc:
                    self._problem(
                        "batch_recovery_save_failed",
                        level="warning",
                        message="Пакет найден в старом журнале, но его новую контрольную точку сохранить не удалось.",
                        exception={
                            "type": type(exc).__name__,
                            "category": classify_exception(exc),
                            "message": compact_exception(exc),
                        },
                    )
        if not state or str(state.get("status") or "") == "completed":
            return

        pending = batch_recovery_pending_entries(state)
        if not pending:
            return
        available = []
        missing = []
        changed = []
        for entry in pending:
            input_info = entry.get("input") or {}
            path = str(input_info.get("path") or "")
            if not path or not os.path.isfile(path):
                missing.append(path or "<путь не записан>")
                continue
            current = input_file_signature(path)
            if (
                input_info.get("size") is not None
                and (
                    int(input_info.get("size")) != int(current.get("size", -1))
                    or int(input_info.get("modified_ns", -1)) != int(current.get("modified_ns", -2))
                )
            ):
                changed.append(path)
            available.append(path)

        if not available:
            self._problem(
                "batch_recovery_unavailable",
                level="warning",
                message="Сохранённую пакетную задачу нельзя продолжить: входные файлы недоступны.",
                batch_id=state.get("batch_id") or "",
                missing_files=missing,
            )
            return

        succeeded = sum(str(item.get("status") or "") == "succeeded" for item in state.get("files", []))
        text = (
            "Найдена незавершённая пакетная обработка.\n\n"
            f"Уже готово: {succeeded}\n"
            f"Осталось доступных файлов: {len(available)}\n"
            f"Папка результатов: {state.get('output_dir')}\n"
        )
        if missing:
            text += f"Недоступно файлов: {len(missing)}\n"
        if changed:
            text += f"Изменено после прошлого запуска: {len(changed)}\n"
        text += "\nВосстановить список и продолжить с незавершённого файла?"
        accepted = messagebox.askyesno("Продолжить пакет", text)
        self._problem(
            "batch_recovery_offer_answered",
            level="warning" if missing or changed else "info",
            message="Пользователь выбрал, продолжать ли незавершённую пакетную задачу.",
            batch_id=state.get("batch_id") or "",
            accepted=accepted,
            available_files=len(available),
            missing_files=missing,
            changed_files=changed,
        )
        if not accepted:
            return

        self._resume_batch_state = state
        self.video_files = available
        self.listbox.delete(0, tk.END)
        for path in available:
            self.listbox.insert(tk.END, os.path.basename(path))
        self._update_count()
        self._restore_batch_settings(state.get("settings") or {})
        self._set_progress(0, "Пакет восстановлен — нажмите «Начать обработку»")

    def start(self):
        if not self.video_files:
            messagebox.showwarning("Нет файлов", "Добавьте хотя бы одно видео.")
            return

        files = list(self.video_files)
        recovery_state = self._resume_batch_state
        if recovery_state:
            recoverable_paths = [
                str((entry.get("input") or {}).get("path") or "")
                for entry in batch_recovery_pending_entries(recovery_state)
                if os.path.isfile(str((entry.get("input") or {}).get("path") or ""))
            ]
            current_norm = [os.path.normcase(os.path.abspath(path)) for path in files]
            recovery_norm = [os.path.normcase(os.path.abspath(path)) for path in recoverable_paths]
            if current_norm != recovery_norm:
                recovery_state = None

        out_folder = str((recovery_state or {}).get("output_dir") or "")
        if not out_folder or not os.path.isdir(out_folder):
            out_folder = filedialog.askdirectory(title="Папка для сохранения")
        if not out_folder:
            return

        try:
            if self.file_logger:
                self.file_logger.close()
        except Exception:
            pass

        try:
            if recovery_state:
                prefix = "video_translator_batch_resume"
            elif len(files) > 1:
                prefix = "video_translator_batch"
            else:
                prefix = f"video_translator_{safe_log_filename(Path(files[0]).stem)}"
            self.file_logger = FileLogger(prefix=prefix)
            self.last_log_path = str(self.file_logger.path)
        except Exception as exc:
            self.file_logger = None
            self.last_log_path = ""
            messagebox.showwarning(
                "Логи не созданы",
                f"Не удалось создать папку/файл логов рядом с программой:\n{exc}",
            )

        if self.problem_logger_error:
            messagebox.showwarning(
                "Логи проблем не созданы",
                "Не удалось создать подробный журнал в папке «Логи проблем».\n"
                f"Причина: {self.problem_logger_error}",
            )
            self.problem_logger_error = ""

        self._cancel_event.clear()
        self._processing = True
        self._set_busy(True)

        language_label = self.combo_language.get() or DEFAULT_TARGET_LANGUAGE
        target_info = get_target_language(language_label)
        voices = get_voice_options(language_label)
        voice = voices.get(self.combo_voice.get()) or next(iter(voices.values()))
        model = MODELS_MAP[self.combo_model.get()]
        keep = bool(self.var_keep.get())
        volume = int(self.var_volume.get())
        review = bool(self.var_review.get())
        audio_settings = self._get_audio_settings()
        batch_settings = {
            "language_label": language_label,
            "voice": voice,
            "model": model,
            "target_language": target_info.get("code"),
            "review_before_tts": review,
            "keep_original_audio": keep,
            "original_volume_pct": volume,
            "audio": audio_settings,
        }
        if recovery_state:
            batch_state = recovery_state
            batch_state["status"] = "running"
            batch_state["output_dir"] = os.path.abspath(out_folder)
            batch_state["settings"] = diagnostic_json_value(batch_settings)
            resumed = True
        else:
            batch_state = create_batch_recovery_state(files, out_folder, batch_settings)
            resumed = False
        self._resume_batch_state = batch_state
        try:
            save_batch_recovery_state(batch_state)
        except OSError as exc:
            self._problem(
                "batch_recovery_save_failed",
                level="warning",
                message="Не удалось сохранить состояние пакетной обработки; сама обработка будет продолжена.",
                exception={
                    "type": type(exc).__name__,
                    "category": classify_exception(exc),
                    "message": compact_exception(exc),
                },
            )

        state_files = [
            str((entry.get("input") or {}).get("path") or "")
            for entry in batch_state.get("files", [])
        ]
        file_states = [
            {
                "file_index": int(entry.get("file_index") or index),
                "input_path": str((entry.get("input") or {}).get("path") or ""),
                "output_path": str(entry.get("output_path") or ""),
                "status": str(entry.get("status") or "pending"),
            }
            for index, entry in enumerate(batch_state.get("files", []), 1)
        ]
        previously_successful = sum(
            str(entry.get("status") or "") == "succeeded"
            for entry in batch_state.get("files", [])
        )
        self._problem(
            "batch_started",
            message="Пользователь продолжил пакетную обработку." if resumed else "Пользователь запустил пакетную обработку.",
            batch_id=batch_state.get("batch_id") or "",
            resumed=resumed,
            previously_successful=previously_successful,
            files_count=len(state_files),
            files=state_files,
            file_states=file_states,
            output_dir=os.path.abspath(out_folder),
            normal_log=self.last_log_path,
            recovery_state_path=str(get_batch_recovery_state_path()),
            settings=batch_settings,
        )

        thread = threading.Thread(
            target=self._worker,
            args=(batch_state, out_folder, voice, model, keep, volume, target_info, review, audio_settings),
            daemon=True,
        )
        thread.start()

    def _worker(self, batch_state, out_folder, voice, model, keep, volume, target_info, review, audio_settings):
        entries = list(batch_state.get("files") or [])
        total = len(entries)
        successful = sum(str(entry.get("status") or "") == "succeeded" for entry in entries)
        cancelled = False
        log_path = self.last_log_path
        batch_started_at = time.monotonic()

        def save_state_safely():
            try:
                save_batch_recovery_state(batch_state)
            except OSError as exc:
                self._problem(
                    "batch_recovery_save_failed",
                    level="warning",
                    message="Не удалось обновить контрольную точку пакетной обработки.",
                    batch_id=batch_state.get("batch_id") or "",
                    exception={
                        "type": type(exc).__name__,
                        "category": classify_exception(exc),
                        "message": compact_exception(exc),
                    },
                )

        try:
            problem_log_path = str(self.problem_logger.problem_path) if self.problem_logger else "не создан"
            problem_summary_path = str(self.problem_logger.summary_path) if self.problem_logger else "не создан"
            problem_report_path = str(self.problem_logger.report_path) if self.problem_logger else "не создан"
            self._log(
                f"\n{'═' * 56}\n"
                f"📦 Файлов: {total}\n"
                f"🧾 Обычный лог: {log_path or 'не создан'}\n"
                f"🧩 Предупреждения и ошибки для Codex: {problem_log_path}\n"
                f"📌 Машиночитаемая сводка: {problem_summary_path}\n"
                f"📖 Краткий диагностический отчёт: {problem_report_path}\n"
                f"{'═' * 56}"
            )

            for entry in entries:
                if str(entry.get("status") or "") == "succeeded":
                    continue
                if self._cancel_event.is_set():
                    cancelled = True
                    break

                idx = int(entry.get("file_index") or 0)
                file_path = str((entry.get("input") or {}).get("path") or "")
                self._log(f"\n[{idx}/{total}] ▶ {os.path.basename(file_path)}")
                file_started_at = time.monotonic()
                if not file_path or not os.path.isfile(file_path):
                    entry.update({
                        "status": "missing",
                        "elapsed_sec": 0.0,
                        "last_error": "Входной файл недоступен.",
                    })
                    save_state_safely()
                    self._log(f"⚠️ Входной файл недоступен: {file_path or '<путь не записан>'}")
                    self._problem(
                        "batch_file_finished",
                        level="warning",
                        message="Файл пропущен: входной путь недоступен.",
                        file_index=idx,
                        files_total=total,
                        input_path=os.path.abspath(file_path) if file_path else "",
                        output_path=entry.get("output_path") or "",
                        success=False,
                        missing=True,
                        elapsed_sec=0.0,
                    )
                    continue

                base = os.path.splitext(os.path.basename(file_path))[0]
                stored_input = dict(entry.get("input") or {})
                current_input = input_file_signature(file_path)
                input_changed = (
                    stored_input.get("size") is not None
                    and (
                        int(stored_input.get("size")) != int(current_input.get("size", -1))
                        or int(stored_input.get("modified_ns", -1)) != int(current_input.get("modified_ns", -2))
                    )
                )
                output_path = str(entry.get("output_path") or "")
                if input_changed:
                    # Старый результат относится к другой версии входа. Не перезаписываем
                    # его и не считаем завершённым — ниже будет выбрано новое уникальное имя.
                    output_path = ""
                if output_path and os.path.exists(output_path):
                    verified_existing = False
                    try:
                        ffmpeg = find_ffmpeg()
                        ffprobe = find_ffprobe(ffmpeg)
                        verified_existing = bool(
                            ffprobe and output_has_video_and_audio(ffprobe, output_path, log=self._log)
                        )
                    except Exception:
                        verified_existing = False
                    if verified_existing:
                        successful += 1
                        entry.update({
                            "status": "succeeded",
                            "elapsed_sec": 0.0,
                            "last_error": "",
                        })
                        save_state_safely()
                        self._problem(
                            "batch_file_recovered_from_verified_output",
                            message="Найден и проверен готовый MP4 прерванной задачи; повторная обработка не нужна.",
                            file_index=idx,
                            files_total=total,
                            input_path=os.path.abspath(file_path),
                            output_path=os.path.abspath(output_path),
                        )
                        self._problem(
                            "batch_file_finished",
                            message="Обработка файла уже была успешно завершена до прерывания.",
                            file_index=idx,
                            files_total=total,
                            input_path=os.path.abspath(file_path),
                            output_path=os.path.abspath(output_path),
                            success=True,
                            resumed_verified_output=True,
                            elapsed_sec=0.0,
                        )
                        continue
                    output_path = ""
                if not output_path:
                    output_path = make_unique_output_path(
                        out_folder,
                        base,
                        target_info.get("suffix", "_TR"),
                        ".mp4",
                    )
                entry.update({
                    "input": current_input,
                    "status": "processing",
                    "output_path": os.path.abspath(output_path),
                    "last_error": "",
                })
                save_state_safely()
                self._problem(
                    "batch_file_started",
                    message="Пакет перешёл к следующему видео.",
                    file_index=idx,
                    files_total=total,
                    input_path=os.path.abspath(file_path),
                    output_path=os.path.abspath(output_path),
                )

                translator = VideoTranslator(
                    self._log,
                    self._threadsafe_progress,
                    self._cancel_event,
                    review_callback=self.review_translations,
                    problem_cb=self._problem,
                    network_notice_cb=self._threadsafe_network_notice,
                )
                self._active_translator = translator
                try:
                    file_ok = translator.process(
                        file_path,
                        output_path,
                        voice,
                        model,
                        keep,
                        volume,
                        target_info=target_info,
                        review_before_tts=review,
                        audio_settings=audio_settings,
                    )
                    if file_ok:
                        successful += 1
                        entry.update({"status": "succeeded", "last_error": ""})
                    else:
                        self._log(f"⚠️ Файл не обработан: {file_path}")
                        entry.update({
                            "status": "cancelled" if self._cancel_event.is_set() else "failed",
                            "last_error": "Обработка отменена." if self._cancel_event.is_set() else "Файл не обработан.",
                        })
                    entry["elapsed_sec"] = round(time.monotonic() - file_started_at, 3)
                    self._problem(
                        "batch_file_finished",
                        level="info" if file_ok else "warning",
                        message="Обработка файла закончена.",
                        file_index=idx,
                        files_total=total,
                        input_path=os.path.abspath(file_path),
                        output_path=os.path.abspath(output_path),
                        success=bool(file_ok),
                        elapsed_sec=entry["elapsed_sec"],
                    )
                except Exception as exc:
                    # Защита на случай, если ошибка вылетит выше process().
                    self._log(f"\n❌ НЕОЖИДАННАЯ ОШИБКА ФАЙЛА: {file_path}")
                    self._log(f"{type(exc).__name__}: {exc}")
                    self._log(traceback.format_exc())
                    entry.update({
                        "status": "failed",
                        "elapsed_sec": round(time.monotonic() - file_started_at, 3),
                        "last_error": compact_exception(exc, max_len=1000),
                    })
                    try:
                        if self.problem_logger:
                            self.problem_logger.write_exception(
                                "unexpected_batch_file_exception",
                                exc,
                                message="Исключение вышло выше защиты обработки одного видео.",
                                file_index=idx,
                                files_total=total,
                                input_path=os.path.abspath(file_path),
                                output_path=os.path.abspath(output_path),
                                elapsed_sec=round(time.monotonic() - file_started_at, 3),
                            )
                    except Exception:
                        pass
                    self._problem(
                        "batch_file_finished",
                        level="error",
                        message="Обработка файла завершилась неожиданным исключением.",
                        file_index=idx,
                        files_total=total,
                        input_path=os.path.abspath(file_path),
                        output_path=os.path.abspath(output_path),
                        success=False,
                        elapsed_sec=entry["elapsed_sec"],
                    )
                finally:
                    if self._active_translator is translator:
                        self._active_translator = None
                    self._safe_after(0, self._close_vpn_notice)
                    save_state_safely()

                self._safe_after(300, self._set_progress, 0, "")

            if self._cancel_event.is_set():
                cancelled = True

            pending = sum(str(entry.get("status") or "") != "succeeded" for entry in entries)
            failed = sum(str(entry.get("status") or "") in {"failed", "missing"} for entry in entries)
            batch_state["status"] = (
                "completed" if successful == total else ("cancelled" if cancelled else "incomplete")
            )
            save_state_safely()
            self._resume_batch_state = None if successful == total else batch_state

            self._log(
                f"\n{'═' * 56}\n"
                f"🏁 ИТОГ: {successful}/{total}\n"
                f"📁 Результаты: {out_folder}\n"
                f"🧾 Лог: {log_path or 'не создан'}\n"
                f"{'═' * 56}"
            )
            self._problem(
                "batch_finished",
                level="warning" if successful < total else "info",
                message="Пакетная обработка завершена.",
                successful=successful,
                total=total,
                failed=failed,
                pending=pending,
                cancelled=cancelled,
                elapsed_sec=round(time.monotonic() - batch_started_at, 3),
                output_dir=os.path.abspath(out_folder),
                normal_log=log_path,
            )
            self._safe_after(0, self._set_busy, False)
            has_errors = successful < total and not cancelled
            status_text = "Отменено" if cancelled else ("Завершено с ошибками" if has_errors else "Завершено")
            dialog_title = "Отменено" if cancelled else ("Есть ошибки" if has_errors else "Готово")
            dialog_message = (
                f"Прервано. Успешно: {successful}/{total}\n🧾 Лог: {log_path}"
                if cancelled else
                f"Обработано: {successful}/{total}\n📁 {out_folder}\n🧾 Лог: {log_path}"
            )
            if successful < total:
                dialog_message += "\n\nНезавершённые файлы сохранены. Их можно продолжить при следующем запуске."
            self._safe_after(0, self._set_progress, 0, status_text)
            self._safe_after(
                0,
                messagebox.showinfo,
                dialog_title,
                dialog_message,
            )
        except Exception as exc:
            self._log("\n❌ КРИТИЧЕСКАЯ ОШИБКА РАБОЧЕГО ПОТОКА")
            self._log(f"{type(exc).__name__}: {exc}")
            self._log(traceback.format_exc())
            batch_state["status"] = "worker_failed"
            save_state_safely()
            self._resume_batch_state = batch_state
            try:
                if self.problem_logger:
                    self.problem_logger.write_exception(
                        "worker_thread_crashed",
                        exc,
                        message="Критическая ошибка пакетного рабочего потока.",
                        elapsed_sec=round(time.monotonic() - batch_started_at, 3),
                        normal_log=log_path,
                    )
            except Exception:
                pass
            self._safe_after(0, self._set_busy, False)
            self._safe_after(0, self._set_progress, 0, "Ошибка")
            self._safe_after(
                0,
                messagebox.showerror,
                "Ошибка",
                f"Произошла ошибка. Подробности сохранены в логе:\n{log_path or 'лог не создан'}",
            )
        finally:
            try:
                if self.file_logger:
                    self.file_logger.close()
            except Exception:
                pass

    def review_translations(self, segments: list, input_path: str, source_lang: str, target_info: dict) -> list | None:
        if self._cancel_event.is_set() or self._closing:
            return None

        done = threading.Event()
        result = {"segments": None, "error": None}

        def show_editor():
            try:
                if self._cancel_event.is_set() or self._closing:
                    done.set()
                    return
                self._open_translation_review_window(segments, input_path, source_lang, target_info, done, result)
                self._problem(
                    "manual_review_window_opened",
                    message="Окно ручной проверки создано и показано пользователю.",
                    input_path=os.path.abspath(input_path),
                    segments=len(segments),
                )
            except Exception as exc:
                result["error"] = exc
                try:
                    if self.problem_logger:
                        self.problem_logger.write_exception(
                            "manual_review_window_failed",
                            exc,
                            message="Не удалось создать окно ручной проверки; ожидание остановлено.",
                            input_path=os.path.abspath(input_path),
                            segments=len(segments),
                        )
                except Exception:
                    pass
                done.set()

        if not self._safe_after(0, show_editor):
            result["error"] = RuntimeError("Не удалось запланировать открытие окна ручной проверки.")
            done.set()
        wait_started_at = time.monotonic()
        next_wait_log_at = wait_started_at + 300
        while not done.wait(0.1):
            if self._closing:
                self._cancel_event.set()
                return None
            if time.monotonic() >= next_wait_log_at:
                elapsed = int(time.monotonic() - wait_started_at)
                self._log(
                    f"   ⏸️ Всё ещё жду ручную проверку ({fmt_time(elapsed)}). "
                    "Нажмите «Применить и продолжить» в окне проверки."
                )
                self._problem(
                    "manual_review_still_waiting",
                    level="warning",
                    message="Обработка продолжает ждать действия пользователя в окне проверки.",
                    input_path=os.path.abspath(input_path),
                    elapsed_sec=elapsed,
                )
                next_wait_log_at = time.monotonic() + 300
        if result["error"] is not None:
            raise RuntimeError(
                f"Не удалось открыть окно ручной проверки: {compact_exception(result['error'])}"
            ) from result["error"]
        return result["segments"]

    def _open_translation_review_window(self, segments: list, input_path: str, source_lang: str,
                                        target_info: dict, done: threading.Event, result: dict):
        segments_copy = [dict(seg) for seg in (segments or [])]
        if not segments_copy:
            done.set()
            return

        target_info = dict(target_info or get_target_language(DEFAULT_TARGET_LANGUAGE))
        target_name = target_info.get("name") or target_info.get("code") or "перевод"
        source_label = source_lang or "auto"
        current = {"idx": 0, "loading": False}

        window = tk.Toplevel(self.root)
        window.title("Проверка перевода перед озвучкой")
        window.geometry("880x650")
        window.minsize(760, 560)
        window.transient(self.root)
        window.grab_set()
        window.columnconfigure(1, weight=1)
        window.rowconfigure(1, weight=1)

        def bring_to_front():
            try:
                window.deiconify()
                window.lift()
                window.attributes("-topmost", True)
                window.focus_force()
                def clear_topmost():
                    try:
                        if window.winfo_exists():
                            window.attributes("-topmost", False)
                    except Exception:
                        pass
                window.after(900, clear_topmost)
            except Exception:
                pass

        window.after_idle(bring_to_front)

        ttk.Label(
            window,
            text=f"{os.path.basename(input_path)}   |   {source_label} → {target_name}",
            font=("Arial", 10, "bold"),
        ).grid(row=0, column=0, columnspan=2, sticky="ew", padx=10, pady=(10, 6))

        left_frame = ttk.Frame(window)
        left_frame.grid(row=1, column=0, sticky="ns", padx=(10, 6), pady=6)
        ttk.Label(left_frame, text="Сегменты").pack(anchor="w")
        segment_scroll = ttk.Scrollbar(left_frame)
        segment_scroll.pack(side="right", fill="y")
        segment_list = tk.Listbox(
            left_frame,
            width=24,
            height=24,
            yscrollcommand=segment_scroll.set,
            exportselection=False,
            font=("Consolas", 9),
        )
        segment_list.pack(side="left", fill="y")
        segment_scroll.config(command=segment_list.yview)

        for idx, seg in enumerate(segments_copy, 1):
            segment_list.insert(
                tk.END,
                f"{idx:03d}  {fmt_time(seg.get('start', 0.0))} → {fmt_time(seg.get('end', 0.0))}",
            )

        edit_frame = ttk.Frame(window)
        edit_frame.grid(row=1, column=1, sticky="nsew", padx=(6, 10), pady=6)
        edit_frame.columnconfigure(0, weight=1)
        edit_frame.rowconfigure(2, weight=1)
        edit_frame.rowconfigure(4, weight=2)

        lbl_segment = ttk.Label(edit_frame, text="")
        lbl_segment.grid(row=0, column=0, sticky="w", pady=(0, 4))

        ttk.Label(edit_frame, text="Исходный текст").grid(row=1, column=0, sticky="w")
        source_text = scrolledtext.ScrolledText(edit_frame, height=7, wrap="word", font=("Arial", 10))
        source_text.grid(row=2, column=0, sticky="nsew", pady=(2, 8))
        source_text.config(state="disabled")

        ttk.Label(edit_frame, text=f"Перевод ({target_name})").grid(row=3, column=0, sticky="w")
        translated_text = scrolledtext.ScrolledText(edit_frame, height=10, wrap="word", font=("Arial", 10))
        translated_text.grid(row=4, column=0, sticky="nsew", pady=(2, 0))

        buttons = ttk.Frame(window)
        buttons.grid(row=2, column=0, columnspan=2, sticky="ew", padx=10, pady=(4, 10))
        buttons.columnconfigure(5, weight=1)

        def set_text(widget, text: str, readonly: bool = False):
            widget.config(state="normal")
            widget.delete("1.0", tk.END)
            widget.insert("1.0", text or "")
            if readonly:
                widget.config(state="disabled")

        def save_current():
            idx = current["idx"]
            if 0 <= idx < len(segments_copy):
                segments_copy[idx]["translated"] = translated_text.get("1.0", tk.END).strip()

        def make_source_review_text() -> str:
            lines = []
            for idx, seg in enumerate(segments_copy, 1):
                lines.append(f"### {idx:03d} | {fmt_time(seg.get('start', 0.0))} -> {fmt_time(seg.get('end', 0.0))}")
                lines.append((seg.get("source") or "").strip())
                lines.append("")
            return "\n".join(lines).strip() + "\n"

        def make_translation_review_text() -> str:
            lines = [
                "# Файл правки перевода.",
                "# Можно менять только текст после строки ПЕРЕВОД:",
                "# Строки вида ### 001 | 00:00 -> 00:05 и ### END лучше не менять.",
                "",
            ]
            for idx, seg in enumerate(segments_copy, 1):
                lines.append(f"### {idx:03d} | {fmt_time(seg.get('start', 0.0))} -> {fmt_time(seg.get('end', 0.0))}")
                lines.append("ИСХОДНЫЙ:")
                lines.append((seg.get("source") or "").strip())
                lines.append("ПЕРЕВОД:")
                lines.append((seg.get("translated") or "").strip())
                lines.append("### END")
                lines.append("")
            return "\n".join(lines).rstrip() + "\n"

        def parse_translation_review_text(text: str) -> dict[int, str]:
            normalized = (text or "").replace("\r\n", "\n").replace("\r", "\n")
            pattern = re.compile(
                r"(?ms)^###\s+(\d+)\s+\|[^\n]*\nИСХОДНЫЙ:\n.*?\nПЕРЕВОД:\n(.*?)^### END\s*$"
            )
            updates = {}
            for match in pattern.finditer(normalized):
                idx = int(match.group(1)) - 1
                if 0 <= idx < len(segments_copy):
                    updates[idx] = match.group(2).strip()
            return updates

        def apply_translation_review_text(text: str, parent_window) -> bool:
            updates = parse_translation_review_text(text)
            if not updates:
                messagebox.showerror(
                    "Не удалось прочитать TXT",
                    "Не нашёл блоки перевода. Проверьте, что строки ### 001 и ### END остались на месте.",
                    parent=parent_window,
                )
                return False

            missing_count = len(segments_copy) - len(updates)
            if missing_count > 0:
                if not messagebox.askyesno(
                    "Загружены не все сегменты",
                    f"В файле найдено {len(updates)} из {len(segments_copy)} сегментов.\n"
                    f"Обновить найденные, остальные оставить как есть?",
                    parent=parent_window,
                ):
                    return False

            for idx, value in updates.items():
                segments_copy[idx]["translated"] = value
            load_segment(current["idx"])
            messagebox.showinfo(
                "Перевод загружен",
                f"Обновлено сегментов: {len(updates)}",
                parent=parent_window,
            )
            return True

        def save_review_text_to_file(text: str, parent_window) -> str:
            default_name = safe_log_filename(Path(input_path).stem or "translation")
            target_code = str(target_info.get("code") or "target").replace("-", "_")
            path = filedialog.asksaveasfilename(
                parent=parent_window,
                title="Сохранить текст перевода",
                initialdir=str(get_translated_texts_dir()),
                initialfile=f"{default_name}_{target_code}_edit.txt",
                defaultextension=".txt",
                filetypes=[("Текстовый файл", "*.txt"), ("Все файлы", "*.*")],
            )
            if not path:
                return ""
            with open(path, "w", encoding="utf-8") as file:
                file.write(text)
            messagebox.showinfo("TXT сохранён", path, parent=parent_window)
            return path

        def export_translation_txt():
            save_current()
            save_review_text_to_file(make_translation_review_text(), window)

        def import_translation_txt(parent_window=window, editor_widget=None):
            path = filedialog.askopenfilename(
                parent=parent_window,
                title="Загрузить исправленный текст перевода",
                initialdir=str(get_translated_texts_dir()),
                filetypes=[("Текстовый файл", "*.txt"), ("Все файлы", "*.*")],
            )
            if not path:
                return False
            with open(path, "r", encoding="utf-8-sig", errors="replace") as file:
                text = file.read()
            if editor_widget is not None:
                set_text(editor_widget, text, readonly=False)
            return apply_translation_review_text(text, parent_window)

        def open_full_text_editor():
            save_current()

            bulk = tk.Toplevel(window)
            bulk.title("Весь текст перевода")
            bulk.geometry("1100x720")
            bulk.minsize(900, 600)
            bulk.transient(window)
            bulk.columnconfigure(0, weight=1)
            bulk.columnconfigure(1, weight=1)
            bulk.rowconfigure(1, weight=1)

            ttk.Label(
                bulk,
                text=f"{os.path.basename(input_path)}   |   {source_label} -> {target_name}",
                font=("Arial", 10, "bold"),
            ).grid(row=0, column=0, columnspan=2, sticky="ew", padx=10, pady=(10, 6))

            source_frame = ttk.LabelFrame(bulk, text="Весь исходный текст", padding=6)
            source_frame.grid(row=1, column=0, sticky="nsew", padx=(10, 5), pady=6)
            source_frame.rowconfigure(0, weight=1)
            source_frame.columnconfigure(0, weight=1)
            source_all = scrolledtext.ScrolledText(source_frame, wrap="word", font=("Arial", 10))
            source_all.grid(row=0, column=0, sticky="nsew")
            set_text(source_all, make_source_review_text(), readonly=True)

            translation_frame = ttk.LabelFrame(bulk, text=f"Весь перевод ({target_name})", padding=6)
            translation_frame.grid(row=1, column=1, sticky="nsew", padx=(5, 10), pady=6)
            translation_frame.rowconfigure(0, weight=1)
            translation_frame.columnconfigure(0, weight=1)
            translation_all = scrolledtext.ScrolledText(translation_frame, wrap="word", font=("Arial", 10))
            translation_all.grid(row=0, column=0, sticky="nsew")
            set_text(translation_all, make_translation_review_text(), readonly=False)

            bulk_buttons = ttk.Frame(bulk)
            bulk_buttons.grid(row=2, column=0, columnspan=2, sticky="ew", padx=10, pady=(4, 10))
            bulk_buttons.columnconfigure(3, weight=1)

            def apply_bulk_text():
                return apply_translation_review_text(translation_all.get("1.0", tk.END), bulk)

            ttk.Button(bulk_buttons, text="Применить в сегменты", command=apply_bulk_text).grid(row=0, column=0, sticky="w", padx=(0, 6))
            ttk.Button(
                bulk_buttons,
                text="Сохранить TXT",
                command=lambda: save_review_text_to_file(translation_all.get("1.0", tk.END), bulk),
            ).grid(row=0, column=1, sticky="w", padx=(0, 6))
            ttk.Button(
                bulk_buttons,
                text="Загрузить TXT",
                command=lambda: import_translation_txt(bulk, translation_all),
            ).grid(row=0, column=2, sticky="w")
            ttk.Button(bulk_buttons, text="Закрыть", command=bulk.destroy).grid(row=0, column=4, sticky="e")

        def load_segment(idx: int):
            idx = max(0, min(len(segments_copy) - 1, int(idx)))
            current["loading"] = True
            current["idx"] = idx
            segment_list.selection_clear(0, tk.END)
            segment_list.selection_set(idx)
            segment_list.see(idx)
            seg = segments_copy[idx]
            lbl_segment.config(
                text=f"Сегмент {idx + 1}/{len(segments_copy)}   "
                     f"{fmt_time(seg.get('start', 0.0))} → {fmt_time(seg.get('end', 0.0))}"
            )
            set_text(source_text, (seg.get("source") or "").strip(), readonly=True)
            set_text(translated_text, (seg.get("translated") or "").strip(), readonly=False)
            translated_text.focus_set()
            current["loading"] = False

        def on_select(_event=None):
            if current["loading"]:
                return
            selection = segment_list.curselection()
            if not selection:
                return
            save_current()
            load_segment(selection[0])

        def move(delta: int):
            save_current()
            load_segment(current["idx"] + delta)

        def finish():
            save_current()
            empty = [str(i + 1) for i, seg in enumerate(segments_copy) if not (seg.get("translated") or "").strip()]
            if empty:
                preview = ", ".join(empty[:12]) + ("..." if len(empty) > 12 else "")
                if not messagebox.askyesno(
                    "Есть пустые сегменты",
                    f"Пустые сегменты не будут озвучены: {preview}\nПродолжить?",
                    parent=window,
                ):
                    return
            result["segments"] = segments_copy
            close_window(cancelled=False)

        def cancel_editor():
            if messagebox.askyesno(
                "Отменить обработку?",
                "Закрыть проверку и отменить обработку текущего видео?",
                parent=window,
            ):
                self._cancel_event.set()
                result["segments"] = None
                close_window(cancelled=True)

        def close_window(cancelled: bool):
            if done.is_set():
                return
            if cancelled:
                result["segments"] = None
            try:
                window.grab_release()
            except Exception:
                pass
            try:
                window.destroy()
            except Exception:
                pass
            done.set()

        def poll_cancel():
            if done.is_set():
                return
            if self._cancel_event.is_set() or self._closing:
                close_window(cancelled=True)
                return
            window.after(300, poll_cancel)

        ttk.Button(buttons, text="← Назад", command=lambda: move(-1)).grid(row=0, column=0, sticky="w", padx=(0, 6))
        ttk.Button(buttons, text="Дальше →", command=lambda: move(1)).grid(row=0, column=1, sticky="w", padx=(0, 6))
        ttk.Button(buttons, text="Весь текст", command=open_full_text_editor).grid(row=0, column=2, sticky="w", padx=(0, 6))
        ttk.Button(buttons, text="Сохранить TXT", command=export_translation_txt).grid(row=0, column=3, sticky="w", padx=(0, 6))
        ttk.Button(buttons, text="Загрузить TXT", command=import_translation_txt).grid(row=0, column=4, sticky="w")
        ttk.Button(buttons, text="Применить и продолжить", command=finish).grid(row=0, column=6, sticky="e", padx=(6, 6))
        ttk.Button(buttons, text="Отмена", command=cancel_editor).grid(row=0, column=7, sticky="e")

        segment_list.bind("<<ListboxSelect>>", on_select)
        window.protocol("WM_DELETE_WINDOW", cancel_editor)
        load_segment(0)
        poll_cancel()

    def cancel(self):
        if self._processing:
            self._cancel_event.set()
            self._log("⏹ Отмена...")
            self.btn_cancel.config(state="disabled")

    # ── состояние UI ─────────────────────────────────────────────────────────

    def _set_busy(self, busy: bool):
        self._processing = busy
        self.btn_start.config(
            state="disabled" if busy else "normal",
            text="⏳ Обработка..." if busy else "▶  НАЧАТЬ ОБРАБОТКУ",
        )
        self.btn_cancel.config(state="normal" if busy else "disabled")
        for widget in (
            self.btn_add,
            self.btn_remove,
            self.btn_clear,
            self.chk_keep,
            self.chk_review,
            self.scale_vol,
            self.scale_voice_volume,
            self.scale_highpass,
            self.scale_lowpass,
            self.scale_loudness,
            self.chk_denoise,
        ):
            try:
                widget.config(state="disabled" if busy else "normal")
            except Exception:
                pass
        for combo in (self.combo_language, self.combo_voice, self.combo_model, self.combo_speed):
            try:
                combo.config(state="disabled" if busy else "readonly")
            except Exception:
                pass
        self._apply_keep_state(save=False)

    def _set_progress(self, value: int, status: str = ""):
        self.var_prog.set(max(0, min(100, int(value or 0))))
        if status:
            self.lbl_status.config(text=status)

    def _update_count(self):
        self.lbl_count.config(text=f"Файлов: {len(self.video_files)}")

    def _apply_keep_state(self, save: bool = False):
        keep_original = bool(self.var_keep.get())
        if self._processing:
            self.scale_vol.state(["disabled"])
            self.lbl_vol.config(state="disabled")
        else:
            self.scale_vol.state(["!disabled"] if keep_original else ["disabled"])
            self.lbl_vol.config(state="normal" if keep_original else "disabled")
        if save:
            self.save_settings()

    def _refresh_voice_list(self, language_label: str | None = None, preferred_voice_code: str | None = None):
        language_label = language_label or self.combo_language.get() or DEFAULT_TARGET_LANGUAGE
        voices = get_voice_options(language_label)
        labels = list(voices.keys())
        self.combo_voice.config(values=labels)
        selected = labels[0] if labels else ""
        if preferred_voice_code:
            for label, code in voices.items():
                if code == preferred_voice_code:
                    selected = label
                    break
        if selected:
            self.combo_voice.set(selected)

    def _on_language_changed(self, _event=None):
        self._refresh_voice_list(self.combo_language.get())
        self.save_settings()

    def _get_audio_settings(self) -> dict:
        return normalize_audio_settings({
            "voice_volume_pct": int(float(self.var_voice_volume.get())),
            "highpass_hz": int(float(self.var_highpass.get())),
            "lowpass_hz": int(float(self.var_lowpass.get())),
            "noise_reduction": bool(self.var_denoise.get()),
            "master_loudness_i": int(float(self.var_loudness.get())),
            "speech_speed_limit": float(self.combo_speed.get() or TOTAL_MAX_SPEECH_SPEED),
        })

    def _on_vol_changed(self, value):
        volume = int(float(value))
        self.lbl_vol.config(text=f"Громкость оригинала: {volume}%")
        self.save_settings()

    def _on_voice_volume_changed(self, value):
        volume = int(float(value))
        self.lbl_voice_vol.config(text=f"Громкость новой озвучки: {volume}%")
        self.save_settings()

    def _on_highpass_changed(self, value):
        hz = int(float(value))
        label = "выкл" if hz <= 0 else f"{hz} Гц"
        self.lbl_highpass.config(text=f"Убрать гул ниже: {label}")
        self.save_settings()

    def _on_lowpass_changed(self, value):
        hz = int(float(value))
        label = "выкл" if hz < 2500 else f"{hz} Гц"
        self.lbl_lowpass.config(text=f"Смягчить верх: {label}")
        self.save_settings()

    def _on_loudness_changed(self, value):
        loudness = int(float(value))
        self.lbl_loudness.config(text=f"Итоговая громкость: {loudness} LUFS")
        self.save_settings()

    def _clear_log(self):
        self.txt_log.config(state="normal")
        self.txt_log.delete("1.0", tk.END)
        self.txt_log.config(state="disabled")

    # ── настройки ────────────────────────────────────────────────────────────

    def save_settings(self, *_):
        if self._suspend_save:
            return
        try:
            language_label = self.combo_language.get() or DEFAULT_TARGET_LANGUAGE
            target_info = get_target_language(language_label)
            voices = get_voice_options(language_label)
            data = {
                "language_default_version": SETTINGS_LANGUAGE_VERSION,
                "language_code": target_info.get("code", "en"),
                "language_label": language_label,
                "voice_code": voices.get(self.combo_voice.get(), ""),
                "model_idx": max(0, self.combo_model.current()),
                "review": bool(self.var_review.get()),
                "keep": bool(self.var_keep.get()),
                "volume": int(self.var_volume.get()),
                "voice_volume": int(self.var_voice_volume.get()),
                "highpass": int(self.var_highpass.get()),
                "lowpass": int(self.var_lowpass.get()),
                "denoise": bool(self.var_denoise.get()),
                "loudness": int(self.var_loudness.get()),
                "speed_limit": float(self.combo_speed.get() or TOTAL_MAX_SPEECH_SPEED),
            }
            with open(self.config_file, "w", encoding="utf-8") as file:
                json.dump(data, file, ensure_ascii=False, indent=2)
        except Exception:
            pass

    def load_settings(self):
        if not self.config_file.exists():
            return
        try:
            with open(self.config_file, encoding="utf-8") as file:
                data = json.load(file)

            if int(data.get("language_default_version", 0)) >= SETTINGS_LANGUAGE_VERSION:
                saved_label = data.get("language_label")
                language_label = saved_label if saved_label in TARGET_LANGUAGES else get_target_language_by_code(data.get("language_code"))
            else:
                language_label = DEFAULT_TARGET_LANGUAGE
            self.combo_language.set(language_label)
            self._refresh_voice_list(language_label, preferred_voice_code=data.get("voice_code"))

            model_idx = max(0, min(int(data.get("model_idx", 1)), len(MODELS_MAP) - 1))
            volume = max(0, min(100, int(data.get("volume", 15))))
            voice_volume = max(50, min(150, int(data.get("voice_volume", 100))))
            highpass = max(0, min(220, int(data.get("highpass", 55))))
            lowpass = max(0, min(16000, int(data.get("lowpass", 0))))
            loudness = max(-24, min(-12, int(data.get("loudness", -16))))
            try:
                requested_speed = float(data.get("speed_limit", TOTAL_MAX_SPEECH_SPEED))
            except (TypeError, ValueError):
                requested_speed = TOTAL_MAX_SPEECH_SPEED
            speed_limit = min(SPEECH_SPEED_LIMITS, key=lambda value: abs(value - requested_speed))

            self.combo_model.current(model_idx)
            self.var_review.set(bool(data.get("review", True)))
            self.var_keep.set(bool(data.get("keep", False)))
            self.var_volume.set(volume)
            self.var_voice_volume.set(voice_volume)
            self.var_highpass.set(highpass)
            self.var_lowpass.set(lowpass)
            self.var_denoise.set(bool(data.get("denoise", False)))
            self.var_loudness.set(loudness)
            self.combo_speed.set(f"{speed_limit:.2f}")
            self.lbl_vol.config(text=f"Громкость оригинала: {volume}%")
            self.lbl_voice_vol.config(text=f"Громкость новой озвучки: {voice_volume}%")
            self.lbl_highpass.config(text=f"Убрать гул ниже: {'выкл' if highpass <= 0 else str(highpass) + ' Гц'}")
            self.lbl_lowpass.config(text=f"Смягчить верх: {'выкл' if lowpass < 2500 else str(lowpass) + ' Гц'}")
            self.lbl_loudness.config(text=f"Итоговая громкость: {loudness} LUFS")
        except Exception:
            pass

    def _on_close(self):
        if self._processing:
            if not messagebox.askyesno("Выход", "Идёт обработка. Прервать и выйти?"):
                return
            self._cancel_event.set()
            if self._resume_batch_state:
                self._resume_batch_state["status"] = "cancellation_requested"
                try:
                    save_batch_recovery_state(self._resume_batch_state)
                except (OSError, TypeError, ValueError) as exc:
                    self._problem(
                        "batch_recovery_save_failed",
                        level="warning",
                        message="Не удалось сохранить отметку об отмене пакетной обработки.",
                        exception={
                            "type": type(exc).__name__,
                            "category": classify_exception(exc),
                            "message": compact_exception(exc),
                        },
                    )
        self._problem(
            "app_close_requested",
            level="warning" if self._processing else "info",
            message="Пользователь закрыл программу.",
            processing=bool(self._processing),
        )
        self.save_settings()
        self._closing = True
        self._close_vpn_notice()
        try:
            if self.file_logger:
                self.file_logger.close()
        except Exception:
            pass
        try:
            if self.problem_logger:
                self.problem_logger.close()
        except Exception:
            pass
        try:
            self.root.destroy()
        except Exception:
            pass


if __name__ == "__main__":
    startup_logger = None
    startup_problem_logger = None
    app = None
    try:
        root = tk.Tk()
        app = App(root)
        root.mainloop()
    except Exception as exc:
        try:
            startup_logger = FileLogger(prefix="video_translator_startup_error")
            startup_logger.write_exception("Критическая ошибка запуска программы", exc)
            startup_problem_logger = getattr(app, "problem_logger", None) if app is not None else None
            if startup_problem_logger is None:
                startup_problem_logger = ProblemLogger()
            startup_problem_logger.write_exception(
                "application_crashed",
                exc,
                message="Необработанная ошибка главного GUI-потока.",
            )
            messagebox.showerror(
                "Ошибка запуска",
                "Программа упала при запуске. Логи сохранены:\n"
                f"{startup_logger.path}\n"
                f"{startup_problem_logger.path}",
            )
        except Exception:
            pass
        raise
    finally:
        try:
            if startup_logger:
                startup_logger.close()
        except Exception:
            pass
        try:
            if startup_problem_logger:
                startup_problem_logger.close()
        except Exception:
            pass
