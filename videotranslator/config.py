"""Central configuration and target-language metadata."""

from __future__ import annotations

import math

from videotranslator.config_languages import (
    MODELS_MAP, TARGET_LANGUAGES, DEFAULT_TARGET_LANGUAGE,
    get_target_language, get_target_language_by_code,
    get_voice_options, get_language_labels,
)


SETTINGS_LANGUAGE_VERSION = 2


DEFAULT_AUDIO_SETTINGS = {
    "voice_volume_pct": 100,
    "highpass_hz": 55,
    "lowpass_hz": 0,
    "noise_reduction": False,
    "master_loudness_i": -16,
    "speech_speed_limit": 1.15,
    # Hybrid AI v9: local translation does the bulk of work; Google only repairs
    # suspicious segments.  Argos language packages can be installed after
    # Whisper knows the source language.
    "hybrid_local_first": True,
    "auto_install_argos_pairs": True,
    "local_piper_fallback": True,
    "preferred_piper_voice": "piper-dmitri-ru",
}


SPEECH_SPEED_LIMITS = (1.15, 1.20, 1.25)


BATCH_SIZE = 20


SAMPLE_RATE = 44100


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


FINAL_ENCODE_MIN_TIMEOUT = 3600


FINAL_ENCODE_MAX_TIMEOUT = 8 * 3600


HEAVY_PAUSE_SYNC_COUNT = 200


NETWORK_CONNECT_TIMEOUT_SEC = 8


NETWORK_READ_TIMEOUT_SEC = 35


EDGE_TTS_MIN_TIMEOUT_SEC = 45


EDGE_TTS_MAX_TIMEOUT_SEC = 180


PROBLEM_LOG_RETENTION_DAYS = 120


PROBLEM_LOG_MAX_FILES = 80


NORMAL_LOG_RETENTION_DAYS = 120


NORMAL_LOG_MAX_FILES = 80


PROBLEM_SESSION_STALE_SEC = 15 * 60


PROBLEM_LOG_SCHEMA_VERSION = 5


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
    "translation_provider_response_rejected",
    "translation_provider_endpoint_switched",
    "translation_checkpoint_entry_rejected",
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


# Не более ~3 запросов/с к неофициальному Google Translate endpoint.
# Пакетный режим переводит до 8 сегментов одним запросом, поэтому эта
# небольшая пауза почти не влияет на скорость, но сильно снижает шанс 429.
TRANSLATION_MIN_REQUEST_INTERVAL_SEC = 0.35


# После подтверждённого rate-limit делаем один осмысленный cooldown и
# проверочный пакет вместо сотен поштучных запросов в заблокированный сервис.
TRANSLATION_RATE_LIMIT_COOLDOWN_SEC = 60


TRANSLATION_PROVIDER_RETRY_COOLDOWN_SEC = 12


TRANSLATION_PROVIDER_FAILURE_STREAK_LIMIT = 2


NETWORK_STORM_WINDOW_SEC = 90


NETWORK_STORM_THRESHOLD = 6


NETWORK_STORM_RECOVERY_SUCCESSES = 3


# Legacy public setting retained for compatibility. Interactive voice/VPN waiting
# is disabled: the runtime immediately falls back to another local/network route.
EDGE_VOICE_PRESERVE_SEC = 0


EDGE_VOICE_PROBE_INTERVAL_SEC = 15


TTS_PREPARE_WORKERS = 3
# После сетевого gTTS-fallback программа один раз проверяет Edge TTS и, если
# маршрут восстановился, заменяет ограниченное число резервных фраз выбранным голосом.
TTS_FINAL_VOICE_REPAIR_MAX_SEGMENTS = 200


TTS_CACHE_SCHEMA_VERSION = 1


TTS_PREPARED_CACHE_SCHEMA_VERSION = 3


TTS_CACHE_RECOVERY_RETENTION_DAYS = 7


TTS_CACHE_PREPARED_RETENTION_HOURS = 24


TTS_CACHE_ORPHAN_RETENTION_HOURS = 24


TTS_CACHE_MAX_BYTES = 2 * 1024 ** 3


TTS_CACHE_TARGET_BYTES = 1536 * 1024 ** 2


TTS_CACHE_SIZE_CHECK_EVERY = 25


WHISPER_WORD_TIMESTAMPS = True


# Conservative silence window used by OpenAI Whisper's built-in hallucination
# guard. It only takes effect together with word-level timestamps.
WHISPER_HALLUCINATION_SILENCE_THRESHOLD_SEC = 2.0


APP_DIAGNOSTIC_VERSION = "2026.09.20.16"


BATCH_RECOVERY_SCHEMA_VERSION = 1
TASK_QUEUE_SCHEMA_VERSION = 1


def normalize_audio_settings(settings: dict | None = None) -> dict:
    data = dict(DEFAULT_AUDIO_SETTINGS)
    if settings:
        data.update(settings)

    def bounded_int(key: str, default: int, minimum: int, maximum: int) -> int:
        try:
            value = int(data.get(key, default))
        except (TypeError, ValueError, OverflowError):
            value = default
        return max(minimum, min(maximum, value))

    data["voice_volume_pct"] = bounded_int("voice_volume_pct", 100, 50, 150)
    data["highpass_hz"] = bounded_int("highpass_hz", 55, 0, 220)
    data["lowpass_hz"] = bounded_int("lowpass_hz", 0, 0, 16000)
    if data["lowpass_hz"] and data["lowpass_hz"] < 2500:
        data["lowpass_hz"] = 0
    data["noise_reduction"] = bool(data.get("noise_reduction", False))
    data["master_loudness_i"] = bounded_int("master_loudness_i", -16, -24, -12)
    try:
        requested_speed = float(data.get("speech_speed_limit", 1.15))
        if not math.isfinite(requested_speed):
            raise ValueError("non-finite speech speed")
    except (TypeError, ValueError, OverflowError):
        requested_speed = 1.15
    data["speech_speed_limit"] = min(SPEECH_SPEED_LIMITS, key=lambda value: abs(value - requested_speed))
    return data
