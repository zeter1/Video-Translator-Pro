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


    def test_ffmpeg_t_limit_must_cap_pause_sync_video_expectation(self):
        # Regression for 2026-09-13: the pause-expanded video timeline was
        # 4644.067 s, but final assembly explicitly used ``-t 4643.757``.
        # The encoded streams (video 4643.766667 / audio 4643.757007) are valid
        # at that mux boundary and must not be rejected against the uncapped value.
        uncapped_video = 4644.066666333333
        final_dur = 4643.757
        expected_video = min(uncapped_video, final_dur)
        streams = [
            {"codec_type":"video", "duration":"4643.766667", "start_time":"0", "avg_frame_rate":"30/1"},
            {"codec_type":"audio", "duration":"4643.757007", "start_time":"0"},
        ]
        payload = json.dumps({"format":{"duration":"4643.766667"}, "streams":streams})
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory)/'out.mp4'
            path.write_bytes(b'x'*1200)
            fake = types.SimpleNamespace(returncode=0, stdout=payload, stderr='')
            with mock.patch.object(probe, "run_subprocess", return_value=fake):
                valid, details = probe.validate_output_media(
                    'mock', str(path),
                    expected_video_duration=expected_video,
                    expected_audio_duration=final_dur,
                )
        self.assertTrue(valid, details)
        self.assertEqual(details["reason"], "ok")
        self.assertAlmostEqual(expected_video, final_dur, places=6)

    def test_pause_sync_assembly_caps_validation_at_ffmpeg_t_limit(self):
        # The owner logic itself must pass the capped expectation to every validation
        # step, not merely rely on a permissive probe tolerance.
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / 'source.mp4'
            voice = root / 'voice.wav'
            output = root / 'out.mp4'
            source.write_bytes(b'source')
            voice.write_bytes(b'voice')
            seen = []

            def fake_run(cmd, **kwargs):
                Path(cmd[-1]).write_bytes(b'x' * 1200)
                return types.SimpleNamespace(returncode=0, stdout='', stderr='')

            def fake_validate(ffprobe, path, log=None, expected_duration=None,
                              expected_video_duration=None, expected_audio_duration=None):
                seen.append((expected_video_duration, expected_audio_duration))
                return True

            with mock.patch.object(final_video, 'probe_media_timing', return_value={
                    'video': {'duration': 10.0, 'fps': 30.0},
                    'audio': {'duration': 10.0},
                 }), \
                 mock.patch.object(final_video, 'make_filter_script_for_pauses',
                                   return_value=str(root / 'pause.txt')), \
                 mock.patch.object(final_video, 'final_video_encoder_attempts',
                                   return_value=[('test encoder', ['-c:v', 'libx264'])]), \
                 mock.patch.object(final_video, 'run_subprocess', side_effect=fake_run), \
                 mock.patch.object(final_video, 'output_has_video_and_audio', side_effect=fake_validate):
                result = final_video.assemble_final_video(
                    'ffmpeg', 'ffprobe', str(source), str(voice), str(output), directory,
                    video_dur=10.0, keep_original=False, orig_vol_pct=0,
                    pause_plan=[{'at': 5.0, 'duration': 1.0}], final_dur=10.5,
                )

            self.assertEqual(result, str(output))
            self.assertTrue(output.exists())
            self.assertGreaterEqual(len(seen), 2)
            self.assertTrue(all(abs(video - 10.5) < 1e-9 for video, _ in seen), seen)
            self.assertTrue(all(abs(audio - 10.5) < 1e-9 for _, audio in seen), seen)

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
