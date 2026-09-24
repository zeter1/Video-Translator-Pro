"""Method owner for App: add_files, remove_selected, clear_all, _update_count."""

from __future__ import annotations

from tkinter import filedialog
import os
import tkinter as tk


class UIQueueMixin:
    """Behavior-preserving methods extracted from the legacy monolith."""

    def add_files(self):
        paths = filedialog.askopenfilenames(
            filetypes=[
                ("Видео", "*.mp4 *.mkv *.avi *.mov *.webm *.ts *.flv"),
                ("Все", "*.*"),
            ]
        )
        existing = {os.path.normcase(os.path.abspath(p)) for p in self.video_files}
        for path in paths:
            normalized = os.path.normcase(os.path.abspath(path))
            if path and normalized not in existing:
                self.video_files.append(path)
                existing.add(normalized)
                self.listbox.insert(tk.END, os.path.basename(path))
        if paths and not getattr(self, "_processing", False):
            self._resume_batch_state = None
        self._update_count()


    def remove_selected(self):
        selected = list(self.listbox.curselection())
        for index in reversed(selected):
            self.listbox.delete(index)
            del self.video_files[index]
        if selected and not getattr(self, "_processing", False):
            self._resume_batch_state = None
        self._update_count()


    def clear_all(self):
        self.listbox.delete(0, tk.END)
        self.video_files.clear()
        if not getattr(self, "_processing", False):
            self._resume_batch_state = None
        self._update_count()


    def _update_count(self):
        self.lbl_count.config(text=f"Файлов: {len(self.video_files)}")
