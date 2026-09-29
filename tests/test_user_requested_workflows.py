from __future__ import annotations

import tempfile
import threading
import types
import unittest
from contextlib import ExitStack
from pathlib import Path
from unittest import mock

import video_translator as vt
from videotranslator.core.cancel import CancelledError
from videotranslator.reports import output as output_reports
from videotranslator.speech import whisper as whisper_module
from videotranslator.speech.whisper import transcribe_video


class WhisperCancellationRegressionTests(unittest.TestCase):
    class Model:
        def __init__(self, cancel_event=None):
            self.cancel_event = cancel_event
            self.calls = 0

        def transcribe(self, _audio, **_kwargs):
            self.calls += 1
            if self.cancel_event is not None and self.calls == 1:
                self.cancel_event.set()
            return {
                "text": "Привет, это русское видео.",
                "language": "ru",
                "segments": [{
                    "start": 0.0,
                    "end": 0.8,
                    "text": "Привет, это русское видео.",
                    "words": [{"start": 0.0, "end": 0.4, "word": "Привет"}],
                }],
            }

    def test_cancel_is_observed_between_whisper_chunks(self):
        cancel = threading.Event()
        model = self.Model(cancel)
        with tempfile.TemporaryDirectory() as directory:
            audio_path = Path(directory) / "audio.wav"
            audio_path.write_bytes(b"fixture")
            fake_audio = [0.0] * (whisper_module.WHISPER_SAMPLE_RATE * 3)
            with mock.patch.object(whisper_module, "_load_whisper_audio", return_value=fake_audio):
                with self.assertRaises(CancelledError):
                    transcribe_video(
                        model,
                        str(audio_path),
                        use_cuda=False,
                        cancel_event=cancel,
                        chunk_seconds=1.0,
                    )
        self.assertEqual(model.calls, 1)

    def test_russian_detection_can_stop_after_first_confirmed_chunk(self):
        model = self.Model()
        with tempfile.TemporaryDirectory() as directory:
            audio_path = Path(directory) / "audio.wav"
            audio_path.write_bytes(b"fixture")
            fake_audio = [0.0] * (whisper_module.WHISPER_SAMPLE_RATE * 3)
            with mock.patch.object(whisper_module, "_load_whisper_audio", return_value=fake_audio):
                result, features = transcribe_video(
                    model,
                    str(audio_path),
                    use_cuda=False,
                    cancel_event=threading.Event(),
                    chunk_seconds=1.0,
                    stop_after_detected_languages={"ru"},
                )

        self.assertEqual(model.calls, 1)
        self.assertEqual(result["language"], "ru")
        self.assertTrue(features["early_language_stop"])
        self.assertTrue(whisper_module.looks_like_russian_text(result["text"]))


class RussianPassthroughRegressionTests(unittest.TestCase):
    def test_russian_source_is_moved_without_translation_or_tts(self):
        with tempfile.TemporaryDirectory() as directory, ExitStack() as stack:
            root = Path(directory)
            source = root / "already-russian.mp4"
            source_bytes = b"original-video-bytes"
            source.write_bytes(source_bytes)
            output_dir = root / "translated"
            output_dir.mkdir()
            output = output_dir / "already-russian_RU.mp4"

            stack.enter_context(mock.patch("videotranslator.tts.cache.get_tts_cache_dir", return_value=root / "cache"))
            stack.enter_context(mock.patch("videotranslator.pipeline.process.get_translation_glossary_path", return_value=root / "missing-glossary.json"))
            stack.enter_context(mock.patch("videotranslator.pipeline.process.is_cuda_available", return_value=False))
            stack.enter_context(mock.patch("videotranslator.pipeline.process.select_audio_stream", return_value=1))
            model = types.SimpleNamespace(transcribe=lambda *_a, **_k: {
                "language": "ru",
                "text": "Привет, это уже русское видео.",
                "segments": [{"start": 0.0, "end": 2.0, "text": "Привет, это уже русское видео."}],
            })
            for name, value in {
                "find_ffmpeg": "ffmpeg",
                "find_ffprobe": "ffprobe",
                "get_media_duration": 6.0,
                "extract_audio_for_whisper": True,
                "get_whisper_model": model,
            }.items():
                stack.enter_context(mock.patch.object(vt, name, return_value=value))

            events = []
            translator = vt.VideoTranslator(
                lambda _message: None,
                cancel_event=threading.Event(),
                problem_cb=lambda event, **details: events.append((event, details)),
            )
            translator.translate_records_batch = mock.Mock(side_effect=AssertionError("translation must be bypassed"))
            translator.build_timeline = mock.Mock(side_effect=AssertionError("TTS must be bypassed"))

            ok = translator.process(
                str(source),
                str(output),
                "ru-RU-DmitryNeural",
                "small",
                False,
                0,
                target_info={"code": "ru", "name": "Русский", "suffix": "_RU"},
            )

            self.assertTrue(ok)
            self.assertFalse(source.exists())
            self.assertEqual(output.read_bytes(), source_bytes)
            self.assertEqual(translator.completed_output_path, str(output))
            self.assertEqual([path.name for path in output_dir.iterdir()], [output.name])
            translator.translate_records_batch.assert_not_called()
            translator.build_timeline.assert_not_called()
            self.assertTrue(any(event == "russian_source_moved_without_translation" for event, _ in events))


class OutputFolderRegressionTests(unittest.TestCase):
    def test_reports_and_subtitles_do_not_pollute_video_output_folder(self):
        segments = [{
            "start": 0.0,
            "end": 1.0,
            "source": "Hello",
            "translated": "Привет",
        }]
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            output_dir = root / "translated"
            output_dir.mkdir()
            video = output_dir / "movie_RU.mp4"
            video.write_bytes(b"video")
            auxiliary = root / "translated_texts"
            auxiliary.mkdir()

            with mock.patch.object(output_reports, "get_translated_texts_dir", return_value=auxiliary):
                report = output_reports.write_translation_report(
                    str(video),
                    segments,
                    [],
                    1.0,
                    1.0,
                    source_lang="en",
                    target_info={"code": "ru", "name": "Русский"},
                )
                subtitles = output_reports.write_subtitle_files(
                    str(video),
                    segments,
                    [],
                    source_lang="en",
                    target_info={"code": "ru", "name": "Русский"},
                )

            self.assertEqual([path.name for path in output_dir.iterdir()], [video.name])
            self.assertEqual(Path(report).parent, auxiliary)
            self.assertEqual({Path(path).parent for path in subtitles.values()}, {auxiliary})


class MissingLocalRouteRegressionTests(unittest.TestCase):
    def test_missing_local_route_does_not_try_local_translation_per_segment(self):
        with tempfile.TemporaryDirectory() as directory, mock.patch(
            "videotranslator.tts.cache.get_tts_cache_dir", return_value=Path(directory) / "cache"
        ):
            events = []
            translator = vt.VideoTranslator(
                lambda _message: None,
                problem_cb=lambda event, **details: events.append((event, details)),
            )
            translator.source_language = "cy"
            translator.target_info = {"code": "ru"}
            translator.hybrid_translation_settings = {"local_first": True}

            manager = mock.Mock()
            manager.has_local_route.return_value = False
            translator._get_local_translation_manager = mock.Mock(return_value=manager)
            translator._translate_google_segment = mock.Mock(return_value="перевод")

            result = translator.translate_segment("hello", index=1)

            self.assertEqual(result, "перевод")
            manager.translate.assert_not_called()
            self.assertFalse(any(event == "local_translation_unavailable" for event, _ in events))


if __name__ == "__main__":
    unittest.main()
