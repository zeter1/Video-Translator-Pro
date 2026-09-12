"""Saving errors must not launch another encoder. All files are temporary."""
import subprocess
import os
import shutil
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from videotranslator.media import final_video
import video_translator as vt


class FinalSaveFailureTests(unittest.TestCase):
    @unittest.skipUnless(os.environ.get('VT_RUN_MEDIA_TESTS') == '1', 'Opt-in FFmpeg')
    def test_real_mp4_collision_preserves_both_files(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            ffmpeg, ffprobe = shutil.which('ffmpeg'), shutil.which('ffprobe')
            self.assertTrue(ffmpeg and ffprobe)
            source, voice, output = root / 'source.mp4', root / 'voice.wav', root / 'out.mp4'
            subprocess.run([ffmpeg, '-v', 'error', '-nostdin', '-f', 'lavfi', '-i',
                            'color=c=blue:s=160x90:r=25:d=2', '-c:v', 'libx264',
                            '-preset', 'ultrafast', str(source)], check=True, capture_output=True, timeout=30)
            subprocess.run([ffmpeg, '-v', 'error', '-nostdin', '-f', 'lavfi', '-i',
                            'sine=frequency=997:duration=2', str(voice)],
                           check=True, capture_output=True, timeout=30)
            output.write_bytes(b'existing user file')
            with mock.patch.object(final_video, 'run_subprocess', wraps=final_video.run_subprocess) as runner:
                with self.assertRaises(final_video.FinalVideoSaveError) as raised:
                    final_video.assemble_final_video(ffmpeg, ffprobe, str(source), str(voice),
                                                     str(output), directory, 2., False, 0)
            self.assertEqual(runner.call_count, 1)
            self.assertEqual(output.read_bytes(), b'existing user file')
            candidate = raised.exception.candidate_path
            self.assertTrue(final_video.output_has_video_and_audio(ffprobe, candidate, expected_duration=2.))
            decoded = subprocess.run([ffmpeg, '-v', 'error', '-nostdin', '-i', candidate,
                                      '-map', '0:a:0', '-ac', '1', '-ar', '44100', '-f', 's16le', '-'],
                                     check=True, capture_output=True, timeout=30).stdout
            self.assertGreater(len(decoded), 44100 * 2 * 1.9)
            self.assertNotEqual(set(decoded), {0})

    def test_save_failure_survives_pipeline_and_later_cleanup(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            with mock.patch('videotranslator.tts.cache.get_tts_cache_dir', return_value=root / 'cache'):
                translator = vt.VideoTranslator(lambda _: None)
            work = root / 'work'
            work.mkdir()
            candidate = work / 'ready.mp4'
            candidate.write_bytes(b'validated fixture')
            translator.temp_dir = str(work)
            # Inject at an early boundary to exercise the real exception/finally
            # path without starting models, network or media subprocesses.
            translator._close_translation_client = mock.Mock(side_effect=[
                final_video.FinalVideoSaveError(str(candidate), PermissionError('locked')), None])
            self.assertFalse(translator.process('input.mp4', str(root / 'out.mp4'),
                                                'ru-RU-DmitryNeural', 'small', False, 0))
            self.assertEqual(candidate.read_bytes(), b'validated fixture')
            self.assertEqual(translator.temp_dir, '')
            translator.cleanup_temp()
            self.assertTrue(candidate.exists())


    def test_pause_encode_accepts_original_av_duration_skew_without_fallback(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            diagnostics = []
            seen_expectations = []

            def encode(cmd, **kwargs):
                candidate = Path(cmd[-1])
                candidate.write_bytes(b'validated candidate')
                return subprocess.CompletedProcess(cmd, 0, '', 'Late SEI is not implemented')

            def validate(_ffprobe, _path, **kwargs):
                seen_expectations.append((
                    kwargs.get('expected_video_duration'),
                    kwargs.get('expected_audio_duration'),
                ))
                return True

            source_timing = {
                'format_duration': 311.443,
                'video': {'duration': 310.800, 'start_time': 0.0},
                'audio': {'duration': 311.443, 'start_time': 0.0},
            }
            pause_total = 117.597
            with mock.patch.object(final_video, 'probe_media_timing', return_value=source_timing), \
                 mock.patch.object(final_video, 'run_subprocess', side_effect=encode) as runner, \
                 mock.patch.object(final_video, 'output_has_video_and_audio', side_effect=validate), \
                 mock.patch.object(final_video, 'make_filter_script_for_pauses', return_value='filter.txt'), \
                 mock.patch.object(final_video, 'final_video_encoder_attempts', return_value=[
                     ('libx264 quality', ['-c:v', 'libx264']),
                     ('MPEG-4 fallback', ['-c:v', 'mpeg4']),
                 ]):
                result = final_video.assemble_final_video(
                    'ffmpeg', 'ffprobe', 'source.mp4', 'voice.wav', str(root / 'out.mp4'),
                    directory, 311.443, False, 0,
                    pause_plan=[{'at': 100.0, 'duration': pause_total}],
                    final_dur=429.040,
                    diagnostic=lambda event, **details: diagnostics.append((event, details)),
                )

            self.assertEqual(result, str(root / 'out.mp4'))
            self.assertEqual(runner.call_count, 1, 'successful first encode must not start MPEG-4 fallback')
            self.assertTrue(seen_expectations)
            expected_video, expected_audio = seen_expectations[0]
            self.assertAlmostEqual(expected_video, 428.397, places=3)
            self.assertAlmostEqual(expected_audio, 429.040, places=3)
            timing_event = next(details for event, details in diagnostics if event == 'final_video_timing_expectation')
            self.assertAlmostEqual(timing_event['source_av_duration_delta_sec'], 0.643, places=3)
            self.assertTrue(any(event == 'video_encoder_selected' for event, _ in diagnostics))

    def test_copy_success_save_failure_does_not_reencode(self):
        self.check_save_failure(False)

    def test_pause_encode_success_save_failure_does_not_reencode(self):
        self.check_save_failure(True)

    def test_fallback_encode_save_failure_stops_encoder_loop(self):
        self.check_save_failure(False, fail_copy=True)

    def check_save_failure(self, pause, fail_copy=False):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            candidates = []
            def encode(cmd, **kwargs):
                path = Path(cmd[-1])
                if fail_copy and path.name == 'final_build_copy.mp4':
                    return subprocess.CompletedProcess(cmd, 1, '', 'copy unsupported')
                path.write_bytes(b'validated candidate')
                candidates.append(path)
                return subprocess.CompletedProcess(cmd, 0, '', '')
            with mock.patch.object(final_video, 'run_subprocess', side_effect=encode) as runner, \
                 mock.patch.object(final_video, 'output_has_video_and_audio', return_value=True), \
                 mock.patch.object(final_video, 'make_filter_script_for_pauses', return_value='filter.txt'), \
                 mock.patch.object(final_video, 'final_video_encoder_attempts', return_value=[
                     ('encoder1', ['-c:v', 'libx264']), ('encoder2', ['-c:v', 'libx264'])]), \
                 mock.patch.object(final_video, 'publish_video_candidate', side_effect=PermissionError('destination locked')):
                with self.assertRaises(final_video.FinalVideoSaveError) as raised:
                    final_video.assemble_final_video('ffmpeg', 'ffprobe', 'source.mp4', 'voice.wav',
                        str(root / 'out.mp4'), directory, 2., False, 0,
                        pause_plan=[{'at': 1., 'duration': .5}] if pause else [], final_dur=2.5 if pause else 2.)
            self.assertEqual(runner.call_count, 2 if fail_copy else 1,
                             'saving failure must not start another encoder')
            self.assertEqual(Path(raised.exception.candidate_path), candidates[0])
            self.assertTrue(candidates[0].exists())
