import tempfile
import unittest
from pathlib import Path
from unittest import mock
import video_translator as vt


class OverlappingSpeechTests(unittest.TestCase):
    def test_regular_gap_remains_available(self):
        spoken,target,hard=vt.calc_quality_slot([{'start':0,'end':1},{'start':3,'end':4}],0,4)
        self.assertEqual(spoken,1)
        self.assertAlmostEqual(hard,3-vt.MIN_SYNC_GAP)
        self.assertLess(target,hard)

    def test_same_start_has_no_free_time_before_next_phrase(self):
        _,_,hard=vt.calc_quality_slot([{'start':0,'end':3},{'start':0,'end':1}],0,4)
        self.assertEqual(hard,0)

    def test_overlapping_recognition_does_not_mix_two_phrases_at_once(self):
        with tempfile.TemporaryDirectory() as directory, mock.patch(
            'videotranslator.tts.cache.get_tts_cache_dir',return_value=Path(directory)):
            translator=vt.VideoTranslator(lambda _:None)
            translator.temp_dir=directory
            translator.speech_speed_limit=1.2
            translator._prepare_tts_segment=mock.Mock(side_effect=lambda text,voice,index,*args:
                (f'{index}.wav', 2.7 if index==1 else .8, {'tempo':1.,'total_speed':1.}))
            translator._mix_in_batches=mock.Mock(return_value='mixed.wav')
            with mock.patch.object(vt,'master_voice_audio',return_value='master.wav'):
                _,pauses,duration=translator.build_timeline([
                    {'start':0.,'end':3.,'translated':'Первая длинная фраза'},
                    {'start':2.,'end':4.,'translated':'Следующая фраза'},
                ],4.,'voice')
            placed=translator._mix_in_batches.call_args.args[0]
            self.assertGreaterEqual(placed[1][0]/1000,2.7)
            self.assertTrue(pauses)
            self.assertLessEqual(pauses[0]['at'],2.)
            self.assertAlmostEqual(duration,4+sum(p['duration'] for p in pauses))

    def test_nested_short_segment_merge_keeps_outer_end(self):
        merged=vt.merge_short_segments([
            {'start':0.,'end':1.4,'text':'outer'},
            {'start':.5,'end':1.,'text':'inner'},
        ],min_dur=1.5)
        self.assertEqual(len(merged),1)
        self.assertEqual(merged[0]['end'],1.4)
