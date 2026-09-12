"""Small composition facade for timeline planning and audio mixing.

Codex: open timeline_build.py for Pause Sync planning; timeline_mix.py for WAV mixing.
"""
from __future__ import annotations

from videotranslator.pipeline.timeline_build import TimelineBuildMixin
from videotranslator.pipeline.timeline_mix import TimelineMixMixin


class TimelineMixin(TimelineBuildMixin, TimelineMixMixin):
    """Public mixin facade preserving the historical class contract."""

    pass
