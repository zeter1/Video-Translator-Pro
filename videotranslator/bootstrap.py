from __future__ import annotations

import tkinter as tk
from tkinter import messagebox

from videotranslator.core.instance_lock import ApplicationInstanceAlreadyRunning, ApplicationInstanceLock
from videotranslator.diagnostics.file_logger import FileLogger
from videotranslator.diagnostics.problem_logger import ProblemLogger
from videotranslator.ui.app import App


def main():
    startup_logger = None
    startup_problem_logger = None
    app = None
    instance_lock = ApplicationInstanceLock()
    try:
        instance_lock.acquire()
    except ApplicationInstanceAlreadyRunning:
        notice_root = None
        try:
            notice_root = tk.Tk()
            notice_root.withdraw()
            messagebox.showwarning(
                "Видео Переводчик PRO уже запущен",
                "Другая копия программы уже работает с этой очередью переводов.\n\n"
                "Используйте вкладку «Задачи» в уже запущенном окне — туда можно добавлять "
                "сколько угодно переводов, они выполнятся по порядку.",
                parent=notice_root,
            )
        except Exception:
            # A second instance must still exit cleanly on a headless/broken Tk
            # environment; inability to show the informational dialog does not
            # grant it ownership of the shared durable queue.
            pass
        finally:
            try:
                if notice_root is not None:
                    notice_root.destroy()
            except Exception:
                pass
        return
    try:
        root = tk.Tk()
        app = App(root)
        root.mainloop()
    except Exception as exc:
        try:
            startup_logger = FileLogger(prefix="video_translator_startup_error")
            startup_logger.write_exception("Критическая ошибка запуска программы", exc)
            startup_problem_logger = getattr(app, "problem_logger", None) if app is not None else None
            if startup_problem_logger is None:
                startup_problem_logger = ProblemLogger()
            startup_problem_logger.write_exception(
                "application_crashed",
                exc,
                message="Необработанная ошибка главного GUI-потока.",
            )
            messagebox.showerror(
                "Ошибка запуска",
                "Программа упала при запуске. Логи сохранены:\n"
                f"{startup_logger.path}\n"
                f"{startup_problem_logger.path}",
            )
        except Exception:
            pass
        raise
    finally:
        try:
            if startup_logger:
                startup_logger.close()
        except Exception:
            pass
        try:
            if startup_problem_logger:
                startup_problem_logger.close()
        except Exception:
            pass
        instance_lock.release()
