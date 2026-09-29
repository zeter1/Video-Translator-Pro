"""Whisper model loading, video-oriented transcription options and quality telemetry."""

from __future__ import annotations

import os
import re
import threading

from videotranslator.core.cancel import CancelledError

from videotranslator.config import (
    WHISPER_HALLUCINATION_SILENCE_THRESHOLD_SEC,
    WHISPER_WORD_TIMESTAMPS,
)


_whisper_cache = {}
_whisper_lock = threading.Lock()


def is_cuda_available() -> bool:
    try:
        import torch
        return bool(torch.cuda.is_available())
    except Exception:
        return False


def get_whisper_model(size: str):
    """Load and cache a Whisper model; CUDA is selected automatically when available."""
    device = "cuda" if is_cuda_available() else "cpu"
    key = (size, device)
    with _whisper_lock:
        if key not in _whisper_cache:
            import whisper
            _whisper_cache[key] = whisper.load_model(size, device=device)
        return _whisper_cache[key]


WHISPER_SAMPLE_RATE = 16_000
WHISPER_CANCEL_CHUNK_SECONDS = 30.0


def _load_whisper_audio(audio_path: str):
    import whisper
    return whisper.load_audio(audio_path)


def looks_like_russian_text(text: str, *, min_cyrillic_letters: int = 6, min_ratio: float = 0.45) -> bool:
    """Guard destructive Russian passthrough against an isolated language mis-detection."""
    letters = [char.lower() for char in str(text or "") if char.isalpha()]
    if not letters:
        return False
    cyrillic = sum(("а" <= char <= "я") or char == "ё" for char in letters)
    return cyrillic >= int(min_cyrillic_letters) and (cyrillic / len(letters)) >= float(min_ratio)


def transcribe_video(
    model,
    audio_path: str,
    *,
    use_cuda: bool,
    initial_prompt: str | None = None,
    cancel_event=None,
    chunk_seconds: float = WHISPER_CANCEL_CHUNK_SECONDS,
    stop_after_detected_languages=None,
) -> tuple[dict, dict]:
    """Transcribe with timing safeguards and bounded cancellation latency.

    Without a cancellation event this keeps the historical single-call behavior.
    Runtime video processing supplies the event, so real audio is decoded once and
    passed to Whisper in bounded chunks. Cancellation is checked between chunks;
    segment/word timestamps are shifted back to the original media timeline.
    """
    options = {
        "fp16": bool(use_cuda),
        "verbose": False,
        "language": None,
        "task": "transcribe",
        "condition_on_previous_text": True,
    }
    enhanced = []
    prompt = str(initial_prompt or "").strip()
    if prompt:
        options["initial_prompt"] = prompt
        enhanced.append("initial_prompt")
        options["carry_initial_prompt"] = True
        enhanced.append("carry_initial_prompt")
    if WHISPER_WORD_TIMESTAMPS:
        options["word_timestamps"] = True
        enhanced.append("word_timestamps")
        threshold = float(WHISPER_HALLUCINATION_SILENCE_THRESHOLD_SEC)
        if threshold > 0:
            options["hallucination_silence_threshold"] = threshold
            enhanced.append("hallucination_silence_threshold")

    disabled = []

    def check_cancel():
        if cancel_event is not None and cancel_event.is_set():
            raise CancelledError()

    def call_transcribe(audio):
        while True:
            try:
                return model.transcribe(audio, **options)
            except TypeError as exc:
                message = str(exc)
                if "unexpected keyword argument" not in message:
                    raise
                match = re.search(r"""unexpected keyword argument ['"]([^'"]+)['"]""", message)
                unsupported = match.group(1) if match else None
                if unsupported not in options or unsupported not in enhanced:
                    raise
                options.pop(unsupported, None)
                disabled.append(unsupported)
                if unsupported == "initial_prompt" and "carry_initial_prompt" in options:
                    options.pop("carry_initial_prompt", None)
                    disabled.append("carry_initial_prompt")
                if unsupported == "word_timestamps" and "hallucination_silence_threshold" in options:
                    options.pop("hallucination_silence_threshold", None)
                    disabled.append("hallucination_silence_threshold")

    def features(*, chunked: bool, chunks_processed: int = 1, early_language_stop: bool = False):
        return {
            "word_timestamps": bool(options.get("word_timestamps")),
            "hallucination_silence_threshold_sec": options.get("hallucination_silence_threshold"),
            "initial_prompt_used": bool(options.get("initial_prompt")),
            "initial_prompt_chars": len(str(options.get("initial_prompt") or "")),
            "carry_initial_prompt": bool(options.get("carry_initial_prompt")),
            "compat_disabled": tuple(disabled),
            "cancellable_chunks": bool(chunked),
            "chunk_seconds": float(chunk_seconds) if chunked else None,
            "chunks_processed": int(chunks_processed),
            "early_language_stop": bool(early_language_stop),
        }

    check_cancel()
    # Unit/compatibility callers often provide a synthetic path. Keep their old
    # one-call contract; production always has the extracted WAV on disk.
    if cancel_event is None or not os.path.isfile(audio_path):
        result = call_transcribe(audio_path)
        check_cancel()
        return result, features(chunked=False)

    audio = _load_whisper_audio(audio_path)
    check_cancel()
    sample_count = len(audio)
    chunk_samples = max(1, int(max(1.0, float(chunk_seconds)) * WHISPER_SAMPLE_RATE))
    stop_languages = {
        str(value or "").lower().split("-")[0]
        for value in (stop_after_detected_languages or ())
        if str(value or "").strip()
    }

    merged_segments = []
    text_parts = []
    detected_language = ""
    processed_chunks = 0
    early_language_stop = False

    for sample_start in range(0, sample_count, chunk_samples):
        check_cancel()
        sample_end = min(sample_count, sample_start + chunk_samples)
        chunk = audio[sample_start:sample_end]
        if detected_language:
            options["language"] = detected_language

        chunk_result = call_transcribe(chunk)
        processed_chunks += 1
        check_cancel()

        chunk_text = str(chunk_result.get("text") or "").strip()
        candidate_language = str(chunk_result.get("language") or "").lower().split("-")[0]
        if not detected_language and chunk_text and candidate_language:
            detected_language = candidate_language

        offset = sample_start / float(WHISPER_SAMPLE_RATE)
        chunk_segments = list(chunk_result.get("segments") or [])
        for raw_segment in chunk_segments:
            segment = dict(raw_segment)
            for key in ("start", "end"):
                try:
                    segment[key] = float(segment.get(key, 0.0)) + offset
                except (TypeError, ValueError):
                    pass
            words = []
            for raw_word in segment.get("words") or []:
                if not isinstance(raw_word, dict):
                    words.append(raw_word)
                    continue
                word = dict(raw_word)
                for key in ("start", "end"):
                    try:
                        word[key] = float(word.get(key, 0.0)) + offset
                    except (TypeError, ValueError):
                        pass
                words.append(word)
            if words:
                segment["words"] = words
            segment["id"] = len(merged_segments)
            merged_segments.append(segment)

        if chunk_text:
            text_parts.append(chunk_text)

        if (
            detected_language
            and detected_language in stop_languages
            and chunk_text
            and chunk_segments
            and (detected_language != "ru" or looks_like_russian_text(chunk_text))
        ):
            early_language_stop = True
            break

    result = {
        "text": " ".join(text_parts).strip(),
        "language": detected_language or str(options.get("language") or ""),
        "segments": merged_segments,
    }
    return result, features(
        chunked=True,
        chunks_processed=processed_chunks,
        early_language_stop=early_language_stop,
    )

def summarize_transcription_quality(segments: list[dict] | None) -> dict:
    """Return bounded ASR quality telemetry without discarding recognized speech."""
    segments = list(segments or [])
    low_logprob = 0
    high_compression = 0
    high_no_speech = 0
    temperature_fallback = 0
    word_timed = 0
    suspicious_segments = 0
    normalized_texts = []

    for segment in segments:
        segment_suspicious = False
        try:
            if float(segment.get("avg_logprob")) < -0.9:
                low_logprob += 1
                segment_suspicious = True
        except (TypeError, ValueError):
            pass
        try:
            if float(segment.get("compression_ratio")) > 2.4:
                high_compression += 1
                segment_suspicious = True
        except (TypeError, ValueError):
            pass
        try:
            if float(segment.get("no_speech_prob")) > 0.6:
                high_no_speech += 1
        except (TypeError, ValueError):
            pass
        try:
            if float(segment.get("temperature")) > 0.5:
                temperature_fallback += 1
        except (TypeError, ValueError):
            pass
        if segment.get("words"):
            word_timed += 1
        if segment_suspicious:
            suspicious_segments += 1
        normalized = " ".join(re.findall(r"\w+", str(segment.get("text") or "").lower(), flags=re.UNICODE))
        normalized_texts.append(normalized)

    repeated_adjacent = 0
    longest_repeat_run = 0
    run = 0
    previous = None
    for text in normalized_texts:
        if text and text == previous:
            run += 1
            repeated_adjacent += 1
        else:
            run = 1 if text else 0
        longest_repeat_run = max(longest_repeat_run, run)
        previous = text if text else None

    total = len(segments)
    return {
        "segments": total,
        "word_timed_segments": word_timed,
        "low_logprob_segments": low_logprob,
        "high_compression_segments": high_compression,
        "high_no_speech_segments": high_no_speech,
        "temperature_fallback_segments": temperature_fallback,
        "repeated_adjacent_segments": repeated_adjacent,
        "longest_repeat_run": longest_repeat_run,
        "suspicious_ratio": round(suspicious_segments / total, 4) if total else 0.0,
    }
