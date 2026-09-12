"""Compatibility bridge for legacy monkeypatch-based tests and extensions."""
from __future__ import annotations
import sys
from typing import Any, Callable

def resolve_legacy_override(name: str, default: Any) -> Any:
    facade = sys.modules.get("video_translator")
    if facade is None:
        return default
    candidate = getattr(facade, name, default)
    return candidate if candidate is not default else default

def call_legacy_override(name: str, default: Callable, *args, **kwargs):
    return resolve_legacy_override(name, default)(*args, **kwargs)
