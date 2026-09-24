"""Live task-details window for the durable translation queue."""

from __future__ import annotations

from copy import deepcopy
import os
import tkinter as tk
from tkinter import ttk
from typing import Callable


_TASK_STATUS_LABELS = {
    "queued": "В очереди",
    "running": "Переводится",
    "interrupted": "Будет продолжена",
    "completed": "Готово",
    "failed": "Ошибка",
    "cancelled": "Отменено",
}

_FILE_STATUS_LABELS = {
    "pending": "Ожидает",
    "processing": "Переводится",
    "succeeded": "Готово",
    "failed": "Ошибка",
    "missing": "Файл недоступен",
    "cancelled": "Отменено",
}


def _format_time(value: object) -> str:
    text = str(value or "").strip()
    return text.replace("T", " ")[:19] if text else "—"


def _format_elapsed(value: object) -> str:
    try:
        seconds = max(0.0, float(value or 0.0))
    except (TypeError, ValueError, OverflowError):
        return "—"
    if seconds <= 0:
        return "—"
    minutes, seconds = divmod(int(round(seconds)), 60)
    hours, minutes = divmod(minutes, 60)
    if hours:
        return f"{hours}:{minutes:02d}:{seconds:02d}"
    return f"{minutes}:{seconds:02d}"


def build_task_details_snapshot(task: dict | None) -> dict:
    """Build a display-only snapshot independent from Tk widgets.

    Keeping this formatter pure makes the window easy to verify without starting a
    real Tk session and prevents UI rendering from becoming a second owner of queue
    state semantics.
    """
    task = deepcopy(task) if isinstance(task, dict) else {}
    batch = task.get("batch_state") if isinstance(task.get("batch_state"), dict) else {}
    runtime = task.get("runtime") if isinstance(task.get("runtime"), dict) else {}
    settings = batch.get("settings") if isinstance(batch.get("settings"), dict) else {}
    entries = batch.get("files") if isinstance(batch.get("files"), list) else []

    task_status = str(task.get("status") or "queued")
    current_file = str(runtime.get("current_file") or "")
    try:
        progress = max(0, min(100, int(runtime.get("progress") or 0)))
    except (TypeError, ValueError, OverflowError):
        progress = 0

    rows = []
    succeeded = 0
    failed = 0
    processing = 0
    for position, raw_entry in enumerate(entries, 1):
        entry = raw_entry if isinstance(raw_entry, dict) else {}
        input_info = entry.get("input") if isinstance(entry.get("input"), dict) else {}
        input_path = str(input_info.get("path") or "")
        output_path = str(entry.get("output_path") or "")
        file_status = str(entry.get("status") or "pending")
        if file_status == "succeeded":
            succeeded += 1
        elif file_status in {"failed", "missing"}:
            failed += 1
        elif file_status == "processing":
            processing += 1
        try:
            file_index = max(1, int(entry.get("file_index") or position))
        except (TypeError, ValueError, OverflowError):
            file_index = position
        rows.append({
            "row_id": f"file-{position}",
            "file_index": file_index,
            "status": file_status,
            "status_label": _FILE_STATUS_LABELS.get(file_status, file_status or "—"),
            "input_path": input_path,
            "input_name": os.path.basename(input_path) or "<путь не записан>",
            "output_path": output_path,
            "output_name": os.path.basename(output_path) if output_path else "—",
            "elapsed": _format_elapsed(entry.get("elapsed_sec")),
            "last_error": str(entry.get("last_error") or ""),
            "is_current": bool(
                file_status == "processing"
                or (current_file and os.path.basename(input_path) == current_file)
            ),
        })

    total = len(rows)
    status_text = str(runtime.get("status_text") or "").strip()
    if not status_text:
        status_text = _TASK_STATUS_LABELS.get(task_status, task_status or "—")

    return {
        "task_id": str(task.get("task_id") or ""),
        "status": task_status,
        "status_label": _TASK_STATUS_LABELS.get(task_status, task_status or "—"),
        "status_text": status_text,
        "progress": progress,
        "current_file": current_file or "—",
        "file_index": int(runtime.get("file_index") or 0) if str(runtime.get("file_index") or "0").isdigit() else 0,
        "files_total": total,
        "succeeded": succeeded,
        "failed": failed,
        "processing": processing,
        "created_at": _format_time(task.get("created_at")),
        "started_at": _format_time(task.get("started_at")),
        "finished_at": _format_time(task.get("finished_at")),
        "output_dir": str(batch.get("output_dir") or "") or "—",
        "language": str(settings.get("language_label") or settings.get("target_language") or "—"),
        "voice": str(settings.get("voice") or "—"),
        "model": str(settings.get("model") or "—"),
        "log_path": str(runtime.get("log_path") or "") or "—",
        "rows": rows,
    }


class TaskDetailsWindow:
    """Non-modal, auto-refreshing details window for one queue task."""

    def __init__(
        self,
        master: tk.Misc,
        task_id: str,
        task_provider: Callable[[str], dict | None],
        *,
        on_close: Callable[[str], None] | None = None,
        refresh_ms: int = 500,
    ):
        self.task_id = str(task_id or "")
        self._task_provider = task_provider
        self._on_close = on_close
        self._refresh_ms = max(200, int(refresh_ms))
        self._after_id = None
        self._latest_snapshot: dict = {}

        self.window = tk.Toplevel(master)
        self.window.title("Подробности задачи")
        self.window.geometry("1180x760")
        self.window.minsize(900, 620)
        self.window.protocol("WM_DELETE_WINDOW", self.close)
        try:
            self.window.transient(master)
        except tk.TclError:
            pass

        self._build_ui()
        self.refresh()

    def exists(self) -> bool:
        try:
            return bool(self.window.winfo_exists())
        except tk.TclError:
            return False

    def focus(self) -> None:
        if not self.exists():
            return
        try:
            self.window.deiconify()
            self.window.lift()
            self.window.focus_force()
        except tk.TclError:
            pass

    def close(self) -> None:
        if self._after_id:
            try:
                self.window.after_cancel(self._after_id)
            except tk.TclError:
                pass
            self._after_id = None
        try:
            self.window.destroy()
        except tk.TclError:
            pass
        if self._on_close:
            try:
                self._on_close(self.task_id)
            except Exception:
                pass

    def _build_ui(self) -> None:
        root = ttk.Frame(self.window, padding=12)
        root.pack(fill="both", expand=True)

        title_row = ttk.Frame(root)
        title_row.pack(fill="x")
        ttk.Label(title_row, text="Ход выполнения задачи", font=("Arial", 14, "bold")).pack(side="left")
        self.lbl_status_badge = ttk.Label(title_row, text="—", font=("Arial", 11, "bold"))
        self.lbl_status_badge.pack(side="right")

        self.lbl_task_id = ttk.Label(root, text="", foreground="#666")
        self.lbl_task_id.pack(fill="x", pady=(2, 8))

        live = ttk.LabelFrame(root, text="Сейчас", padding=10)
        live.pack(fill="x", pady=(0, 10))
        live.columnconfigure(1, weight=1)
        ttk.Label(live, text="Этап:", width=18).grid(row=0, column=0, sticky="w", pady=2)
        self.lbl_stage = ttk.Label(live, text="—", font=("Arial", 10, "bold"))
        self.lbl_stage.grid(row=0, column=1, sticky="ew", pady=2)
        ttk.Label(live, text="Видео:", width=18).grid(row=1, column=0, sticky="w", pady=2)
        self.lbl_current_file = ttk.Label(live, text="—")
        self.lbl_current_file.grid(row=1, column=1, sticky="ew", pady=2)
        ttk.Label(live, text="Прогресс этапа:", width=18).grid(row=2, column=0, sticky="w", pady=(5, 2))
        progress_row = ttk.Frame(live)
        progress_row.grid(row=2, column=1, sticky="ew", pady=(5, 2))
        progress_row.columnconfigure(0, weight=1)
        self.var_progress = tk.IntVar(value=0)
        self.progress = ttk.Progressbar(progress_row, variable=self.var_progress, maximum=100)
        self.progress.grid(row=0, column=0, sticky="ew")
        self.lbl_progress = ttk.Label(progress_row, text="0%", width=6, anchor="e")
        self.lbl_progress.grid(row=0, column=1, padx=(8, 0))
        self.lbl_summary = ttk.Label(live, text="")
        self.lbl_summary.grid(row=3, column=0, columnspan=2, sticky="w", pady=(5, 0))

        meta = ttk.LabelFrame(root, text="Параметры задачи", padding=8)
        meta.pack(fill="x", pady=(0, 10))
        for col in (1, 3):
            meta.columnconfigure(col, weight=1)
        self._meta_vars = {
            "output": tk.StringVar(value="—"),
            "language": tk.StringVar(value="—"),
            "voice": tk.StringVar(value="—"),
            "model": tk.StringVar(value="—"),
            "created": tk.StringVar(value="—"),
            "started": tk.StringVar(value="—"),
            "finished": tk.StringVar(value="—"),
            "log": tk.StringVar(value="—"),
        }
        meta_items = [
            ("Папка результата:", "output"),
            ("Язык:", "language"),
            ("Голос:", "voice"),
            ("Модель:", "model"),
            ("Добавлена:", "created"),
            ("Запущена:", "started"),
            ("Завершена:", "finished"),
            ("Журнал задачи:", "log"),
        ]
        for idx, (label, key) in enumerate(meta_items):
            row = idx // 2
            column = (idx % 2) * 2
            ttk.Label(meta, text=label).grid(row=row, column=column, sticky="nw", padx=(0, 6), pady=2)
            ttk.Label(meta, textvariable=self._meta_vars[key], wraplength=430, justify="left").grid(
                row=row, column=column + 1, sticky="ew", padx=(0, 14), pady=2
            )

        files_frame = ttk.LabelFrame(root, text="Видео в задаче", padding=6)
        files_frame.pack(fill="both", expand=True)
        columns = ("index", "status", "name", "elapsed", "output", "detail")
        self.files_tree = ttk.Treeview(files_frame, columns=columns, show="headings", height=11)
        headings = {
            "index": "№",
            "status": "Статус",
            "name": "Исходное видео",
            "elapsed": "Время",
            "output": "Результат",
            "detail": "Подробности",
        }
        for key, title in headings.items():
            self.files_tree.heading(key, text=title)
        self.files_tree.column("index", width=45, anchor="center", stretch=False)
        self.files_tree.column("status", width=115, anchor="w", stretch=False)
        self.files_tree.column("name", width=280, anchor="w")
        self.files_tree.column("elapsed", width=80, anchor="center", stretch=False)
        self.files_tree.column("output", width=250, anchor="w")
        self.files_tree.column("detail", width=260, anchor="w")
        self.files_tree.tag_configure("current", background="#fff8d5")
        self.files_tree.tag_configure("error", background="#fdecec")
        scroll_y = ttk.Scrollbar(files_frame, orient="vertical", command=self.files_tree.yview)
        scroll_x = ttk.Scrollbar(files_frame, orient="horizontal", command=self.files_tree.xview)
        self.files_tree.configure(yscrollcommand=scroll_y.set, xscrollcommand=scroll_x.set)
        self.files_tree.grid(row=0, column=0, sticky="nsew")
        scroll_y.grid(row=0, column=1, sticky="ns")
        scroll_x.grid(row=1, column=0, sticky="ew")
        files_frame.rowconfigure(0, weight=1)
        files_frame.columnconfigure(0, weight=1)
        self.files_tree.bind("<<TreeviewSelect>>", lambda _event: self._update_selected_file_details())

        detail_frame = ttk.LabelFrame(root, text="Выбранное видео", padding=6)
        detail_frame.pack(fill="x", pady=(10, 0))
        self.txt_file_details = tk.Text(detail_frame, height=5, wrap="word", font=("Consolas", 9))
        self.txt_file_details.pack(fill="x")
        self.txt_file_details.configure(state="disabled")

    def refresh(self) -> None:
        if not self.exists():
            return
        try:
            raw_task = self._task_provider(self.task_id)
            if not raw_task:
                self.lbl_stage.config(text="Задача больше не найдена в очереди")
                self.lbl_status_badge.config(text="Удалена")
                return
            snapshot = build_task_details_snapshot(raw_task)
            self._latest_snapshot = snapshot
            self._render(snapshot)
            self._after_id = self.window.after(self._refresh_ms, self.refresh)
        except tk.TclError:
            self._after_id = None
        except Exception:
            # The monitor is diagnostic UI. A malformed historical row must not be able
            # to break the translation worker or the main application.
            try:
                self._after_id = self.window.after(self._refresh_ms, self.refresh)
            except tk.TclError:
                self._after_id = None

    def _render(self, snapshot: dict) -> None:
        self.window.title(f"Подробности задачи — {snapshot.get('status_label', '—')}")
        self.lbl_task_id.config(text=f"ID: {snapshot.get('task_id') or '—'}")
        self.lbl_status_badge.config(text=str(snapshot.get("status_label") or "—"))
        self.lbl_stage.config(text=str(snapshot.get("status_text") or "—"))
        self.lbl_current_file.config(text=str(snapshot.get("current_file") or "—"))
        progress = int(snapshot.get("progress") or 0)
        self.var_progress.set(progress)
        self.lbl_progress.config(text=f"{progress}%")
        total = int(snapshot.get("files_total") or 0)
        succeeded = int(snapshot.get("succeeded") or 0)
        failed = int(snapshot.get("failed") or 0)
        processing = int(snapshot.get("processing") or 0)
        current_index = int(snapshot.get("file_index") or 0)
        current_part = f" • сейчас {current_index}/{total}" if current_index and total else ""
        self.lbl_summary.config(
            text=f"Готово: {succeeded}/{total} • ошибок: {failed} • обрабатывается: {processing}{current_part}"
        )

        self._meta_vars["output"].set(str(snapshot.get("output_dir") or "—"))
        self._meta_vars["language"].set(str(snapshot.get("language") or "—"))
        self._meta_vars["voice"].set(str(snapshot.get("voice") or "—"))
        self._meta_vars["model"].set(str(snapshot.get("model") or "—"))
        self._meta_vars["created"].set(str(snapshot.get("created_at") or "—"))
        self._meta_vars["started"].set(str(snapshot.get("started_at") or "—"))
        self._meta_vars["finished"].set(str(snapshot.get("finished_at") or "—"))
        self._meta_vars["log"].set(str(snapshot.get("log_path") or "—"))

        selected = self.files_tree.selection()
        selected_id = str(selected[0]) if selected else ""
        existing = set(self.files_tree.get_children())
        wanted = set()
        for row in snapshot.get("rows") or []:
            row_id = str(row.get("row_id") or "")
            if not row_id:
                continue
            wanted.add(row_id)
            detail = str(row.get("last_error") or "")
            values = (
                row.get("file_index") or "",
                row.get("status_label") or "—",
                row.get("input_name") or "—",
                row.get("elapsed") or "—",
                row.get("output_name") or "—",
                detail,
            )
            tags = []
            if row.get("is_current"):
                tags.append("current")
            if str(row.get("status") or "") in {"failed", "missing"}:
                tags.append("error")
            if row_id in existing:
                self.files_tree.item(row_id, values=values, tags=tuple(tags))
            else:
                self.files_tree.insert("", "end", iid=row_id, values=values, tags=tuple(tags))
        for row_id in existing - wanted:
            self.files_tree.delete(row_id)

        if selected_id and selected_id in wanted:
            self.files_tree.selection_set(selected_id)
        elif wanted:
            current = next(
                (str(row.get("row_id")) for row in snapshot.get("rows") or [] if row.get("is_current")),
                "",
            )
            if current:
                self.files_tree.selection_set(current)
                self.files_tree.see(current)
        self._update_selected_file_details()

    def _update_selected_file_details(self) -> None:
        selected = self.files_tree.selection()
        row_id = str(selected[0]) if selected else ""
        row = next(
            (item for item in self._latest_snapshot.get("rows") or [] if str(item.get("row_id")) == row_id),
            None,
        )
        if not row:
            text = "Выберите видео в таблице, чтобы увидеть полный путь, результат и ошибку."
        else:
            parts = [
                f"Исходник: {row.get('input_path') or '—'}",
                f"Результат: {row.get('output_path') or '—'}",
                f"Статус: {row.get('status_label') or '—'}",
                f"Время: {row.get('elapsed') or '—'}",
            ]
            if row.get("last_error"):
                parts.append(f"Ошибка: {row.get('last_error')}")
            text = "\n".join(parts)
        try:
            self.txt_file_details.configure(state="normal")
            self.txt_file_details.delete("1.0", tk.END)
            self.txt_file_details.insert("1.0", text)
            self.txt_file_details.configure(state="disabled")
        except tk.TclError:
            pass
