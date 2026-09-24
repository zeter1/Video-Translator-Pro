"""Compatibility bridge for legacy monkeypatch-based tests and extensions."""
from __future__ import annotations
import sys
from typing import Any, Callable

def resolve_legacy_override(name: str, default: Any) -> Any:
    # Исторический single-file facade называется ``video_translator``.
    # Тесты и внешние интеграции также импортируют пакетный public_api напрямую;
    # monkeypatch должен одинаково работать через оба официальных входа.
    for module_name in ("video_translator", "videotranslator.public_api"):
        facade = sys.modules.get(module_name)
        if facade is None:
            continue
        candidate = getattr(facade, name, default)
        if candidate is not default:
            return candidate
    return default

def call_legacy_override(name: str, default: Callable, *args, **kwargs):
    return resolve_legacy_override(name, default)(*args, **kwargs)
