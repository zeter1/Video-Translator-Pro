import tempfile
import os
import threading
import unittest
from pathlib import Path
from unittest import mock

import video_translator as vt
from videotranslator.ui.batch_start import UIBatchStartMixin


class DurableTaskQueueTests(unittest.TestCase):
    def test_rapid_tasks_keep_distinct_persistent_ids(self):
        batches = [
            vt.create_batch_recovery_state(["C:/video.mp4"], "C:/out", {})
            for _ in range(100)
        ]
        self.assertEqual(100, len({batch["batch_id"] for batch in batches}))
        tasks = [vt.create_translation_task(batch) for batch in batches]
        self.assertEqual(100, len({task["task_id"] for task in tasks}))

    def test_fifo_round_trip_and_next_task_order(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            out = root / "out"
            out.mkdir()
            one = root / "one.mp4"
            two = root / "two.mp4"
            one.write_bytes(b"1")
            two.write_bytes(b"2")
            first = vt.create_translation_task(vt.create_batch_recovery_state([str(one)], str(out), {"model": "small"}))
            second = vt.create_translation_task(vt.create_batch_recovery_state([str(two)], str(out), {"model": "small"}))
            state = vt.create_task_queue_state()
            state["tasks"] = [first, second]
            path = root / "tasks.json"

            vt.save_task_queue_state(state, path)
            loaded = vt.load_task_queue_state(path)

            self.assertEqual(vt.next_runnable_task(loaded)["task_id"], first["task_id"])
            loaded["tasks"][0]["status"] = "completed"
            self.assertEqual(vt.next_runnable_task(loaded)["task_id"], second["task_id"])
            self.assertFalse(list(root.glob("*.tmp")))

    def test_corrupt_queue_is_preserved_before_safe_reset(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            path = root / "tasks.json"
            path.write_text("{broken json", encoding="utf-8")

            loaded = vt.load_task_queue_state(path)

            self.assertEqual([], loaded["tasks"])
            backups = list(root.glob("tasks.corrupt.*.json"))
            self.assertEqual(1, len(backups))
            self.assertEqual("{broken json", backups[0].read_text(encoding="utf-8"))

    def test_running_task_becomes_resumable_after_restart(self):
        state = vt.create_task_queue_state()
        batch = vt.create_batch_recovery_state(["C:/video.mp4"], "C:/out", {"model": "small"})
        task = vt.create_translation_task(batch)
        task["status"] = "running"
        task["batch_state"]["status"] = "running"
        task["batch_state"]["files"][0]["status"] = "processing"
        state["tasks"].append(task)

        changed = vt.recover_interrupted_tasks(state)

        self.assertTrue(changed)
        self.assertEqual(task["status"], "interrupted")
        self.assertEqual(task["batch_state"]["status"], "interrupted")
        self.assertEqual(vt.next_runnable_task(state)["task_id"], task["task_id"])
        # Per-file processing state is intentionally retained so the worker can
        # validate an existing output/checkpoint before deciding what to redo.
        self.assertEqual(task["batch_state"]["files"][0]["status"], "processing")


class QueueModeStartTests(unittest.TestCase):
    def test_start_enqueues_snapshot_while_another_task_is_processing(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            source = root / "next.mp4"
            source.write_bytes(b"input")
            out = root / "out"
            out.mkdir()

            class Value:
                def __init__(self, value):
                    self.value = value

                def get(self):
                    return self.value

            class Harness(UIBatchStartMixin):
                def __init__(self):
                    self._task_queue_state = {"schema_version": 1, "tasks": []}
                    self._processing = True
                    self._model_action_running = False
                    self._resume_batch_state = {"batch_id": "active-must-not-be-reused"}
                    self.video_files = [str(source)]
                    self.combo_language = Value(vt.DEFAULT_TARGET_LANGUAGE)
                    voice_label = next(iter(vt.get_voice_options(vt.DEFAULT_TARGET_LANGUAGE)))
                    self.combo_voice = Value(voice_label)
                    self.combo_model = Value(next(iter(vt.MODELS_MAP)))
                    self.var_keep = Value(False)
                    self.var_volume = Value(15)
                    self.var_review = Value(False)
                    self.enqueued = []

                def _get_audio_settings(self):
                    return vt.normalize_audio_settings()

                def _enqueue_batch_task(self, state):
                    self.enqueued.append(state)

                def _log(self, _message):
                    pass

            harness = Harness()
            with mock.patch("videotranslator.ui.batch_start.filedialog.askdirectory", return_value=str(out)), \
                    mock.patch("videotranslator.ui.batch_start.threading.Thread") as thread_class:
                harness.start()

            self.assertEqual(1, len(harness.enqueued))
            queued = harness.enqueued[0]
            self.assertNotEqual("active-must-not-be-reused", queued["batch_id"])
            self.assertEqual("queued", queued["status"])
            self.assertTrue(os.path.samefile(source, queued["files"][0]["input"]["path"]))
            thread_class.assert_not_called()

    def test_start_uses_visible_selected_output_directory_without_prompt(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            source = root / "source.mp4"
            source.write_bytes(b"input")
            out = root / "chosen-output"
            out.mkdir()

            class Value:
                def __init__(self, value):
                    self.value = value

                def get(self):
                    return self.value

                def set(self, value):
                    self.value = value

            class Harness(UIBatchStartMixin):
                def __init__(self):
                    self._task_queue_state = {"schema_version": 1, "tasks": []}
                    self._processing = False
                    self._model_action_running = False
                    self._resume_batch_state = None
                    self.video_files = [str(source)]
                    self.var_output_dir = Value(str(out))
                    self.combo_language = Value(vt.DEFAULT_TARGET_LANGUAGE)
                    voice_label = next(iter(vt.get_voice_options(vt.DEFAULT_TARGET_LANGUAGE)))
                    self.combo_voice = Value(voice_label)
                    self.combo_model = Value(next(iter(vt.MODELS_MAP)))
                    self.var_keep = Value(False)
                    self.var_volume = Value(15)
                    self.var_review = Value(False)
                    self.enqueued = []
                    self.saved_settings = 0

                def _get_audio_settings(self):
                    return vt.normalize_audio_settings()

                def _enqueue_batch_task(self, state):
                    self.enqueued.append(state)
                    return state

                def _log(self, _message):
                    pass

                def save_settings(self):
                    self.saved_settings += 1

            harness = Harness()
            with mock.patch("videotranslator.ui.batch_start.filedialog.askdirectory") as ask_directory:
                harness.start()

            ask_directory.assert_not_called()
            self.assertEqual(1, len(harness.enqueued))
            self.assertTrue(os.path.samefile(out, harness.enqueued[0]["output_dir"]))
            self.assertTrue(os.path.samefile(out, harness.var_output_dir.get()))
            self.assertEqual(1, harness.saved_settings)


if __name__ == "__main__":
    unittest.main()
