"""TTS fallback chain: online neural -> offline -> system."""
from __future__ import annotations

class TTSRouter:
    def __init__(self, edge=None, piper=None, system=None):
        self.edge=edge; self.piper=piper; self.system=system

    def synthesize(self, text, voice):
        errors=[]
        for name, engine in (("edge",self.edge),("piper",self.piper),("system",self.system)):
            if not engine:
                continue
            try:
                return engine.synthesize(text, voice), name
            except Exception as exc:
                errors.append(f"{name}:{type(exc).__name__}")
        raise RuntimeError("All TTS engines failed: "+", ".join(errors))
