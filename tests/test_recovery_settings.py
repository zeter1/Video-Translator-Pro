import copy
import tempfile
import threading
import unittest
from pathlib import Path
from unittest import mock
import video_translator as vt
from videotranslator.ui import batch_start


class RecoverySettingsTests(unittest.TestCase):
    def test_changed_language_creates_new_pending_job_preserving_old_file(self):
        self._check('language')

    def test_changed_voice_creates_new_pending_job(self):
        self._check('voice')

    def test_identical_settings_preserve_recovery(self):
        self._check(None)

    def _check(self, change):
        with tempfile.TemporaryDirectory(prefix='vt_recovery_settings_') as directory:
            root=Path(directory)
            source=root/'source.mp4'; source.write_bytes(b'input')
            old=root/'old_RU.mp4'; old.write_bytes(b'old output must survive')
            language=vt.DEFAULT_TARGET_LANGUAGE
            voices=vt.get_voice_options(language)
            model_label=next(iter(vt.MODELS_MAP))
            settings={'language_label':language,'voice':next(iter(voices.values())),
                'model':vt.MODELS_MAP[model_label],'target_language':vt.get_target_language(language)['code'],
                'review_before_tts':False,'keep_original_audio':True,'original_volume_pct':15,
                'audio':vt.normalize_audio_settings()}
            state=vt.create_batch_recovery_state([str(source)],directory,settings)
            state['files'][0].update(status='processing',output_path=str(old))
            if change=='language':
                state['settings']['target_language']='de'
            if change=='voice':
                state['settings']['voice']='different old voice'
            original=copy.deepcopy(state)
            app=object.__new__(vt.App)
            app.video_files=[str(source)]; app._resume_batch_state=state
            app.file_logger=None; app.problem_logger_error=''; app._cancel_event=threading.Event()
            app._set_busy=mock.Mock(); app._log=mock.Mock(); app._problem=mock.Mock(); app._worker=mock.Mock()
            for name,value in {'combo_language':language,'combo_voice':next(iter(voices)),
                'combo_model':model_label,'var_keep':True,'var_volume':15,'var_review':False}.items():
                setattr(app,name,mock.Mock(get=mock.Mock(return_value=value)))
            app._get_audio_settings=mock.Mock(return_value=vt.normalize_audio_settings())
            with mock.patch.object(batch_start,'FileLogger'), mock.patch.object(batch_start,'save_batch_recovery_state'), \
                 mock.patch.object(batch_start,'get_batch_recovery_state_path',return_value=root/'state.json'), \
                 mock.patch.object(batch_start.threading,'Thread') as thread:
                app.start()
            current=thread.call_args.kwargs['args'][0]
            if change:
                self.assertNotEqual(current['batch_id'],original['batch_id'])
                self.assertEqual(current['files'][0]['output_path'],'')
                self.assertEqual(current['files'][0]['status'],'pending')
                self.assertEqual(state,original)
            else:
                self.assertEqual(current['batch_id'],original['batch_id'])
                self.assertEqual(current['files'][0]['output_path'],str(old))
            self.assertEqual(old.read_bytes(),b'old output must survive')
            self.assertEqual(current['settings'],settings)
