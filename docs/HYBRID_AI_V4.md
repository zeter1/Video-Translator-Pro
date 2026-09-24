# Hybrid AI Translation v4

Pipeline:

Whisper
-> local translation
-> quality checker
-> Google repair only for failed segments
-> TTS router

Goals:
- reduce online requests
- survive rate limits
- keep offline fallback
- expose model sizes before install

Components:
- Argos
- MarianMT
- NLLB
- Piper voices
