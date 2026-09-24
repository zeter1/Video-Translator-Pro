"""TTS provider health tracking."""
from __future__ import annotations
import time

class TTSHealth:
    def __init__(self):
        self.failures={}

    def fail(self, engine):
        self.failures[engine]=self.failures.get(engine,0)+1

    def available(self, engine, limit=3):
        return self.failures.get(engine,0)<limit
