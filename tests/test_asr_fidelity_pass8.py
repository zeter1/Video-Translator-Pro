from __future__ import annotations

from contextlib import contextmanager
from pathlib import Path
import tempfile
import unittest
from unittest import mock

from videotranslator.pipeline.tts_generate import TTSGenerateMixin
from videotranslator.pipeline.tts_prepare import TTSPrepareMixin
from videotranslator.speech.whisper import summarize_transcription_quality, transcribe_video
from videotranslator.translation.quality_checker import inspect


class WhisperTranscriptionOptionsTests(unittest.TestCase):
    class RecordingModel:
        def __init__(self, reject: str | None = None):
            self.reject = reject
            self.calls = []

        def transcribe(self, audio_path, **kwargs):
            self.calls.append((audio_path, dict(kwargs)))
            if self.reject and self.reject in kwargs:
                rejected = self.reject
                self.reject = None
                raise TypeError(f"transcribe() got an unexpected keyword argument '{rejected}'")
            return {"text": "hello", "language": "en", "segments": []}

    def test_enables_word_timestamps_and_hallucination_silence_guard(self):
        model = self.RecordingModel()
        result, features = transcribe_video(model, "input.wav", use_cuda=False)

        self.assertEqual(result["text"], "hello")
        kwargs = model.calls[-1][1]
        self.assertTrue(kwargs["word_timestamps"])
        self.assertGreater(kwargs["hallucination_silence_threshold"], 0)
        self.assertTrue(features["word_timestamps"])
        self.assertEqual(features["compat_disabled"], ())

    def test_old_whisper_can_drop_only_unsupported_hallucination_argument(self):
        model = self.RecordingModel("hallucination_silence_threshold")
        _result, features = transcribe_video(model, "input.wav", use_cuda=False)

        self.assertEqual(len(model.calls), 2)
        self.assertTrue(model.calls[-1][1]["word_timestamps"])
        self.assertNotIn("hallucination_silence_threshold", model.calls[-1][1])
        self.assertEqual(features["compat_disabled"], ("hallucination_silence_threshold",))

    def test_old_whisper_without_word_timestamps_drops_dependent_guard_too(self):
        model = self.RecordingModel("word_timestamps")
        _result, features = transcribe_video(model, "input.wav", use_cuda=False)

        self.assertEqual(len(model.calls), 2)
        self.assertNotIn("word_timestamps", model.calls[-1][1])
        self.assertNotIn("hallucination_silence_threshold", model.calls[-1][1])
        self.assertEqual(
            features["compat_disabled"],
            ("word_timestamps", "hallucination_silence_threshold"),
        )

    def test_unrelated_type_error_is_not_hidden_by_compatibility_retry(self):
        class BrokenModel:
            def transcribe(self, *_args, **_kwargs):
                raise TypeError("internal decoder bug")

        with self.assertRaisesRegex(TypeError, "internal decoder bug"):
            transcribe_video(BrokenModel(), "input.wav", use_cuda=False)


class WhisperQualityTelemetryTests(unittest.TestCase):
    def test_detects_repetition_and_low_confidence_without_dropping_segments(self):
        segments = [
            {"text": " same text ", "avg_logprob": -1.2, "compression_ratio": 1.2, "no_speech_prob": 0.1, "temperature": 0.0, "words": [{"word": "same"}]},
            {"text": "same text", "avg_logprob": -0.2, "compression_ratio": 2.8, "no_speech_prob": 0.7, "temperature": 0.6, "words": [{"word": "same"}]},
            {"text": "same text", "avg_logprob": -0.1, "compression_ratio": 1.0, "no_speech_prob": 0.1, "temperature": 0.0},
        ]
        summary = summarize_transcription_quality(segments)

        self.assertEqual(summary["segments"], 3)
        self.assertEqual(summary["word_timed_segments"], 2)
        self.assertEqual(summary["low_logprob_segments"], 1)
        self.assertEqual(summary["high_compression_segments"], 1)
        self.assertEqual(summary["high_no_speech_segments"], 1)
        self.assertEqual(summary["temperature_fallback_segments"], 1)
        self.assertEqual(summary["repeated_adjacent_segments"], 2)
        self.assertEqual(summary["longest_repeat_run"], 3)
        self.assertAlmostEqual(summary["suspicious_ratio"], 2 / 3, places=4)


class TranslationPayloadIntegrityTests(unittest.TestCase):
    def test_preserved_number_and_url_do_not_trigger_repair(self):
        result = inspect(
            "Version 3.14 docs: https://docs.example.com/v3",
            "Документация версии 3,14: https://docs.example.com/v3",
            "en",
            "ru",
        )
        self.assertTrue(result["ok"], result)

    def test_changed_numeric_payload_requests_selective_repair(self):
        result = inspect(
            "Use version 3.14 with 50% opacity",
            "Используйте версию 3.15 с прозрачностью 50%",
            "en",
            "ru",
        )
        self.assertFalse(result["ok"])
        self.assertIn("numeric_token_changed", result["reasons"])

    def test_changed_url_requests_selective_repair(self):
        result = inspect(
            "Open https://docs.example.com/v3 for details",
            "Откройте https://docs.example.com/v4 для подробностей",
            "en",
            "ru",
        )
        self.assertFalse(result["ok"])
        self.assertIn("protected_token_changed", result["reasons"])

    def test_spaced_thousands_keep_same_numeric_identity(self):
        result = inspect(
            "The file has 12,500 samples",
            "В файле 12 500 отсчётов",
            "en",
            "ru",
        )
        self.assertTrue(result["ok"], result)


class _PreparedCache:
    def __init__(self):
        self.requests = []

    def make_key(self, text, voice, rate_pct, provider, language, extra=None):
        record = {
            "text": text,
            "voice": voice,
            "rate_pct": rate_pct,
            "provider": provider,
            "language": language,
            "extra": dict(extra or {}),
        }
        self.requests.append(record)
        revision = record["extra"].get("piper_voice_revision", "")
        return f"{provider}|{revision}"

    @contextmanager
    def key_lock(self, _key):
        yield

    def restore(self, key, _path, suffix=".mp3"):
        return str(key).startswith("prepared_wav_piper|")

    def store(self, *_args, **_kwargs):
        return True


class _PreparedPiperHarness(TTSGenerateMixin, TTSPrepareMixin):
    def __init__(self, directory):
        self.temp_dir = directory
        self.target_info = {"code": "ru"}
        self.audio_settings = {}
        self.hybrid_translation_settings = {
            "local_piper_fallback": True,
            "preferred_piper_voice": "piper-dmitri-ru",
        }
        self.tts_cache = _PreparedCache()
        self._tts_allow_gtts_fallback = False
        self._piper_model_selection = None
        self.problems = []

    def _increment_tts_stat(self, _name):
        return None

    def _problem(self, event, **details):
        self.problems.append((event, details))


class PreparedPiperCacheRevisionTests(unittest.TestCase):
    def test_prepared_piper_cache_key_changes_after_voice_model_update(self):
        with tempfile.TemporaryDirectory() as directory:
            model_path = Path(directory) / "dmitri.onnx"
            model_path.write_bytes(b"voice-v1")
            Path(str(model_path) + ".json").write_text("{}", encoding="utf-8")

            class FakeManager:
                def resolve_piper_voice(self, preferred_id, language="ru"):
                    return preferred_id, model_path

            harness = _PreparedPiperHarness(directory)
            with mock.patch("videotranslator.pipeline.tts_generate.RuntimeModelManager", return_value=FakeManager()):
                first = harness._get_prepared_tts_audio("Привет", "ru-RU-DmitryNeural", 1, 0, "edge_0")
                first_revision = [
                    row["extra"].get("piper_voice_revision")
                    for row in harness.tts_cache.requests
                    if row["provider"] == "prepared_wav_piper"
                ][-1]

                model_path.write_bytes(b"voice-version-two-and-new")
                second = harness._get_prepared_tts_audio("Привет", "ru-RU-DmitryNeural", 2, 0, "edge_0")
                second_revision = [
                    row["extra"].get("piper_voice_revision")
                    for row in harness.tts_cache.requests
                    if row["provider"] == "prepared_wav_piper"
                ][-1]

            self.assertEqual(first[1], "piper")
            self.assertEqual(second[1], "piper")
            self.assertTrue(first_revision)
            self.assertTrue(second_revision)
            self.assertNotEqual(first_revision, second_revision)

    def test_cache_lookup_does_not_emit_voice_substitution_warning_before_fallback_is_needed(self):
        with tempfile.TemporaryDirectory() as directory:
            model_path = Path(directory) / "irina.onnx"
            model_path.write_bytes(b"voice")
            Path(str(model_path) + ".json").write_text("{}", encoding="utf-8")

            class FakeManager:
                def resolve_piper_voice(self, preferred_id, language="ru"):
                    return "piper-irina-ru", model_path

            harness = _PreparedPiperHarness(directory)
            with mock.patch("videotranslator.pipeline.tts_generate.RuntimeModelManager", return_value=FakeManager()):
                result = harness._get_prepared_tts_audio("Привет", "ru-RU-DmitryNeural", 1, 0, "edge_0")

            self.assertEqual(result[1], "piper")
            self.assertFalse(
                any(event == "tts_piper_voice_substituted" for event, _details in harness.problems),
                harness.problems,
            )


if __name__ == "__main__":
    unittest.main()
