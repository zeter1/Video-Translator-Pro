from __future__ import annotations

import tkinter as tk
from tkinter import messagebox

from videotranslator.diagnostics.file_logger import FileLogger
from videotranslator.diagnostics.problem_logger import ProblemLogger
from videotranslator.ui.app import App


def main():
    startup_logger = None
    startup_problem_logger = None
    app = None
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
