"""Method owner for App: _threadsafe_network_notice, _handle_network_notice, _show_vpn_notice, _vpn_probe_now, _vpn_allow_fallback_now, _close_vpn_notice."""

from __future__ import annotations

from tkinter import messagebox
import tkinter as tk
from tkinter import ttk
from videotranslator.config import EDGE_VOICE_PRESERVE_SEC


class UINetworkMixin:
    """Behavior-preserving methods extracted from the legacy monolith."""

    def _threadsafe_network_notice(self, action: str, details: dict):
        self._safe_after(0, self._handle_network_notice, str(action), dict(details or {}))


    def _handle_network_notice(self, action: str, details: dict):
        if self._closing:
            return
        if action == "unstable":
            self._show_vpn_notice(details)
            return

        window = self._vpn_notice_window
        if not window or not window.winfo_exists():
            return

        if action == "recovered":
            self._vpn_notice_status.set(
                "✅ Edge TTS снова отвечает. Озвучка продолжится выбранным голосом."
            )
            self._vpn_notice_detail.set(
                "Новый VPN-маршрут успешно проверен. Резервный голос для ожидавших сегментов не нужен."
            )
            self._problem(
                "vpn_instability_notification_updated",
                message="Уведомление обновлено: Edge TTS восстановился.",
                action=action,
                **details,
            )
            self._safe_after(5000, self._close_vpn_notice)
        elif action == "checking":
            self._vpn_notice_status.set("🔎 Проверяю выбранный VPN-сервер через Edge TTS...")
            self._vpn_notice_detail.set(
                "Программа создаёт настоящий аудиофрагмент выбранным голосом. "
                "Результат проверки появится автоматически."
            )
        elif action == "fallback":
            self._vpn_notice_status.set(
                "⚠️ Edge TTS не восстановился. Программа продолжает через резервный gTTS."
            )
            self._vpn_notice_detail.set(
                "gTTS не умеет выбирать заданный Edge-голос, поэтому тембр только оставшихся фраз может отличаться."
            )
            self._problem(
                "vpn_instability_notification_updated",
                level="warning",
                message="Уведомление обновлено: включён резервный gTTS.",
                action=action,
                **details,
            )
        elif action == "stage_finished":
            self._close_vpn_notice()


    def _show_vpn_notice(self, details: dict):
        window = self._vpn_notice_window
        if window and window.winfo_exists():
            window.deiconify()
            window.lift()
            try:
                window.attributes("-topmost", True)
                window.after(3000, lambda: window.winfo_exists() and window.attributes("-topmost", False))
            except tk.TclError:
                pass
            return

        wait_sec = int(details.get("wait_sec") or EDGE_VOICE_PRESERVE_SEC)
        window = tk.Toplevel(self.root)
        self._vpn_notice_window = window
        window.title("VPN нестабилен — требуется внимание")
        window.resizable(False, False)
        window.transient(self.root)
        try:
            window.attributes("-topmost", True)
        except tk.TclError:
            pass

        body = ttk.Frame(window, padding=16)
        body.pack(fill="both", expand=True)
        ttk.Label(
            body,
            text="🔔 Edge TTS не отвечает через текущий VPN-сервер",
            font=("Arial", 11, "bold"),
            foreground="#b45309",
        ).pack(anchor="w")
        selected_voice = str(details.get("voice") or "выбранный в программе")
        ttk.Label(
            body,
            text=f"Выбранный голос: {selected_voice}",
            foreground="#444",
        ).pack(anchor="w", pady=(3, 0))

        self._vpn_notice_status = tk.StringVar(
            value=(
                "Программа пока НЕ переключается на другой голос и сохраняет "
                f"выбранный Edge-голос до {wait_sec} секунд."
            )
        )
        self._vpn_notice_detail = tk.StringVar(
            value=(
                "Переключите сервер в вашей VPN-программе, затем нажмите кнопку проверки. "
                "Окно не блокирует обработку и не мешает работать с VPN."
            )
        )
        ttk.Label(
            body,
            textvariable=self._vpn_notice_status,
            wraplength=540,
            justify="left",
        ).pack(fill="x", pady=(10, 5))
        ttk.Label(
            body,
            textvariable=self._vpn_notice_detail,
            wraplength=540,
            justify="left",
            foreground="#555",
        ).pack(fill="x", pady=(0, 12))

        buttons = ttk.Frame(body)
        buttons.pack(fill="x")
        ttk.Button(
            buttons,
            text="✅ VPN-сервер сменён — проверить",
            command=self._vpn_probe_now,
        ).pack(side="left", padx=(0, 6))
        ttk.Button(
            buttons,
            text="Использовать gTTS сейчас",
            command=self._vpn_allow_fallback_now,
        ).pack(side="left", padx=6)
        ttk.Button(
            buttons,
            text="Скрыть",
            command=self._close_vpn_notice,
        ).pack(side="right")

        def on_destroy(event=None):
            if event is not None and event.widget is not window:
                return
            if self._vpn_notice_window is window:
                self._vpn_notice_window = None
                self._vpn_notice_status = None
                self._vpn_notice_detail = None

        window.protocol("WM_DELETE_WINDOW", self._close_vpn_notice)
        window.bind("<Destroy>", on_destroy, add="+")
        window.update_idletasks()
        try:
            x = self.root.winfo_rootx() + max(20, (self.root.winfo_width() - window.winfo_width()) // 2)
            y = self.root.winfo_rooty() + 80
            window.geometry(f"+{x}+{y}")
            window.lift()
            window.bell()
            window.after(3500, lambda: window.winfo_exists() and window.attributes("-topmost", False))
        except tk.TclError:
            pass

        self._problem(
            "vpn_instability_notification_shown",
            level="warning",
            message="Пользователю показано неблокирующее уведомление о нестабильном VPN для Edge TTS.",
            **details,
        )


    def _vpn_probe_now(self):
        translator = self._active_translator
        if translator and translator.request_edge_probe_now():
            if self._vpn_notice_status is not None:
                self._vpn_notice_status.set("✅ Новый VPN-сервер отмечен; проверка поставлена в очередь.")
            if self._vpn_notice_detail is not None:
                self._vpn_notice_detail.set(
                    "Сначала безопасно завершатся уже начатые сетевые попытки, затем программа "
                    "создаст контрольный аудиофрагмент выбранным голосом."
                )
        elif self._vpn_notice_detail is not None:
            self._vpn_notice_detail.set("Проверка уже не требуется: этап ожидания завершён.")


    def _vpn_allow_fallback_now(self):
        window = self._vpn_notice_window
        if not messagebox.askyesno(
            "Перейти на резервный gTTS?",
            "gTTS поможет закончить обработку, но не умеет выбирать заданный Edge-голос.\n\n"
            "Тембр оставшихся фраз может отличаться. Продолжить?",
            parent=window if window and window.winfo_exists() else self.root,
        ):
            return
        translator = self._active_translator
        if translator and translator.allow_gtts_fallback_now(reason="user_confirmed_fallback"):
            if self._vpn_notice_status is not None:
                self._vpn_notice_status.set("↩️ Переход на резервный gTTS подтверждён...")


    def _close_vpn_notice(self):
        window = self._vpn_notice_window
        self._vpn_notice_window = None
        self._vpn_notice_status = None
        self._vpn_notice_detail = None
        if window and window.winfo_exists():
            try:
                window.destroy()
            except tk.TclError:
                pass
