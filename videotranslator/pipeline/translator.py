"""VideoTranslator composition root; long process workflow lives in process.py."""

from __future__ import annotations

from videotranslator.core.compat_bridge import call_legacy_override

import hashlib
import os
import shutil
import tempfile
import threading
import time
import traceback
from videotranslator.config import DEFAULT_TARGET_LANGUAGE, EDGE_TTS_MAX_TIMEOUT_SEC, EDGE_TTS_MIN_TIMEOUT_SEC, EDGE_VOICE_PRESERVE_SEC, EDGE_VOICE_PROBE_INTERVAL_SEC, NETWORK_CONNECT_TIMEOUT_SEC, NETWORK_READ_TIMEOUT_SEC, TOTAL_MAX_SPEECH_SPEED, TRANSLATION_BATCH_MAX_CHARS, TRANSLATION_BATCH_MAX_SEGMENTS, TRANSLATION_CHECKPOINT_EVERY, TRANSLATION_PROGRESS_EVERY, TTS_CACHE_MAX_BYTES, TTS_CACHE_ORPHAN_RETENTION_HOURS, TTS_CACHE_PREPARED_RETENTION_HOURS, TTS_CACHE_RECOVERY_RETENTION_DAYS, TTS_CACHE_SCHEMA_VERSION, TTS_CACHE_TARGET_BYTES, TTS_PREPARED_CACHE_SCHEMA_VERSION, get_target_language, normalize_audio_settings
from videotranslator.core.cancel import CancelledError
from videotranslator.core.diagnostics import classify_exception, compact_exception, exception_chain, redact_diagnostic_text
from videotranslator.core.timefmt import fmt_time
from videotranslator.diagnostics.heartbeat import ActivityHeartbeat
from videotranslator.media.audio import calc_audio_work_timeout
from videotranslator.media.process import find_ffmpeg as _legacy_find_ffmpeg, find_ffprobe as _legacy_find_ffprobe
from videotranslator.media.video import assemble_final_video as _legacy_assemble_final_video, extract_audio_for_whisper as _legacy_extract_audio_for_whisper, get_media_duration as _legacy_get_media_duration
from videotranslator.network.guard import NetworkStormGuard
from videotranslator.recovery.translation import load_translation_checkpoint, translation_checkpoint_path as _legacy_translation_checkpoint_path, translation_segment_key
from videotranslator.reports.output import write_translated_text_file as _legacy_write_translated_text_file, write_translation_report as _legacy_write_translation_report
from videotranslator.speech.whisper import get_whisper_model as _legacy_get_whisper_model, is_cuda_available
from videotranslator.sync.pause import merge_short_segments
from videotranslator.translation.batching import split_translation_batches
from videotranslator.tts.cache import TTSCache
from videotranslator.pipeline.network_voice import NetworkVoiceMixin
from videotranslator.pipeline.translation import TranslationMixin
from videotranslator.pipeline.tts import TTSMixin
from videotranslator.pipeline.timeline import TimelineMixin


def find_ffmpeg(*args, **kwargs):
    return call_legacy_override('find_ffmpeg', _legacy_find_ffmpeg, *args, **kwargs)

def find_ffprobe(*args, **kwargs):
    return call_legacy_override('find_ffprobe', _legacy_find_ffprobe, *args, **kwargs)

def get_media_duration(*args, **kwargs):
    return call_legacy_override('get_media_duration', _legacy_get_media_duration, *args, **kwargs)

def extract_audio_for_whisper(*args, **kwargs):
    return call_legacy_override('extract_audio_for_whisper', _legacy_extract_audio_for_whisper, *args, **kwargs)

def get_whisper_model(*args, **kwargs):
    return call_legacy_override('get_whisper_model', _legacy_get_whisper_model, *args, **kwargs)

def translation_checkpoint_path(*args, **kwargs):
    return call_legacy_override('translation_checkpoint_path', _legacy_translation_checkpoint_path, *args, **kwargs)

def write_translated_text_file(*args, **kwargs):
    return call_legacy_override('write_translated_text_file', _legacy_write_translated_text_file, *args, **kwargs)

def assemble_final_video(*args, **kwargs):
    return call_legacy_override('assemble_final_video', _legacy_assemble_final_video, *args, **kwargs)

def write_translation_report(*args, **kwargs):
    return call_legacy_override('write_translation_report', _legacy_write_translation_report, *args, **kwargs)
from videotranslator.pipeline.process import ProcessMixin

class VideoTranslator(ProcessMixin, NetworkVoiceMixin, TranslationMixin, TTSMixin, TimelineMixin):
    """Per-video translation pipeline composition root."""

    def __init__(self, log_cb, progress_cb=None, cancel_event=None, review_callback=None,
                 problem_cb=None, network_notice_cb=None):
        self.log = log_cb
        self.set_progress = progress_cb or (lambda v, t="": None)
        self.cancel = cancel_event or threading.Event()
        self.ffmpeg = ""
        self.ffprobe = ""
        self.temp_dir = ""
        self.translator = None
        self.translation_client = None
        self.target_info = get_target_language(DEFAULT_TARGET_LANGUAGE)
        self.audio_settings = normalize_audio_settings()
        self.speech_speed_limit = TOTAL_MAX_SPEECH_SPEED
        self.review_callback = review_callback
        self.problem_cb = problem_cb
        self.network_notice_cb = network_notice_cb
        self.current_input_path = ""
        self.current_stage = "idle"
        self.selected_voice = ""
        self.edge_voice_lock = threading.Lock()
        self.edge_voice_incident = 0
        self.edge_voice_preservation_active = False
        self.edge_voice_deadline = 0.0
        self.edge_voice_probe_requested = threading.Event()
        self.edge_voice_fallback_requested = threading.Event()
        self.edge_voice_fallback_request_reason = ""
        self._tts_allow_gtts_fallback = False
        self._tts_fallback_reason = ""
        self.translation_network_guard = NetworkStormGuard(
            log=self.log,
            event_cb=self._problem,
            service="google_translate",
            threshold=6,
            window_sec=90,
            initial_pause_sec=30,
            recovery_successes=3,
            recovery_min_sec=10,
        )
        self.edge_network_guard = NetworkStormGuard(
            log=self.log,
            event_cb=self._problem,
            service="edge_tts",
            threshold=3,
            window_sec=120,
            initial_pause_sec=30,
            recovery_successes=3,
            recovery_min_sec=30,
            probe_interval_sec=EDGE_VOICE_PROBE_INTERVAL_SEC,
        )
        self.gtts_network_guard = NetworkStormGuard(
            log=self.log,
            event_cb=self._problem,
            service="gtts",
            threshold=4,
            window_sec=120,
            initial_pause_sec=30,
            recovery_successes=2,
            recovery_min_sec=10,
        )
        # Совместимость для внешних расширений; перевод использует отдельную защиту.
        self.network_guard = self.translation_network_guard
        self.edge_parallel_gate = threading.BoundedSemaphore(2)
        self.edge_probe_gate = threading.Lock()
        self.gtts_parallel_gate = threading.BoundedSemaphore(2)
        self.tts_cache = TTSCache()
        self.tts_stats_lock = threading.Lock()
        self.source_language = ""
        self.hybrid_translation_settings = {
            "local_first": True,
            "auto_install_argos_pairs": True,
            "local_piper_fallback": True,
            "preferred_piper_voice": "piper-dmitri-ru",
        }
        self.local_translation_manager = None
        self.shared_translation_circuit = None
        self._piper_voice_engine = None
        self.tts_stats = {}
        self.tts_gtts_segments = set()

    def _emit_problem(self, event: str, level: str = "info", message: str = "", **details):
        if not self.problem_cb:
            return
        try:
            self.problem_cb(
                event,
                level=level,
                message=message,
                input_path=self.current_input_path,
                stage=self.current_stage,
                **details,
            )
        except Exception:
            pass

    def _problem(self, event: str, level: str = "info", message: str = "", **details):
        self._emit_problem(event, level=level, message=message, **details)
        service = str(details.get("service") or "")
        if event == "network_storm_started" and service == "edge_tts":
            self._begin_edge_voice_preservation(reason="edge_network_storm")
        elif event == "network_storm_finished" and service == "edge_tts":
            self._finish_edge_voice_preservation(reason="edge_recovered", notify_action="recovered")

    def _check_cancel(self):
        if self.cancel.is_set():
            raise CancelledError()

    def _sleep_or_cancel(self, seconds: float):
        if self.cancel.wait(seconds):
            raise CancelledError()

    def _increment_tts_stat(self, name: str, amount: int = 1):
        with self.tts_stats_lock:
            self.tts_stats[name] = int(self.tts_stats.get(name, 0)) + int(amount)
            return self.tts_stats[name]

    def _mark_gtts_fallback(self, segment_index: int) -> int:
        return self._set_final_tts_provider(segment_index, "gtts")

    def _set_final_tts_provider(self, segment_index: int, provider: str) -> int:
        """Keeps ``gtts_fallback`` aligned with the provider used by the final segment audio.

        A segment may temporarily fall back to gTTS and later be successfully regenerated
        by Edge TTS (for example, with a native rate).  Counting the temporary fallback
        forever made the diagnostics report a mixed voice even when the final WAV was Edge.
        """
        index = int(segment_index or 0)
        normalized = str(provider or "").strip().lower()
        lock = getattr(self, "tts_stats_lock", None)
        if lock is None:
            return 0
        with lock:
            if not hasattr(self, "tts_gtts_segments"):
                self.tts_gtts_segments = set()
            if not hasattr(self, "tts_stats"):
                self.tts_stats = {}
            if normalized == "gtts":
                self.tts_gtts_segments.add(index)
            else:
                # Edge or local Piper replaced any temporary gTTS audio.
                self.tts_gtts_segments.discard(index)
            self.tts_stats["gtts_fallback"] = len(self.tts_gtts_segments)
            return self.tts_stats["gtts_fallback"]
