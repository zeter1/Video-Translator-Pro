"""Offline cancellation/recovery regressions; all runtime state is temporary."""
import tempfile
import threading
import types
import unittest
from contextlib import ExitStack
from pathlib import Path
from unittest import mock

import video_translator as vt


class TranslationCancellationTests(unittest.TestCase):
    def test_partial_fallback_updates_records_before_cancellation(self):
        with tempfile.TemporaryDirectory() as directory, mock.patch(
            "videotranslator.tts.cache.get_tts_cache_dir", return_value=Path(directory)
        ):
            translator = vt.VideoTranslator(lambda _: None)
            records = [(1, {"source": "one", "translated": ""}),
                       (2, {"source": "two", "translated": ""})]
            translator.translate_segment = mock.Mock(
                side_effect=["invalid batch", "один", vt.CancelledError()]
            )
            with self.assertRaises(vt.CancelledError):
                translator.translate_records_batch(records)
            self.assertEqual(records[0][1]["translated"], "один")
            self.assertEqual(records[1][1]["translated"], "")

    def test_cancel_then_resume_preserves_completed_translations(self):
        self._check_interrupted_progress(vt.CancelledError())

    def test_unexpected_failure_preserves_completed_translations(self):
        self._check_interrupted_progress(RuntimeError("interrupted batch"))

    def test_cancel_during_plan_keeps_existing_complete_checkpoint(self):
        self._check_interrupted_progress(vt.CancelledError(), cancel_plan=True)

    def _check_interrupted_progress(self, interruption, cancel_plan=False):
        with tempfile.TemporaryDirectory() as directory, ExitStack() as stack:
            root = Path(directory)
            checkpoint = root / "checkpoint.json"
            source = root / "source.mp4"
            source.write_bytes(b"synthetic fixture; extraction is mocked")
            stack.enter_context(mock.patch("videotranslator.tts.cache.get_tts_cache_dir",
                                           return_value=root / "cache"))
            stack.enter_context(mock.patch("videotranslator.pipeline.process.is_cuda_available",
                                           return_value=False))
            stack.enter_context(mock.patch("videotranslator.pipeline.process.select_audio_stream", return_value=1))
            model = types.SimpleNamespace(transcribe=lambda *a, **k: {
                "language": "en", "text": "one two",
                "segments": [{"start": 0, "end": 2, "text": "one"},
                             {"start": 3, "end": 5, "text": "two"}],
            })
            for name, value in {
                "find_ffmpeg": "ffmpeg", "find_ffprobe": "ffprobe",
                "get_media_duration": 6.0, "extract_audio_for_whisper": True,
                "get_whisper_model": model, "translation_checkpoint_path": checkpoint,
            }.items():
                stack.enter_context(mock.patch.object(vt, name, return_value=value))
            translator = vt.VideoTranslator(lambda _: None, cancel_event=threading.Event())

            if cancel_plan:
                vt.save_translation_checkpoint(checkpoint, str(source), "en", "ru", [
                    {"start": 0, "end": 2, "source": "one", "translated": "один"},
                    {"start": 3, "end": 5, "source": "two", "translated": "два"},
                ])
                saved_bytes = checkpoint.read_bytes()
                stack.enter_context(mock.patch(
                    "videotranslator.pipeline.process.translation_segment_key",
                    side_effect=[vt.translation_segment_key(0, 2, "one"), vt.CancelledError()],
                ))

            def interrupt_after_one(records, **kwargs):
                records[0][1]["translated"] = "один"
                raise interruption

            translator.translate_records_batch = interrupt_after_one
            args = (str(source), str(root / "out.mp4"), "ru-RU-DmitryNeural", "small", False, 0)
            self.assertFalse(translator.process(*args))
            if cancel_plan:
                self.assertEqual(checkpoint.read_bytes(), saved_bytes)
                return
            key = vt.translation_segment_key(0, 2, "one")
            self.assertEqual(vt.load_translation_checkpoint(checkpoint).get(key), "один")

            # A fresh process must request only the unfinished second segment.
            resumed = vt.VideoTranslator(lambda _: None)
            seen = []
            def inspect_pending(records, **kwargs):
                seen.extend(index for index, _ in records)
                raise vt.CancelledError()
            resumed.translate_records_batch = inspect_pending
            self.assertFalse(resumed.process(*args))
            self.assertEqual(seen, [2])
            self.assertEqual(vt.load_translation_checkpoint(checkpoint).get(key), "один")


if __name__ == "__main__":
    unittest.main()
