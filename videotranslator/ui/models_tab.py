"""Separate local translator/voice management tab for Hybrid AI v9."""
from __future__ import annotations

import threading
import sys
import tkinter as tk
from tkinter import messagebox, ttk

from videotranslator.models.catalog import get_catalog
from videotranslator.models.manager_v8 import RuntimeModelManager


class UIModelsMixin:
    def _build_ai_models_panel(self, parent):
        self.runtime_model_manager = RuntimeModelManager()

        intro = ttk.LabelFrame(parent, text="Hybrid AI v9 — локальные переводчики и голоса", padding=10)
        intro.pack(fill="x", padx=10, pady=(10, 5))
        ttk.Label(
            intro,
            text=(
                "Основной режим: локальный перевод → проверка качества → Google Repair только для "
                "подозрительных фрагментов. Первичная установка моделей и голосов выполняется вручную; "
                "после установки они сохраняются постоянно и повторно не скачиваются без необходимости."
            ),
            wraplength=720,
            foreground="#1565c0",
        ).pack(anchor="w")
        if getattr(sys, "frozen", False):
            ttk.Label(
                intro,
                text=(
                    "EXE-режим: Python-движки должны быть встроены при сборке. "
                    "Из этой вкладки после этого устанавливаются/обновляются модели, языковые пакеты и голоса."
                ),
                wraplength=720,
                foreground="#6a1b9a",
            ).pack(anchor="w", pady=(4, 0))

        self.var_hybrid_local_first = tk.BooleanVar(value=True)
        self.var_auto_install_argos = tk.BooleanVar(value=True)
        self.var_local_piper_fallback = tk.BooleanVar(value=True)
        self.var_auto_update_local_ai = tk.BooleanVar(value=True)
        options = ttk.Frame(intro)
        options.pack(fill="x", pady=(8, 0))
        ttk.Checkbutton(
            options,
            text="Локальный перевод — основной; Google только чинит проблемные сегменты",
            variable=self.var_hybrid_local_first,
            command=self.save_settings,
        ).pack(anchor="w")
        ttk.Checkbutton(
            options,
            text="После определения языка автоматически ставить недостающий маршрут Argos (при необходимости через English)",
            variable=self.var_auto_install_argos,
            command=self.save_settings,
        ).pack(anchor="w")
        ttk.Checkbutton(
            options,
            text="При шторме Edge TTS использовать установленный локальный Piper до gTTS",
            variable=self.var_local_piper_fallback,
            command=self.save_settings,
        ).pack(anchor="w")
        ttk.Checkbutton(
            options,
            text="При запуске проверять установленные модели/голоса и предлагать обновление или восстановление",
            variable=self.var_auto_update_local_ai,
            command=self.save_settings,
        ).pack(anchor="w")
        ttk.Label(
            options,
            text=f"Постоянное хранилище: {self.runtime_model_manager.root}",
            wraplength=720,
            foreground="#555",
        ).pack(anchor="w", pady=(3, 0))

        voice_row = ttk.Frame(intro)
        voice_row.pack(fill="x", pady=(6, 0))
        ttk.Label(voice_row, text="Предпочитаемый локальный голос:").pack(side="left")
        voice_rows = [row for row in get_catalog() if row.get("type") == "voice"]
        self._piper_voice_label_to_id = {row["name"]: row["id"] for row in voice_rows}
        self.combo_local_voice = ttk.Combobox(
            voice_row, values=list(self._piper_voice_label_to_id), state="readonly", width=42
        )
        if voice_rows:
            self.combo_local_voice.set(voice_rows[0]["name"])
        self.combo_local_voice.pack(side="left", padx=8)
        self.combo_local_voice.bind("<<ComboboxSelected>>", lambda _event: self.save_settings())

        table_frame = ttk.LabelFrame(parent, text="Каталог — размер показывается до установки", padding=8)
        table_frame.pack(fill="both", expand=True, padx=10, pady=5)
        columns = ("name", "type", "quality", "download", "disk", "status", "license")
        self.ai_models_tree = ttk.Treeview(table_frame, columns=columns, show="headings", height=11)
        headings = {
            "name": "Модель / голос",
            "type": "Тип",
            "quality": "Качество",
            "download": "Загрузка",
            "disk": "На диске",
            "status": "Состояние",
            "license": "Лицензия / примечание",
        }
        widths = {"name": 230, "type": 105, "quality": 150, "download": 80, "disk": 80, "status": 110, "license": 260}
        for key in columns:
            self.ai_models_tree.heading(key, text=headings[key])
            self.ai_models_tree.column(key, width=widths[key], minwidth=65, stretch=key in {"name", "quality", "license"})
        scroll_y = ttk.Scrollbar(table_frame, orient="vertical", command=self.ai_models_tree.yview)
        scroll_x = ttk.Scrollbar(table_frame, orient="horizontal", command=self.ai_models_tree.xview)
        self.ai_models_tree.configure(yscrollcommand=scroll_y.set, xscrollcommand=scroll_x.set)
        self.ai_models_tree.grid(row=0, column=0, sticky="nsew")
        scroll_y.grid(row=0, column=1, sticky="ns")
        scroll_x.grid(row=1, column=0, sticky="ew")
        table_frame.rowconfigure(0, weight=1)
        table_frame.columnconfigure(0, weight=1)

        controls = ttk.Frame(parent)
        controls.pack(fill="x", padx=10, pady=(3, 5))
        self.btn_model_install = ttk.Button(controls, text="Установить", command=lambda: self._model_action("install"))
        self.btn_model_remove = ttk.Button(controls, text="Удалить", command=lambda: self._model_action("remove"))
        self.btn_model_verify = ttk.Button(controls, text="Проверить", command=lambda: self._model_action("verify"))
        self.btn_model_update = ttk.Button(controls, text="Обновить", command=lambda: self._model_action("update"))
        self.btn_model_refresh = ttk.Button(controls, text="Обновить список", command=self.refresh_ai_models)
        for button in (self.btn_model_install, self.btn_model_remove, self.btn_model_verify, self.btn_model_update):
            button.pack(side="left", padx=(0, 5))
        self.btn_model_refresh.pack(side="right")

        self.lbl_model_action = ttk.Label(parent, text="Готово. Ничего не скачивается без команды пользователя.", foreground="#555")
        self.lbl_model_action.pack(fill="x", padx=12, pady=(0, 10))
        self.refresh_ai_models()

    def schedule_model_maintenance(self):
        """At startup inspect installed AI components, then ask before any download."""
        if not hasattr(self, "runtime_model_manager") or not hasattr(self, "var_auto_update_local_ai"):
            return
        if not bool(self.var_auto_update_local_ai.get()):
            return
        if getattr(self, "_startup_model_check_started", False):
            return
        if getattr(self, "_processing", False) or getattr(self, "_model_action_running", False):
            self._safe_after(5_000, self.schedule_model_maintenance)
            return

        self._startup_model_check_started = True
        self._set_model_buttons_busy(True)
        self.lbl_model_action.config(text="При запуске проверяю установленные модели и голоса. Ничего не скачиваю…")

        def run():
            try:
                result = self.runtime_model_manager.startup_scan_installed_updates()
            except Exception as exc:
                result = {
                    "checked": False,
                    "items": [],
                    "errors": [{"model_id": "startup", "error": f"{type(exc).__name__}: {exc}"}],
                }
            self._safe_after(0, self._finish_startup_model_scan, result)

        worker = threading.Thread(target=run, daemon=True, name="local-ai-startup-check")
        try:
            worker.start()
        except Exception as exc:
            self._startup_model_check_started = False
            self._set_model_buttons_busy(False)
            message = f"Не удалось запустить фоновую проверку моделей: {type(exc).__name__}: {exc}"
            self.lbl_model_action.config(text=message)
            self._problem(
                "local_ai_startup_thread_failed",
                level="error",
                message=message,
            )

    def _finish_startup_model_scan(self, result: dict):
        items = list(result.get("items") or [])
        errors = list(result.get("errors") or [])
        scan_mode = str(result.get("scan_mode") or "full")
        actionable = [
            row for row in items
            if row.get("can_update", True) and row.get("action") in {"update_available", "repair_needed"}
        ]
        app_update = [row for row in items if row.get("action") == "app_update_required"]

        if errors:
            self._problem(
                "local_ai_startup_check_partial",
                level="warning",
                message="Проверка обновлений локальных моделей при запуске завершилась с отдельными ошибками.",
                errors=errors[:8],
            )

        if not actionable:
            if app_update:
                names = ", ".join(row.get("name", row.get("model_id", "")) for row in app_update[:4])
                self.lbl_model_action.config(
                    text=f"Для встроенного движка требуется новая версия программы: {names}. Модели/голоса останутся сохранены."
                )
            elif errors:
                self.lbl_model_action.config(
                    text=f"Проверка обновлений завершена с ошибками: {len(errors)}. Можно продолжать перевод."
                )
            else:
                installed = len(items)
                self.lbl_model_action.config(
                    text=(
                        (
                            f"Проверка при запуске завершена: установленных компонентов {installed}, "
                            f"обновления не требуются ({'полная проверка целостности' if scan_mode == 'full' else 'быстрая проверка'})."
                        )
                        if installed else "Проверка при запуске завершена: установленных локальных моделей/голосов пока нет."
                    )
                )
            self.refresh_ai_models()
            self._set_model_buttons_busy(False)
            return

        lines = []
        total_mb = 0.0
        for row in actionable[:8]:
            verb = "восстановить" if row.get("action") == "repair_needed" else "обновить"
            lines.append(f"• {row.get('name', row.get('model_id', ''))} — {verb}")
            total_mb += float(row.get("size_download_mb") or 0)
        if len(actionable) > 8:
            lines.append(f"• ещё: {len(actionable) - 8}")
        if app_update:
            lines.append("\nВстроенный Piper Engine обновляется только вместе с новой версией программы.")

        prompt = (
            "Перед переводом найдены обновления/восстановление для уже установленных компонентов:\n\n"
            + "\n".join(lines)
            + f"\n\nМаксимальная повторная загрузка: примерно {total_mb:g} МБ."
            + "\nУстановленные модели и голоса сохраняются между запусками.\n\nОбновить сейчас?"
        )
        if not messagebox.askyesno("Обновления локального AI", prompt):
            self.lbl_model_action.config(text="Обновление отложено. Можно начинать перевод; компоненты не переустанавливались.")
            self.refresh_ai_models()
            self._set_model_buttons_busy(False)
            return

        self.lbl_model_action.config(text="Обновляю выбранные программой установленные компоненты…")
        plan = [(row.get("model_id", ""), row.get("action", "update_available")) for row in actionable]

        def progress(message: str):
            self._safe_after(0, lambda m=str(message): self.lbl_model_action.config(text=m))

        def run_updates():
            completed = []
            update_errors = []
            for model_id, action in plan:
                try:
                    status = self.runtime_model_manager.update(model_id, progress_cb=progress)
                    completed.append({"model_id": model_id, "action": action, "integrity": status.get("integrity")})
                except Exception as exc:
                    update_errors.append({"model_id": model_id, "error": f"{type(exc).__name__}: {exc}"})

            def finish():
                if update_errors:
                    self.lbl_model_action.config(
                        text=f"Обновление завершено частично: успешно {len(completed)}, ошибок {len(update_errors)}."
                    )
                    self._problem(
                        "local_ai_startup_update_partial",
                        level="warning",
                        message="Не все предложенные обновления локального AI удалось применить.",
                        completed=completed[:8],
                        errors=update_errors[:8],
                    )
                    messagebox.showwarning(
                        "Обновление локального AI",
                        f"Успешно: {len(completed)}. Ошибок: {len(update_errors)}.\n"
                        "Подробности сохранены в диагностике. Перевод можно запускать после проверки нужного компонента.",
                    )
                else:
                    self.lbl_model_action.config(
                        text=f"Обновление завершено: {len(completed)} компонент(ов). Можно начинать перевод."
                    )
                self.refresh_ai_models()
                self._set_model_buttons_busy(False)

            self._safe_after(0, finish)

        worker = threading.Thread(target=run_updates, daemon=True, name="local-ai-startup-update")
        try:
            worker.start()
        except Exception as exc:
            self._set_model_buttons_busy(False)
            message = f"Не удалось запустить фоновое обновление моделей: {type(exc).__name__}: {exc}"
            self.lbl_model_action.config(text=message)
            self._problem(
                "local_ai_update_thread_failed",
                level="error",
                message=message,
            )
            messagebox.showerror("Обновление локального AI", message)


    def _preferred_piper_voice_id(self) -> str:
        if not hasattr(self, "combo_local_voice"):
            return "piper-dmitri-ru"
        return self._piper_voice_label_to_id.get(self.combo_local_voice.get(), "piper-dmitri-ru")

    def _set_preferred_piper_voice_id(self, model_id: str):
        if not hasattr(self, "combo_local_voice"):
            return
        for label, value in self._piper_voice_label_to_id.items():
            if value == model_id:
                self.combo_local_voice.set(label)
                return

    def refresh_ai_models(self):
        if not hasattr(self, "ai_models_tree"):
            return
        selected = self.ai_models_tree.selection()
        selected_id = selected[0] if selected else ""
        for row_id in self.ai_models_tree.get_children():
            self.ai_models_tree.delete(row_id)
        try:
            rows = self.runtime_model_manager.get_status()
        except Exception as exc:
            self.lbl_model_action.config(text=f"Не удалось прочитать состояние моделей: {type(exc).__name__}: {exc}")
            return
        for item in rows:
            kind = {"translation": "Переводчик", "voice": "Голос", "voice_engine": "TTS движок"}.get(item.get("type"), item.get("type", ""))
            if item.get("installed"):
                status = "Установлено" if item.get("integrity") == "ok" else f"Проблема: {item.get('integrity')}"
            else:
                status = "Не установлено"
            self.ai_models_tree.insert(
                "", "end", iid=item["id"],
                values=(
                    item["name"], kind, item.get("quality", ""),
                    item.get("size_note") or f"~{item.get('size_download_mb', 0):g} МБ",
                    f"~{item.get('size_disk_mb', 0):g} МБ",
                    status, item.get("license_note", ""),
                ),
            )
        if selected_id and self.ai_models_tree.exists(selected_id):
            self.ai_models_tree.selection_set(selected_id)

    def _selected_model_id(self) -> str:
        if not hasattr(self, "ai_models_tree"):
            return ""
        selected = self.ai_models_tree.selection()
        return selected[0] if selected else ""

    def _refresh_model_button_state(self):
        model_disabled = bool(getattr(self, "_model_action_running", False) or getattr(self, "_processing", False))
        state = "disabled" if model_disabled else "normal"
        for name in ("btn_model_install", "btn_model_remove", "btn_model_verify", "btn_model_update", "btn_model_refresh"):
            try:
                getattr(self, name).config(state=state)
            except Exception:
                pass
        try:
            start_disabled = bool(getattr(self, "_model_action_running", False))
            if getattr(self, "_processing", False) and not hasattr(self, "_task_queue_state"):
                start_disabled = True
            self.btn_start.config(state="disabled" if start_disabled else "normal")
        except Exception:
            pass

    def _set_model_buttons_busy(self, busy: bool):
        self._model_action_running = bool(busy)
        self._refresh_model_button_state()

    def _model_action(self, action: str):
        if getattr(self, "_model_action_running", False):
            return
        if getattr(self, "_processing", False):
            messagebox.showinfo(
                "Модели заняты",
                "Дождитесь завершения обработки видео. Установка, обновление и удаление моделей во время работы отключены.",
            )
            return
        model_id = self._selected_model_id()
        if not model_id:
            messagebox.showinfo("Выберите модель", "Сначала выберите переводчик, движок или голос в таблице.")
            return
        catalog = {row["id"]: row for row in get_catalog()}
        item = catalog.get(model_id) or {}
        if action in {"install", "update"}:
            size = item.get("size_download_mb", 0)
            verb = "обновить" if action == "update" else "установить"
            if not messagebox.askyesno(
                "Подтверждение загрузки",
                f"{verb.capitalize()} «{item.get('name', model_id)}»?\n\n"
                f"Загрузка: примерно {size:g} МБ\n"
                f"Место на диске: примерно {item.get('size_disk_mb', 0):g} МБ\n\n"
                "Большие файлы будут скачаны только после этого подтверждения.",
            ):
                return
        if action == "remove" and not messagebox.askyesno("Удаление", f"Удалить «{item.get('name', model_id)}»?"):
            return

        self._set_model_buttons_busy(True)
        self.lbl_model_action.config(text=f"{item.get('name', model_id)}: {action}…")

        def progress(message: str):
            self._safe_after(0, lambda m=str(message): self.lbl_model_action.config(text=m))

        def run():
            try:
                if action == "install":
                    status = self.runtime_model_manager.install(model_id, progress_cb=progress)
                    message = f"Установлено и проверено: {item.get('name', model_id)} ({status.get('size_mb', 0):g} МБ локально)."
                elif action == "update":
                    status = self.runtime_model_manager.update(model_id, progress_cb=progress)
                    message = f"Обновлено и проверено: {item.get('name', model_id)}."
                elif action == "remove":
                    removed = self.runtime_model_manager.remove(model_id)
                    message = (
                        f"Удалено: {item.get('name', model_id)}."
                        if removed else f"Компонент уже отсутствует: {item.get('name', model_id)}."
                    )
                else:
                    status = self.runtime_model_manager.verify(model_id)
                    message = f"Проверка: {item.get('name', model_id)} — {status.get('integrity')} ({status.get('size_mb', 0):g} МБ)."
                self._safe_after(0, lambda m=message: self.lbl_model_action.config(text=m))
            except Exception as exc:
                message = f"Ошибка {action}: {type(exc).__name__}: {exc}"
                self._safe_after(0, lambda m=message: self.lbl_model_action.config(text=m))
                self._safe_after(0, messagebox.showerror, "Менеджер моделей", message)
            finally:
                self._safe_after(0, self.refresh_ai_models)
                self._safe_after(0, self._set_model_buttons_busy, False)

        worker = threading.Thread(target=run, daemon=True, name=f"local-ai-{action}")
        try:
            worker.start()
        except Exception as exc:
            self._set_model_buttons_busy(False)
            message = f"Не удалось запустить операцию {action}: {type(exc).__name__}: {exc}"
            self.lbl_model_action.config(text=message)
            self._problem(
                "local_ai_action_thread_failed",
                level="error",
                message=message,
                action=action,
                model_id=model_id,
            )
            messagebox.showerror("Менеджер моделей", message)
