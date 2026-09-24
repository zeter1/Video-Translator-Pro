import tempfile
import threading
import unittest
from contextlib import contextmanager
from pathlib import Path
from unittest import mock

from videotranslator.models.manager_v8 import RuntimeModelManager
from videotranslator.tts.cache import TTSCache
from videotranslator.ui.batch_start import UIBatchStartMixin
from videotranslator.ui.models_tab import UIModelsMixin


class TTSCacheLockPoolTests(unittest.TestCase):
    def test_key_locks_are_bounded_and_same_key_is_stable(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            cache = TTSCache(Path(temp_dir), max_bytes=0)
            same = cache.key_lock("same-key")
            self.assertIs(same, cache.key_lock("same-key"))

            lock_ids = {id(cache.key_lock(f"segment-{index}")) for index in range(10_000)}
            self.assertEqual(TTSCache.KEY_LOCK_STRIPES, len(cache._key_locks))
            self.assertLessEqual(len(lock_ids), TTSCache.KEY_LOCK_STRIPES)
            self.assertFalse(hasattr(cache, "_locks"))


class ModelStoreScanLockTests(unittest.TestCase):
    @staticmethod
    def _busy_store_lock():
        @contextmanager
        def busy():
            raise RuntimeError("model store busy")
            yield  # pragma: no cover
        return busy

    def test_public_scan_refuses_to_adopt_manifests_while_other_process_owns_store(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            manager = RuntimeModelManager(root=Path(temp_dir))
            manager._process_store_lock = self._busy_store_lock()

            with self.assertRaisesRegex(RuntimeError, "model store busy"):
                manager.scan_installed_updates(verify_integrity=False)

    def test_startup_scan_refuses_to_advance_state_while_other_process_owns_store(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            manager = RuntimeModelManager(root=Path(temp_dir))
            manager._process_store_lock = self._busy_store_lock()

            with self.assertRaisesRegex(RuntimeError, "model store busy"):
                manager.startup_scan_installed_updates()
            self.assertFalse((Path(temp_dir) / "manager_state.json").exists())

    def test_size_metadata_skips_one_unreadable_entry_instead_of_failing_status(self):
        class Entry:
            def __init__(self, size=None, error=None):
                self.size = size
                self.error = error

            def is_file(self):
                return True

            def stat(self):
                if self.error:
                    raise self.error
                return type("Stat", (), {"st_size": self.size})()

        class Directory:
            def exists(self):
                return True

            def is_file(self):
                return False

            def rglob(self, _pattern):
                return [Entry(size=2 * 1024 * 1024), Entry(error=PermissionError("locked"))]

        self.assertEqual(2.0, RuntimeModelManager._dir_size_mb(Directory()))


class BatchStartLifecycleTests(unittest.TestCase):
    def test_reentrant_start_returns_before_touching_queue(self):
        class Harness(UIBatchStartMixin):
            _processing = True
            _model_action_running = False

            @property
            def video_files(self):
                raise AssertionError("active batch must short-circuit before reading the queue")

        Harness().start()

    def test_worker_start_failure_restores_idle_state_and_marks_recovery(self):
        class FailingThread:
            def start(self):
                raise RuntimeError("cannot create thread")

        class Harness(UIBatchStartMixin):
            def __init__(self):
                self._cancel_event = threading.Event()
                self._cancel_event.set()
                self._processing = False
                self._worker_thread = None
                self.file_logger = mock.Mock()
                self.events = []
                self.busy_states = []

            def _set_busy(self, busy):
                self._processing = bool(busy)
                self.busy_states.append(bool(busy))

            def _problem(self, event, **details):
                self.events.append((event, details))

        harness = Harness()
        state = {"status": "running"}
        with mock.patch("videotranslator.ui.batch_start.save_batch_recovery_state") as save_state, \
                mock.patch("videotranslator.ui.batch_start.messagebox.showerror") as showerror:
            started = harness._start_batch_worker(FailingThread(), state)

        self.assertFalse(started)
        self.assertEqual("start_failed", state["status"])
        self.assertIsNone(harness._worker_thread)
        self.assertFalse(harness._processing)
        self.assertEqual([True, False], harness.busy_states)
        self.assertFalse(harness._cancel_event.is_set())
        self.assertEqual("batch_worker_start_failed", harness.events[-1][0])
        save_state.assert_called_once_with(state)
        harness.file_logger.close.assert_called_once()
        showerror.assert_called_once()


class _Value:
    def __init__(self, value):
        self.value = value

    def get(self):
        return self.value


class _Label:
    def __init__(self):
        self.text = ""

    def config(self, **kwargs):
        if "text" in kwargs:
            self.text = kwargs["text"]


class ModelUIThreadLifecycleTests(unittest.TestCase):
    def test_startup_thread_failure_releases_model_busy_gate(self):
        class Harness(UIModelsMixin):
            def __init__(self):
                self.runtime_model_manager = object()
                self.var_auto_update_local_ai = _Value(True)
                self._startup_model_check_started = False
                self._processing = False
                self._model_action_running = False
                self.lbl_model_action = _Label()
                self.events = []

            def _set_model_buttons_busy(self, busy):
                self._model_action_running = bool(busy)

            def _problem(self, event, **details):
                self.events.append((event, details))

            def _safe_after(self, *_args):
                raise AssertionError("worker must not have started")

        harness = Harness()
        failing_thread = mock.Mock()
        failing_thread.start.side_effect = RuntimeError("thread quota")
        with mock.patch("videotranslator.ui.models_tab.threading.Thread", return_value=failing_thread):
            harness.schedule_model_maintenance()

        self.assertFalse(harness._startup_model_check_started)
        self.assertFalse(harness._model_action_running)
        self.assertIn("Не удалось запустить", harness.lbl_model_action.text)
        self.assertEqual("local_ai_startup_thread_failed", harness.events[-1][0])

    def test_manual_model_thread_failure_releases_model_busy_gate(self):
        class Harness(UIModelsMixin):
            def __init__(self):
                self._processing = False
                self._model_action_running = False
                self.lbl_model_action = _Label()
                self.events = []

            def _selected_model_id(self):
                return "test-model"

            def _set_model_buttons_busy(self, busy):
                self._model_action_running = bool(busy)

            def _problem(self, event, **details):
                self.events.append((event, details))

            def _safe_after(self, *_args):
                raise AssertionError("worker must not have started")

        harness = Harness()
        failing_thread = mock.Mock()
        failing_thread.start.side_effect = RuntimeError("thread quota")
        with mock.patch("videotranslator.ui.models_tab.get_catalog", return_value=[{"id": "test-model", "name": "Test"}]), \
                mock.patch("videotranslator.ui.models_tab.threading.Thread", return_value=failing_thread), \
                mock.patch("videotranslator.ui.models_tab.messagebox.showerror") as showerror:
            harness._model_action("verify")

        self.assertFalse(harness._model_action_running)
        self.assertIn("Не удалось запустить", harness.lbl_model_action.text)
        self.assertEqual("local_ai_action_thread_failed", harness.events[-1][0])
        showerror.assert_called_once()


if __name__ == "__main__":
    unittest.main()
