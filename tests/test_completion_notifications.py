import os
import tempfile
import threading
import tkinter as tk
import unittest
from pathlib import Path
from unittest import mock

from videotranslator.ui import notifications


@unittest.skipUnless(os.name == "nt", "Real Windows Tk notification checks")
class CompletionNotificationTests(unittest.TestCase):
    def setUp(self):
        self.root = tk.Tk()
        self.root.withdraw()
        self.addCleanup(self.root.destroy)

    def show(self, folder="", message="Задача успешно завершена: 1/1."):
        notifications.show_topmost_notification(
            self.root, "Перевод завершён", message, output_dir=folder,
        )
        self.root.update()
        return self.root._translation_notifications

    @staticmethod
    def button(card, text):
        for widget in card.winfo_children():
            if isinstance(widget, (tk.Button, notifications.ttk.Button)) and widget.cget("text") == text:
                return widget
            for child in widget.winfo_children():
                if isinstance(child, tk.Button) and child.cget("text") == text:
                    return child
        raise AssertionError(f"Button not found: {text}")

    def test_visible_topmost_at_bottom_right_without_timer_or_modal_grab(self):
        with mock.patch.object(self.root, "after", side_effect=AssertionError("Auto-dismiss timer")):
            notice = self.show()
        left, top, right, bottom = notifications._work_area(self.root)
        self.assertTrue(notice.window.winfo_viewable())
        self.assertTrue(notice.window.attributes("-topmost"))
        self.assertIsNone(self.root.grab_current())
        self.assertEqual(right - 16, notice.window.winfo_x() + notice.window.winfo_width())
        self.assertEqual(bottom - 16, notice.window.winfo_y() + notice.window.winfo_height())
        self.assertGreaterEqual(notice.window.winfo_x(), left)
        self.assertGreaterEqual(notice.window.winfo_y(), top)
        # A WM close request cannot bypass the dedicated cross.
        self.root.tk.call(notice.window.protocol("WM_DELETE_WINDOW"))
        self.assertTrue(notice.window.winfo_exists())

    def test_open_folder_keeps_notice_and_cross_allows_next_notice(self):
        with tempfile.TemporaryDirectory(prefix="vt-уведомление ") as directory:
            folder = Path(directory) / "Готовые видео"
            folder.mkdir()
            notice = self.show(str(folder))
            card = notice.cards[0][0]
            with mock.patch.object(os, "startfile") as startfile:
                self.button(card, "Открыть папку").invoke()
            startfile.assert_called_once()
            # Windows may use an 8.3 short alias (RUNNER~1) for this folder.
            # Check the actual directory identity, not the string spelling.
            self.assertTrue(os.path.samefile(startfile.call_args.args[0], folder))
            self.assertTrue(notice.window.winfo_exists())
            self.button(card, "×").invoke()
            self.assertFalse(notice.window.winfo_exists())
            self.assertIsNone(self.root._translation_notifications)
            second = self.show()
            self.assertIsNot(second, notice)
            self.assertTrue(second.window.winfo_viewable())

    def test_multiple_tasks_keep_their_own_folders_until_cross(self):
        with tempfile.TemporaryDirectory() as directory:
            first, second = Path(directory) / "Первое", Path(directory) / "Второе"
            first.mkdir()
            second.mkdir()
            notice = self.show(str(first))
            first_card = notice.cards[0][0]
            self.assertIs(notice, self.show(str(second)))
            second_card = notice.cards[1][0]
            with mock.patch.object(os, "startfile") as open_folder:
                self.button(first_card, "Открыть папку").invoke()
                self.button(second_card, "Открыть папку").invoke()
            self.assertEqual([mock.call(str(first)), mock.call(str(second))], open_folder.call_args_list)
            self.button(second_card, "×").invoke()
            self.root.update()
            self.assertEqual(1, len(notice.cards))
            self.assertTrue(notice.window.winfo_viewable())
            self.button(first_card, "×").invoke()
            self.assertFalse(notice.window.winfo_exists())

    def test_missing_or_unavailable_disk_does_not_close_notice(self):
        with tempfile.TemporaryDirectory() as directory:
            missing = str(Path(directory) / "Нет папки")
            notice = self.show(missing)
            with mock.patch.object(notifications.messagebox, "showerror") as error, \
                    mock.patch.object(os, "startfile") as open_folder:
                self.button(notice.cards[0][0], "Открыть папку").invoke()
            error.assert_called_once()
            open_folder.assert_not_called()
            self.assertTrue(notice.window.winfo_exists())

    def test_many_notices_fit_work_area_and_remain_scrollable(self):
        for index in range(10):
            notice = self.show(message=f"Задача {index}: " + "Длинный путь к результатам " * 10)
        _, top, _, bottom = notifications._work_area(self.root)
        self.assertEqual(10, len(notice.cards))
        self.assertLessEqual(notice.window.winfo_height(), min(520, bottom - top - 32))
        self.assertGreater(notice.content.winfo_reqheight(), notice.canvas.winfo_height())
        notice.canvas.yview_moveto(0)
        self.root.update()
        self.assertEqual(0, notice.canvas.yview()[0])

    def test_root_shutdown_owns_notification_without_pending_callbacks(self):
        notice = self.show()
        self.assertEqual("", self.root.tk.call("after", "info"))
        notice.window.destroy()
        replacement = self.show()
        self.assertIsNot(notice, replacement)


class NotificationThreadTests(unittest.TestCase):
    def test_worker_is_rejected_before_any_tk_access(self):
        errors = []

        def worker():
            try:
                notifications.show_topmost_notification(object(), "Готово", "Результат")
            except RuntimeError as exc:
                errors.append(str(exc))

        thread = threading.Thread(target=worker)
        thread.start()
        thread.join(timeout=2)
        self.assertFalse(thread.is_alive())
        self.assertEqual(1, len(errors))
