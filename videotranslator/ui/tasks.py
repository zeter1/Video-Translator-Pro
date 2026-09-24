"""Tasks tab and durable FIFO dispatcher for translation jobs."""

from __future__ import annotations

from copy import deepcopy
from datetime import datetime
import os
from tkinter import messagebox, ttk

from videotranslator.recovery.batch import batch_recovery_pending_entries, load_batch_recovery_state
from videotranslator.recovery.tasks import (
    RUNNABLE_TASK_STATUSES,
    TERMINAL_TASK_STATUSES,
    create_task_queue_state,
    create_translation_task,
    find_task,
    load_task_queue_state,
    next_runnable_task,
    recover_interrupted_tasks,
    save_task_queue_state,
)
from videotranslator.ui.notifications import show_topmost_notification
from videotranslator.ui.task_details import TaskDetailsWindow


_STATUS_LABELS = {
    "queued": "В очереди",
    "running": "Переводится",
    "interrupted": "Продолжение",
    "completed": "Готово",
    "failed": "Ошибка",
    "cancelled": "Отменено",
}


def _now_iso() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")


class UITasksMixin:
    """One authoritative owner for persisted queue state and Tasks UI."""

    def _build_tasks_panel(self, parent):
        header = ttk.Frame(parent, padding=(10, 10, 10, 4))
        header.pack(fill="x")
        ttk.Label(
            header,
            text="Очередь переводов",
            font=("Arial", 12, "bold"),
        ).pack(side="left")
        self.lbl_tasks_summary = ttk.Label(header, text="Задач: 0", foreground="#666")
        self.lbl_tasks_summary.pack(side="right")

        info = ttk.Label(
            parent,
            text=(
                "Новые задачи можно добавлять во время перевода. Они сохраняются на диск, "
                "выполняются строго по порядку и продолжаются после перезапуска программы. "
                "Дважды щёлкните по задаче, чтобы открыть подробный монитор выполнения."
            ),
            foreground="#555",
            wraplength=900,
            justify="left",
        )
        info.pack(fill="x", padx=10, pady=(0, 8))

        table_frame = ttk.Frame(parent, padding=(10, 0, 10, 6))
        table_frame.pack(fill="both", expand=True)
        columns = ("status", "progress", "files", "current", "created")
        self.tasks_tree = ttk.Treeview(table_frame, columns=columns, show="headings", height=15)
        self.tasks_tree.heading("status", text="Статус")
        self.tasks_tree.heading("progress", text="Прогресс")
        self.tasks_tree.heading("files", text="Файлов")
        self.tasks_tree.heading("current", text="Сейчас")
        self.tasks_tree.heading("created", text="Добавлено")
        self.tasks_tree.column("status", width=110, anchor="w")
        self.tasks_tree.column("progress", width=120, anchor="center")
        self.tasks_tree.column("files", width=70, anchor="center")
        self.tasks_tree.column("current", width=290, anchor="w")
        self.tasks_tree.column("created", width=145, anchor="center")
        scroll = ttk.Scrollbar(table_frame, orient="vertical", command=self.tasks_tree.yview)
        self.tasks_tree.configure(yscrollcommand=scroll.set)
        self.tasks_tree.pack(side="left", fill="both", expand=True)
        scroll.pack(side="right", fill="y")
        self.tasks_tree.bind("<Double-1>", self._on_tasks_tree_double_click)

        controls = ttk.Frame(parent, padding=(10, 4, 10, 10))
        controls.pack(fill="x")
        self.btn_tasks_resume = ttk.Button(controls, text="▶ Продолжить очередь", command=self._dispatch_next_task)
        self.btn_tasks_resume.pack(side="left", padx=(0, 5))
        self.btn_tasks_retry = ttk.Button(controls, text="↻ Повторить выбранную", command=self._retry_selected_task)
        self.btn_tasks_retry.pack(side="left", padx=5)
        self.btn_tasks_remove = ttk.Button(controls, text="🗑 Удалить выбранную", command=self._remove_selected_task)
        self.btn_tasks_remove.pack(side="left", padx=5)
        self.btn_tasks_clear = ttk.Button(controls, text="Очистить завершённые", command=self._clear_finished_tasks)
        self.btn_tasks_clear.pack(side="right")

        self.lbl_tasks_status = ttk.Label(parent, text="Очередь готова", foreground="#555", padding=(10, 0, 10, 10))
        self.lbl_tasks_status.pack(fill="x")

    def _select_tasks_tab(self):
        try:
            self.notebook.select(self.tasks_tab)
        except Exception:
            pass

    def _load_task_queue(self):
        state = load_task_queue_state()
        changed = recover_interrupted_tasks(state)

        # One-time compatibility migration from the former single-batch recovery file.
        known_batch_ids = {
            str((task.get("batch_state") or {}).get("batch_id") or "")
            for task in state.get("tasks") or []
        }
        known_task_batch_ids = {
            str(value or "")
            for value in state.get("known_task_batch_ids") or []
            if str(value or "")
        }
        missing_known_ids = known_batch_ids - known_task_batch_ids
        if missing_known_ids:
            known_task_batch_ids.update(missing_known_ids)
            state["known_task_batch_ids"] = sorted(known_task_batch_ids)
            changed = True
        legacy = load_batch_recovery_state()
        if legacy and str(legacy.get("status") or "") != "completed" and batch_recovery_pending_entries(legacy):
            legacy_id = str(legacy.get("batch_id") or "")
            if legacy_id and legacy_id not in known_task_batch_ids:
                migrated = create_translation_task(legacy)
                migrated["status"] = "interrupted"
                migrated["runtime"]["status_text"] = "Восстановлено после предыдущей версии"
                state.setdefault("tasks", []).append(migrated)
                known_task_batch_ids.add(legacy_id)
                state["known_task_batch_ids"] = sorted(known_task_batch_ids)
                changed = True

        with self._task_queue_lock:
            self._task_queue_state = state
        if changed:
            self._persist_task_queue_safely()
        self._refresh_tasks_view()

    def _persist_task_queue_safely(self):
        try:
            with self._task_queue_lock:
                save_task_queue_state(self._task_queue_state)
                self._task_queue_dirty = False
            return True
        except (OSError, TypeError, ValueError) as exc:
            self._task_queue_dirty = True
            self._problem(
                "task_queue_save_failed",
                level="error",
                message="Не удалось атомарно сохранить очередь задач; изменение не считается надёжно зафиксированным.",
                exception={"type": type(exc).__name__, "message": str(exc)[:1000]},
            )
            return False

    def _show_queue_save_error(self, action: str):
        try:
            messagebox.showerror(
                "Очередь задач не сохранена",
                f"Не удалось сохранить очередь задач на диск во время операции «{action}».\n\n"
                "Изменение не будет запускаться/считаться применённым, чтобы не потерять задачу после перезапуска. "
                "Проверьте свободное место и права записи, затем повторите действие.",
            )
        except Exception:
            pass

    def _save_task_queue_state_from_worker(self, batch_state: dict):
        # Worker events carry their own operation identity. Never infer the owner
        # from the mutable global "current" task: a late worker must not overwrite
        # checkpoints of a newer task.
        task_id = str((batch_state or {}).get("batch_id") or "")
        if not task_id:
            return
        try:
            with self._task_queue_lock:
                task = find_task(self._task_queue_state, task_id)
                if not task:
                    return
                task["batch_state"] = deepcopy(batch_state)
                task["updated_at"] = _now_iso()
                save_task_queue_state(self._task_queue_state)
                self._task_queue_dirty = False
        except (OSError, TypeError, ValueError) as exc:
            self._task_queue_dirty = True
            self._problem(
                "task_queue_checkpoint_failed",
                level="warning",
                message="Не удалось обновить сохранённую очередь из рабочего потока.",
                task_id=task_id,
                exception={"type": type(exc).__name__, "message": str(exc)[:1000]},
            )

    def _task_queue_has_work(self) -> bool:
        with self._task_queue_lock:
            return any(
                str(task.get("status") or "queued") in RUNNABLE_TASK_STATUSES | {"running"}
                for task in self._task_queue_state.get("tasks") or []
            )

    def _request_active_task_cancel(self) -> None:
        """Durably record user cancellation intent before the worker finishes cleanup."""
        task_id = str(getattr(self, "_active_task_id", "") or "")
        if not task_id:
            return
        with self._task_queue_lock:
            task = find_task(self._task_queue_state, task_id)
            if not task or str(task.get("status") or "") != "running":
                return
            batch = task.get("batch_state") or {}
            batch["status"] = "cancellation_requested"
            task["updated_at"] = _now_iso()
            runtime = task.setdefault("runtime", {})
            runtime["status_text"] = "Остановка..."
        if not self._persist_task_queue_safely():
            self._problem(
                "task_cancel_intent_not_persisted",
                level="warning",
                message="Отмена передана worker, но отметку об отмене не удалось сохранить в очереди.",
                task_id=task_id,
            )
        self._refresh_tasks_view(select_task_id=task_id)

    def _enqueue_batch_task(self, batch_state: dict):
        task = create_translation_task(batch_state)
        with self._task_queue_lock:
            previous_state = deepcopy(self._task_queue_state)
            previous_dirty = bool(getattr(self, "_task_queue_dirty", False))
            self._task_queue_state.setdefault("tasks", []).append(task)
            known_ids = self._task_queue_state.setdefault("known_task_batch_ids", [])
            if task["task_id"] not in known_ids:
                known_ids.append(task["task_id"])
            persisted = self._persist_task_queue_safely()
            if not persisted:
                self._task_queue_state = previous_state
                self._task_queue_dirty = previous_dirty
        if not persisted:
            self._problem(
                "translation_task_enqueue_rolled_back",
                level="error",
                message="Постановка задачи отменена, потому что очередь не удалось сохранить на диск.",
                task_id=task["task_id"],
            )
            self._refresh_tasks_view()
            self._select_tasks_tab()
            self._set_progress(0, "Задача не добавлена: очередь не сохранена")
            self._show_queue_save_error("добавление задачи")
            return None
        self._problem(
            "translation_task_queued",
            message="Пользователь добавил перевод в постоянную очередь задач.",
            task_id=task["task_id"],
            files_count=len((task.get("batch_state") or {}).get("files") or []),
            output_dir=str((task.get("batch_state") or {}).get("output_dir") or ""),
        )
        self._refresh_tasks_view(select_task_id=task["task_id"])
        self._select_tasks_tab()
        self._set_progress(0, "Задача добавлена в очередь")
        self._schedule_task_dispatch(10)
        return task

    def _schedule_task_dispatch(self, delay_ms: int = 100):
        if self._closing:
            return
        callback_id = getattr(self, "_task_dispatch_after_id", None)
        if callback_id:
            try:
                self.root.after_cancel(callback_id)
            except Exception:
                pass
        try:
            self._task_dispatch_after_id = self.root.after(max(0, int(delay_ms)), self._dispatch_next_task)
        except Exception:
            self._task_dispatch_after_id = None

    def _dispatch_next_task(self):
        self._task_dispatch_after_id = None
        if self._closing:
            return
        if bool(getattr(self, "_model_action_running", False)):
            self._schedule_task_dispatch(500)
            return
        thread = getattr(self, "_worker_thread", None)
        if thread is not None and thread.is_alive():
            self._schedule_task_dispatch(100)
            return
        if thread is not None:
            self._worker_thread = None
        if bool(getattr(self, "_processing", False)):
            return

        # Never transfer ownership to the next task while the previous queue
        # mutation is still only in memory. A later "running" commit would often
        # include that state too, but a crash in between could resurrect the old
        # task or lose its terminal status. Flush dirty state first.
        if bool(getattr(self, "_task_queue_dirty", False)):
            if not self._persist_task_queue_safely():
                try:
                    self.lbl_tasks_status.config(
                        text="Очередь приостановлена: последнее состояние не удалось сохранить"
                    )
                except Exception:
                    pass
                return

        with self._task_queue_lock:
            task = next_runnable_task(self._task_queue_state)
            if not task:
                if self._task_queue_dirty and not self._persist_task_queue_safely():
                    try:
                        self.lbl_tasks_status.config(text="Очередь завершена, но последнее состояние не сохранено")
                    except Exception:
                        pass
                    return
                self._active_task_id = None
                self._refresh_tasks_view()
                try:
                    self.lbl_tasks_status.config(text="Очередь выполнена")
                except Exception:
                    pass
                return
            previous_state = deepcopy(self._task_queue_state)
            previous_dirty = bool(getattr(self, "_task_queue_dirty", False))
            task["status"] = "running"
            task["started_at"] = task.get("started_at") or _now_iso()
            task["updated_at"] = _now_iso()
            runtime = task.setdefault("runtime", {})
            runtime.update({"progress": 0, "status_text": "Запуск..."})
            batch_state = task.get("batch_state") or {}
            batch_state["status"] = "running"
            task_id = str(task.get("task_id") or "")
            persisted = self._persist_task_queue_safely()
            if not persisted:
                self._task_queue_state = previous_state
                self._task_queue_dirty = previous_dirty
        if not persisted:
            self._active_task_id = None
            self._resume_batch_state = None
            self._refresh_tasks_view(select_task_id=task_id)
            try:
                self.lbl_tasks_status.config(text="Запуск остановлен: очередь не удалось сохранить")
            except Exception:
                pass
            self._show_queue_save_error("запуск следующей задачи")
            return

        self._active_task_id = task_id
        self._resume_batch_state = batch_state
        self._refresh_tasks_view(select_task_id=self._active_task_id)
        self._select_tasks_tab()
        self._set_busy(True)
        self.lbl_tasks_status.config(text="Выполняется задача из начала очереди")
        if not self._launch_queued_task(task):
            # _launch_queued_task records the failure and restores the UI.
            self._schedule_task_dispatch(150)

    def _task_worker_file_started(self, task_id: str, file_index: int, files_total: int, file_path: str):
        task_id = str(task_id or "")
        if not task_id:
            return
        if task_id != str(getattr(self, "_active_task_id", "") or ""):
            return
        with self._task_queue_lock:
            task = find_task(self._task_queue_state, task_id)
            if not task or str(task.get("status") or "") != "running":
                return
            runtime = task.setdefault("runtime", {})
            runtime.update({
                "file_index": int(file_index or 0),
                "files_total": int(files_total or 0),
                "current_file": os.path.basename(file_path or ""),
                "progress": 0,
                "status_text": "Переводится",
            })
        self._refresh_tasks_view(select_task_id=task_id)

    def _update_active_task_progress(self, value: int, status: str = "", task_id: str = ""):
        task_id = str(task_id or getattr(self, "_active_task_id", "") or "")
        if not task_id:
            return
        if task_id != str(getattr(self, "_active_task_id", "") or ""):
            return
        with self._task_queue_lock:
            task = find_task(self._task_queue_state, task_id)
            if not task or str(task.get("status") or "") != "running":
                return
            runtime = task.setdefault("runtime", {})
            runtime["progress"] = max(0, min(100, int(value or 0)))
            if status:
                runtime["status_text"] = str(status)
        self._refresh_tasks_view(select_task_id=task_id)

    def _on_task_batch_finished(self, task_id: str, batch_state: dict, successful: int, total: int, cancelled: bool):
        task_id = str(task_id or "")
        is_current = task_id == str(getattr(self, "_active_task_id", "") or "")
        with self._task_queue_lock:
            task = find_task(self._task_queue_state, task_id)
            if not task:
                return
            task["batch_state"] = deepcopy(batch_state)
            if successful == total:
                task["status"] = "completed"
                task["finished_at"] = _now_iso()
            elif self._closing:
                task["status"] = "interrupted"
                task["finished_at"] = ""
            elif cancelled:
                task["status"] = "cancelled"
                task["finished_at"] = _now_iso()
            else:
                task["status"] = "failed"
                task["finished_at"] = _now_iso()
            task["updated_at"] = _now_iso()
            runtime = task.setdefault("runtime", {})
            runtime["progress"] = 100 if successful == total else runtime.get("progress", 0)
            runtime["status_text"] = _STATUS_LABELS.get(task["status"], task["status"])
        persisted = self._persist_task_queue_safely()
        if is_current:
            self._active_task_id = None
            self._resume_batch_state = None
            self._set_busy(False)
        else:
            self._problem(
                "stale_task_completion_callback",
                level="warning",
                message="Поздний callback завершённой задачи не получил право менять состояние новой активной задачи.",
                task_id=task_id,
                active_task_id=str(getattr(self, "_active_task_id", "") or ""),
            )
        active_after = str(getattr(self, "_active_task_id", "") or "")
        self._refresh_tasks_view(select_task_id=task_id if is_current or not active_after else active_after)

        if not self._closing:
            output_dir = str(batch_state.get("output_dir") or "")
            if successful == total:
                title = "Перевод завершён"
                text = f"Задача успешно завершена: {successful}/{total}.\nРезультаты: {output_dir}"
                if is_current:
                    self.lbl_tasks_status.config(text="Задача завершена — проверяю следующую в очереди")
                show_topmost_notification(self.root, title, text)
            elif cancelled:
                if is_current:
                    self.lbl_tasks_status.config(text="Текущая задача отменена")
            else:
                if is_current:
                    self.lbl_tasks_status.config(text=f"Задача завершена с ошибками: {successful}/{total}")
                show_topmost_notification(
                    self.root,
                    "Перевод завершён с ошибками",
                    f"Успешно обработано: {successful}/{total}.\nНезавершённую задачу можно повторить на вкладке «Задачи».",
                )
            if is_current:
                self._schedule_task_dispatch(150)
            elif not persisted:
                self._task_queue_dirty = True

    def _on_task_worker_crashed(self, task_id: str, batch_state: dict, error_text: str):
        task_id = str(task_id or "")
        is_current = task_id == str(getattr(self, "_active_task_id", "") or "")
        with self._task_queue_lock:
            task = find_task(self._task_queue_state, task_id)
            if task:
                task["batch_state"] = deepcopy(batch_state)
                task["status"] = "interrupted" if self._closing else "failed"
                task["updated_at"] = _now_iso()
                if not self._closing:
                    task["finished_at"] = _now_iso()
                task.setdefault("runtime", {})["status_text"] = "Ошибка рабочего потока"
        self._persist_task_queue_safely()
        if is_current:
            self._active_task_id = None
            self._resume_batch_state = None
            self._set_busy(False)
        else:
            self._problem(
                "stale_task_crash_callback",
                level="warning",
                message="Поздний callback ошибки старой задачи не меняет состояние новой активной задачи.",
                task_id=task_id,
                active_task_id=str(getattr(self, "_active_task_id", "") or ""),
            )
        active_after = str(getattr(self, "_active_task_id", "") or "")
        self._refresh_tasks_view(select_task_id=task_id if is_current or not active_after else active_after)
        if not self._closing:
            show_topmost_notification(self.root, "Ошибка перевода", error_text)
            if is_current:
                self._schedule_task_dispatch(150)

    def _task_start_failed(self, batch_state: dict):
        task_id = str((batch_state or {}).get("batch_id") or getattr(self, "_active_task_id", "") or "")
        if not task_id:
            return
        is_current = task_id == str(getattr(self, "_active_task_id", "") or "")
        with self._task_queue_lock:
            task = find_task(self._task_queue_state, task_id)
            if task:
                task["batch_state"] = deepcopy(batch_state)
                task["status"] = "failed"
                task["finished_at"] = _now_iso()
                task["updated_at"] = _now_iso()
                task.setdefault("runtime", {})["status_text"] = "Не удалось запустить worker"
        self._persist_task_queue_safely()
        if is_current:
            self._active_task_id = None
            self._resume_batch_state = None
        self._refresh_tasks_view(select_task_id=task_id)

    def _selected_task_id(self) -> str:
        try:
            selection = self.tasks_tree.selection()
            return str(selection[0]) if selection else ""
        except Exception:
            return ""

    def _task_snapshot(self, task_id: str) -> dict:
        """Return an isolated display snapshot so detail windows never own queue state."""
        task_id = str(task_id or "")
        if not task_id:
            return {}
        with self._task_queue_lock:
            task = find_task(self._task_queue_state, task_id)
            return deepcopy(task) if task else {}

    def _on_tasks_tree_double_click(self, event=None):
        try:
            task_id = str(self.tasks_tree.identify_row(getattr(event, "y", 0)) or "")
        except Exception:
            task_id = ""
        if not task_id:
            task_id = self._selected_task_id()
        if not task_id:
            return
        try:
            self.tasks_tree.selection_set(task_id)
            self.tasks_tree.see(task_id)
        except Exception:
            pass
        self._open_task_details(task_id)

    def _open_task_details(self, task_id: str):
        task_id = str(task_id or "")
        if not task_id or not self._task_snapshot(task_id):
            return
        windows = getattr(self, "_task_detail_windows", None)
        if not isinstance(windows, dict):
            windows = {}
            self._task_detail_windows = windows
        existing = windows.get(task_id)
        if existing and existing.exists():
            existing.focus()
            return
        window = TaskDetailsWindow(
            self.root,
            task_id,
            self._task_snapshot,
            on_close=self._forget_task_details_window,
        )
        windows[task_id] = window
        window.focus()

    def _forget_task_details_window(self, task_id: str):
        windows = getattr(self, "_task_detail_windows", None)
        if isinstance(windows, dict):
            windows.pop(str(task_id or ""), None)

    def _retry_selected_task(self):
        task_id = self._selected_task_id()
        if not task_id:
            return
        already_completed = False
        persisted = False
        with self._task_queue_lock:
            previous_state = deepcopy(self._task_queue_state)
            previous_dirty = bool(getattr(self, "_task_queue_dirty", False))
            task = find_task(self._task_queue_state, task_id)
            if not task or str(task.get("status") or "") == "running":
                return
            if str(task.get("status") or "") == "completed":
                already_completed = True
            else:
                task["status"] = "queued"
                task["finished_at"] = ""
                task["updated_at"] = _now_iso()
                batch = task.get("batch_state") or {}
                batch["status"] = "queued"
                runtime = task.setdefault("runtime", {})
                runtime.update({
                    "current_file": "",
                    "file_index": 0,
                    "files_total": len(batch.get("files") or []),
                    "progress": 0,
                    "status_text": "Повторно поставлено в очередь",
                })
                persisted = self._persist_task_queue_safely()
                if not persisted:
                    self._task_queue_state = previous_state
                    self._task_queue_dirty = previous_dirty
        if already_completed:
            messagebox.showinfo("Задача завершена", "Готовая задача уже успешно выполнена.")
            return
        if not persisted:
            self._refresh_tasks_view(select_task_id=task_id)
            self._show_queue_save_error("повтор задачи")
            return
        self._refresh_tasks_view(select_task_id=task_id)
        self._schedule_task_dispatch(10)

    def _remove_selected_task(self):
        task_id = self._selected_task_id()
        if not task_id:
            return
        running = False
        persisted = False
        with self._task_queue_lock:
            previous_state = deepcopy(self._task_queue_state)
            previous_dirty = bool(getattr(self, "_task_queue_dirty", False))
            task = find_task(self._task_queue_state, task_id)
            if not task:
                return
            if str(task.get("status") or "") == "running":
                running = True
            else:
                self._task_queue_state["tasks"] = [
                    item for item in self._task_queue_state.get("tasks") or []
                    if str(item.get("task_id") or "") != task_id
                ]
                persisted = self._persist_task_queue_safely()
                if not persisted:
                    self._task_queue_state = previous_state
                    self._task_queue_dirty = previous_dirty
        if running:
            messagebox.showwarning("Задача выполняется", "Сначала отмените текущую задачу.")
            return
        if not persisted:
            self._refresh_tasks_view(select_task_id=task_id)
            self._show_queue_save_error("удаление задачи")
            return
        self._refresh_tasks_view()

    def _clear_finished_tasks(self):
        with self._task_queue_lock:
            previous_state = deepcopy(self._task_queue_state)
            previous_dirty = bool(getattr(self, "_task_queue_dirty", False))
            self._task_queue_state["tasks"] = [
                task for task in self._task_queue_state.get("tasks") or []
                if str(task.get("status") or "") not in TERMINAL_TASK_STATUSES
            ]
            persisted = self._persist_task_queue_safely()
            if not persisted:
                self._task_queue_state = previous_state
                self._task_queue_dirty = previous_dirty
        if not persisted:
            self._refresh_tasks_view()
            self._show_queue_save_error("очистка завершённых задач")
            return
        self._refresh_tasks_view()

    def _refresh_tasks_view(self, select_task_id: str = ""):
        if not hasattr(self, "tasks_tree"):
            return
        with self._task_queue_lock:
            tasks = deepcopy(self._task_queue_state.get("tasks") or [])
        try:
            existing = set(self.tasks_tree.get_children())
            wanted = {str(task.get("task_id") or "") for task in tasks}
            for item_id in existing - wanted:
                self.tasks_tree.delete(item_id)

            for task in tasks:
                task_id = str(task.get("task_id") or "")
                batch = task.get("batch_state") or {}
                entries = batch.get("files") or []
                total = len(entries)
                succeeded = sum(str(entry.get("status") or "") == "succeeded" for entry in entries)
                runtime = task.get("runtime") or {}
                status = str(task.get("status") or "queued")
                if status == "running":
                    progress = f"{succeeded}/{total} • {int(runtime.get('progress') or 0)}%"
                else:
                    progress = f"{succeeded}/{total}"
                current = str(runtime.get("current_file") or "")
                if not current and status == "queued":
                    current = "Ожидает запуска"
                elif not current and status == "interrupted":
                    current = "Будет продолжена"
                elif not current and status == "completed":
                    current = "Завершена"
                created = str(task.get("created_at") or "").replace("T", " ")[:16]
                values = (_STATUS_LABELS.get(status, status), progress, total, current, created)
                if task_id in existing:
                    self.tasks_tree.item(task_id, values=values)
                else:
                    self.tasks_tree.insert("", "end", iid=task_id, values=values)

            queued = sum(str(task.get("status") or "") in RUNNABLE_TASK_STATUSES for task in tasks)
            running = sum(str(task.get("status") or "") == "running" for task in tasks)
            self.lbl_tasks_summary.config(text=f"Задач: {len(tasks)} • в очереди: {queued} • активных: {running}")
            if select_task_id and select_task_id in wanted:
                self.tasks_tree.selection_set(select_task_id)
                self.tasks_tree.see(select_task_id)
        except Exception:
            pass
