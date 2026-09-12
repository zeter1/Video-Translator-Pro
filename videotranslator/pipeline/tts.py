"""Small composition facade for TTS network generation and prepared-audio work.

Codex: open tts_generate.py for Edge/gTTS behavior; tts_prepare.py for local audio preparation.
"""
from __future__ import annotations

from videotranslator.pipeline.tts_generate import TTSGenerateMixin
from videotranslator.pipeline.tts_prepare import TTSPrepareMixin


class TTSMixin(TTSGenerateMixin, TTSPrepareMixin):
    """Public mixin facade preserving the historical class contract."""

    pass
