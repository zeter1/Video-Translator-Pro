from __future__ import annotations

from pathlib import Path
import math
import os
import shutil
import struct
import subprocess
import tempfile
import unittest
import wave

from videotranslator.media.audio import build_original_voice_mix_tail
from videotranslator.reports.output import _render_srt, write_subtitle_files
from videotranslator.speech.whisper import transcribe_video
from videotranslator.sync.pause import make_filter_script_for_pauses
from videotranslator.translation.glossary import Glossary


class GlossaryPipelineTests(unittest.TestCase):
    def test_whisper_prompt_uses_only_source_terms_and_stays_bounded(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "translation_glossary.json"
            glossary = Glossary(path)
            glossary.items = {
                "CTranslate2": "Си Транслейт 2",
                "OpenAI Whisper": "OpenAI Whisper",
                "CUDA": "CUDA",
                "x" * 130: "ignored-too-long",
            }
            prompt = glossary.build_whisper_prompt(max_chars=30, max_terms=3)

        self.assertLessEqual(len(prompt), 30)
        self.assertIn("CTranslate2", prompt)
        self.assertNotIn("Си Транслейт", prompt)
        self.assertNotIn("ignored-too-long", prompt)

    def test_post_translation_cleanup_is_source_gated_and_term_safe(self):
        glossary = Glossary(Path("/definitely/missing/glossary.json"))
        glossary.items = {"CUDA": "КУДА", "RAM": "ОЗУ"}

        cleaned, count = glossary.apply(
            "Используйте cuda и программу RAM.",
            source_text="Use CUDA with enough RAM.",
            return_count=True,
        )
        self.assertEqual(cleaned, "Используйте КУДА и программу ОЗУ.")
        self.assertEqual(count, 2)

        untouched, count = glossary.apply(
            "CUDA appeared only in translation",
            source_text="This source has no such term",
            return_count=True,
        )
        self.assertEqual(untouched, "CUDA appeared only in translation")
        self.assertEqual(count, 0)


class WhisperGlossaryPromptTests(unittest.TestCase):
    class RecordingModel:
        def __init__(self, reject_prompt=False):
            self.reject_prompt = reject_prompt
            self.calls = []

        def transcribe(self, audio_path, **kwargs):
            self.calls.append((audio_path, dict(kwargs)))
            if self.reject_prompt and "initial_prompt" in kwargs:
                self.reject_prompt = False
                raise TypeError("transcribe() got an unexpected keyword argument 'initial_prompt'")
            return {"text": "ok", "language": "en", "segments": []}

    def test_initial_prompt_reaches_whisper_and_is_reported(self):
        model = self.RecordingModel()
        _result, features = transcribe_video(
            model, "audio.wav", use_cuda=False, initial_prompt="OpenAI Whisper, CUDA"
        )
        self.assertEqual(model.calls[-1][1]["initial_prompt"], "OpenAI Whisper, CUDA")
        self.assertTrue(model.calls[-1][1]["carry_initial_prompt"])
        self.assertTrue(features["initial_prompt_used"])
        self.assertTrue(features["carry_initial_prompt"])
        self.assertEqual(features["initial_prompt_chars"], len("OpenAI Whisper, CUDA"))

    def test_old_whisper_can_drop_only_unsupported_initial_prompt(self):
        model = self.RecordingModel(reject_prompt=True)
        _result, features = transcribe_video(
            model, "audio.wav", use_cuda=False, initial_prompt="CUDA"
        )
        self.assertEqual(len(model.calls), 2)
        self.assertNotIn("initial_prompt", model.calls[-1][1])
        self.assertNotIn("carry_initial_prompt", model.calls[-1][1])
        self.assertFalse(features["initial_prompt_used"])
        self.assertFalse(features["carry_initial_prompt"])
        self.assertIn("initial_prompt", features["compat_disabled"])
        self.assertIn("carry_initial_prompt", features["compat_disabled"])
        self.assertTrue(model.calls[-1][1].get("word_timestamps"))

    def test_old_whisper_can_drop_carry_prompt_but_keep_initial_prompt(self):
        class RejectCarry(self.RecordingModel):
            def transcribe(self, audio_path, **kwargs):
                self.calls.append((audio_path, dict(kwargs)))
                if "carry_initial_prompt" in kwargs:
                    raise TypeError("transcribe() got an unexpected keyword argument 'carry_initial_prompt'")
                return {"text": "ok", "language": "en", "segments": []}

        model = RejectCarry()
        _result, features = transcribe_video(
            model, "audio.wav", use_cuda=False, initial_prompt="CUDA"
        )
        self.assertEqual(len(model.calls), 2)
        self.assertEqual(model.calls[-1][1].get("initial_prompt"), "CUDA")
        self.assertNotIn("carry_initial_prompt", model.calls[-1][1])
        self.assertTrue(features["initial_prompt_used"])
        self.assertFalse(features["carry_initial_prompt"])
        self.assertIn("carry_initial_prompt", features["compat_disabled"])


class SubtitleSidecarTests(unittest.TestCase):
    def test_srt_timestamps_follow_pause_sync_output_timeline(self):
        segments = [
            {"start": 0.0, "end": 1.0, "source": "Hello", "translated": "Привет"},
            {"start": 1.0, "end": 2.0, "source": "World", "translated": "Мир"},
        ]
        pauses = [{"at": 1.0, "duration": 0.75, "segments": [1]}]
        rendered = _render_srt(segments, "translated", pauses)

        self.assertIn("00:00:00,000 --> 00:00:01,750", rendered)
        self.assertIn("00:00:01,750 --> 00:00:02,750", rendered)
        self.assertIn("Привет", rendered)
        self.assertIn("Мир", rendered)

    def test_writes_distinct_source_and_translated_sidecars(self):
        segments = [{"start": 0.2, "end": 1.2, "source": "Hello", "translated": "Привет"}]
        with tempfile.TemporaryDirectory() as directory:
            video = Path(directory) / "movie_TR.mp4"
            paths = write_subtitle_files(
                str(video), segments, [], source_lang="en", target_info={"code": "ru", "name": "Русский"}
            )
            self.assertEqual(set(paths), {"source", "translated"})
            self.assertTrue(Path(paths["source"]).exists())
            self.assertTrue(Path(paths["translated"]).exists())
            self.assertIn("Hello", Path(paths["source"]).read_text(encoding="utf-8"))
            self.assertIn("Привет", Path(paths["translated"]).read_text(encoding="utf-8"))


class AdaptiveDuckingGraphTests(unittest.TestCase):
    def test_sidechain_graph_splits_voice_before_duck_and_mix(self):
        graph = build_original_voice_mix_tail(
            "[orig]", "[ru]", duck_original=True, label_prefix="test"
        )
        self.assertIn("[ru]asplit=2[test_sc][test_voice]", graph)
        self.assertIn("[orig][test_sc]sidechaincompress=", graph)
        self.assertIn("[test_orig][test_voice]amix=inputs=2", graph)
        self.assertIn("detection=rms", graph)

    def test_compatibility_graph_keeps_historical_amix_without_sidechain(self):
        graph = build_original_voice_mix_tail("[orig]", "[ru]", duck_original=False)
        self.assertNotIn("sidechaincompress", graph)
        self.assertEqual(graph.count("amix=inputs=2"), 1)

    def test_pause_sync_script_can_use_adaptive_ducking(self):
        with tempfile.TemporaryDirectory() as directory:
            script = make_filter_script_for_pauses(
                directory,
                [{"at": 1.0, "duration": 0.5, "segments": [1]}],
                3.0,
                3.5,
                True,
                15,
                duck_original=True,
                video_fps=25.0,
            )
            text = Path(script).read_text(encoding="utf-8")
        self.assertIn("sidechaincompress=", text)
        self.assertIn("[ru]asplit=2[pause_duck_sc][pause_duck_voice]", text)
        self.assertIn("tpad=stop_mode=clone:stop=13", text)
        self.assertNotIn("stop_duration=", text)

    @unittest.skipUnless(os.environ.get("VT_RUN_MEDIA_TESTS") == "1", "Set VT_RUN_MEDIA_TESTS=1")
    def test_real_ffmpeg_ducking_reduces_original_only_under_voice(self):
        ffmpeg = shutil.which("ffmpeg")
        if not ffmpeg:
            self.skipTest("ffmpeg not available")
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "duck.wav"
            graph = (
                "[0:a]volume=0.5,aformat=sample_fmts=fltp:channel_layouts=stereo[orig];"
                "[1:a][2:a][3:a]concat=n=3:v=0:a=1,"
                "aformat=sample_fmts=fltp:channel_layouts=stereo[voice];"
                + build_original_voice_mix_tail(
                    "[orig]", "[voice]", duck_original=True, label_prefix="real"
                )
            )
            command = [
                ffmpeg, "-y", "-v", "error",
                "-f", "lavfi", "-i", "sine=frequency=440:sample_rate=48000:duration=3",
                "-f", "lavfi", "-i", "anullsrc=r=48000:cl=mono:d=1",
                "-f", "lavfi", "-i", "sine=frequency=1000:sample_rate=48000:duration=1",
                "-f", "lavfi", "-i", "anullsrc=r=48000:cl=mono:d=1",
                "-filter_complex", graph, "-map", "[aout]",
                "-ar", "48000", "-ac", "2", str(output),
            ]
            result = subprocess.run(command, capture_output=True, text=True, timeout=30)
            self.assertEqual(result.returncode, 0, result.stderr[-1500:])

            with wave.open(str(output), "rb") as audio:
                rate = audio.getframerate()
                channels = audio.getnchannels()
                frames = audio.getnframes()
                raw = audio.readframes(frames)
            values = struct.unpack("<" + "h" * (len(raw) // 2), raw)
            mono = [values[index * channels] / 32768.0 for index in range(frames)]

            def projected_440_amplitude(start: float, end: float) -> float:
                first = int(start * rate)
                last = int(end * rate)
                count = max(1, last - first)
                sin_part = sum(
                    mono[index] * math.sin(2 * math.pi * 440 * index / rate)
                    for index in range(first, last)
                ) * 2 / count
                cos_part = sum(
                    mono[index] * math.cos(2 * math.pi * 440 * index / rate)
                    for index in range(first, last)
                ) * 2 / count
                return math.hypot(sin_part, cos_part)

            before = projected_440_amplitude(0.25, 0.75)
            under_voice = projected_440_amplitude(1.25, 1.75)
            after = projected_440_amplitude(2.25, 2.75)
            self.assertLess(under_voice, before * 0.75)
            self.assertAlmostEqual(after, before, delta=before * 0.15)


if __name__ == "__main__":
    unittest.main()
