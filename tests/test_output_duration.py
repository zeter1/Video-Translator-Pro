import json
import tempfile
import types
import unittest
from pathlib import Path
from unittest import mock
from videotranslator.media import final_video, probe


class OutputDurationTests(unittest.TestCase):
    def test_invalid_stream_timing_is_rejected(self):
        for update in ({"duration":"1"}, {"start_time":"2"}, {"duration":"nan"},
                       {"duration":"N/A"}, {"duration":"inf"}):
            with self.subTest(update=update):
                self.assertFalse(self._validate(update))

    def test_small_codec_padding_is_allowed(self):
        self.assertTrue(self._validate({"duration":"4.02"}))


    def test_separate_video_and_audio_expectations_allow_source_tail_skew(self):
        streams = [
            {"codec_type":"video", "duration":"428.400", "start_time":"0", "avg_frame_rate":"25/1"},
            {"codec_type":"audio", "duration":"429.040", "start_time":"0"},
        ]
        payload = json.dumps({"format":{"duration":"429.040"}, "streams":streams})
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory)/'out.mp4'
            path.write_bytes(b'x'*1200)
            fake = types.SimpleNamespace(returncode=0, stdout=payload, stderr='')
            with mock.patch.object(probe, "run_subprocess", return_value=fake):
                valid, details = probe.validate_output_media(
                    'mock', str(path),
                    expected_video_duration=428.400,
                    expected_audio_duration=429.040,
                )
            self.assertTrue(valid, details)
            self.assertEqual(details["reason"], "ok")

        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory)/'out.mp4'
            path.write_bytes(b'x'*1200)
            fake = types.SimpleNamespace(returncode=0, stdout=payload, stderr='')
            with mock.patch.object(probe, "run_subprocess", return_value=fake):
                valid, details = probe.validate_output_media('mock', str(path), expected_duration=429.040)
            self.assertFalse(valid)
            self.assertEqual(details["reason"], "video_duration_mismatch")


    def test_pause_sync_video_expectation_matches_ffmpeg_frame_quantization(self):
        # Reproduces the 2026-09-12 LARK failure class: many tpad durations are
        # rounded independently to 25 fps frames, so their raw seconds cannot be
        # added directly to the expected video duration.
        pauses = [
            3.73, 2.45, 3.01, 2.13, 0.21, 1.41, 2.93, 2.85, 3.65, 1.73,
            3.33, 1.89, 2.53, 2.29, 1.81, 2.45, 1.89, 2.37, 2.61, 2.45,
            3.01, 2.21, 2.61, 2.69, 2.77, 2.69, 2.45, 4.13, 1.73, 1.97,
            0.93, 2.77, 2.45, 0.93, 2.45, 2.05, 2.37, 2.61, 2.21, 3.73,
            2.13, 2.93, 2.37, 3.09, 3.89, 3.65, 2.96,
        ]
        effective, frames = final_video._quantize_pause_total_to_video_frames(
            [{"duration": value} for value in pauses], 25.0
        )
        self.assertEqual(frames, 2926)
        self.assertAlmostEqual(effective, 117.04, places=6)
        self.assertAlmostEqual(311.36 + effective, 428.40, places=6)
        self.assertGreater(sum(pauses) - effective, 0.40)

    def _validate(self, update):
        streams = [{"codec_type":"video", "duration":"4", "start_time":"0", "avg_frame_rate":"25/1"},
                   {"codec_type":"audio", "duration":"4", "start_time":"0"}]
        streams[1].update(update)
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory)/'out.mp4'
            path.write_bytes(b'x'*1200)
            with mock.patch.object(probe, "run_subprocess", return_value=types.SimpleNamespace(
                returncode=0, stdout=json.dumps({"streams":streams}))):
                return probe.output_has_video_and_audio('mock', str(path), expected_duration=4)


class StreamPolicyTests(unittest.TestCase):
    def test_single_stream_and_no_stream(self):
        with mock.patch.object(probe, "run_subprocess", return_value=types.SimpleNamespace(
            returncode=0, stdout=json.dumps({"streams":[{"index":3}]}))):
            self.assertEqual(probe.select_audio_stream('mock','unused'),3)
        with mock.patch.object(probe, "run_subprocess", return_value=types.SimpleNamespace(returncode=0,stdout='{}')):
            with self.assertRaises(RuntimeError):
                probe.select_audio_stream('mock','unused')
