"""Method owner for App: review_translations, _open_translation_review_window."""

from __future__ import annotations

from pathlib import Path
from tkinter import filedialog
from tkinter import messagebox
import os
import re
from tkinter import scrolledtext
import threading
import tkinter as tk
from tkinter import ttk
from videotranslator.config import DEFAULT_TARGET_LANGUAGE, get_target_language
from videotranslator.core.diagnostics import safe_log_filename
from videotranslator.core.paths import get_translated_texts_dir
from videotranslator.core.timefmt import fmt_time

class UIReviewWindowMixin:
    """Focused behavior-preserving mixin extracted for AI-local navigation."""

    def _open_translation_review_window(self, segments: list, input_path: str, source_lang: str,
                                        target_info: dict, done: threading.Event, result: dict):
        segments_copy = [dict(seg) for seg in (segments or [])]
        if not segments_copy:
            done.set()
            return

        target_info = dict(target_info or get_target_language(DEFAULT_TARGET_LANGUAGE))
        target_name = target_info.get("name") or target_info.get("code") or "перевод"
        source_label = source_lang or "auto"
        current = {"idx": 0, "loading": False}

        window = tk.Toplevel(self.root)
        window.title("Проверка перевода перед озвучкой")
        window.geometry("880x650")
        window.minsize(760, 560)
        window.transient(self.root)
        window.grab_set()
        window.columnconfigure(1, weight=1)
        window.rowconfigure(1, weight=1)

        def bring_to_front():
            try:
                window.deiconify()
                window.lift()
                window.attributes("-topmost", True)
                window.focus_force()
                def clear_topmost():
                    try:
                        if window.winfo_exists():
                            window.attributes("-topmost", False)
                    except Exception:
                        pass
                window.after(900, clear_topmost)
            except Exception:
                pass

        window.after_idle(bring_to_front)

        ttk.Label(
            window,
            text=f"{os.path.basename(input_path)}   |   {source_label} → {target_name}",
            font=("Arial", 10, "bold"),
        ).grid(row=0, column=0, columnspan=2, sticky="ew", padx=10, pady=(10, 6))

        left_frame = ttk.Frame(window)
        left_frame.grid(row=1, column=0, sticky="ns", padx=(10, 6), pady=6)
        ttk.Label(left_frame, text="Сегменты").pack(anchor="w")
        segment_scroll = ttk.Scrollbar(left_frame)
        segment_scroll.pack(side="right", fill="y")
        segment_list = tk.Listbox(
            left_frame,
            width=24,
            height=24,
            yscrollcommand=segment_scroll.set,
            exportselection=False,
            font=("Consolas", 9),
        )
        segment_list.pack(side="left", fill="y")
        segment_scroll.config(command=segment_list.yview)

        for idx, seg in enumerate(segments_copy, 1):
            segment_list.insert(
                tk.END,
                f"{idx:03d}  {fmt_time(seg.get('start', 0.0))} → {fmt_time(seg.get('end', 0.0))}",
            )

        edit_frame = ttk.Frame(window)
        edit_frame.grid(row=1, column=1, sticky="nsew", padx=(6, 10), pady=6)
        edit_frame.columnconfigure(0, weight=1)
        edit_frame.rowconfigure(2, weight=1)
        edit_frame.rowconfigure(4, weight=2)

        lbl_segment = ttk.Label(edit_frame, text="")
        lbl_segment.grid(row=0, column=0, sticky="w", pady=(0, 4))

        ttk.Label(edit_frame, text="Исходный текст").grid(row=1, column=0, sticky="w")
        source_text = scrolledtext.ScrolledText(edit_frame, height=7, wrap="word", font=("Arial", 10))
        source_text.grid(row=2, column=0, sticky="nsew", pady=(2, 8))
        source_text.config(state="disabled")

        ttk.Label(edit_frame, text=f"Перевод ({target_name})").grid(row=3, column=0, sticky="w")
        translated_text = scrolledtext.ScrolledText(edit_frame, height=10, wrap="word", font=("Arial", 10))
        translated_text.grid(row=4, column=0, sticky="nsew", pady=(2, 0))

        buttons = ttk.Frame(window)
        buttons.grid(row=2, column=0, columnspan=2, sticky="ew", padx=10, pady=(4, 10))
        buttons.columnconfigure(5, weight=1)

        def set_text(widget, text: str, readonly: bool = False):
            widget.config(state="normal")
            widget.delete("1.0", tk.END)
            widget.insert("1.0", text or "")
            if readonly:
                widget.config(state="disabled")

        def save_current():
            idx = current["idx"]
            if 0 <= idx < len(segments_copy):
                segments_copy[idx]["translated"] = translated_text.get("1.0", tk.END).strip()

        def make_source_review_text() -> str:
            lines = []
            for idx, seg in enumerate(segments_copy, 1):
                lines.append(f"### {idx:03d} | {fmt_time(seg.get('start', 0.0))} -> {fmt_time(seg.get('end', 0.0))}")
                lines.append((seg.get("source") or "").strip())
                lines.append("")
            return "\n".join(lines).strip() + "\n"

        def make_translation_review_text() -> str:
            lines = [
                "# Файл правки перевода.",
                "# Можно менять только текст после строки ПЕРЕВОД:",
                "# Строки вида ### 001 | 00:00 -> 00:05 и ### END лучше не менять.",
                "",
            ]
            for idx, seg in enumerate(segments_copy, 1):
                lines.append(f"### {idx:03d} | {fmt_time(seg.get('start', 0.0))} -> {fmt_time(seg.get('end', 0.0))}")
                lines.append("ИСХОДНЫЙ:")
                lines.append((seg.get("source") or "").strip())
                lines.append("ПЕРЕВОД:")
                lines.append((seg.get("translated") or "").strip())
                lines.append("### END")
                lines.append("")
            return "\n".join(lines).rstrip() + "\n"

        def parse_translation_review_text(text: str) -> dict[int, str]:
            normalized = (text or "").replace("\r\n", "\n").replace("\r", "\n")
            pattern = re.compile(
                r"(?ms)^###\s+(\d+)\s+\|[^\n]*\nИСХОДНЫЙ:\n.*?\nПЕРЕВОД:\n(.*?)^### END\s*$"
            )
            updates = {}
            for match in pattern.finditer(normalized):
                idx = int(match.group(1)) - 1
                if 0 <= idx < len(segments_copy):
                    updates[idx] = match.group(2).strip()
            return updates

        def apply_translation_review_text(text: str, parent_window) -> bool:
            updates = parse_translation_review_text(text)
            if not updates:
                messagebox.showerror(
                    "Не удалось прочитать TXT",
                    "Не нашёл блоки перевода. Проверьте, что строки ### 001 и ### END остались на месте.",
                    parent=parent_window,
                )
                return False

            missing_count = len(segments_copy) - len(updates)
            if missing_count > 0:
                if not messagebox.askyesno(
                    "Загружены не все сегменты",
                    f"В файле найдено {len(updates)} из {len(segments_copy)} сегментов.\n"
                    f"Обновить найденные, остальные оставить как есть?",
                    parent=parent_window,
                ):
                    return False

            for idx, value in updates.items():
                segments_copy[idx]["translated"] = value
            load_segment(current["idx"])
            messagebox.showinfo(
                "Перевод загружен",
                f"Обновлено сегментов: {len(updates)}",
                parent=parent_window,
            )
            return True

        def save_review_text_to_file(text: str, parent_window) -> str:
            default_name = safe_log_filename(Path(input_path).stem or "translation")
            target_code = str(target_info.get("code") or "target").replace("-", "_")
            path = filedialog.asksaveasfilename(
                parent=parent_window,
                title="Сохранить текст перевода",
                initialdir=str(get_translated_texts_dir()),
                initialfile=f"{default_name}_{target_code}_edit.txt",
                defaultextension=".txt",
                filetypes=[("Текстовый файл", "*.txt"), ("Все файлы", "*.*")],
            )
            if not path:
                return ""
            with open(path, "w", encoding="utf-8") as file:
                file.write(text)
            messagebox.showinfo("TXT сохранён", path, parent=parent_window)
            return path

        def export_translation_txt():
            save_current()
            save_review_text_to_file(make_translation_review_text(), window)

        def import_translation_txt(parent_window=window, editor_widget=None):
            path = filedialog.askopenfilename(
                parent=parent_window,
                title="Загрузить исправленный текст перевода",
                initialdir=str(get_translated_texts_dir()),
                filetypes=[("Текстовый файл", "*.txt"), ("Все файлы", "*.*")],
            )
            if not path:
                return False
            with open(path, "r", encoding="utf-8-sig", errors="replace") as file:
                text = file.read()
            if editor_widget is not None:
                set_text(editor_widget, text, readonly=False)
            return apply_translation_review_text(text, parent_window)

        def open_full_text_editor():
            save_current()

            bulk = tk.Toplevel(window)
            bulk.title("Весь текст перевода")
            bulk.geometry("1100x720")
            bulk.minsize(900, 600)
            bulk.transient(window)
            bulk.columnconfigure(0, weight=1)
            bulk.columnconfigure(1, weight=1)
            bulk.rowconfigure(1, weight=1)

            ttk.Label(
                bulk,
                text=f"{os.path.basename(input_path)}   |   {source_label} -> {target_name}",
                font=("Arial", 10, "bold"),
            ).grid(row=0, column=0, columnspan=2, sticky="ew", padx=10, pady=(10, 6))

            source_frame = ttk.LabelFrame(bulk, text="Весь исходный текст", padding=6)
            source_frame.grid(row=1, column=0, sticky="nsew", padx=(10, 5), pady=6)
            source_frame.rowconfigure(0, weight=1)
            source_frame.columnconfigure(0, weight=1)
            source_all = scrolledtext.ScrolledText(source_frame, wrap="word", font=("Arial", 10))
            source_all.grid(row=0, column=0, sticky="nsew")
            set_text(source_all, make_source_review_text(), readonly=True)

            translation_frame = ttk.LabelFrame(bulk, text=f"Весь перевод ({target_name})", padding=6)
            translation_frame.grid(row=1, column=1, sticky="nsew", padx=(5, 10), pady=6)
            translation_frame.rowconfigure(0, weight=1)
            translation_frame.columnconfigure(0, weight=1)
            translation_all = scrolledtext.ScrolledText(translation_frame, wrap="word", font=("Arial", 10))
            translation_all.grid(row=0, column=0, sticky="nsew")
            set_text(translation_all, make_translation_review_text(), readonly=False)

            bulk_buttons = ttk.Frame(bulk)
            bulk_buttons.grid(row=2, column=0, columnspan=2, sticky="ew", padx=10, pady=(4, 10))
            bulk_buttons.columnconfigure(3, weight=1)

            def apply_bulk_text():
                return apply_translation_review_text(translation_all.get("1.0", tk.END), bulk)

            ttk.Button(bulk_buttons, text="Применить в сегменты", command=apply_bulk_text).grid(row=0, column=0, sticky="w", padx=(0, 6))
            ttk.Button(
                bulk_buttons,
                text="Сохранить TXT",
                command=lambda: save_review_text_to_file(translation_all.get("1.0", tk.END), bulk),
            ).grid(row=0, column=1, sticky="w", padx=(0, 6))
            ttk.Button(
                bulk_buttons,
                text="Загрузить TXT",
                command=lambda: import_translation_txt(bulk, translation_all),
            ).grid(row=0, column=2, sticky="w")
            ttk.Button(bulk_buttons, text="Закрыть", command=bulk.destroy).grid(row=0, column=4, sticky="e")

        def load_segment(idx: int):
            idx = max(0, min(len(segments_copy) - 1, int(idx)))
            current["loading"] = True
            current["idx"] = idx
            segment_list.selection_clear(0, tk.END)
            segment_list.selection_set(idx)
            segment_list.see(idx)
            seg = segments_copy[idx]
            lbl_segment.config(
                text=f"Сегмент {idx + 1}/{len(segments_copy)}   "
                     f"{fmt_time(seg.get('start', 0.0))} → {fmt_time(seg.get('end', 0.0))}"
            )
            set_text(source_text, (seg.get("source") or "").strip(), readonly=True)
            set_text(translated_text, (seg.get("translated") or "").strip(), readonly=False)
            translated_text.focus_set()
            current["loading"] = False

        def on_select(_event=None):
            if current["loading"]:
                return
            selection = segment_list.curselection()
            if not selection:
                return
            save_current()
            load_segment(selection[0])

        def move(delta: int):
            save_current()
            load_segment(current["idx"] + delta)

        def finish():
            save_current()
            empty = [str(i + 1) for i, seg in enumerate(segments_copy) if not (seg.get("translated") or "").strip()]
            if empty:
                preview = ", ".join(empty[:12]) + ("..." if len(empty) > 12 else "")
                if not messagebox.askyesno(
                    "Есть пустые сегменты",
                    f"Пустые сегменты не будут озвучены: {preview}\nПродолжить?",
                    parent=window,
                ):
                    return
            result["segments"] = segments_copy
            close_window(cancelled=False)

        def cancel_editor():
            if messagebox.askyesno(
                "Отменить обработку?",
                "Закрыть проверку и отменить обработку текущего видео?",
                parent=window,
            ):
                self._cancel_event.set()
                result["segments"] = None
                close_window(cancelled=True)

        def close_window(cancelled: bool):
            if done.is_set():
                return
            if cancelled:
                result["segments"] = None
            try:
                window.grab_release()
            except Exception:
                pass
            try:
                window.destroy()
            except Exception:
                pass
            done.set()

        def poll_cancel():
            if done.is_set():
                return
            if self._cancel_event.is_set() or self._closing:
                close_window(cancelled=True)
                return
            window.after(300, poll_cancel)

        ttk.Button(buttons, text="← Назад", command=lambda: move(-1)).grid(row=0, column=0, sticky="w", padx=(0, 6))
        ttk.Button(buttons, text="Дальше →", command=lambda: move(1)).grid(row=0, column=1, sticky="w", padx=(0, 6))
        ttk.Button(buttons, text="Весь текст", command=open_full_text_editor).grid(row=0, column=2, sticky="w", padx=(0, 6))
        ttk.Button(buttons, text="Сохранить TXT", command=export_translation_txt).grid(row=0, column=3, sticky="w", padx=(0, 6))
        ttk.Button(buttons, text="Загрузить TXT", command=import_translation_txt).grid(row=0, column=4, sticky="w")
        ttk.Button(buttons, text="Применить и продолжить", command=finish).grid(row=0, column=6, sticky="e", padx=(6, 6))
        ttk.Button(buttons, text="Отмена", command=cancel_editor).grid(row=0, column=7, sticky="e")

        segment_list.bind("<<ListboxSelect>>", on_select)
        window.protocol("WM_DELETE_WINDOW", cancel_editor)
        load_segment(0)
        poll_cancel()
