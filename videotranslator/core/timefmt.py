"""Owner module for: fmt_time."""

from __future__ import annotations

def fmt_time(sec: float) -> str:
    sec = max(0, float(sec or 0))
    return f"{int(sec) // 60:02d}:{int(sec) % 60:02d}"
