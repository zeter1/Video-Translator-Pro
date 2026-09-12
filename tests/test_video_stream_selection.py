"""Video selection excludes attached cover art in all assembly paths."""
import subprocess
import os
import shutil
import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock
from videotranslator.media import final_video
from videotranslator.sync.pause import make_filter_script_for_pauses


class VideoStreamSelectionTests(unittest.TestCase):
    @unittest.skipUnless(os.environ.get('VT_RUN_MEDIA_TESTS') == '1', 'Opt-in FFmpeg')
    def test_real_cover_is_excluded_from_output(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            ffmpeg, ffprobe = shutil.which('ffmpeg'), shutil.which('ffprobe')
            self.assertTrue(ffmpeg and ffprobe)
            def run(args):
                return subprocess.run(args, check=True, capture_output=True, timeout=30)
            cover, source, voice = root/'cover.jpg', root/'source.mp4', root/'voice.wav'
            run([ffmpeg, '-v', 'error', '-f', 'lavfi', '-i', 'color=red:s=160x90',
                 '-frames:v', '1', str(cover)])
            run([ffmpeg, '-v', 'error', '-i', str(cover), '-f', 'lavfi', '-i',
                 'color=blue:s=160x90:r=25:d=2', '-map', '0:v', '-map', '1:v',
                 '-c:v:0', 'copy', '-c:v:1', 'libx264', '-disposition:v:0', 'attached_pic', str(source)])
            streams = json.loads(run([ffprobe, '-v', 'error', '-show_streams', '-of', 'json', str(source)]).stdout)['streams']
            self.assertTrue(any(s.get('disposition', {}).get('attached_pic') for s in streams))
            run([ffmpeg, '-v', 'error', '-f', 'lavfi', '-i', 'sine=frequency=997:duration=2.5', str(voice)])
            for pause in (False, True):
                output = root/f'out{pause}.mp4'
                with mock.patch.object(final_video, 'final_video_encoder_attempts',
                                       return_value=[('test', ['-c:v', 'libx264', '-preset', 'ultrafast'])]):
                    final_video.assemble_final_video(ffmpeg, ffprobe, str(source), str(voice),
                        str(output), directory, 2., False, 0,
                        pause_plan=[{'at': 1., 'duration': .5}] if pause else [], final_dur=2.5 if pause else 2.)
                frame = run([ffmpeg, '-v', 'error', '-i', str(output), '-ss', '1.2', '-frames:v', '1',
                             '-vf', 'scale=1:1', '-pix_fmt', 'rgb24', '-f', 'rawvideo', '-']).stdout
                self.assertEqual(len(frame), 3)
                self.assertGreater(frame[2], frame[0] + 100, 'blue video must survive, not red cover')

    def test_copy_and_fallback_select_motion_video(self):
        with tempfile.TemporaryDirectory() as directory:
            commands = []
            def run(cmd, **kwargs):
                commands.append(cmd)
                return subprocess.CompletedProcess(cmd, 1, '', 'test failure')
            with mock.patch.object(final_video, 'run_subprocess', side_effect=run), \
                 mock.patch.object(final_video, 'final_video_encoder_attempts',
                                   return_value=[('test', ['-c:v', 'libx264'])]):
                with self.assertRaises(RuntimeError):
                    final_video.assemble_final_video('ffmpeg', 'ffprobe', 'input', 'voice',
                                                     str(Path(directory)/'out.mp4'), directory,
                                                     2., False, 0)
            self.assertEqual(len(commands), 2)
            for cmd in commands:
                self.assertEqual(cmd[cmd.index('-map') + 1], '0:V:0')

    def test_pause_fragments_select_same_motion_video(self):
        with tempfile.TemporaryDirectory() as directory:
            for pauses in ([], [{'at': 0., 'duration': .5}], [{'at': 1., 'duration': .5}]):
                path = make_filter_script_for_pauses(directory, pauses, 2., 2.5, False, 0)
                script = Path(path).read_text(encoding='utf-8')
                self.assertNotIn('[0:v]', script)
                self.assertIn('[0:V:0]', script)
