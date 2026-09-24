"""Validation of translation-provider text before it can enter checkpoints or TTS."""

from __future__ import annotations

import hashlib
import re


_ERROR_STATUS_RE = re.compile(
    r"^\s*(?:error\s*)?(?:http(?:/\d(?:\.\d)?)?\s*)?(?:code\s*)?(429|5\d\d)\b",
    re.IGNORECASE,
)
_HTML_RE = re.compile(r"<(?:!doctype\s+html|html|head|body|title|script|style)\b", re.IGNORECASE)
_SERVER_ERROR_RE = re.compile(
    r"\b(?:server\s+error|service\s+unavailable|bad\s+gateway|gateway\s+time-?out|too\s+many\s+requests)\b",
    re.IGNORECASE,
)
_BLOCK_PAGE_RE = re.compile(
    r"\b(?:our\s+systems\s+have\s+detected\s+unusual\s+traffic|automated\s+queries|captcha|"
    r"access\s+denied|temporarily\s+blocked)\b",
    re.IGNORECASE,
)
_GOOGLE_ERROR_SENTENCE_RE = re.compile(
    r"there\s+was\s+an\s+error\.?\s*please\s+try\s+again\s+later\.?",
    re.IGNORECASE,
)


class InvalidTranslationResponseError(RuntimeError):
    """Provider returned text, but the text is an error/block page rather than a translation."""

    def __init__(self, inspection: dict):
        self.inspection = dict(inspection or {})
        reason = self.inspection.get("reason") or "invalid_translation_response"
        preview = self.inspection.get("preview") or ""
        super().__init__(f"translation provider response rejected: {reason}; preview={preview!r}")


def _compact_preview(text: str, limit: int = 320) -> str:
    value = re.sub(r"\s+", " ", str(text or "")).strip()
    if len(value) > limit:
        value = value[: limit - 3].rstrip() + "..."
    return value


def inspect_translation_response(text: str, source_text: str = "") -> dict:
    """Return a stable machine-readable verdict for provider output.

    The guard is deliberately conservative: textual HTTP errors are rejected only when the
    source itself is not discussing the same error phrase. HTML/block pages are always rejected.
    """
    value = str(text or "").strip()
    source = str(source_text or "").strip()
    preview = _compact_preview(value)
    result = {
        "valid": True,
        "reason": "ok",
        "signals": [],
        "response_length": len(value),
        "response_sha256": hashlib.sha256(value.encode("utf-8", errors="replace")).hexdigest(),
        "preview": preview,
    }
    if not value:
        result.update(valid=False, reason="empty_response", signals=["empty"])
        return result

    lower_source = source.lower()
    source_mentions_error_template = bool(
        "error 500" in lower_source
        or "server error" in lower_source
        or "there was an error" in lower_source
        or "service unavailable" in lower_source
        or "bad gateway" in lower_source
        or "too many requests" in lower_source
    )

    signals = []
    has_html = bool(_HTML_RE.search(value))
    has_server_error = bool(_SERVER_ERROR_RE.search(value))
    has_google_error_sentence = bool(_GOOGLE_ERROR_SENTENCE_RE.search(value))
    has_block_page = bool(_BLOCK_PAGE_RE.search(value))
    status_match = _ERROR_STATUS_RE.search(value)

    if has_html:
        signals.append("html_markup")
    if has_server_error:
        signals.append("server_error_phrase")
    if has_google_error_sentence:
        signals.append("google_error_sentence")
    if has_block_page:
        signals.append("provider_block_page")
    if status_match:
        signals.append(f"http_status_{status_match.group(1)}")

    # HTML returned by a text translation endpoint is never acceptable translation payload.
    if has_html and (has_server_error or has_google_error_sentence or has_block_page or status_match):
        result.update(valid=False, reason="provider_html_error_page", signals=signals)
        return result

    # Known Google error body observed in the failing user run. Require multiple signals so a
    # genuine sentence about HTTP errors is not rejected when the source itself discusses it.
    if not source_mentions_error_template and has_google_error_sentence and (
        has_server_error or status_match
    ):
        reason = "provider_error_page_500" if status_match and status_match.group(1) == "500" else "provider_error_text"
        result.update(valid=False, reason=reason, signals=signals)
        return result

    if not source_mentions_error_template and has_block_page:
        result.update(valid=False, reason="provider_block_or_captcha_page", signals=signals)
        return result

    # Short responses beginning with HTTP 429/5xx plus an error phrase are provider failures.
    if not source_mentions_error_template and len(value) <= 5000 and status_match and has_server_error:
        result.update(valid=False, reason=f"provider_http_{status_match.group(1)}", signals=signals)
        return result

    result["signals"] = signals
    return result


def ensure_valid_translation_response(text: str, source_text: str = "") -> str:
    inspection = inspect_translation_response(text, source_text=source_text)
    if not inspection.get("valid"):
        raise InvalidTranslationResponseError(inspection)
    return str(text or "").strip()
