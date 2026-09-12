"""Whisper model loading and process-wide cache."""

from __future__ import annotations

import threading


_whisper_cache = {}


_whisper_lock = threading.Lock()


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
