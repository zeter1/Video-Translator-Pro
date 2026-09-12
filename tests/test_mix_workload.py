import math
import os
import shutil
import tempfile
import threading
import unittest
import wave
import array
from pathlib import Path
from unittest import mock
from videotranslator.pipeline import timeline_mix


class MixWorkloadTests(unittest.TestCase):
    def test_unknown_audio_format_keeps_full_duration(self):
        with tempfile.TemporaryDirectory() as directory:
            phrase = Path(directory) / 'unknown.mp3'
            phrase.write_bytes(b'unknown format')
            mixer = timeline_mix.TimelineMixMixin()
            mixer.log = lambda _: None
            mixer._check_cancel = lambda: None
            mixer._amix_batch = mock.Mock(return_value='batch.wav')
            mixer._amix_wavs = mock.Mock(return_value='final.wav')
            with mock.patch.object(timeline_mix, 'BATCH_SIZE', 2):
                mixer._mix_in_batches([(0, str(phrase))] * 3, 60.)
            self.assertEqual([c.args[1] for c in mixer._amix_batch.call_args_list], [60., 60.])

    def test_early_batch_does_not_render_unused_video_tail(self):
        with tempfile.TemporaryDirectory() as directory:
            phrase=Path(directory)/'phrase.wav'
            with wave.open(str(phrase),'wb') as audio:
                audio.setparams((2,2,44100,0,'NONE','not compressed'))
                audio.writeframes(b'\0'*44100*4)
            mixer=timeline_mix.TimelineMixMixin()
            mixer.log=lambda _:None
            mixer._check_cancel=lambda:None
            mixer._amix_batch=mock.Mock(side_effect=lambda segments,duration,tag:tag+'.wav')
            mixer._amix_wavs=mock.Mock(return_value='final.wav')
            with mock.patch.object(timeline_mix,'BATCH_SIZE',2):
                mixer._mix_in_batches([(0,str(phrase)),(2000,str(phrase)),(50000,str(phrase))],60.)
            self.assertLess(mixer._amix_batch.call_args_list[0].args[1],4.)
            self.assertEqual(mixer._amix_batch.call_args_list[1].args[1],60.)


@unittest.skipUnless(os.environ.get('VT_RUN_MEDIA_TESTS')=='1','Opt-in FFmpeg')
class MixEquivalenceTests(unittest.TestCase):
    def test_compact_batches_keep_final_pcm(self):
        self.check_mix(3)

    def test_multiple_merge_rounds_keep_final_pcm(self):
        self.check_mix(2)

    def check_mix(self, batch_size):
        with tempfile.TemporaryDirectory(prefix='vt_mix_speed_') as directory:
            root=Path(directory)
            mixer=timeline_mix.TimelineMixMixin()
            mixer.temp_dir=directory; mixer.ffmpeg=shutil.which('ffmpeg')
            self.assertTrue(mixer.ffmpeg)
            mixer.cancel=threading.Event(); mixer.log=lambda _:None; mixer._check_cancel=lambda:None
            segments=[]
            for index in range(6):
                phrase=root/f'phrase{index}.wav'
                data=array.array('h',(int(2000*math.sin(2*math.pi*(440+index*80)*i/44100))
                    for i in range(11025)))
                with wave.open(str(phrase),'wb') as audio:
                    audio.setparams((1,2,44100,0,'NONE','not compressed')); audio.writeframes(data.tobytes())
                segments.append((index*1000,str(phrase)))
            # Original algorithm: every intermediate spans the entire video.
            old=[mixer._amix_batch(segments[i:i+batch_size],8.,f'old{i}') for i in range(0,6,batch_size)]
            merged = old
            round_index = 0
            while len(merged) > batch_size:
                merged = [mixer._amix_wavs(merged[i:i+batch_size], 8., f'oldmerge{round_index}_{i}')
                          for i in range(0, len(merged), batch_size)]
                round_index += 1
            original=mixer._amix_wavs(merged,8.,'reference')
            with mock.patch.object(timeline_mix,'BATCH_SIZE',batch_size):
                optimized=mixer._mix_in_batches(segments,8.)
            with wave.open(original,'rb') as audio:
                reference=audio.readframes(audio.getnframes())
            with wave.open(optimized,'rb') as audio:
                result=audio.readframes(audio.getnframes())
            self.assertEqual(result,reference,'sample-exact final audio must be preserved')
            old_bytes=sum(Path(p).stat().st_size for p in old)
            new_bytes=sum((root/f'mix_batch_{i:03d}.wav').stat().st_size for i in range(1,len(old)+1))
            self.assertLess(new_bytes,old_bytes*.66)
