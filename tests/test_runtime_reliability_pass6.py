import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from videotranslator.core import io as core_io
from videotranslator.core import paths
from videotranslator.translation.glossary import Glossary
from videotranslator.translation import report as translation_report
from videotranslator.ui.settings import UISettingsMixin


class RuntimePathFallbackTests(unittest.TestCase):
    def test_runtime_directory_falls_back_when_program_location_is_not_writable_directory(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            fake_program_path = root / "VideoTranslatorPRO.exe"
            fake_program_path.write_bytes(b"not-a-directory")
            fallback_root = root / "user-runtime"

            with mock.patch.dict(os.environ, {"VIDEO_TRANSLATOR_RUNTIME_DIR": ""}, clear=False), \
                    mock.patch.object(paths, "get_program_dir", return_value=fake_program_path), \
                    mock.patch.object(paths, "_fallback_runtime_root", return_value=fallback_root):
                resolved = paths.get_logs_dir()

            self.assertEqual(fallback_root / "logs", resolved)
            self.assertTrue(resolved.is_dir())

    def test_runtime_directory_keeps_portable_location_when_writable(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            program_dir = Path(temp_dir) / "portable"
            with mock.patch.dict(os.environ, {"VIDEO_TRANSLATOR_RUNTIME_DIR": ""}, clear=False), \
                    mock.patch.object(paths, "get_program_dir", return_value=program_dir):
                resolved = paths.get_tts_cache_dir()

            self.assertEqual(program_dir / "tts_cache", resolved)
            self.assertTrue(resolved.is_dir())
            self.assertEqual([], list(resolved.glob(".vt-write-probe-*.tmp")))


class CorruptPersistentStateTests(unittest.TestCase):
    def test_corrupt_settings_are_preserved_before_future_overwrite(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            config_path = Path(temp_dir) / "settings.json"
            original = '{"language": "ru", broken'
            config_path.write_text(original, encoding="utf-8")

            class Harness(UISettingsMixin):
                def __init__(self, path):
                    self.config_file = path
                    self.events = []

                def _problem(self, event, **details):
                    self.events.append((event, details))

            harness = Harness(config_path)
            harness.load_settings()

            backups = list(config_path.parent.glob("settings.corrupt.*.json"))
            self.assertEqual(1, len(backups))
            self.assertEqual(original, backups[0].read_text(encoding="utf-8"))
            self.assertEqual("settings_load_failed", harness.events[-1][0])
            self.assertEqual(str(backups[0]), harness.events[-1][1]["corrupt_backup_path"])

    def test_corrupt_glossary_is_backed_up_and_next_save_is_valid_json(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            glossary_path = Path(temp_dir) / "translation_glossary.json"
            original = "{broken glossary"
            glossary_path.write_text(original, encoding="utf-8")

            glossary = Glossary(glossary_path)
            self.assertEqual({}, glossary.items)
            backups = list(glossary_path.parent.glob("translation_glossary.corrupt.*.json"))
            self.assertEqual(1, len(backups))
            self.assertEqual(original, backups[0].read_text(encoding="utf-8"))

            glossary.add("CPU", "процессор")
            self.assertIn('"CPU": "процессор"', glossary_path.read_text(encoding="utf-8"))
            self.assertEqual(original, backups[0].read_text(encoding="utf-8"))


class AtomicAuxiliaryOutputTests(unittest.TestCase):
    def test_translation_report_does_not_truncate_existing_file_if_commit_fails(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            report_path = Path(temp_dir) / "report.json"
            report_path.write_text("old-report", encoding="utf-8")

            with mock.patch.object(core_io.os, "replace", side_effect=OSError("disk commit failed")):
                with self.assertRaises(OSError):
                    translation_report.save_report(report_path, {"ok": True})

            self.assertEqual("old-report", report_path.read_text(encoding="utf-8"))
            self.assertEqual([], list(report_path.parent.glob(".*.tmp")))


class ModelOperationShutdownGuardTests(unittest.TestCase):
    def test_close_is_blocked_while_model_operation_is_running(self):
        class Harness:
            _on_close = UISettingsMixin._on_close

            def __init__(self):
                self._closing = False
                self._model_action_running = True
                self._processing = False
                self.events = []

            def _problem(self, event, **details):
                self.events.append((event, details))

        harness = Harness()
        with mock.patch("videotranslator.ui.settings.messagebox.showinfo") as showinfo:
            harness._on_close()

        showinfo.assert_called_once()
        self.assertFalse(harness._closing)
        self.assertEqual("app_close_blocked_model_operation", harness.events[-1][0])


if __name__ == "__main__":
    unittest.main()
