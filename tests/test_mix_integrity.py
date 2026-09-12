"""Offline fallback tests: synthetic arrays and temporary output only."""
import importlib.util
import sys
import tempfile
import threading
import types
import unittest
import wave
from pathlib import Path
from unittest import mock

import video_translator as vt


@unittest.skipUnless(importlib.util.find_spec("numpy"), "numpy is required")
class MixIntegrityTests(unittest.TestCase):
    def setUp(self):
        import numpy as np
        self.np = np
        temporary = tempfile.TemporaryDirectory(prefix="vt_mix_test_")
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.translator = object.__new__(vt.VideoTranslator)
        self.translator.temp_dir = temporary.name
        self.translator.ffmpeg = "mock-ffmpeg"
        self.translator.cancel = threading.Event()
        self.translator.log = lambda _: None
        self.clips = []
        def load(path):
            if path == "bad.wav":
                raise OSError("unreadable audio")
            data = np.zeros((0, 2)) if path == "empty.wav" else np.full((4800, 2), 0.2)
            clip = mock.Mock()
            clip.to_soundarray.return_value = data
            if path == "cancel.wav":
                clip.to_soundarray.side_effect = vt.CancelledError()
            self.clips.append(clip)
            return clip
        patcher = mock.patch.dict(sys.modules, {"moviepy.editor": types.SimpleNamespace(AudioFileClip=load)})
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_failed_ffmpeg_and_missing_phrase_do_not_return_partial_mix(self):
        with mock.patch("videotranslator.pipeline.timeline_mix.run_subprocess",
                        return_value=types.SimpleNamespace(returncode=1, stderr="decode failed")):
            with self.assertRaisesRegex(RuntimeError, "bad.wav"):
                self.translator._amix_batch([(0, "good.wav"), (500, "bad.wav")], 1.0, "test")
        self.assertFalse((self.root / "mix_test.wav").exists())
        self.clips[0].close.assert_called_once()

    def test_cancel_in_last_clip_propagates_without_writing_audio(self):
        output = self.root / "cancelled.wav"
        with self.assertRaises(vt.CancelledError):
            self.translator._mix_numpy([(0, "cancel.wav")], 1.0, str(output))
        self.assertFalse(output.exists())
        self.clips[0].close.assert_called_once()

    def test_failed_intermediate_batch_does_not_return_incomplete_voice(self):
        with mock.patch("videotranslator.pipeline.timeline_mix.run_subprocess",
                        return_value=types.SimpleNamespace(returncode=1, stderr="decode failed")):
            with self.assertRaisesRegex(RuntimeError, "bad.wav"):
                self.translator._amix_wavs(["good.wav", "bad.wav"], 1.0)
        self.assertFalse((self.root / "final_voice_final.wav").exists())

    def test_empty_or_out_of_range_required_phrase_is_rejected(self):
        for delay, name in [(0, "empty.wav"), (1000, "good.wav"), (-100, "good.wav")]:
            with self.subTest(delay=delay, name=name):
                output = self.root / "invalid.wav"
                with self.assertRaises(RuntimeError):
                    self.translator._mix_numpy([(delay, name)], 1.0, str(output))
                self.assertFalse(output.exists())

    def test_valid_phrases_keep_positions_and_close_readers(self):
        output = self.root / "valid.wav"
        self.translator._mix_numpy([(0, "good.wav"), (500, "good.wav")], 1.0, str(output))
        with wave.open(str(output), "rb") as audio:
            frames = audio.readframes(audio.getnframes())
            data = self.np.frombuffer(frames, dtype=self.np.int16).reshape(-1, 2)
        self.assertEqual(len(data), vt.SAMPLE_RATE)
        self.assertGreater(abs(data[:4000]).max(), 5000)
        self.assertEqual(abs(data[10000:15000]).max(), 0)
        self.assertGreater(abs(data[24000:28000]).max(), 5000)
        for clip in self.clips:
            clip.close.assert_called_once()


if __name__ == "__main__":
    unittest.main()
