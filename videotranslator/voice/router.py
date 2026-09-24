"""Voice engine selection for Hybrid AI v9."""
class VoiceRouter:
    def __init__(self, engines=None):
        self.engines = engines or {}
    def synthesize(self, text, preferred='piper'):
        engine = self.engines.get(preferred) or self.engines.get('edge')
        if not engine:
            raise RuntimeError('Нет доступного voice engine')
        return engine.synthesize(text)
