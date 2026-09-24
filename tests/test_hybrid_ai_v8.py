from __future__ import annotations

import tempfile
import threading
import unittest
from pathlib import Path
from unittest import mock

from videotranslator.models.catalog import get_catalog
from videotranslator.network.translation_circuit import SharedTranslationCircuit, TranslationProviderCoolingDownError
from videotranslator.pipeline.translator import VideoTranslator
from videotranslator.translation.local.manager import LocalResult


class _FakeLocalManager:
    def __init__(self, text: str, engine: str = "fake_local"):
        self.text = text
        self.engine = engine
        self.auto_install_pairs = False

    def has_local_route(self, source, target):
        return True

    def translate(self, text, source, target):
        return LocalResult(self.text, self.engine, 0.9)


class HybridTranslationTests(unittest.TestCase):
    def _translator(self):
        translator = VideoTranslator(lambda _msg: None, cancel_event=threading.Event())
        translator.target_info = {"code": "ru"}
        translator.source_language = "en"
        translator.hybrid_translation_settings = {
            "local_first": True,
            "auto_install_argos_pairs": False,
            "local_piper_fallback": True,
            "preferred_piper_voice": "piper-dmitri-ru",
        }
        return translator

    def test_good_local_translation_does_not_call_google(self):
        translator = self._translator()
        translator.local_translation_manager = _FakeLocalManager("Это полностью локальный перевод предложения.")
        translator._translate_google_segment = mock.Mock(side_effect=AssertionError("Google must not be called"))

        value = translator.translate_segment("This is a completely local sentence.", index=1)

        self.assertEqual(value, "Это полностью локальный перевод предложения.")
        translator._translate_google_segment.assert_not_called()

    def test_suspicious_local_translation_gets_selective_google_repair(self):
        translator = self._translator()
        translator.local_translation_manager = _FakeLocalManager("This sentence was not translated locally at all")
        translator._translate_google_segment = mock.Mock(return_value="Это исправленный перевод Google")

        value = translator.translate_segment("This sentence was not translated locally at all", index=2)

        self.assertEqual(value, "Это исправленный перевод Google")
        translator._translate_google_segment.assert_called_once()

    def test_google_repair_failure_keeps_usable_local_candidate(self):
        translator = self._translator()
        translator.local_translation_manager = _FakeLocalManager("Перевод есть, but several words remain untranslated")
        translator._translate_google_segment = mock.Mock(side_effect=TimeoutError("offline"))

        value = translator.translate_segment("The local translator missed several words", index=3)

        self.assertEqual(value, "Перевод есть, but several words remain untranslated")

    def test_same_source_and_target_language_skips_translation_provider(self):
        translator = self._translator()
        translator.source_language = "ru"
        translator._translate_google_segment = mock.Mock(side_effect=AssertionError("Google must not be called"))
        translator._get_local_translation_manager = mock.Mock(side_effect=AssertionError("local translator must not be called"))

        source = "Это уже русский текст с API и Python."
        self.assertEqual(translator.translate_segment(source, index=4), source)
        translator._translate_google_segment.assert_not_called()

    def test_same_language_batch_is_identity_without_any_translator(self):
        translator = self._translator()
        translator.source_language = "ru"
        translator._get_local_translation_manager = mock.Mock(side_effect=AssertionError("manager must not be created"))
        translator._translate_google_records_batch = mock.Mock(side_effect=AssertionError("Google must not be called"))
        rows = [(1, {"source": "Первая фраза", "translated": ""}), (2, {"source": "Вторая фраза", "translated": ""})]

        results, failures = translator.translate_records_batch(rows)

        self.assertFalse(failures)
        self.assertEqual(results, {1: "Первая фраза", 2: "Вторая фраза"})
        self.assertEqual(rows[0][1]["translated"], "Первая фраза")
        self.assertEqual(rows[1][1]["translated"], "Вторая фраза")

    def test_batch_repairs_only_suspicious_rows(self):
        translator = self._translator()

        class PerTextManager(_FakeLocalManager):
            def translate(self, text, source, target):
                if "good" in text:
                    return LocalResult("Хороший локальный перевод.", "fake_local", 0.9)
                return LocalResult("bad segment remains in English words", "fake_local", 0.9)

        translator.local_translation_manager = PerTextManager("")
        translator._translate_google_records_batch = mock.Mock(
            return_value=({2: "Исправленный второй сегмент"}, [])
        )
        rows = [
            (1, {"source": "good source sentence", "translated": ""}),
            (2, {"source": "bad source sentence", "translated": ""}),
        ]

        results, failures = translator.translate_records_batch(rows)

        self.assertFalse(failures)
        self.assertEqual(results[1], "Хороший локальный перевод.")
        self.assertEqual(results[2], "Исправленный второй сегмент")
        repair_rows = translator._translate_google_records_batch.call_args.args[0]
        self.assertEqual([index for index, _record in repair_rows], [2])


class SharedCircuitTests(unittest.TestCase):
    def test_batch_circuit_blocks_until_cooldown(self):
        now = [100.0]
        circuit = SharedTranslationCircuit(clock=lambda: now[0])
        circuit.mark_failure("rate_limit", 60)
        with self.assertRaises(TranslationProviderCoolingDownError):
            circuit.assert_available()
        now[0] = 161.0
        circuit.assert_available()


class CatalogTests(unittest.TestCase):
    def test_curated_catalog_has_sizes_and_multiple_russian_voices(self):
        rows = get_catalog()
        voices = [row for row in rows if row.get("type") == "voice"]
        translators = [row for row in rows if row.get("type") == "translation"]
        self.assertGreaterEqual(len(voices), 4)
        self.assertGreaterEqual(len(translators), 2)
        for row in rows:
            self.assertGreater(float(row["size_download_mb"]), 0)
            self.assertGreater(float(row["size_disk_mb"]), 0)


if __name__ == "__main__":
    unittest.main()
