# Hybrid AI v6

Added:
- local-first translation flow;
- Google repair only for low quality segments;
- model catalog with sizes;
- RAM based recommendations;
- translation decision history;
- HTML statistics report.

Flow:

Whisper -> Local engine -> Quality check -> Google repair -> TTS router

Large models are never downloaded silently.
