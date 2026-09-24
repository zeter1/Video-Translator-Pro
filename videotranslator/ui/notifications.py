"""Desktop completion notifications with a Windows topmost fallback-free path."""

from __future__ import annotations

import os
import threading
from tkinter import messagebox


def show_topmost_notification(root, title: str, message: str) -> None:
    """Show a completion notice above other windows without blocking the queue worker."""
    if os.name == "nt":
        def worker():
            try:
                import ctypes

                # MB_OK | MB_ICONINFORMATION | MB_SETFOREGROUND | MB_TOPMOST
                flags = 0x00000000 | 0x00000040 | 0x00010000 | 0x00040000
                ctypes.windll.user32.MessageBoxW(None, str(message), str(title), flags)
            except Exception:
                # The queue itself must never fail because a notification API failed.
                pass

        threading.Thread(
            target=worker,
            name="translation-completion-notification",
            daemon=True,
        ).start()
        return

    try:
        messagebox.showinfo(title, message, parent=root)
    except Exception:
        pass
