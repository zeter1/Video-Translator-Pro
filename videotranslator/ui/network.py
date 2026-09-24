"""Silent network failover UI bridge.

Network/TTS routing is automatic.  These compatibility methods intentionally keep
legacy callbacks/extensions working without ever asking the user to change VPN or
confirm a fallback provider.
"""

from __future__ import annotations

import tkinter as tk


class UINetworkMixin:
    """Consume network routing events without interactive VPN notifications."""

    def _threadsafe_network_notice(self, action: str, details: dict):
        self._safe_after(0, self._handle_network_notice, str(action), dict(details or {}))

    def _handle_network_notice(self, action: str, details: dict):
        if self._closing:
            return
        # A window from an older app state must not survive into automatic mode.
        self._close_vpn_notice()
        if action in {"unstable", "fallback"}:
            self._problem(
                "network_failover_ui_suppressed",
                message=(
                    "Интерактивное VPN-уведомление не показано: программа сама "
                    "переключает доступные локальные и сетевые TTS-маршруты."
                ),
                action=action,
                user_action_required=False,
                **details,
            )

    def _show_vpn_notice(self, details: dict):
        """Legacy entry point: deliberately never creates a popup."""
        self._handle_network_notice("unstable", dict(details or {}))

    def _vpn_probe_now(self):
        """Compatibility hook for old extensions; no manual VPN step is required."""
        translator = self._active_translator
        if translator:
            translator.request_edge_probe_now()

    def _vpn_allow_fallback_now(self):
        """Compatibility hook: fallback is allowed without confirmation dialogs."""
        translator = self._active_translator
        if translator:
            translator.allow_gtts_fallback_now(reason="legacy_ui_request")

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
