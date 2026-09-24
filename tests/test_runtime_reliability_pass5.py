import json
import os
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest import mock

from videotranslator.core import settings_io
from videotranslator.core import process_flags
from videotranslator.core.diagnostics import compact_exception
from videotranslator.diagnostics import file_logger
from videotranslator.ui.settings import UISettingsMixin
from videotranslator.media import process as media_process
import video_translator as vt


class SettingsAtomicityTests(unittest.TestCase):
    def test_concurrent_settings_writers_share_canonical_unique_temp_safely(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "settings.json"
            errors = []
            barrier = threading.Barrier(8)

            def writer(index):
                try:
                    barrier.wait(timeout=2)
                    settings_io.atomic_write_json(path, {"writer": index, "payload": "x" * 2000})
                except Exception as exc:  # pragma: no cover - asserted below
                    errors.append(exc)

            threads = [threading.Thread(target=writer, args=(index,)) for index in range(8)]
            for thread in threads:
                thread.start()
            for thread in threads:
                thread.join(timeout=5)

            self.assertFalse(errors)
            self.assertTrue(all(not thread.is_alive() for thread in threads))
            data = json.loads(path.read_text(encoding="utf-8"))
            self.assertIn(data["writer"], range(8))
            self.assertEqual(data["payload"], "x" * 2000)
            self.assertEqual([], list(path.parent.glob("*.tmp")))
            self.assertEqual([], list(path.parent.glob(".*.tmp")))


class _FakeRoot:
    def __init__(self):
        self.callbacks = {}
        self.cancelled = []
        self.next_id = 0

    def after(self, _delay, callback):
        self.next_id += 1
        callback_id = f"after-{self.next_id}"
        self.callbacks[callback_id] = callback
        return callback_id

    def after_cancel(self, callback_id):
        self.cancelled.append(callback_id)
        self.callbacks.pop(callback_id, None)


class _DebounceHarness(UISettingsMixin):
    def __init__(self):
        self.root = _FakeRoot()
        self._suspend_save = False
        self._closing = False
        self._settings_save_after_id = None
        self.saved = 0

    def save_settings(self, *_):
        self.saved += 1


class SettingsDebounceTests(unittest.TestCase):
    def test_slider_style_saves_are_coalesced(self):
        harness = _DebounceHarness()
        harness._schedule_settings_save()
        first = harness._settings_save_after_id
        harness._schedule_settings_save()
        second = harness._settings_save_after_id
        harness._schedule_settings_save()
        third = harness._settings_save_after_id

        self.assertNotEqual(first, second)
        self.assertNotEqual(second, third)
        self.assertEqual([first, second], harness.root.cancelled)
        self.assertEqual([third], list(harness.root.callbacks))

        callback = harness.root.callbacks.pop(third)
        callback()
        self.assertEqual(1, harness.saved)
        self.assertIsNone(harness._settings_save_after_id)


class PlainTextLogTests(unittest.TestCase):
    def test_plain_log_redacts_credentials_before_disk_write_and_uses_unique_name(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            directory = Path(temp_dir)
            with mock.patch.object(file_logger, "get_logs_dir", return_value=directory), \
                    mock.patch.object(file_logger, "get_program_dir", return_value=directory):
                first = file_logger.FileLogger(prefix="video_translator_test")
                first.write(
                    "Authorization: Bearer TOP_SECRET_TOKEN\n"
                    "api_key=VERY_SECRET\n"
                    "https://example.test/path?token=SECRET_QUERY&x=1"
                )
                first_path = first.path
                first.close()
                second = file_logger.FileLogger(prefix="video_translator_test")
                second_path = second.path
                second.close()

            self.assertNotEqual(first_path, second_path)
            text = first_path.read_text(encoding="utf-8")
            self.assertNotIn("TOP_SECRET_TOKEN", text)
            self.assertNotIn("VERY_SECRET", text)
            self.assertNotIn("SECRET_QUERY", text)
            self.assertIn("<скрыто>", text)
            self.assertIn("https://example.test/path?...", text)

    def test_plain_log_retention_only_deletes_owned_logs(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            directory = Path(temp_dir)
            now = time.time()
            for index in range(file_logger.NORMAL_LOG_MAX_FILES + 7):
                path = directory / f"video_translator_{index:03d}.txt"
                path.write_text(str(index), encoding="utf-8")
                os.utime(path, (now - index, now - index))
            unrelated = directory / "my_notes.txt"
            unrelated.write_text("keep", encoding="utf-8")

            file_logger.cleanup_old_text_logs(directory)

            owned = list(directory.glob("video_translator*.txt"))
            self.assertLessEqual(len(owned), file_logger.NORMAL_LOG_MAX_FILES)
            self.assertTrue(unrelated.exists())


class _FakeWorker:
    def __init__(self, alive=True):
        self.alive = alive
        self.name = "video-translation-worker"

    def is_alive(self):
        return self.alive


class _CloseRoot(_FakeRoot):
    def __init__(self):
        super().__init__()
        self.destroyed = False

    def destroy(self):
        self.destroyed = True


class _Closable:
    # Bind only the close-lifecycle methods under test; real App supplies the rest.
    _cancel_scheduled_settings_save = UISettingsMixin._cancel_scheduled_settings_save
    _finalize_close = UISettingsMixin._finalize_close
    _wait_for_worker_before_close = UISettingsMixin._wait_for_worker_before_close
    _on_close = UISettingsMixin._on_close

    def __init__(self):
        self.root = _CloseRoot()
        self._closing = False
        self._processing = True
        self._settings_save_after_id = None
        self._worker_thread = _FakeWorker(alive=True)
        self._close_deadline_monotonic = None
        self._cancel_event = threading.Event()
        self._resume_batch_state = None
        self.file_logger = mock.Mock()
        self.problem_logger = mock.Mock()
        self.events = []
        self.saved = 0

    def _problem(self, event, **details):
        self.events.append((event, details))

    def save_settings(self):
        self.saved += 1

    def _close_vpn_notice(self):
        pass


class ShutdownLifecycleTests(unittest.TestCase):
    def test_close_requests_cancel_and_waits_for_worker_before_destroying_gui(self):
        app = _Closable()
        with mock.patch("videotranslator.ui.settings.messagebox.askyesno", return_value=True):
            app._on_close()

        self.assertTrue(app._cancel_event.is_set())
        self.assertTrue(app._closing)
        self.assertFalse(app.root.destroyed)
        self.assertEqual(1, app.saved)
        self.assertEqual(1, len(app.root.callbacks))
        app.file_logger.close.assert_not_called()
        app.problem_logger.close.assert_not_called()

        app._worker_thread.alive = False
        callback_id, callback = next(iter(app.root.callbacks.items()))
        app.root.callbacks.pop(callback_id)
        callback()

        self.assertTrue(app.root.destroyed)
        app.file_logger.close.assert_called_once()
        app.problem_logger.close.assert_called_once()

    def test_close_without_processing_finishes_immediately(self):
        app = _Closable()
        app._processing = False
        app._worker_thread = None
        app._on_close()
        self.assertTrue(app.root.destroyed)
        self.assertFalse(app._cancel_event.is_set())
        app.file_logger.close.assert_called_once()
        app.problem_logger.close.assert_called_once()

class WindowsSubprocessUXTests(unittest.TestCase):
    def test_windows_gui_children_use_no_console_flag(self):
        with mock.patch.object(process_flags.os, "name", "nt"), \
                mock.patch.object(process_flags.subprocess, "CREATE_NO_WINDOW", 0x08000000, create=True):
            self.assertEqual(0x08000000, process_flags.no_console_creationflags())

    def test_media_subprocess_passes_platform_creation_flags(self):
        completed = media_process.subprocess.CompletedProcess(["ffmpeg"], 0, "", "")
        with mock.patch.object(media_process, "no_console_creationflags", return_value=12345), \
                mock.patch.object(media_process.subprocess, "run", return_value=completed) as runner:
            result = media_process.run_subprocess(["ffmpeg"], timeout=5)

        self.assertIs(result, completed)
        self.assertEqual(12345, runner.call_args.kwargs["creationflags"])


class DiagnosticRedactionTests(unittest.TestCase):
    def test_compact_exception_redacts_bearer_and_api_key(self):
        text = compact_exception(RuntimeError(
            "Authorization: Bearer TOP_SECRET api_key=SECOND_SECRET"
        ))
        self.assertNotIn("TOP_SECRET", text)
        self.assertNotIn("SECOND_SECRET", text)
        self.assertIn("<скрыто>", text)


class TempCleanupTests(unittest.TestCase):
    def test_cleanup_failure_is_visible_but_does_not_raise(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            work = root / "work"
            work.mkdir()
            with mock.patch("videotranslator.tts.cache.get_tts_cache_dir", return_value=root / "cache"):
                translator = vt.VideoTranslator(lambda _message: None)
            translator.temp_dir = str(work)
            translator.log = mock.Mock()
            translator._problem = mock.Mock()

            with mock.patch("videotranslator.pipeline.process.shutil.rmtree", side_effect=PermissionError("locked")):
                translator.cleanup_temp()

            self.assertEqual("", translator.temp_dir)
            translator.log.assert_called_once()
            translator._problem.assert_called_once()
            event, = translator._problem.call_args.args
            self.assertEqual("temp_cleanup_failed", event)
            self.assertEqual("warning", translator._problem.call_args.kwargs["level"])


if __name__ == "__main__":
    unittest.main()
