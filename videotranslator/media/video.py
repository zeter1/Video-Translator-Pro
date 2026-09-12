"""Compatibility facade for media probing and final MP4 assembly.

Codex routing:
- probe.py: duration/audio extraction/output validation
- final_video.py: final MP4/NVENC/encoder fallback
"""
from __future__ import annotations

from videotranslator.media.probe import (
    get_media_duration,
    extract_audio_for_whisper,
    output_has_video_and_audio,
)
from videotranslator.media.final_video import assemble_final_video

__all__ = [
    "get_media_duration",
    "extract_audio_for_whisper",
    "output_has_video_and_audio",
    "assemble_final_video",
]
