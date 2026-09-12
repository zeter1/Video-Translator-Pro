"""Method owner for VideoTranslator: _notify_network_notice, _begin_edge_voice_preservation, _finish_edge_voice_preservation, request_edge_probe_now, allow_gtts_fallback_now, _enable_gtts_fallback, _acquire_network_gate, _await_selected_voice_or_fallback."""

from __future__ import annotations

import os
import time
from videotranslator.config import EDGE_VOICE_PRESERVE_SEC, EDGE_VOICE_PROBE_INTERVAL_SEC


class NetworkVoiceMixin:
    """Behavior-preserving methods extracted from the legacy monolith."""

    def _notify_network_notice(self, action: str, **details):
        if not self.network_notice_cb:
            return
        try:
            self.network_notice_cb(action, {
                "input_path": self.current_input_path,
                "stage": self.current_stage,
                "voice": self.selected_voice,
                **details,
            })
        except Exception:
            pass


    def _begin_edge_voice_preservation(self, reason: str) -> int:
        now = time.monotonic()
        with self.edge_voice_lock:
            if self.edge_voice_preservation_active:
                return self.edge_voice_incident
            self.edge_voice_incident += 1
            incident = self.edge_voice_incident
            self.edge_voice_preservation_active = True
            self.edge_voice_deadline = now + EDGE_VOICE_PRESERVE_SEC
            self.edge_voice_probe_requested.clear()
            self.edge_voice_fallback_requested.clear()
            self.edge_voice_fallback_request_reason = ""

        self.log(
            "      🔔 Edge TTS нестабилен через текущий VPN. "
            f"До {EDGE_VOICE_PRESERVE_SEC}с сохраняю выбранный голос и жду смены VPN-сервера."
        )
        self._emit_problem(
            "tts_voice_preservation_started",
            level="warning",
            message="Edge TTS нестабилен; программа временно не использует голос gTTS и ждёт смены VPN.",
            voice=self.selected_voice,
            incident=incident,
            reason=reason,
            wait_sec=EDGE_VOICE_PRESERVE_SEC,
            automatic_fallback_after_sec=EDGE_VOICE_PRESERVE_SEC,
        )
        self._notify_network_notice(
            "unstable",
            incident=incident,
            reason=reason,
            wait_sec=EDGE_VOICE_PRESERVE_SEC,
        )
        return incident


    def _finish_edge_voice_preservation(self, reason: str, notify_action: str = ""):
        with self.edge_voice_lock:
            if not self.edge_voice_preservation_active:
                return False
            incident = self.edge_voice_incident
            self.edge_voice_preservation_active = False
            self.edge_voice_deadline = 0.0
            self.edge_voice_probe_requested.clear()
            self.edge_voice_fallback_requested.clear()
            self.edge_voice_fallback_request_reason = ""

        self._emit_problem(
            "tts_voice_preservation_finished",
            message="Ожидание выбранного голоса завершено.",
            voice=self.selected_voice,
            incident=incident,
            reason=reason,
            gtts_enabled=bool(self._tts_allow_gtts_fallback),
        )
        if notify_action:
            self._notify_network_notice(notify_action, incident=incident, reason=reason)
        return True


    def request_edge_probe_now(self):
        """Вызывается кнопкой после того, как пользователь сменил VPN-сервер."""
        with self.edge_voice_lock:
            if not self.edge_voice_preservation_active:
                return False
            incident = self.edge_voice_incident
        self.edge_network_guard.request_probe_now()
        self.edge_voice_probe_requested.set()
        self._emit_problem(
            "vpn_server_change_probe_requested",
            message="Пользователь сообщил о смене VPN-сервера; Edge TTS будет проверен немедленно.",
            voice=self.selected_voice,
            incident=incident,
        )
        return True


    def allow_gtts_fallback_now(self, reason: str = "user_requested"):
        """Разрешает пользователю не ждать окончания защитного окна."""
        with self.edge_voice_lock:
            if not self.edge_voice_preservation_active:
                return False
            incident = self.edge_voice_incident
            self.edge_voice_fallback_request_reason = str(reason or "user_requested")
        self.edge_voice_fallback_requested.set()
        self._emit_problem(
            "tts_gtts_fallback_requested",
            message="Пользователь разрешил перейти на резервный gTTS до окончания ожидания Edge TTS.",
            voice=self.selected_voice,
            incident=incident,
            reason=reason,
            voice_may_differ=True,
        )
        return True


    def _enable_gtts_fallback(self, reason: str):
        with self.edge_voice_lock:
            if self._tts_allow_gtts_fallback:
                return False
            self._tts_allow_gtts_fallback = True
            self._tts_fallback_reason = str(reason or "edge_unavailable")
            incident = self.edge_voice_incident

        self.log(
            "      ↩️ Edge TTS не восстановился. Включён резервный gTTS только для "
            "оставшихся сегментов; тембр голоса может отличаться."
        )
        self._emit_problem(
            "tts_gtts_fallback_enabled",
            level="warning",
            message="Edge TTS не восстановился; для оставшихся сегментов разрешён резервный gTTS.",
            voice=self.selected_voice,
            incident=incident,
            reason=self._tts_fallback_reason,
            voice_may_differ=True,
        )
        self._notify_network_notice(
            "fallback",
            incident=incident,
            reason=self._tts_fallback_reason,
            voice_may_differ=True,
        )
        return True


    def _acquire_network_gate(self, gate):
        while not gate.acquire(timeout=0.25):
            self._check_cancel()


    def _await_selected_voice_or_fallback(self, text: str, voice: str,
                                           segment_index: int) -> str:
        """
        Даёт пользователю время сменить VPN и проверяет Edge реальным TTS-запросом.

        Возвращает ``edge_recovered`` либо ``gtts_allowed``. Ожидание ограничено,
        поэтому полностью недоступный Edge не может навсегда остановить программу.
        """
        incident = self._begin_edge_voice_preservation(reason="edge_segments_deferred")
        with self.edge_voice_lock:
            deadline = self.edge_voice_deadline

        next_probe_at = time.monotonic() + max(
            1.0,
            min(30.0, self.edge_network_guard.seconds_until_probe() or 15.0),
        )
        probe_number = 0
        probe_path = os.path.join(self.temp_dir, f"edge_vpn_probe_{segment_index:05d}.mp3")

        while True:
            self._check_cancel()
            now = time.monotonic()

            if self.edge_voice_fallback_requested.is_set():
                with self.edge_voice_lock:
                    reason = self.edge_voice_fallback_request_reason or "user_requested"
                self._enable_gtts_fallback(reason)
                return "gtts_allowed"

            remaining = max(0.0, deadline - now)
            if remaining <= 0:
                self._enable_gtts_fallback("edge_wait_timeout")
                return "gtts_allowed"

            manual_probe = self.edge_voice_probe_requested.is_set()
            if manual_probe:
                self.edge_voice_probe_requested.clear()
                self.edge_network_guard.request_probe_now()

            automatic_probe = (
                now >= next_probe_at
                and self.edge_network_guard.seconds_until_probe() <= 0
            )
            if manual_probe or automatic_probe:
                probe_number += 1
                self.set_progress(
                    81,
                    f"Проверка Edge TTS после смены VPN (попытка {probe_number})...",
                )
                self._emit_problem(
                    "tts_edge_probe_started",
                    message="Проверяется доступность выбранного голоса Edge TTS.",
                    tts_segment_index=segment_index,
                    voice=voice,
                    incident=incident,
                    probe_number=probe_number,
                    manual=manual_probe,
                    remaining_wait_sec=round(remaining, 1),
                )
                self._notify_network_notice(
                    "checking",
                    incident=incident,
                    probe_number=probe_number,
                    manual=manual_probe,
                )
                provider = self.generate_tts(
                    text,
                    probe_path,
                    voice,
                    rate_pct=0,
                    segment_index=segment_index,
                    allow_gtts=False,
                )
                if provider == "edge_tts":
                    forced = self.edge_network_guard.force_recovery(
                        "edge_tts",
                        reason="successful_vpn_probe",
                    )
                    if not forced:
                        self._finish_edge_voice_preservation(
                            reason="successful_vpn_probe",
                            notify_action="recovered",
                        )
                    self.log("      ✅ Новый VPN-маршрут подходит: выбранный голос Edge TTS восстановлен.")
                    self._emit_problem(
                        "tts_voice_preservation_recovered",
                        message="Проверка Edge TTS успешна; озвучка продолжится выбранным голосом.",
                        tts_segment_index=segment_index,
                        voice=voice,
                        incident=incident,
                        probe_number=probe_number,
                    )
                    return "edge_recovered"

                wait_for_probe = self.edge_network_guard.seconds_until_probe()
                next_probe_at = time.monotonic() + max(
                    float(EDGE_VOICE_PROBE_INTERVAL_SEC),
                    wait_for_probe,
                )
                self._emit_problem(
                    "tts_edge_probe_failed",
                    level="warning",
                    message="Edge TTS всё ещё недоступен; ожидание смены VPN продолжается.",
                    tts_segment_index=segment_index,
                    voice=voice,
                    incident=incident,
                    probe_number=probe_number,
                    next_probe_after_sec=round(max(0.0, next_probe_at - time.monotonic()), 1),
                    remaining_wait_sec=round(max(0.0, deadline - time.monotonic()), 1),
                )

            self.set_progress(
                80,
                f"VPN нестабилен: смените сервер и нажмите «Проверить» "
                f"(ожидание ещё {int(remaining) + 1}с)...",
            )
            self._sleep_or_cancel(min(0.5, remaining))
