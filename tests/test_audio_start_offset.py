import os
import shutil
import subprocess
import tempfile
import unittest
import wave
import array
import threading
from unittest import mock
from pathlib import Path
from videotranslator.media import probe, final_video
from videotranslator.core.cancel import CancelledError


@unittest.skipUnless(os.environ.get("VT_RUN_MEDIA_TESTS") == "1", "Opt-in FFmpeg")
class AudioStartOffsetTests(unittest.TestCase):
    def test_delayed_audio_preserves_initial_silence_for_whisper(self):
        self._check(1.0)

    def test_zero_offset_audio_keeps_its_start(self):
        self._check(0.0)

    def _check(self, delay):
        ffmpeg, ffprobe = shutil.which('ffmpeg'), shutil.which('ffprobe')
        self.assertTrue(ffmpeg and ffprobe)
        with tempfile.TemporaryDirectory(prefix='vt_offset_') as directory:
            source, output = Path(directory)/'source.mkv', Path(directory)/'extracted.wav'
            subprocess.run([ffmpeg, '-v', 'error', '-f', 'lavfi', '-i', 'color=blue:s=160x90:r=25:d=3',
                '-itsoffset', str(delay), '-f', 'lavfi', '-i', 'sine=frequency=440:duration=2',
                '-map','0:v','-map','1:a','-c:v','libx264','-preset','ultrafast','-c:a','pcm_s16le',str(source)],
                check=True,capture_output=True,timeout=30)
            self.assertTrue(probe.extract_audio_for_whisper(ffmpeg,str(source),str(output),
                audio_stream_index=probe.select_audio_stream(ffprobe,str(source))))
            with wave.open(str(output),'rb') as audio:
                rate=audio.getframerate()
                data=array.array('h',audio.readframes(audio.getnframes()))
            self.assertAlmostEqual(len(data)/rate, 2+delay, delta=0.03)
            if delay:
                self.assertLess(max(abs(x) for x in data[int(.1*rate):int(.8*rate)]),10)
            self.assertGreater(max(abs(x) for x in data[int((delay+.1)*rate):int((delay+.5)*rate)]),1000)
            if delay:
                voice=Path(directory)/'voice.wav'
                subprocess.run([ffmpeg,'-v','error','-f','lavfi','-i','anullsrc=r=48000:cl=stereo:d=3.5',str(voice)],
                    check=True,capture_output=True,timeout=30)
                for pause in (False,True):
                    result=Path(directory)/f'out-{pause}.mp4'
                    with mock.patch.object(final_video,'final_video_encoder_attempts',return_value=[
                        ('test',['-c:v','libx264','-preset','ultrafast'])]):
                        final_video.assemble_final_video(ffmpeg,ffprobe,str(source),str(voice),str(result),directory,
                            3,True,30,pause_plan=[{'at':.5,'duration':.5}] if pause else [],
                            final_dur=3.5 if pause else 3,audio_stream_index=1)
                    for start,silent in ((.1,True),(1.1,True)) if pause else ((.1,True),(1.1,False)):
                        decoded=subprocess.run([ffmpeg,'-v','error','-i',str(result),'-ss',str(start),'-t','0.2',
                            '-map','0:a:0','-ac','1','-ar','16000','-f','s16le','-'],capture_output=True,check=True,timeout=30)
                        values=array.array('h',decoded.stdout)
                        self.assertGreater(len(values),3000)
                        if silent:
                            self.assertLess(max(abs(x) for x in values),10)
                        else:
                            self.assertGreater(max(abs(x) for x in values),500)
                    decoded=subprocess.run([ffmpeg,'-v','error','-i',str(result),'-ss','2','-t','0.2',
                        '-map','0:a:0','-ac','1','-ar','16000','-f','s16le','-'],capture_output=True,check=True,timeout=30)
                    self.assertGreater(max(abs(x) for x in array.array('h',decoded.stdout)),500)


class ProbeCancellationTests(unittest.TestCase):
    def test_cancel_before_probe_does_not_start_process(self):
        event=threading.Event()
        event.set()
        with mock.patch.object(probe,'run_subprocess') as run:
            with self.assertRaises(CancelledError):
                probe.select_audio_stream('unused','unused',cancel_event=event)
            run.assert_not_called()

    def test_cancel_during_probe_propagates(self):
        event=threading.Event()
        def cancelled(*args,**kwargs):
            self.assertIs(kwargs['cancel_event'],event)
            raise CancelledError()
        with mock.patch.object(probe,'run_subprocess',side_effect=cancelled):
            with self.assertRaises(CancelledError):
                probe.select_audio_stream('unused','unused',cancel_event=event)

