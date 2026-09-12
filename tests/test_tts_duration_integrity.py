"""TTS preparation must not replace a usable phrase with an unmeasurable result."""
import tempfile
import threading
import unittest
from unittest import mock

import video_translator as vt


class TtsDurationIntegrityTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix="vt_duration_")
        self.addCleanup(temporary.cleanup)
        self.translator = object.__new__(vt.VideoTranslator)
        self.translator.temp_dir = temporary.name
        self.translator.ffmpeg = self.translator.ffprobe = "unused"
        self.translator.speech_speed_limit = 1.5
        self.translator.cancel = threading.Event()
        self.translator._check_cancel = mock.Mock()
        self.translator._tts_segment_log = mock.Mock()

    def test_invalid_stretched_duration_keeps_verified_source_phrase(self):
        for bad_duration in (0.0, -1.0, float("nan"), float("inf")):
            with self.subTest(duration=bad_duration):
                self.translator._get_prepared_tts_audio = mock.Mock(side_effect=["base.wav", None])
                with mock.patch("videotranslator.pipeline.tts_prepare.get_audio_duration",
                                side_effect=[2.0, bad_duration]), \
                     mock.patch("videotranslator.pipeline.tts_prepare.time_stretch_audio", return_value=(True, "atempo")):
                    path, duration, stats = self.translator._prepare_tts_segment("text", "voice", 1, 1.0, 1.0)
                self.assertEqual((path, duration), ("base.wav", 2.0))
                self.assertEqual(stats["tempo"], 1.0)
                self.assertTrue(stats["video_pause_needed"])

    def test_nonfinite_base_duration_cannot_become_ready_speech(self):
        for bad_duration in (float("nan"), float("inf")):
            with self.subTest(duration=bad_duration):
                self.translator._get_prepared_tts_audio = mock.Mock(return_value="base.wav")
                with mock.patch("videotranslator.pipeline.tts_prepare.get_audio_duration", return_value=bad_duration), \
                     mock.patch("videotranslator.pipeline.tts_prepare.time_stretch_audio", return_value=(False, "failed")):
                    path, duration, _ = self.translator._prepare_tts_segment("text", "voice", 1, 1.0, 1.0)
                self.assertIsNone(path)
                self.assertEqual(duration, 0.0)

    def test_valid_stretched_duration_is_used(self):
        self.translator._get_prepared_tts_audio = mock.Mock(side_effect=["base.wav", None])
        with mock.patch("videotranslator.pipeline.tts_prepare.get_audio_duration", side_effect=[2.0, 1.8]), \
             mock.patch("videotranslator.pipeline.tts_prepare.time_stretch_audio", return_value=(True, "atempo")):
            path, duration, stats = self.translator._prepare_tts_segment("text", "voice", 1, 1.0, 1.0)
        self.assertTrue(path.endswith("_tempo.wav"))
        self.assertEqual(duration, 1.8)
        self.assertEqual(stats["tempo_method"], "atempo")
