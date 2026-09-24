"""User glossary for technical translation terms and ASR vocabulary hints."""
from __future__ import annotations

import json
import re
from pathlib import Path

from videotranslator.core.io import atomic_write_text, backup_corrupt_file


class Glossary:
    """Small persistent source→target glossary.

    The glossary is intentionally conservative in the automatic pipeline:
    * source terms can seed Whisper's ``initial_prompt`` to improve proper nouns;
    * after MT, only source terms that were actually present in the source segment
      and survived untranslated are replaced.  We do not blindly rewrite normal
      translated words or override a user's manual review.
    """

    def __init__(self, file=None):
        self.file = Path(file or "translation_glossary.json")
        self.items = self._load()

    def _load(self) -> dict[str, str]:
        try:
            data = json.loads(self.file.read_text(encoding="utf8"))
            if not isinstance(data, dict):
                raise ValueError("Корень glossary JSON должен быть объектом")
            cleaned = {}
            for source, target in data.items():
                source_text = " ".join(str(source).split())
                target_text = " ".join(str(target).split())
                if source_text and target_text:
                    cleaned[source_text] = target_text
            return cleaned
        except FileNotFoundError:
            return {}
        except (OSError, UnicodeError, ValueError, TypeError):
            try:
                backup_corrupt_file(self.file, label="corrupt")
            except OSError:
                pass
            return {}

    def save(self):
        atomic_write_text(self.file, json.dumps(self.items, ensure_ascii=False, indent=2))

    def add(self, source, target):
        source_text = " ".join(str(source).split())
        target_text = " ".join(str(target).split())
        if not source_text or not target_text:
            raise ValueError("Термин и перевод в glossary не должны быть пустыми")
        self.items[source_text] = target_text
        self.save()

    def build_whisper_prompt(self, *, max_chars: int = 900, max_terms: int = 64) -> str:
        """Return a bounded vocabulary hint for Whisper without leaking target text.

        OpenAI Whisper documents ``initial_prompt`` as a way to provide custom
        vocabulary/proper nouns.  Source terms are sorted by length so specific
        multi-word names win the limited prompt budget.
        """
        budget = max(0, int(max_chars or 0))
        limit = max(0, int(max_terms or 0))
        if not budget or not limit:
            return ""

        selected = []
        used = 0
        seen = set()
        for term in sorted(self.items, key=lambda value: (-len(value), value.casefold())):
            clean = " ".join(str(term).replace("\x00", " ").split())
            folded = clean.casefold()
            if not clean or len(clean) > 120 or folded in seen:
                continue
            extra = len(clean) + (2 if selected else 0)
            if used + extra > budget:
                continue
            selected.append(clean)
            seen.add(folded)
            used += extra
            if len(selected) >= limit:
                break
        return ", ".join(selected)

    @staticmethod
    def _literal_term_pattern(term: str) -> re.Pattern:
        escaped = re.escape(term)
        prefix = r"(?<!\w)" if term and term[0].isalnum() else ""
        suffix = r"(?!\w)" if term and term[-1].isalnum() else ""
        return re.compile(prefix + escaped + suffix, flags=re.IGNORECASE)

    def apply(self, text, *, source_text: str | None = None, return_count: bool = False):
        """Replace retained glossary source terms, optionally gated by source text.

        When ``source_text`` is supplied, a glossary entry is eligible only when the
        source segment contained that term.  This keeps post-MT cleanup targeted and
        avoids replacing an unrelated identical token introduced by the translator.
        """
        result = str(text or "")
        source = str(source_text or "") if source_text is not None else None
        replacements = 0
        for src, dst in sorted(self.items.items(), key=lambda item: -len(item[0])):
            pattern = self._literal_term_pattern(src)
            if source is not None and pattern.search(source) is None:
                continue
            result, count = pattern.subn(lambda _match, value=dst: value, result)
            replacements += count
        if return_count:
            return result, replacements
        return result
