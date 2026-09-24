"""Translation context helpers.

Keeps previous segments available for future context-aware engines.
"""
from __future__ import annotations
from collections import deque

class TranslationContext:
    def __init__(self, size=3):
        self.items=deque(maxlen=size)

    def add(self, text):
        self.items.append(str(text))

    def get(self):
        return list(self.items)
