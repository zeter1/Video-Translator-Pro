"""Heuristics that decide whether a local translation needs selective Google repair."""
from __future__ import annotations

from collections import Counter
from difflib import SequenceMatcher
import re

TECH = {
    "api", "http", "https", "docker", "python", "github", "cuda", "sql", "json",
    "ffmpeg", "facebook", "google", "windows", "linux", "openai", "chatgpt", "gpu",
    "cpu", "url", "vpn", "wifi", "wi-fi", "youtube", "html", "css", "javascript",
}

_LATIN_CLASS = r"A-Za-zÀ-ÖØ-öø-ÿĀ-ſ"
_WORD_RE = re.compile(rf"[{_LATIN_CLASS}А-Яа-яЁё0-9][{_LATIN_CLASS}А-Яа-яЁё0-9_+.#/-]*")
_LATIN_RE = re.compile(rf"[{_LATIN_CLASS}]")
_CYR_RE = re.compile(r"[А-Яа-яЁё]")
_LATIN_WORD_RE = re.compile(rf"[{_LATIN_CLASS}][{_LATIN_CLASS}'-]{{2,}}")
_NUMERIC_TOKEN_RE = re.compile(r"\d+(?:(?:[.,:/-]\d+)|(?:[ \u00A0]\d{3}))*")
_PROTECTED_TOKEN_RE = re.compile(
    r"(?:https?://[^\s<>]+|www\.[^\s<>]+|[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,})",
    re.IGNORECASE,
)


def _numeric_tokens(text: str) -> Counter:
    values = []
    for match in _NUMERIC_TOKEN_RE.finditer(str(text or "")):
        digits = "".join(ch for ch in match.group(0) if ch.isdigit())
        if digits:
            values.append(digits)
    return Counter(values)


def _protected_tokens(text: str) -> Counter:
    values = []
    for match in _PROTECTED_TOKEN_RE.finditer(str(text or "")):
        token = match.group(0).rstrip(".,;:!?)]}").lower()
        if token:
            values.append(token)
    return Counter(values)


def _counter_missing(source: Counter, translated: Counter) -> bool:
    return any(translated.get(token, 0) < count for token, count in source.items())

_SOURCE_SCRIPT_PATTERNS = {
    # Scripts visually distinct from Russian Cyrillic.  A few source-script
    # characters surviving a mostly Russian result are a strong signal that
    # selective Google Repair is useful.
    "ar": re.compile(r"[\u0600-\u06FF]"),
    "fa": re.compile(r"[\u0600-\u06FF]"),
    "he": re.compile(r"[\u0590-\u05FF]"),
    "el": re.compile(r"[\u0370-\u03FF]"),
    "zh": re.compile(r"[\u3400-\u4DBF\u4E00-\u9FFF]"),
    "ja": re.compile(r"[\u3040-\u30FF\u3400-\u4DBF\u4E00-\u9FFF]"),
    "ko": re.compile(r"[\uAC00-\uD7AF\u1100-\u11FF]"),
}
_LATIN_SOURCE_LANGS = frozenset({
    "en", "de", "es", "fr", "it", "pt", "nl", "sv", "no", "da", "fi",
    "pl", "cs", "sk", "sl", "hr", "ro", "hu", "tr", "id", "ms", "vi",
})


def _norm(text: str) -> str:
    return " ".join(re.findall(r"\w+", str(text or "").lower(), flags=re.UNICODE))


def inspect(source: str, translated: str, source_lang: str = "", target_lang: str = "ru") -> dict:
    src, dst = str(source or ""), str(translated or "")
    source_code = str(source_lang or "").lower().split("-")[0]
    target_code = str(target_lang or "").lower().split("-")[0]
    reasons: list[str] = []

    if not dst.strip():
        reasons.append("empty")
        return {"ok": False, "reasons": reasons, "source": src, "translated": dst, "repair": True}

    if len(dst.strip()) < max(3, int(len(src.strip()) * 0.16)):
        reasons.append("too_short")

    src_norm, dst_norm = _norm(src), _norm(dst)
    similarity = SequenceMatcher(None, src_norm, dst_norm).ratio() if src_norm and dst_norm else 0.0
    if source_code and source_code != target_code and similarity >= 0.88 and len(src_norm) >= 8:
        reasons.append("mostly_unchanged")

    # Local MT occasionally damages version numbers, dates, percentages, URLs or
    # e-mail addresses. These tokens are factual payload, not prose. Trigger the
    # existing selective online repair instead of silently changing them. Numeric
    # punctuation is normalized (3.14 == 3,14), but digit content/count is kept.
    source_numbers = _numeric_tokens(src)
    if source_numbers and _counter_missing(source_numbers, _numeric_tokens(dst)):
        reasons.append("numeric_token_changed")
    source_protected = _protected_tokens(src)
    if source_protected and _counter_missing(source_protected, _protected_tokens(dst)):
        reasons.append("protected_token_changed")

    # For Latin-script source languages → Russian, allow product/technical names
    # but repair sentences where ordinary source words remain untranslated.
    # The source-word intersection keeps this bounded and avoids treating every
    # intentional Latin product name as a translation failure.
    if source_code in _LATIN_SOURCE_LANGS and target_code == "ru":
        # URLs/e-mails are intentionally preserved verbatim and must not be
        # mistaken for untranslated Latin prose by the leftover-word detector.
        src_for_words = _PROTECTED_TOKEN_RE.sub(" ", src)
        dst_for_words = _PROTECTED_TOKEN_RE.sub(" ", dst)
        words = _WORD_RE.findall(dst_for_words)
        latin_words = []
        for word in words:
            low = word.lower().strip("._-/")
            if not _LATIN_RE.search(word):
                continue
            if low in TECH or any(ch.isdigit() for ch in word):
                continue
            # Short abbreviations and CamelCase product names are often intentional.
            if len(word) <= 2 or (word[:1].isupper() and any(c.isupper() for c in word[1:])):
                continue
            latin_words.append(word)
        latin_letters = len(_LATIN_RE.findall(dst))
        cyr_letters = len(_CYR_RE.findall(dst))
        script_ratio = latin_letters / max(1, latin_letters + cyr_letters)
        if len(latin_words) >= 3 and script_ratio >= 0.28:
            reasons.append("source_language_left")

        # Catch smaller leftovers too. A local engine can produce a mostly good
        # Russian sentence but leave one long ordinary source word or a short
        # untranslated phrase. Those are exactly the cases selective Google
        # Repair should fix, while known technical terms remain allow-listed.
        source_words = {word.lower() for word in _LATIN_WORD_RE.findall(src_for_words)}
        shared_ordinary = []
        for word in _LATIN_WORD_RE.findall(dst_for_words):
            low = word.lower()
            if low not in source_words or low in TECH:
                continue
            if len(word) <= 3:
                continue
            # Capitalized words are often names/brands. Only flag one on its
            # own when it is long and lower-case in the translated output.
            shared_ordinary.append(word)
        long_lower = [word for word in shared_ordinary if len(word) >= 9 and word.islower()]
        if "source_language_left" not in reasons:
            if len(shared_ordinary) >= 2 and cyr_letters >= 8:
                reasons.append("source_words_left")
            elif long_lower and cyr_letters >= 8:
                reasons.append("source_word_left")

    if target_code == "ru" and source_code in _SOURCE_SCRIPT_PATTERNS:
        pattern = _SOURCE_SCRIPT_PATTERNS[source_code]
        source_script_chars = len(pattern.findall(dst))
        cyr_letters = len(_CYR_RE.findall(dst))
        # Require a small cluster plus actual Russian output: isolated symbols
        # and names should not send otherwise good translations to the network.
        if source_script_chars >= 2 and cyr_letters >= 6:
            reasons.append("source_script_left")

    return {
        "ok": not reasons,
        "reasons": reasons,
        "source": src,
        "translated": dst,
        "similarity": round(similarity, 4),
        "repair": bool(reasons),
    }
