"""Offline translation guards: no cache, model, GUI or network is created."""
import unittest
from unittest import mock

from videotranslator.config import TRANSLATION_BATCH_MAX_CHARS
from videotranslator.core.cancel import CancelledError
from videotranslator.pipeline.translation import TranslationMixin
from videotranslator.translation.batching import split_translation_text


class LongTranslationTests(unittest.TestCase):
    def test_split_preserves_characters_and_length_boundaries(self):
        limit = TRANSLATION_BATCH_MAX_CHARS
        for text in ('', 'a' * limit, '中' * (limit + 1),
                     ('hello\nмир\t世界 ' * 900), 'a' + ' ' * (limit * 2) + 'b'):
            with self.subTest(length=len(text)):
                parts = split_translation_text(text)
                self.assertEqual(''.join(parts), text)
                self.assertTrue(all(0 < len(part) <= limit for part in parts))

    def test_retry_of_later_part_does_not_repeat_completed_part(self):
        translator, client = self.make_translator(None)
        client.translate.side_effect = ['начало', RuntimeError('temporary'), 'конец']
        text = 'a' * TRANSLATION_BATCH_MAX_CHARS + 'z' * 20
        self.assertEqual(translator.translate_segment(text, attempts=2), 'начало конец')
        self.assertEqual([c.args[0][0] for c in client.translate.call_args_list], ['a', 'z', 'z'])
        self.assertEqual(translator._sleep_or_cancel.call_count, 1)

    def make_translator(self, translate):
        translator = TranslationMixin()
        translator._check_cancel = mock.Mock()
        translator._sleep_or_cancel = mock.Mock()
        translator.translation_network_guard = mock.Mock()
        translator.target_info = {'code': 'ru'}
        translator._problem = mock.Mock()
        translator.log = mock.Mock()
        client = mock.Mock()
        client.translate.side_effect = translate
        translator._get_translation_client = lambda: client
        return translator, client

    def test_long_single_record_uses_bounded_requests(self):
        def translate(text):
            if len(text) > TRANSLATION_BATCH_MAX_CHARS:
                raise ValueError('request too long')
            return text.replace('hello', 'привет')
        translator, client = self.make_translator(translate)
        record = {'source': 'hello ' * 1100}
        result, failures = translator.translate_records_batch([(1, record)], attempts=2)
        self.assertEqual(failures, [])
        self.assertEqual(result[1].split(), ['привет'] * 1100)
        self.assertEqual(record['translated'], result[1])
        self.assertEqual(client.translate.call_count, 2)
        translator._sleep_or_cancel.assert_not_called()
        translator.translation_network_guard.record_failure.assert_not_called()

    def test_later_chunk_failure_does_not_publish_partial_record(self):
        def translate(text):
            if text.startswith('z'):
                raise RuntimeError('offline')
            return 'первая часть'
        translator, _ = self.make_translator(translate)
        record = {'source': 'a' * TRANSLATION_BATCH_MAX_CHARS + 'z' * 20}
        result, failures = translator.translate_records_batch([(1, record)], attempts=1)
        self.assertEqual(result, {})
        self.assertEqual(len(failures), 1)
        self.assertNotIn('translated', record)

    def test_cancellation_between_chunks_is_propagated(self):
        translator, client = self.make_translator(lambda text: 'перевод')
        translator._check_cancel.side_effect = [None, CancelledError('stop')]
        with self.assertRaises(CancelledError):
            translator.translate_segment('a' * (TRANSLATION_BATCH_MAX_CHARS + 10))
        self.assertEqual(client.translate.call_count, 1)
