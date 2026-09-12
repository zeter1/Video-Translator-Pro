import subprocess
import os
import shutil
import unittest
from unittest import mock
from videotranslator.media import audio
from videotranslator.core.cancel import CancelledError


INVENTORY = 'Encoders:\n V..... h264_nvenc NVIDIA\n V..... h264_amf AMD\n V..... h264_qsv Intel\n A..... aac AAC\n'


class EncoderInventoryTests(unittest.TestCase):
    @unittest.skipUnless(os.environ.get('VT_RUN_MEDIA_TESTS') == '1', 'Opt-in installed FFmpeg')
    def test_installed_ffmpeg_inventory_is_reused(self):
        ffmpeg = shutil.which('ffmpeg')
        self.assertTrue(ffmpeg)
        with mock.patch.object(audio, 'run_subprocess', wraps=audio.run_subprocess) as run:
            self.assertTrue(audio.ffmpeg_has_encoder(ffmpeg, 'libx264'))
            self.assertFalse(audio.ffmpeg_has_encoder(ffmpeg, 'vt_nonexistent_codec'))
            self.assertEqual(run.call_count, 1)

    def setUp(self):
        self.patch = mock.patch.object(audio.ffmpeg_has_encoder, '_cache', {}, create=True)
        self.patch.start()
        self.addCleanup(self.patch.stop)

    def test_three_candidates_share_one_probe(self):
        with mock.patch.object(audio, 'run_subprocess', return_value=subprocess.CompletedProcess([], 0, INVENTORY, '')) as run:
            for name in ('h264_nvenc', 'h264_amf', 'h264_qsv'):
                self.assertTrue(audio.ffmpeg_has_encoder('ffmpeg', name))
            self.assertFalse(audio.ffmpeg_has_encoder('ffmpeg', 'unknown_codec'))
            self.assertEqual(run.call_count, 1)

    def test_failed_probe_is_not_cached_as_missing_encoder(self):
        with mock.patch.object(audio, 'run_subprocess', side_effect=[
            subprocess.CompletedProcess([], 1, '', 'temporary failure'),
            subprocess.CompletedProcess([], 0, INVENTORY, '')]):
            self.assertFalse(audio.ffmpeg_has_encoder('ffmpeg', 'h264_nvenc'))
            self.assertTrue(audio.ffmpeg_has_encoder('ffmpeg', 'h264_nvenc'))

    def test_cancel_is_not_converted_to_missing_encoder(self):
        with mock.patch.object(audio, 'run_subprocess', side_effect=CancelledError()):
            with self.assertRaises(CancelledError):
                audio.ffmpeg_has_encoder('ffmpeg', 'h264_nvenc')

    def test_names_in_descriptions_do_not_count_as_encoders(self):
        with mock.patch.object(audio, 'run_subprocess', return_value=subprocess.CompletedProcess(
            [], 0, ' V..... libx264 description mentions h264_nvenc\n', '')):
            self.assertFalse(audio.ffmpeg_has_encoder('ffmpeg', 'h264_nvenc'))

    def test_unparseable_success_is_not_cached(self):
        with mock.patch.object(audio, 'run_subprocess', side_effect=[
            subprocess.CompletedProcess([], 0, 'Encoders:\n V..... = Video\n', ''),
            subprocess.CompletedProcess([], 0, INVENTORY, '')]):
            self.assertFalse(audio.ffmpeg_has_encoder('ffmpeg', 'h264_amf'))
            self.assertTrue(audio.ffmpeg_has_encoder('ffmpeg', 'h264_amf'))
