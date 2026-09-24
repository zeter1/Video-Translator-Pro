"""Whisper model loading, video-oriented transcription options and quality telemetry."""

from __future__ import annotations

import re
import threading

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


def transcribe_video(model, audio_path: str, *, use_cuda: bool, initial_prompt: str | None = None) -> tuple[dict, dict]:
    """Transcribe with timing/hallucination safeguards while tolerating older Whisper builds.

    Current OpenAI Whisper can refine segment boundaries with word timestamps and can
    skip long silent spans around suspected hallucinations. Some older installations
    reject one of these keyword arguments, so only that unsupported enhancement is
    removed and the call is retried instead of failing the whole translation.
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
        # Keep the compact terminology prompt active on every internal Whisper
        # window so proper nouns do not lose the hint later in long videos.
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
    while True:
        try:
            result = model.transcribe(audio_path, **options)
            return result, {
                "word_timestamps": bool(options.get("word_timestamps")),
                "hallucination_silence_threshold_sec": options.get("hallucination_silence_threshold"),
                "initial_prompt_used": bool(options.get("initial_prompt")),
                "initial_prompt_chars": len(str(options.get("initial_prompt") or "")),
                "carry_initial_prompt": bool(options.get("carry_initial_prompt")),
                "compat_disabled": tuple(disabled),
            }
        except TypeError as exc:
            message = str(exc)
            if "unexpected keyword argument" not in message:
                raise
            match = re.search(r"unexpected keyword argument [\'\"]([^\'\"]+)[\'\"]", message)
            unsupported = match.group(1) if match else None
            if unsupported not in options or unsupported not in enhanced:
                raise
            options.pop(unsupported, None)
            disabled.append(unsupported)
            if unsupported == "initial_prompt":
                # carry_initial_prompt is meaningless without the prompt itself.
                if "carry_initial_prompt" in options:
                    options.pop("carry_initial_prompt", None)
                    disabled.append("carry_initial_prompt")
            if unsupported == "word_timestamps":
                # Whisper's hallucination silence guard requires word timestamps.
                if "hallucination_silence_threshold" in options:
                    options.pop("hallucination_silence_threshold", None)
                    disabled.append("hallucination_silence_threshold")


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
