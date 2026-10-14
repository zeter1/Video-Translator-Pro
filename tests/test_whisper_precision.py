"""Offline guards for the CUDA FP16 NaN failure; no model or GPU is loaded."""

import tempfile
import threading
import unittest
from pathlib import Path
from unittest import mock

from videotranslator.core.cancel import CancelledError
from videotranslator.speech import whisper as whisper_module


class WhisperPrecisionRegressionTests(unittest.TestCase):
    class HalfPrecisionBrokenModel:
        def __init__(self, cancel=None):
            self.cancel = cancel
            self.completed = 0

        def transcribe(self, audio, **options):
            # Model boundary reproduces the observed numerical failure. The
            # production wrapper must still merge real results and timestamps.
            if options.get("fp16", True):
                raise ValueError("Categorical logits contain nan on cuda:0")
            self.completed += 1
            text = "first" if self.completed == 1 else "second"
            if self.cancel is not None:
                self.cancel.set()
            return {
                "text": text,
                "language": "en",
                "segments": [{
                    "start": 0.25, "end": 0.75, "text": text,
                    "words": [{"start": 0.25, "end": 0.75, "word": text}],
                }],
            }

    def test_cuda_transcription_avoids_nonfinite_half_precision(self):
        result, _features = whisper_module.transcribe_video(
            self.HalfPrecisionBrokenModel(), "synthetic.wav", use_cuda=True,
        )
        self.assertEqual(result["text"], "first")
        self.assertEqual(result["language"], "en")
        self.assertEqual(result["segments"][0]["words"][0]["end"], 0.75)

    def test_cuda_chunks_preserve_timestamps_and_full_text(self):
        with tempfile.TemporaryDirectory() as directory:
            audio_path = Path(directory) / "audio.wav"
            audio_path.write_bytes(b"synthetic fixture; decoder is replaced")
            audio = [0.0] * (whisper_module.WHISPER_SAMPLE_RATE * 2)
            with mock.patch.object(whisper_module, "_load_whisper_audio", return_value=audio):
                result, features = whisper_module.transcribe_video(
                    self.HalfPrecisionBrokenModel(), str(audio_path),
                    use_cuda=True, cancel_event=threading.Event(), chunk_seconds=1.0,
                )
        self.assertEqual(result["text"], "first second")
        self.assertEqual([s["id"] for s in result["segments"]], [0, 1])
        self.assertEqual([s["start"] for s in result["segments"]], [0.25, 1.25])
        self.assertEqual([s["end"] for s in result["segments"]], [0.75, 1.75])
        self.assertEqual(result["segments"][1]["words"][0]["start"], 1.25)
        self.assertEqual(result["segments"][1]["words"][0]["end"], 1.75)
        self.assertEqual(features["chunks_processed"], 2)

    def test_cancel_during_cuda_chunk_does_not_start_next_chunk(self):
        cancel = threading.Event()
        model = self.HalfPrecisionBrokenModel(cancel)
        with tempfile.TemporaryDirectory() as directory:
            audio_path = Path(directory) / "audio.wav"
            audio_path.write_bytes(b"synthetic fixture; decoder is replaced")
            audio = [0.0] * (whisper_module.WHISPER_SAMPLE_RATE * 2)
            with mock.patch.object(whisper_module, "_load_whisper_audio", return_value=audio):
                with self.assertRaises(CancelledError):
                    whisper_module.transcribe_video(
                        model, str(audio_path), use_cuda=True,
                        cancel_event=cancel, chunk_seconds=1.0,
                    )
        self.assertEqual(model.completed, 1)

    def test_unrelated_decoder_failure_is_not_hidden(self):
        class BrokenModel:
            def transcribe(self, *_args, **_options):
                raise ValueError("invalid input shape")

        with self.assertRaisesRegex(ValueError, "invalid input shape"):
            whisper_module.transcribe_video(BrokenModel(), "synthetic.wav", use_cuda=True)


if __name__ == "__main__":
    unittest.main()
