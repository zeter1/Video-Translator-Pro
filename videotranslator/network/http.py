"""Owner module for: install_requests_default_timeout, edge_tts_timeout_for_text, progressive_retry_delay."""

from __future__ import annotations

from videotranslator.config import EDGE_TTS_MAX_TIMEOUT_SEC, EDGE_TTS_MIN_TIMEOUT_SEC, NETWORK_CONNECT_TIMEOUT_SEC, NETWORK_READ_TIMEOUT_SEC


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
