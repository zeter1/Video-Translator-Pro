import array
import math
import os
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from videotranslator.media import probe, final_video


@unittest.skipUnless(os.environ.get("VT_RUN_MEDIA_TESTS") == "1", "Opt-in FFmpeg")
class AudioSelectionTests(unittest.TestCase):
    def test_default_track_matches_extraction_and_both_mux_paths(self):
        self._check(True)

    def test_without_default_first_track_matches_both_mux_paths(self):
        self._check(False)

    def _check(self, default):
        ffmpeg, ffprobe = shutil.which("ffmpeg"), shutil.which("ffprobe")
        self.assertTrue(ffmpeg and ffprobe)
        def run(args):
            return subprocess.run(args, capture_output=True, check=True, timeout=30)
        def samples(path, start):
            return array.array("h", run([ffmpeg, "-v", "error", "-i", str(path), "-ss", str(start),
                "-t", "0.2", "-map", "0:a:0", "-ac", "1", "-ar", "16000", "-f", "s16le", "-"]).stdout)
        def assert_tone(path, start, frequency):
            data = samples(path, start)
            self.assertGreater(len(data), 3000)
            def strength(hz):
                return abs(sum(value * complex(math.cos(2*math.pi*hz*i/16000),
                    math.sin(2*math.pi*hz*i/16000)) for i, value in enumerate(data))) / len(data)
            self.assertGreater(strength(frequency), 100)
            self.assertGreater(strength(frequency), 10 * strength(440 if frequency == 880 else 880))
        with tempfile.TemporaryDirectory(prefix="vt_streams_") as directory:
            root = Path(directory)
            source, voice, extracted = root/'source.mkv', root/'voice.wav', root/'extracted.wav'
            run([ffmpeg, "-v", "error", "-f", "lavfi", "-i", "color=blue:s=160x90:r=25:d=2",
                 "-f", "lavfi", "-i", "sine=frequency=440:duration=2",
                 "-f", "lavfi", "-i", "sine=frequency=880:duration=2", "-map", "0:v", "-map", "1:a",
                 "-map", "2:a", "-c:v", "libx264", "-preset", "ultrafast", "-c:a", "pcm_s16le",
                 "-disposition:a:0", "0", "-disposition:a:1", "default" if default else "0", str(source)])
            index = probe.select_audio_stream(ffprobe, str(source))
            self.assertEqual(index, 2 if default else 1)
            self.assertTrue(probe.extract_audio_for_whisper(ffmpeg, str(source), str(extracted), audio_stream_index=index))
            frequency = 880 if default else 440
            assert_tone(extracted, 0.2, frequency)
            run([ffmpeg, "-v", "error", "-f", "lavfi", "-i", "anullsrc=r=48000:cl=stereo:d=2.5", str(voice)])
            for pause in (False, True):
                output = root / f'out-{pause}.mp4'
                with mock.patch.object(final_video, "final_video_encoder_attempts", return_value=[
                    ("test", ["-c:v", "libx264", "-preset", "ultrafast"])]):
                    final_video.assemble_final_video(ffmpeg, ffprobe, str(source), str(voice), str(output),
                        directory, 2, True, 30, pause_plan=[{"at":1,"duration":0.5}] if pause else [],
                        final_dur=2.5 if pause else 2, audio_stream_index=index)
                assert_tone(output, 0.2, frequency)
                assert_tone(output, 1.7 if pause else 1.2, frequency)
                if pause:
                    self.assertLess(max(abs(s) for s in samples(output, 1.15)), 10)
