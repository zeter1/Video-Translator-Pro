import json
import tempfile
import threading
import unittest
from copy import deepcopy
from pathlib import Path
from unittest import mock

import video_translator as vt
from videotranslator.ui.batch_start import UIBatchStartMixin
from videotranslator.ui.batch_worker import UIBatchWorkerMixin
from videotranslator.ui.tasks import UITasksMixin


class TaskQueuePass11Label:
    def __init__(self):
        self.text = ""

    def config(self, **kwargs):
        if "text" in kwargs:
            self.text = kwargs["text"]


class DurableQueueRecoveryPass11Tests(unittest.TestCase):
    def test_valid_json_with_non_object_root_is_preserved_and_reset(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            path = root / "tasks.json"
            path.write_text("[]", encoding="utf-8")

            loaded = vt.load_task_queue_state(path)

            self.assertEqual([], loaded["tasks"])
            backups = list(root.glob("tasks.corrupt.*.json"))
            self.assertEqual(1, len(backups))
            self.assertEqual("[]", backups[0].read_text(encoding="utf-8"))
            repaired = json.loads(path.read_text(encoding="utf-8"))
            self.assertEqual([], repaired["tasks"])

    def test_valid_object_with_non_array_tasks_is_backed_up_instead_of_silently_dropped(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            path = root / "tasks.json"
            payload = {"schema_version": vt.TASK_QUEUE_SCHEMA_VERSION, "tasks": {"unexpected": "object"}}
            path.write_text(json.dumps(payload), encoding="utf-8")

            loaded = vt.load_task_queue_state(path)

            self.assertEqual([], loaded["tasks"])
            backups = list(root.glob("tasks.corrupt.*.json"))
            self.assertEqual(1, len(backups))
            self.assertIn('"unexpected": "object"', backups[0].read_text(encoding="utf-8"))
            repaired = json.loads(path.read_text(encoding="utf-8"))
            self.assertEqual([], repaired["tasks"])

    def test_malformed_runtime_numbers_are_normalized_for_tasks_ui(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "tasks.json"
            batch = vt.create_batch_recovery_state(["C:/video.mp4"], "C:/out", {"model": "small"})
            task = vt.create_translation_task(batch)
            task["runtime"].update({
                "progress": "not-a-number",
                "file_index": {"bad": True},
                "files_total": "broken",
            })
            payload = vt.create_task_queue_state()
            payload["tasks"] = [task]
            path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")

            loaded = vt.load_task_queue_state(path)

            runtime = loaded["tasks"][0]["runtime"]
            self.assertEqual(0, runtime["progress"])
            self.assertEqual(0, runtime["file_index"])
            self.assertEqual(1, runtime["files_total"])

    def test_completed_batch_left_running_is_finalized_without_reprocessing(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            output = root / "translated.mp4"
            output.write_bytes(b"verified-earlier-output")
            state = vt.create_task_queue_state()
            batch = vt.create_batch_recovery_state(["C:/video.mp4"], str(root), {"model": "small"})
            task = vt.create_translation_task(batch)
            task["status"] = "running"
            task["batch_state"]["status"] = "completed"
            task["batch_state"]["files"][0]["status"] = "succeeded"
            task["batch_state"]["files"][0]["output_path"] = str(output)
            state["tasks"].append(task)

            changed = vt.recover_interrupted_tasks(state)

            self.assertTrue(changed)
            self.assertEqual("completed", task["status"])
            self.assertEqual(100, task["runtime"]["progress"])
            self.assertIsNone(vt.next_runnable_task(state))

    def test_unknown_status_is_normalized_to_resumable_state(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "tasks.json"
            batch = vt.create_batch_recovery_state(["C:/video.mp4"], "C:/out", {"model": "small"})
            task = vt.create_translation_task(batch)
            task["status"] = "future-or-corrupt-status"
            payload = vt.create_task_queue_state()
            payload["tasks"] = [task]
            path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")

            loaded = vt.load_task_queue_state(path)

            self.assertEqual("interrupted", loaded["tasks"][0]["status"])
            self.assertEqual(task["task_id"], vt.next_runnable_task(loaded)["task_id"])

    def test_load_repairs_task_and_batch_identity_mismatch(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "tasks.json"
            batch = vt.create_batch_recovery_state(["C:/video.mp4"], "C:/out", {"model": "small"})
            task = vt.create_translation_task(batch)
            task["task_id"] = "queue-owner-id"
            task["batch_state"]["batch_id"] = "stale-batch-id"
            payload = vt.create_task_queue_state()
            payload["tasks"] = [task]
            path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")

            loaded = vt.load_task_queue_state(path)

            self.assertEqual("queue-owner-id", loaded["tasks"][0]["task_id"])
            self.assertEqual("queue-owner-id", loaded["tasks"][0]["batch_state"]["batch_id"])


class QueuePersistenceTransactionsPass11Tests(unittest.TestCase):
    class Harness(UITasksMixin):
        def __init__(self):
            self._task_queue_lock = threading.RLock()
            self._task_queue_state = vt.create_task_queue_state()
            self._task_queue_dirty = False
            self._active_task_id = None
            self._resume_batch_state = None
            self._closing = False
            self._processing = False
            self._model_action_running = False
            self._worker_thread = None
            self._task_dispatch_after_id = None
            self.lbl_tasks_status = TaskQueuePass11Label()
            self.scheduled = []
            self.launched = []
            self.errors = []

        def _problem(self, *_args, **_kwargs):
            pass

        def _refresh_tasks_view(self, *_args, **_kwargs):
            pass

        def _select_tasks_tab(self):
            pass

        def _set_progress(self, *_args, **_kwargs):
            pass

        def _set_busy(self, busy):
            self._processing = bool(busy)

        def _schedule_task_dispatch(self, delay_ms=100):
            self.scheduled.append(delay_ms)

        def _launch_queued_task(self, task):
            self.launched.append(task["task_id"])
            return True

        def _show_queue_save_error(self, action):
            self.errors.append(action)

    def _batch(self):
        return vt.create_batch_recovery_state(["C:/video.mp4"], "C:/out", {"model": "small"})

    def test_enqueue_rolls_back_if_durable_write_fails(self):
        harness = self.Harness()
        with mock.patch("videotranslator.ui.tasks.save_task_queue_state", side_effect=OSError("disk full")):
            result = harness._enqueue_batch_task(self._batch())

        self.assertIsNone(result)
        self.assertEqual([], harness._task_queue_state["tasks"])
        self.assertEqual([], harness.scheduled)
        self.assertEqual(["добавление задачи"], harness.errors)

    def test_failed_transaction_preserves_preexisting_dirty_state(self):
        harness = self.Harness()
        harness._task_queue_dirty = True

        with mock.patch("videotranslator.ui.tasks.save_task_queue_state", side_effect=OSError("disk full")):
            result = harness._enqueue_batch_task(self._batch())

        self.assertIsNone(result)
        self.assertTrue(harness._task_queue_dirty)
        self.assertEqual([], harness._task_queue_state["tasks"])

    def test_dispatch_does_not_launch_uncommitted_running_transition(self):
        harness = self.Harness()
        task = vt.create_translation_task(self._batch())
        harness._task_queue_state["tasks"].append(task)

        with mock.patch("videotranslator.ui.tasks.save_task_queue_state", side_effect=OSError("read only")):
            harness._dispatch_next_task()

        restored = harness._task_queue_state["tasks"][0]
        self.assertEqual("queued", restored["status"])
        self.assertEqual([], harness.launched)
        self.assertIsNone(harness._active_task_id)
        self.assertFalse(harness._processing)
        self.assertEqual(["запуск следующей задачи"], harness.errors)

    def test_worker_checkpoint_updates_its_batch_not_new_active_task(self):
        harness = self.Harness()
        first = vt.create_translation_task(self._batch())
        second = vt.create_translation_task(self._batch())
        first["batch_state"]["batch_id"] = first["task_id"]
        second["batch_state"]["batch_id"] = second["task_id"]
        harness._task_queue_state["tasks"] = [first, second]
        harness._active_task_id = second["task_id"]
        worker_state = deepcopy(first["batch_state"])
        worker_state["status"] = "running"
        worker_state["files"][0]["status"] = "processing"

        with mock.patch("videotranslator.ui.tasks.save_task_queue_state"):
            harness._save_task_queue_state_from_worker(worker_state)

        self.assertEqual("processing", first["batch_state"]["files"][0]["status"])
        self.assertNotEqual("processing", second["batch_state"]["files"][0]["status"])


class QueueGenerationGuardPass11Tests(unittest.TestCase):
    class Harness(UITasksMixin):
        def __init__(self):
            self._task_queue_lock = threading.RLock()
            self._task_queue_state = vt.create_task_queue_state()
            self._task_queue_dirty = False
            self._closing = False
            self._processing = True
            self._active_task_id = None
            self._resume_batch_state = None
            self.root = object()
            self.lbl_tasks_status = TaskQueuePass11Label()
            self.busy_changes = []
            self.dispatches = []
            self.problems = []

        def _persist_task_queue_safely(self):
            return True

        def _set_busy(self, value):
            self.busy_changes.append(bool(value))
            self._processing = bool(value)

        def _refresh_tasks_view(self, *_args, **_kwargs):
            pass

        def _schedule_task_dispatch(self, delay_ms=100):
            self.dispatches.append(delay_ms)

        def _problem(self, event, **details):
            self.problems.append((event, details))

    def test_current_success_finalizes_task_notifies_and_advances_fifo(self):
        harness = self.Harness()
        task = vt.create_translation_task(vt.create_batch_recovery_state(["C:/a.mp4"], "C:/out", {"model": "small"}))
        task["status"] = "running"
        harness._task_queue_state["tasks"] = [task]
        harness._active_task_id = task["task_id"]
        harness._resume_batch_state = task["batch_state"]
        completed_batch = deepcopy(task["batch_state"])
        completed_batch["status"] = "completed"
        completed_batch["files"][0]["status"] = "succeeded"

        with mock.patch("videotranslator.ui.tasks.show_topmost_notification") as notify:
            harness._on_task_batch_finished(task["task_id"], completed_batch, 1, 1, False)

        self.assertEqual("completed", task["status"])
        self.assertIsNone(harness._active_task_id)
        self.assertIsNone(harness._resume_batch_state)
        self.assertEqual([False], harness.busy_changes)
        self.assertEqual([150], harness.dispatches)
        notify.assert_called_once()

    def test_late_completion_cannot_release_newer_active_task(self):
        harness = self.Harness()
        first = vt.create_translation_task(vt.create_batch_recovery_state(["C:/a.mp4"], "C:/out", {"model": "small"}))
        second = vt.create_translation_task(vt.create_batch_recovery_state(["C:/b.mp4"], "C:/out", {"model": "small"}))
        first["status"] = "running"
        second["status"] = "running"
        harness._task_queue_state["tasks"] = [first, second]
        harness._active_task_id = second["task_id"]
        harness._resume_batch_state = second["batch_state"]
        harness.lbl_tasks_status.text = "Новая задача переводится"
        completed_batch = deepcopy(first["batch_state"])
        completed_batch["status"] = "completed"
        completed_batch["files"][0]["status"] = "succeeded"

        with mock.patch("videotranslator.ui.tasks.show_topmost_notification"):
            harness._on_task_batch_finished(first["task_id"], completed_batch, 1, 1, False)

        self.assertEqual("completed", first["status"])
        self.assertEqual(second["task_id"], harness._active_task_id)
        self.assertIs(second["batch_state"], harness._resume_batch_state)
        self.assertEqual([], harness.busy_changes)
        self.assertEqual([], harness.dispatches)
        self.assertEqual("Новая задача переводится", harness.lbl_tasks_status.text)
        self.assertTrue(any(event == "stale_task_completion_callback" for event, _ in harness.problems))


class WorkerLifecyclePass11Tests(unittest.TestCase):
    class Harness(UIBatchWorkerMixin):
        def __init__(self):
            self.last_log_path = ""
            self.file_logger = None
            self.problem_logger = None
            self._cancel_event = threading.Event()
            self._resume_batch_state = None
            self._task_queue_lock = threading.RLock()
            self._task_queue_state = vt.create_task_queue_state()
            self.callbacks = []

        def _safe_after(self, _delay, func, *args):
            self.callbacks.append((func, args))
            return True

        def _set_progress(self, *_args):
            pass

        def _set_busy(self, *_args):
            pass

        def _on_task_batch_finished(self, *_args):
            pass

        def _save_task_queue_state_from_worker(self, *_args):
            pass

        def _log(self, *_args):
            pass

        def _problem(self, *_args, **_kwargs):
            pass

    def test_queue_worker_does_not_release_busy_before_task_finalizer(self):
        harness = self.Harness()
        batch = {"batch_id": "task-1", "status": "running", "files": []}

        with mock.patch("videotranslator.ui.batch_worker.save_batch_recovery_state"):
            harness._worker(batch, "C:/out", "voice", "small", False, 15, {}, False, {})

        callback_names = [getattr(func, "__name__", "") for func, _args in harness.callbacks]
        self.assertIn("_on_task_batch_finished", callback_names)
        self.assertNotIn("_set_busy", callback_names)


class LegacyMigrationPass11Tests(unittest.TestCase):
    class Harness(UITasksMixin):
        def __init__(self):
            self._task_queue_lock = threading.RLock()
            self._task_queue_state = vt.create_task_queue_state()
            self._task_queue_dirty = False
            self.persist_calls = 0

        def _persist_task_queue_safely(self):
            self.persist_calls += 1
            return True

        def _refresh_tasks_view(self, *_args, **_kwargs):
            pass

    def test_removed_migrated_legacy_task_does_not_resurrect(self):
        harness = self.Harness()
        state = vt.create_task_queue_state()
        state["known_task_batch_ids"] = ["legacy-1"]
        legacy = vt.create_batch_recovery_state(["C:/video.mp4"], "C:/out", {"model": "small"})
        legacy["batch_id"] = "legacy-1"
        legacy["status"] = "interrupted"

        with mock.patch("videotranslator.ui.tasks.load_task_queue_state", return_value=state), \
                mock.patch("videotranslator.ui.tasks.load_batch_recovery_state", return_value=legacy):
            harness._load_task_queue()

        self.assertEqual([], harness._task_queue_state["tasks"])


class LegacyBatchRecoveryHardeningPass11Tests(unittest.TestCase):
    def test_non_object_legacy_recovery_json_is_ignored_without_startup_crash(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "latest_batch_recovery.json"
            path.write_text("[]", encoding="utf-8")

            loaded = vt.load_batch_recovery_state(path)

            self.assertEqual({}, loaded)

    def test_malformed_legacy_file_entries_are_ignored_without_pending_entry_crash(self):
        state = {"files": [None, "broken", {"status": "pending"}]}

        pending = vt.batch_recovery_pending_entries(state)

        self.assertEqual([{"status": "pending"}], pending)


class InvalidSavedSettingsPass11Tests(unittest.TestCase):
    class Harness(UIBatchStartMixin):
        def __init__(self):
            self.failed = []
            self.busy = []

        def _problem(self, *_args, **_kwargs):
            pass

        def _task_start_failed(self, state):
            self.failed.append(state["status"])

        def _set_busy(self, value):
            self.busy.append(bool(value))

    def test_corrupt_saved_settings_fail_cleanly_before_thread_start(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            batch = vt.create_batch_recovery_state(["C:/video.mp4"], temp_dir, {})
            batch["settings"] = {
                "language_label": vt.DEFAULT_TARGET_LANGUAGE,
                "voice": "voice-code",
                "model": "small",
                "original_volume_pct": "not-an-int",
            }
            task = vt.create_translation_task(batch)
            harness = self.Harness()

            with mock.patch("videotranslator.ui.batch_start.messagebox.showerror"), \
                    mock.patch("videotranslator.ui.batch_start.threading.Thread") as thread_class:
                result = harness._launch_queued_task(task)

            self.assertFalse(result)
            self.assertEqual(["start_failed"], harness.failed)
            thread_class.assert_not_called()


if __name__ == "__main__":
    unittest.main()


class LegacyRecoveryDialogPass11Tests(unittest.TestCase):
    def test_known_removed_task_is_not_offered_by_legacy_recovery_dialog(self):
        app = object.__new__(vt.App)
        app._closing = False
        app._processing = False
        app._task_queue_state = vt.create_task_queue_state()
        app._task_queue_state["known_task_batch_ids"] = ["removed-task"]
        legacy = vt.create_batch_recovery_state(["C:/video.mp4"], "C:/out", {"model": "small"})
        legacy["batch_id"] = "removed-task"
        app.problem_logger = None

        with mock.patch("videotranslator.ui.batch_recovery.load_batch_recovery_state", return_value=legacy), \
                mock.patch("videotranslator.ui.batch_recovery.messagebox.askyesno") as ask:
            app._offer_batch_recovery()

        ask.assert_not_called()
