"""Opt-in FFmpeg content tests using synthetic media only."""
import array
import json
import math
import os
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from videotranslator.media import final_video


@unittest.skipUnless(os.environ.get("VT_RUN_MEDIA_TESTS") == "1",
                     "Set VT_RUN_MEDIA_TESTS=1 for synthetic FFmpeg checks")
class FinalAudioIntegrationTests(unittest.TestCase):
    def test_incomplete_voice_is_not_published_as_success(self):
        self._check_incomplete(4, 1)

    def test_incomplete_video_is_not_published_as_success(self):
        self._check_incomplete(1, 4)

    def _check_incomplete(self, video_seconds, voice_seconds):
        ffmpeg, ffprobe = shutil.which("ffmpeg"), shutil.which("ffprobe")
        self.assertTrue(ffmpeg and ffprobe)
        with tempfile.TemporaryDirectory(prefix="vt_incomplete_") as directory:
            root = Path(directory)
            source, voice, output = root / "source.mp4", root / "voice.wav", root / "out.mp4"
            subprocess.run([ffmpeg, "-v", "error", "-nostdin", "-f", "lavfi", "-i",
                            f"color=c=blue:s=160x90:r=25:d={video_seconds}", "-c:v", "libx264",
                            "-preset", "ultrafast", str(source)], check=True, capture_output=True, timeout=30)
            subprocess.run([ffmpeg, "-v", "error", "-nostdin", "-f", "lavfi", "-i",
                            f"sine=frequency=997:sample_rate=48000:duration={voice_seconds}", str(voice)],
                           check=True, capture_output=True, timeout=30)
            with mock.patch.object(final_video, "final_video_encoder_attempts", return_value=[
                ("libx264 test", ["-c:v", "libx264", "-preset", "ultrafast"])
            ]):
                with self.assertRaises(RuntimeError):
                    final_video.assemble_final_video(ffmpeg, ffprobe, str(source), str(voice),
                                                     str(output), directory, 4.0, False, 0, final_dur=4.0)
            self.assertFalse(output.exists(), "incomplete candidate must remain staged")

    def test_short_original_keeps_late_translation_copy(self):
        self._check_audio(keep_original=True)

    def test_short_original_keeps_late_translation_reencode(self):
        self._check_audio(keep_original=True, reencode=True)

    def test_translation_only_keeps_late_translation(self):
        self._check_audio(keep_original=False)

    def _check_audio(self, keep_original, reencode=False):
        ffmpeg, ffprobe = shutil.which("ffmpeg"), shutil.which("ffprobe")
        self.assertTrue(ffmpeg and ffprobe, "FFmpeg and ffprobe are required")
        def run(args):
            return subprocess.run(args, capture_output=True, check=True, timeout=30)
        with tempfile.TemporaryDirectory(prefix="vt_media_") as directory:
            root = Path(directory)
            source, voice, output = root / "source.mp4", root / "voice.wav", root / "out.mp4"
            run([ffmpeg, "-v", "error", "-nostdin", "-f", "lavfi", "-i",
                 "color=c=blue:s=160x90:r=25:d=4", "-f", "lavfi", "-i",
                 "anullsrc=r=48000:cl=stereo:d=1", "-c:v", "libx264",
                 "-preset", "ultrafast", "-c:a", "aac", str(source)])
            # Distinct late voice signal, after the original audio has ended.
            run([ffmpeg, "-v", "error", "-nostdin", "-f", "lavfi", "-i",
                 "sine=frequency=997:sample_rate=48000:duration=4", "-af",
                 "volume=0:enable='lt(t,2)'", "-c:a", "pcm_s16le", str(voice)])
            real_run = final_video.run_subprocess
            def controlled_run(cmd, **kwargs):
                if reencode and Path(cmd[-1]).name == "final_build_copy.mp4":
                    return subprocess.CompletedProcess(cmd, 1, "", "forced copy rejection")
                return real_run(cmd, **kwargs)
            with mock.patch.object(final_video, "run_subprocess", side_effect=controlled_run), \
                 mock.patch.object(final_video, "final_video_encoder_attempts", return_value=[
                     ("libx264 test", ["-c:v", "libx264", "-preset", "ultrafast", "-pix_fmt", "yuv420p"])
                 ]):
                final_video.assemble_final_video(
                    ffmpeg, ffprobe, str(source), str(voice), str(output), directory,
                    4.0, keep_original, 15, final_dur=4.0)
            streams = json.loads(run([ffprobe, "-v", "error", "-show_streams",
                                      "-of", "json", str(output)]).stdout)["streams"]
            for kind in ("video", "audio"):
                selected = next(s for s in streams if s["codec_type"] == kind)
                self.assertAlmostEqual(float(selected["duration"]), 4.0, delta=0.06)
            decoded = run([ffmpeg, "-v", "error", "-nostdin", "-i", str(output),
                           "-ss", "3", "-t", "0.5", "-map", "0:a:0", "-ac", "1",
                           "-ar", "48000", "-f", "s16le", "-"]).stdout
            samples = array.array("h", decoded)
            self.assertGreaterEqual(len(samples), 23500, "late translated audio is missing")
            rms = math.sqrt(sum(s * s for s in samples) / len(samples))
            self.assertGreater(rms, 500, "late translated signal was replaced with silence")


if __name__ == "__main__":
    unittest.main()
