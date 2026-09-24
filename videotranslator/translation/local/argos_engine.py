"""Argos Translate adapter. The engine and language packages are optional."""
from __future__ import annotations


class ArgosEngine:
    name = "argos"

    def available(self, source_lang: str | None = None, target_lang: str | None = None) -> bool:
        try:
            import argostranslate.translate
        except ImportError:
            return False
        if not source_lang or not target_lang:
            return True
        source = str(source_lang).lower().split("-")[0]
        target = str(target_lang).lower().split("-")[0]
        if source == target:
            return True
        try:
            languages = argostranslate.translate.get_installed_languages()
            src = next((lang for lang in languages if lang.code == source), None)
            dst = next((lang for lang in languages if lang.code == target), None)
            if not src or not dst:
                return False
            src.get_translation(dst)
            return True
        except Exception:
            return False

    def translate(self, text: str, source_lang: str, target_lang: str) -> str:
        import argostranslate.translate
        source = str(source_lang or "").lower().split("-")[0]
        target = str(target_lang or "").lower().split("-")[0]
        if source == target:
            return str(text or "")
        if not self.available(source, target):
            raise RuntimeError(f"Argos package {source}→{target} не установлен")
        return argostranslate.translate.translate(str(text or ""), source, target).strip()
