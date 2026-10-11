"""Persistent, nonmodal completion notices owned by the Tk main thread."""

from __future__ import annotations

import logging
import os
import threading
import tkinter as tk
from tkinter import messagebox, ttk


def _work_area(root) -> tuple[int, int, int, int]:
    """Primary desktop bounds excluding the Windows taskbar."""
    if os.name == "nt":
        import ctypes
        from ctypes import wintypes

        rect = wintypes.RECT()
        if ctypes.windll.user32.SystemParametersInfoW(0x0030, 0, ctypes.byref(rect), 0):
            return rect.left, rect.top, rect.right, rect.bottom
        logging.getLogger(__name__).warning("Не удалось определить рабочую область Windows.")
    return 0, 0, root.winfo_screenwidth(), root.winfo_screenheight()


class CompletionNotifications:
    """One scrollable window; each task remains until its own cross is clicked."""

    def __init__(self, root):
        self.root = root
        self.cards = []
        self.window = tk.Toplevel(root)
        self.window.withdraw()
        self.window.overrideredirect(True)
        self.window.attributes("-topmost", True)
        self.window.resizable(False, False)
        # No transient/grab: minimizing the app must not hide the notice or stop FIFO.
        self.window.protocol("WM_DELETE_WINDOW", lambda: None)
        self.window.bind("<Alt-F4>", lambda _event: "break")
        self.window.configure(background="#cbd5e1")
        self.canvas = tk.Canvas(self.window, background="#ffffff", highlightthickness=0)
        self.scrollbar = ttk.Scrollbar(self.window, orient="vertical", command=self.canvas.yview)
        self.canvas.configure(yscrollcommand=self.scrollbar.set)
        self.scrollbar.pack(side="right", fill="y", padx=(0, 1), pady=1)
        self.canvas.pack(side="left", fill="both", expand=True, padx=(1, 0), pady=1)
        self.content = tk.Frame(self.canvas, background="#ffffff")
        self.content_id = self.canvas.create_window(0, 0, anchor="nw", window=self.content)

    def add(self, title: str, message: str, output_dir: str = "") -> None:
        card = tk.Frame(self.content, background="#ffffff", padx=16, pady=12)
        card.pack(fill="x")
        header = tk.Frame(card, background="#ffffff")
        header.pack(fill="x")
        tk.Button(
            header, text="×", command=lambda: self.dismiss(card), font=("Segoe UI", 14),
            background="#ffffff", foreground="#475569", activebackground="#e2e8f0",
            relief="flat", borderwidth=0, cursor="hand2", takefocus=True,
        ).pack(side="right")
        label = tk.Label(
            header, text=title, font=("Segoe UI", 11, "bold"), background="#ffffff",
            foreground="#0f172a", anchor="w", justify="left",
        )
        label.pack(side="left", fill="x", expand=True)
        details = tk.Label(
            card, text=message, font=("Segoe UI", 10), background="#ffffff",
            foreground="#334155", anchor="w", justify="left",
        )
        details.pack(fill="x", pady=(6, 10))
        # Capture this task's folder; later jobs can use a different disk/folder.
        if output_dir:
            folder = os.path.abspath(output_dir)
            ttk.Button(
                card, text="Открыть папку", command=lambda: self.open_folder(folder),
            ).pack(anchor="w")
        self.cards.append((card, label, details))
        self.window.title(title)
        self._layout()
        self.window.deiconify()
        self.window.lift()
        self.canvas.yview_moveto(1.0)

    def _layout(self) -> None:
        left, top, right, bottom = _work_area(self.root)
        width = min(440, max(160, right - left - 32))
        for _card, label, details in self.cards:
            label.configure(wraplength=max(80, width - 90))
            details.configure(wraplength=max(100, width - 60))
        self.canvas.itemconfigure(self.content_id, width=width - 20)
        self.window.update_idletasks()
        height = min(self.content.winfo_reqheight() + 2, 520, max(100, bottom - top - 32))
        self.window.geometry(f"{width}x{height}+{right - width - 16}+{bottom - height - 16}")
        self.canvas.configure(scrollregion=self.canvas.bbox("all"))

    def dismiss(self, card) -> None:
        self.cards = [item for item in self.cards if item[0] is not card]
        card.destroy()
        if self.cards:
            self._layout()
        else:
            self.root._translation_notifications = None
            self.window.destroy()

    def open_folder(self, folder: str) -> None:
        try:
            if not os.path.isdir(folder):
                raise FileNotFoundError("Папка недоступна. Проверьте, подключён ли диск.")
            if os.name != "nt":
                raise OSError("Открытие папки этой кнопкой поддерживается в Windows.")
            os.startfile(folder)
        except OSError as exc:
            messagebox.showerror("Не удалось открыть папку", f"{folder}\n\n{exc}", parent=self.window)
        # Opening Explorer (including an error) never dismisses the notice.


def show_topmost_notification(root, title: str, message: str, *, output_dir: str = "") -> None:
    """Called by task completion on the GUI thread, never by the batch worker."""
    if threading.current_thread() is not threading.main_thread():
        raise RuntimeError("Уведомление должно создаваться в главном потоке Tkinter.")
    try:
        notices = getattr(root, "_translation_notifications", None)
        if notices is None or not notices.window.winfo_exists():
            notices = CompletionNotifications(root)
            root._translation_notifications = notices
        notices.add(str(title), str(message), str(output_dir or ""))
    except tk.TclError:
        logging.getLogger(__name__).exception("Не удалось показать уведомление о завершении перевода.")
