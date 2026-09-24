"""Opt-in checks of actual stop-frame placement, using synthetic colour changes."""
import json
import array
import math
import os
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

from videotranslator.sync.pause import make_filter_script_for_pauses


@unittest.skipUnless(os.environ.get("VT_RUN_MEDIA_TESTS") == "1", "Opt-in FFmpeg test")
class PauseMediaTests(unittest.TestCase):
    def test_pause_at_start_keeps_freeze_and_following_video(self):
        self._check(0.0)

    def test_pause_inside_first_frame_is_not_discarded(self):
        self._check(0.01)

    def test_middle_and_end_pauses_preserve_content(self):
        for position in (0.5, 4.0):
            with self.subTest(position=position):
                self._check(position)

    def _check(self, position):
        ffmpeg, ffprobe = shutil.which("ffmpeg"), shutil.which("ffprobe")
        self.assertTrue(ffmpeg and ffprobe)
        def run(args):
            return subprocess.run(args, capture_output=True, timeout=30, check=True)
        with tempfile.TemporaryDirectory(prefix="vt_pause_test_") as directory:
            root = Path(directory)
            source, voice, output = root / "source.mp4", root / "voice.wav", root / "out.mp4"
            run([ffmpeg, "-v", "error", "-nostdin", "-f", "lavfi", "-i",
                 "color=red:s=160x90:r=25:d=1[r];color=blue:s=160x90:r=25:d=3[b];[r][b]concat=n=2:v=1:a=0",
                 "-f", "lavfi", "-i", "sine=frequency=440:duration=4",
                 "-c:v", "libx264", "-preset", "ultrafast", "-c:a", "aac", str(source)])
            run([ffmpeg, "-v", "error", "-nostdin", "-f", "lavfi", "-i",
                 "anullsrc=r=48000:cl=stereo:d=5", str(voice)])
            script = make_filter_script_for_pauses(
                directory, [{"at": position, "duration": 1.0}],
                4.0, 5.0, True, 15, video_fps=25.0,
            )
            run([ffmpeg, "-v", "error", "-nostdin", "-i", str(source), "-i", str(voice),
                 "-filter_complex_script", script, "-map", "[vout]", "-map", "[aout]",
                 "-c:v", "libx264", "-preset", "ultrafast", "-c:a", "aac", "-t", "5", str(output)])
            streams = json.loads(run([ffprobe, "-v", "error", "-show_streams", "-of", "json",
                                      str(output)]).stdout)["streams"]
            for kind in ("video", "audio"):
                stream = next(s for s in streams if s["codec_type"] == kind)
                self.assertAlmostEqual(float(stream["duration"]), 5.0, delta=0.08)
            # The red-to-blue boundary must shift only when a freeze precedes it.
            boundary = 2.0 if position < 1.0 else 1.0
            for timestamp, channel in [(boundary - 0.3, 0), (boundary + 0.3, 2), (4.7, 2)]:
                pixels = run([ffmpeg, "-v", "error", "-nostdin", "-i", str(output),
                              "-ss", str(timestamp), "-frames:v", "1", "-vf", "scale=1:1",
                              "-pix_fmt", "rgb24", "-f", "rawvideo", "-"]).stdout
                self.assertEqual(len(pixels), 3)
                self.assertGreater(pixels[channel], 180, (timestamp, list(pixels)))
                self.assertLess(pixels[2 if channel == 0 else 0], 50)
            # The original background must pause too, rather than start early.
            for timestamp, silent in [(position + 0.3, True),
                                       (1.3 if position < 0.1 else 0.2, False)]:
                raw = run([ffmpeg, "-v", "error", "-nostdin", "-i", str(output),
                           "-ss", str(timestamp), "-t", "0.1", "-map", "0:a:0",
                           "-ac", "1", "-ar", "48000", "-f", "s16le", "-"]).stdout
                samples = array.array("h", raw)
                self.assertGreaterEqual(len(samples), 4700)
                rms = math.sqrt(sum(s * s for s in samples) / len(samples))
                if silent:
                    self.assertLess(rms, 10)
                else:
                    self.assertGreater(rms, 100)


if __name__ == "__main__":
    unittest.main()
