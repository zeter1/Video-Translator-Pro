import threading
import unittest
from types import SimpleNamespace
from unittest import mock

import video_translator as vt
from videotranslator.ui.task_details import build_task_details_snapshot
from videotranslator.ui.tasks import UITasksMixin


class TaskDetailsSnapshotTests(unittest.TestCase):
    def test_running_task_snapshot_exposes_live_stage_and_all_video_rows(self):
        batch = vt.create_batch_recovery_state(
            ["C:/videos/one.mp4", "C:/videos/two.mp4", "C:/videos/three.mp4"],
            "C:/translated",
            {"language_label": "Русский", "voice": "ru-RU-DmitryNeural", "model": "small"},
        )
        task = vt.create_translation_task(batch)
        task["status"] = "running"
        task["runtime"].update({
            "current_file": "two.mp4",
            "file_index": 2,
            "progress": 47,
            "status_text": "Озвучка сегментов 12/25",
            "log_path": "C:/logs/task.txt",
        })
        task["batch_state"]["files"][0].update({"status": "succeeded", "elapsed_sec": 65.2})
        task["batch_state"]["files"][1].update({"status": "processing", "output_path": "C:/translated/two_RU.mp4"})
        task["batch_state"]["files"][2].update({"status": "failed", "last_error": "network timeout"})

        snapshot = build_task_details_snapshot(task)

        self.assertEqual("Переводится", snapshot["status_label"])
        self.assertEqual("Озвучка сегментов 12/25", snapshot["status_text"])
        self.assertEqual(47, snapshot["progress"])
        self.assertEqual("two.mp4", snapshot["current_file"])
        self.assertEqual(3, snapshot["files_total"])
        self.assertEqual(1, snapshot["succeeded"])
        self.assertEqual(1, snapshot["failed"])
        self.assertTrue(snapshot["rows"][1]["is_current"])
        self.assertEqual("Ошибка", snapshot["rows"][2]["status_label"])
        self.assertEqual("network timeout", snapshot["rows"][2]["last_error"])
        self.assertEqual("C:/logs/task.txt", snapshot["log_path"])

    def test_snapshot_tolerates_malformed_historical_file_row(self):
        task = {
            "task_id": "old-task",
            "status": "interrupted",
            "runtime": {"progress": "bad"},
            "batch_state": {
                "output_dir": "C:/out",
                "settings": {},
                "files": [None, {"file_index": "bad", "input": "broken", "status": "missing"}],
            },
        }

        snapshot = build_task_details_snapshot(task)

        self.assertEqual(0, snapshot["progress"])
        self.assertEqual(2, snapshot["files_total"])
        self.assertEqual("Файл недоступен", snapshot["rows"][1]["status_label"])


class TaskDetailsRoutingTests(unittest.TestCase):
    class FakeTree:
        def __init__(self):
            self.selected = []
            self.seen = []

        def identify_row(self, _y):
            return "task-2"

        def selection_set(self, task_id):
            self.selected = [task_id]

        def see(self, task_id):
            self.seen.append(task_id)

        def selection(self):
            return tuple(self.selected)

    class Harness(UITasksMixin):
        def __init__(self):
            self.tasks_tree = TaskDetailsRoutingTests.FakeTree()
            self._task_queue_lock = threading.RLock()
            self._task_queue_state = {"tasks": [{"task_id": "task-2", "batch_state": {"files": []}}]}
            self.opened = []

        def _open_task_details(self, task_id):
            self.opened.append(task_id)

    def test_double_click_opens_the_row_under_pointer(self):
        harness = self.Harness()

        harness._on_tasks_tree_double_click(SimpleNamespace(y=20))

        self.assertEqual(["task-2"], harness.opened)
        self.assertEqual(["task-2"], harness.tasks_tree.selected)

    def test_task_snapshot_is_deep_copy(self):
        harness = self.Harness()

        snapshot = UITasksMixin._task_snapshot(harness, "task-2")
        snapshot["batch_state"]["files"].append({"status": "failed"})

        self.assertEqual([], harness._task_queue_state["tasks"][0]["batch_state"]["files"])


if __name__ == "__main__":
    unittest.main()
