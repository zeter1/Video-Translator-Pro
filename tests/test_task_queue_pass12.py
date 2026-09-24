import json
import tempfile
import threading
import unittest
from pathlib import Path
from unittest import mock

import video_translator as vt
from videotranslator.core.instance_lock import ApplicationInstanceAlreadyRunning, ApplicationInstanceLock
from videotranslator.ui.batch_recovery import UIBatchRecoveryMixin
from videotranslator.ui.batch_start import UIBatchStartMixin
from videotranslator.ui.batch_worker import UIBatchWorkerMixin
from videotranslator.ui.controls import UIControlsMixin
from videotranslator.ui.settings import UISettingsMixin
from videotranslator.ui.tasks import UITasksMixin


class Pass12Label:
    def __init__(self):
        self.text = ""

    def config(self, **kwargs):
        if "text" in kwargs:
            self.text = kwargs["text"]


class DurableQueuePass12Tests(unittest.TestCase):
    def test_non_finite_schema_version_is_backed_up_instead_of_crashing_startup(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            path = root / "tasks.json"
            path.write_text('{"schema_version": 1e999, "tasks": []}', encoding="utf-8")

            loaded = vt.load_task_queue_state(path)

            self.assertEqual([], loaded["tasks"])
            backups = list(root.glob("tasks.corrupt.*.json"))
            self.assertEqual(1, len(backups))
            self.assertIn("1e999", backups[0].read_text(encoding="utf-8"))

    def test_legacy_non_finite_schema_version_is_ignored_without_overflow(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "legacy.json"
            path.write_text(
                '{"schema_version": 1e999, "files": [], "output_dir": "C:/out"}',
                encoding="utf-8",
            )

            self.assertEqual({}, vt.load_batch_recovery_state(path))

    def test_scalar_known_task_id_tombstone_is_preserved(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "tasks.json"
            payload = vt.create_task_queue_state()
            payload["known_task_batch_ids"] = "removed-legacy-task"
            path.write_text(json.dumps(payload), encoding="utf-8")

            loaded = vt.load_task_queue_state(path)

            self.assertEqual(["removed-legacy-task"], loaded["known_task_batch_ids"])

    def test_duplicate_task_ids_are_repaired_without_ambiguous_routing(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "tasks.json"
            first = vt.create_translation_task(
                vt.create_batch_recovery_state(["C:/one.mp4"], "C:/out", {"model": "small"})
            )
            second = vt.create_translation_task(
                vt.create_batch_recovery_state(["C:/two.mp4"], "C:/out", {"model": "small"})
            )
            second["task_id"] = first["task_id"]
            second["batch_state"]["batch_id"] = first["task_id"]
            payload = vt.create_task_queue_state()
            payload["tasks"] = [first, second]
            path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")

            loaded = vt.load_task_queue_state(path)

            ids = [task["task_id"] for task in loaded["tasks"]]
            self.assertEqual(2, len(set(ids)))
            self.assertEqual("failed", loaded["tasks"][1]["status"])
            self.assertIn("recovered", loaded["tasks"][1]["task_id"])
            self.assertEqual(
                loaded["tasks"][1]["task_id"],
                loaded["tasks"][1]["batch_state"]["batch_id"],
            )

    def test_semantically_broken_task_is_backed_up_before_valid_subset_is_published(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            path = root / "tasks.json"
            valid = vt.create_translation_task(
                vt.create_batch_recovery_state(["C:/valid.mp4"], "C:/out", {"model": "small"})
            )
            payload = vt.create_task_queue_state()
            payload["tasks"] = [valid, None, {"task_id": "broken", "batch_state": {}}]
            path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")

            loaded = vt.load_task_queue_state(path)

            self.assertEqual([valid["task_id"]], [task["task_id"] for task in loaded["tasks"]])
            backups = list(root.glob("tasks.partial.*.json"))
            self.assertEqual(1, len(backups))
            backup_payload = json.loads(backups[0].read_text(encoding="utf-8"))
            self.assertEqual(3, len(backup_payload["tasks"]))
            primary_payload = json.loads(path.read_text(encoding="utf-8"))
            self.assertEqual(1, len(primary_payload["tasks"]))

    def test_completed_crash_window_is_not_finalized_when_output_disappeared(self):
        state = vt.create_task_queue_state()
        batch = vt.create_batch_recovery_state(["C:/video.mp4"], "C:/out", {"model": "small"})
        task = vt.create_translation_task(batch)
        task["status"] = "running"
        task["batch_state"]["status"] = "completed"
        task["batch_state"]["files"][0]["status"] = "succeeded"
        task["batch_state"]["files"][0]["output_path"] = "C:/missing-output.mp4"
        state["tasks"].append(task)

        vt.recover_interrupted_tasks(state)

        self.assertEqual("interrupted", task["status"])
        self.assertEqual("interrupted", task["batch_state"]["status"])
        self.assertIs(task, vt.next_runnable_task(state))

    def test_persisted_cancel_request_does_not_resume_after_restart(self):
        state = vt.create_task_queue_state()
        batch = vt.create_batch_recovery_state(["C:/video.mp4"], "C:/out", {"model": "small"})
        task = vt.create_translation_task(batch)
        task["status"] = "running"
        task["batch_state"]["status"] = "cancellation_requested"
        state["tasks"].append(task)

        changed = vt.recover_interrupted_tasks(state)

        self.assertTrue(changed)
        self.assertEqual("cancelled", task["status"])
        self.assertEqual("cancelled", task["batch_state"]["status"])
        self.assertIsNone(vt.next_runnable_task(state))


class BatchWorkerPass12Tests(unittest.TestCase):
    class Harness(UIBatchWorkerMixin):
        def __init__(self):
            self.last_log_path = ""
            self.file_logger = None
            self.problem_logger = None
            self._cancel_event = threading.Event()
            self._resume_batch_state = None
            self._active_translator = None
            self.callbacks = []
            self.problems = []

        def _safe_after(self, _delay, func, *args):
            self.callbacks.append((func, args))
            return True

        def _set_progress(self, *_args):
            pass

        def _set_busy(self, *_args):
            pass

        def _log(self, *_args):
            pass

        def _problem(self, event, **details):
            self.problems.append((event, details))

        def _threadsafe_progress(self, *_args):
            pass

        def review_translations(self, *_args):
            return None

        def _threadsafe_network_notice(self, *_args):
            pass

        def _close_vpn_notice(self):
            pass

    def test_corrupt_file_index_does_not_crash_entire_worker(self):
        harness = self.Harness()
        batch = {
            "batch_id": "task-1",
            "status": "running",
            "files": [{"file_index": "broken", "input": {"path": "Z:/definitely-missing.mp4"}, "status": "pending"}],
        }

        with mock.patch("videotranslator.ui.batch_worker.save_batch_recovery_state"):
            harness._worker(batch, "C:/out", "voice", "small", False, 15, {}, False, {})

        self.assertEqual("incomplete", batch["status"])
        self.assertEqual("missing", batch["files"][0]["status"])
        self.assertFalse(any(event == "worker_thread_crashed" for event, _ in harness.problems))

    def test_missing_previously_succeeded_output_is_processed_again(self):
        harness = self.Harness()
        calls = []

        class FakeTranslator:
            def __init__(self, *_args, **_kwargs):
                self.shared_translation_circuit = None

            def process(self, input_path, output_path, *_args, **_kwargs):
                calls.append((input_path, output_path))
                Path(output_path).write_bytes(b"replacement")
                return True

        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            source = root / "source.mp4"
            source.write_bytes(b"source")
            missing_output = root / "translated.mp4"
            batch = vt.create_batch_recovery_state([str(source)], str(root), {"model": "small"})
            batch["files"][0]["status"] = "succeeded"
            batch["files"][0]["output_path"] = str(missing_output)

            with mock.patch("videotranslator.ui.batch_worker.save_batch_recovery_state"), \
                    mock.patch("videotranslator.ui.batch_worker.VideoTranslator", FakeTranslator):
                harness._worker(batch, str(root), "voice", "small", False, 15, {"suffix": "_TR"}, False, {})

            self.assertEqual(1, len(calls))
            self.assertTrue(missing_output.is_file())
            self.assertEqual("completed", batch["status"])
            self.assertTrue(any(event == "batch_verified_output_lost" for event, _ in harness.problems))

    def test_corrupt_persisted_input_signature_reprocesses_without_worker_crash(self):
        harness = self.Harness()
        calls = []

        class FakeTranslator:
            def __init__(self, *_args, **_kwargs):
                self.shared_translation_circuit = None

            def process(self, input_path, output_path, *_args, **_kwargs):
                calls.append((input_path, output_path))
                Path(output_path).write_bytes(b"new")
                return True

        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            source = root / "source.mp4"
            source.write_bytes(b"source")
            stale_output = root / "stale.mp4"
            stale_output.write_bytes(b"stale")
            batch = vt.create_batch_recovery_state([str(source)], str(root), {"model": "small"})
            batch["files"][0]["input"]["size"] = "not-an-int"
            batch["files"][0]["input"]["modified_ns"] = {"bad": True}
            batch["files"][0]["output_path"] = str(stale_output)

            with mock.patch("videotranslator.ui.batch_worker.save_batch_recovery_state"), \
                    mock.patch("videotranslator.ui.batch_worker.VideoTranslator", FakeTranslator), \
                    mock.patch("videotranslator.ui.batch_worker.make_unique_output_path", return_value=str(root / "fresh.mp4")):
                harness._worker(batch, str(root), "voice", "small", False, 15, {"suffix": "_TR"}, False, {})

            self.assertEqual(1, len(calls))
            self.assertEqual("completed", batch["status"])
            self.assertFalse(any(event == "worker_thread_crashed" for event, _ in harness.problems))

    def test_changed_source_invalidates_previously_succeeded_output(self):
        harness = self.Harness()
        calls = []

        class FakeTranslator:
            def __init__(self, *_args, **_kwargs):
                self.shared_translation_circuit = None

            def process(self, input_path, output_path, *_args, **_kwargs):
                calls.append((input_path, output_path))
                Path(output_path).write_bytes(b"fresh-translation")
                return True

        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            source = root / "source.mp4"
            source.write_bytes(b"old-source")
            old_output = root / "old-translation.mp4"
            old_output.write_bytes(b"valid-old-translation")
            fresh_output = root / "fresh-translation.mp4"
            batch = vt.create_batch_recovery_state([str(source)], str(root), {"model": "small"})
            batch["files"][0]["status"] = "succeeded"
            batch["files"][0]["output_path"] = str(old_output)
            source.write_bytes(b"new-source-with-different-size")

            with mock.patch("videotranslator.ui.batch_worker.save_batch_recovery_state"), \
                    mock.patch("videotranslator.ui.batch_worker.VideoTranslator", FakeTranslator), \
                    mock.patch("videotranslator.ui.batch_worker.find_ffmpeg", return_value="ffmpeg"), \
                    mock.patch("videotranslator.ui.batch_worker.find_ffprobe", return_value="ffprobe"), \
                    mock.patch("videotranslator.ui.batch_worker.output_has_video_and_audio", return_value=True), \
                    mock.patch("videotranslator.ui.batch_worker.make_unique_output_path", return_value=str(fresh_output)):
                harness._worker(batch, str(root), "voice", "small", False, 15, {"suffix": "_TR"}, False, {})

            self.assertEqual([(str(source), str(fresh_output))], calls)
            self.assertEqual(str(fresh_output), batch["files"][0]["output_path"])
            self.assertEqual(b"valid-old-translation", old_output.read_bytes())
            self.assertEqual("completed", batch["status"])
            event = next(details for name, details in harness.problems if name == "batch_verified_output_lost")
            self.assertTrue(event["source_changed"])


class LegacyRecoveryPass12Tests(unittest.TestCase):
    class Harness(UIBatchRecoveryMixin):
        def __init__(self):
            self._closing = False
            self._processing = False
            self._task_queue_state = vt.create_task_queue_state()
            self.problem_logger = None
            self.problems = []

        def _problem(self, event, **details):
            self.problems.append((event, details))

    def test_corrupt_signature_does_not_crash_recovery_offer(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            source = root / "source.mp4"
            source.write_bytes(b"source")
            state = vt.create_batch_recovery_state([str(source)], str(root), {"model": "small"})
            state["status"] = "interrupted"
            state["files"][0]["input"]["size"] = "broken"
            state["files"][0]["input"]["modified_ns"] = {"bad": True}
            harness = self.Harness()

            with mock.patch("videotranslator.ui.batch_recovery.load_batch_recovery_state", return_value=state), \
                    mock.patch("videotranslator.ui.batch_recovery.messagebox.askyesno", return_value=False) as ask:
                harness._offer_batch_recovery()

            ask.assert_called_once()
            event = next(details for name, details in harness.problems if name == "batch_recovery_offer_answered")
            self.assertEqual([str(source)], event["changed_files"])

    def test_audio_normalization_survives_corrupt_numeric_fields(self):
        normalized = vt.normalize_audio_settings({
            "voice_volume_pct": {"bad": True},
            "highpass_hz": float("inf"),
            "lowpass_hz": "broken",
            "master_loudness_i": None,
            "speech_speed_limit": float("inf"),
        })

        self.assertEqual(100, normalized["voice_volume_pct"])
        self.assertEqual(55, normalized["highpass_hz"])
        self.assertEqual(0, normalized["lowpass_hz"])
        self.assertEqual(-16, normalized["master_loudness_i"])
        self.assertEqual(1.15, normalized["speech_speed_limit"])

    def test_non_dict_input_metadata_does_not_crash_recovery_offer(self):
        state = {
            "schema_version": vt.BATCH_RECOVERY_SCHEMA_VERSION,
            "batch_id": "legacy-broken-input",
            "status": "interrupted",
            "output_dir": "C:/out",
            "settings": {},
            "files": [{"status": "pending", "input": ["not", "a", "dict"]}],
        }
        harness = self.Harness()

        with mock.patch("videotranslator.ui.batch_recovery.load_batch_recovery_state", return_value=state), \
                mock.patch("videotranslator.ui.batch_recovery.messagebox.askyesno") as ask:
            harness._offer_batch_recovery()

        ask.assert_not_called()
        self.assertTrue(any(name == "batch_recovery_unavailable" for name, _ in harness.problems))

    def test_restore_batch_settings_accepts_non_dict_settings(self):
        class Value:
            def __init__(self):
                self.value = None

            def set(self, value):
                self.value = value

        harness = self.Harness()
        harness.combo_language = Value()
        harness.combo_model = Value()
        harness.var_keep = Value()
        harness.var_volume = Value()
        harness.var_review = Value()
        harness.var_voice_volume = Value()
        harness.var_highpass = Value()
        harness.var_lowpass = Value()
        harness.var_denoise = Value()
        harness.var_loudness = Value()
        harness.combo_speed = Value()
        harness._refresh_voice_list = lambda *_args, **_kwargs: None
        harness._apply_keep_state = lambda **_kwargs: None

        harness._restore_batch_settings(["broken"])

        self.assertEqual(vt.DEFAULT_TARGET_LANGUAGE, harness.combo_language.value)


class QueueProgressGenerationPass12Tests(unittest.TestCase):
    class Harness(UIControlsMixin):
        def __init__(self):
            self._active_task_id = "old-task"
            self.callbacks = []
            self.progress_updates = []

        def _safe_after(self, _delay, func, *args):
            self.callbacks.append((func, args))
            return True

        def _set_progress(self, value, status=""):
            self.progress_updates.append((value, status))

        def _update_active_task_progress(self, *_args):
            pass

    def test_late_progress_callback_from_previous_task_cannot_reset_new_task(self):
        harness = self.Harness()
        harness._threadsafe_progress(73, "Старая задача")
        progress_callback, args = harness.callbacks[0]

        harness._active_task_id = "new-task"
        progress_callback(*args)

        self.assertEqual([], harness.progress_updates)

    def test_current_task_progress_still_updates_global_progress(self):
        harness = self.Harness()

        harness._set_progress_for_task("old-task", 42, "Переводится")

        self.assertEqual([(42, "Переводится")], harness.progress_updates)


class DirtyQueueGatePass12Tests(unittest.TestCase):
    class Harness(UITasksMixin):
        def __init__(self):
            self._task_queue_lock = threading.RLock()
            self._task_queue_state = vt.create_task_queue_state()
            self._task_queue_dirty = True
            self._closing = False
            self._processing = False
            self._model_action_running = False
            self._worker_thread = None
            self._task_dispatch_after_id = None
            self._active_task_id = None
            self._resume_batch_state = None
            self.lbl_tasks_status = Pass12Label()
            self.launched = []

        def _problem(self, *_args, **_kwargs):
            pass

        def _refresh_tasks_view(self, *_args, **_kwargs):
            pass

        def _select_tasks_tab(self):
            pass

        def _set_busy(self, value):
            self._processing = bool(value)

        def _launch_queued_task(self, task):
            self.launched.append(task["task_id"])
            return True

        def _show_queue_save_error(self, _action):
            pass

    def test_dirty_terminal_state_must_persist_before_next_task_can_launch(self):
        harness = self.Harness()
        first = vt.create_translation_task(
            vt.create_batch_recovery_state(["C:/done.mp4"], "C:/out", {"model": "small"})
        )
        first["status"] = "completed"
        second = vt.create_translation_task(
            vt.create_batch_recovery_state(["C:/next.mp4"], "C:/out", {"model": "small"})
        )
        harness._task_queue_state["tasks"] = [first, second]

        with mock.patch("videotranslator.ui.tasks.save_task_queue_state", side_effect=OSError("disk full")):
            harness._dispatch_next_task()

        self.assertEqual([], harness.launched)
        self.assertEqual("queued", second["status"])
        self.assertTrue(harness._task_queue_dirty)
        self.assertIn("приостановлена", harness.lbl_tasks_status.text)


class ShutdownQueuePass12Tests(unittest.TestCase):
    class Worker:
        def __init__(self, alive=True):
            self.alive = alive
            self.name = "worker"

        def is_alive(self):
            return self.alive

    class Root:
        def __init__(self):
            self.callbacks = []
            self.destroyed = False

        def after(self, delay, callback):
            self.callbacks.append((delay, callback))
            return "after-id"

        def after_cancel(self, _callback_id):
            pass

        def destroy(self):
            self.destroyed = True

    class Harness(UISettingsMixin):
        def __init__(self):
            self.root = ShutdownQueuePass12Tests.Root()
            self._closing = False
            self._processing = False
            self._model_action_running = False
            self._settings_save_after_id = None
            self._task_dispatch_after_id = None
            self._worker_thread = ShutdownQueuePass12Tests.Worker(alive=True)
            self._close_deadline_monotonic = None
            self._cancel_event = threading.Event()
            self._resume_batch_state = None
            self._task_queue_dirty = False
            self.file_logger = None
            self.problem_logger = None
            self.events = []
            self.persist_calls = 0
            self.settings_saved = 0

        def _problem(self, event, **details):
            self.events.append((event, details))

        def _close_vpn_notice(self):
            pass

        def save_settings(self):
            self.settings_saved += 1

        def _persist_task_queue_safely(self):
            self.persist_calls += 1
            self._task_queue_dirty = False
            return True

    def test_close_waits_for_live_worker_even_after_busy_flag_was_cleared(self):
        harness = self.Harness()

        with mock.patch("videotranslator.ui.settings.messagebox.askyesno") as ask:
            harness._on_close()

        ask.assert_not_called()
        self.assertTrue(harness._closing)
        self.assertTrue(harness._cancel_event.is_set())
        self.assertFalse(harness.root.destroyed)
        self.assertEqual(1, len(harness.root.callbacks))

    def test_close_persists_queue_cancellation_request_before_waiting(self):
        harness = self.Harness()
        harness._processing = True
        harness._resume_batch_state = {"batch_id": "task-1", "status": "running"}

        with mock.patch("videotranslator.ui.settings.messagebox.askyesno", return_value=True), \
                mock.patch("videotranslator.ui.settings.save_batch_recovery_state"):
            harness._on_close()

        self.assertEqual("cancellation_requested", harness._resume_batch_state["status"])
        self.assertGreaterEqual(harness.persist_calls, 1)
        self.assertFalse(harness._task_queue_dirty)


class SingleInstanceQueuePass12Tests(unittest.TestCase):
    def test_second_process_lock_owner_is_rejected_and_release_allows_reacquire(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            lock_path = Path(temp_dir) / ".application.lock"
            first = ApplicationInstanceLock(lock_path)
            second = ApplicationInstanceLock(lock_path)
            first.acquire()
            try:
                with self.assertRaises(ApplicationInstanceAlreadyRunning):
                    second.acquire()
            finally:
                first.release()

            second.acquire()
            self.assertTrue(second.acquired)
            second.release()
            self.assertFalse(second.acquired)


class TaskActionLockPass12Tests(unittest.TestCase):
    class TrackingLock:
        def __init__(self):
            self.held = False

        def __enter__(self):
            self.held = True
            return self

        def __exit__(self, _exc_type, _exc, _tb):
            self.held = False

    class Harness(UITasksMixin):
        def __init__(self, task):
            self._task_queue_lock = TaskActionLockPass12Tests.TrackingLock()
            self._task_queue_state = vt.create_task_queue_state()
            self._task_queue_state["tasks"] = [task]
            self._task_queue_dirty = False
            self.selected_id = task["task_id"]
            self.refreshes = []
            self.dispatched = []

        def _selected_task_id(self):
            return self.selected_id

        def _persist_task_queue_safely(self):
            return True

        def _refresh_tasks_view(self, **kwargs):
            self.refreshes.append(kwargs)

        def _schedule_task_dispatch(self, delay):
            self.dispatched.append(delay)

        def _show_queue_save_error(self, *_args):
            raise AssertionError("unexpected save error")

    def test_completed_retry_dialog_is_shown_after_queue_lock_is_released(self):
        task = vt.create_translation_task(
            vt.create_batch_recovery_state(["C:/done.mp4"], "C:/out", {"model": "small"})
        )
        task["status"] = "completed"
        harness = self.Harness(task)

        def assert_unlocked(*_args, **_kwargs):
            self.assertFalse(harness._task_queue_lock.held)

        with mock.patch("videotranslator.ui.tasks.messagebox.showinfo", side_effect=assert_unlocked) as dialog:
            harness._retry_selected_task()

        dialog.assert_called_once()

    def test_running_remove_dialog_is_shown_after_queue_lock_is_released(self):
        task = vt.create_translation_task(
            vt.create_batch_recovery_state(["C:/running.mp4"], "C:/out", {"model": "small"})
        )
        task["status"] = "running"
        harness = self.Harness(task)

        def assert_unlocked(*_args, **_kwargs):
            self.assertFalse(harness._task_queue_lock.held)

        with mock.patch("videotranslator.ui.tasks.messagebox.showwarning", side_effect=assert_unlocked) as dialog:
            harness._remove_selected_task()

        dialog.assert_called_once()

    def test_retry_clears_stale_runtime_file_before_requeue(self):
        task = vt.create_translation_task(
            vt.create_batch_recovery_state(["C:/failed.mp4"], "C:/out", {"model": "small"})
        )
        task["status"] = "failed"
        task["runtime"].update({"current_file": "old.mp4", "file_index": 7, "progress": 88})
        harness = self.Harness(task)

        harness._retry_selected_task()

        runtime = task["runtime"]
        self.assertEqual("", runtime["current_file"])
        self.assertEqual(0, runtime["file_index"])
        self.assertEqual(0, runtime["progress"])
        self.assertEqual("queued", task["status"])
        self.assertEqual([10], harness.dispatched)


class CancelIntentPass12Tests(unittest.TestCase):
    class Button:
        def __init__(self):
            self.states = []

        def config(self, **kwargs):
            self.states.append(kwargs)

    class Harness(UIControlsMixin, UITasksMixin):
        def __init__(self):
            self._processing = True
            self._cancel_event = threading.Event()
            self.btn_cancel = CancelIntentPass12Tests.Button()
            self._active_task_id = "task-cancel"
            self._task_queue_lock = threading.RLock()
            self._task_queue_state = vt.create_task_queue_state()
            batch = vt.create_batch_recovery_state(["C:/video.mp4"], "C:/out", {"model": "small"})
            batch["batch_id"] = "task-cancel"
            task = vt.create_translation_task(batch)
            task["status"] = "running"
            task["batch_state"]["status"] = "running"
            self._task_queue_state["tasks"] = [task]
            self._task_queue_dirty = False
            self.persist_calls = 0

        def _log(self, *_args):
            pass

        def _persist_task_queue_safely(self):
            self.persist_calls += 1
            return True

        def _refresh_tasks_view(self, *_args, **_kwargs):
            pass

        def _problem(self, *_args, **_kwargs):
            pass

    def test_cancel_button_persists_cancellation_intent_before_worker_finishes(self):
        harness = self.Harness()

        harness.cancel()

        task = harness._task_queue_state["tasks"][0]
        self.assertTrue(harness._cancel_event.is_set())
        self.assertEqual("running", task["status"])
        self.assertEqual("cancellation_requested", task["batch_state"]["status"])
        self.assertEqual("Остановка...", task["runtime"]["status_text"])
        self.assertEqual(1, harness.persist_calls)


class SavedSettingsOverflowPass12Tests(unittest.TestCase):
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

    def test_infinite_saved_original_volume_fails_cleanly_before_thread_start(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            batch = vt.create_batch_recovery_state(["C:/video.mp4"], temp_dir, {})
            batch["settings"] = {
                "language_label": vt.DEFAULT_TARGET_LANGUAGE,
                "voice": "voice-code",
                "model": "small",
                "original_volume_pct": float("inf"),
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
