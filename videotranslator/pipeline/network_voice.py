"""Method owner for VideoTranslator: _notify_network_notice, _begin_edge_voice_preservation, _finish_edge_voice_preservation, request_edge_probe_now, allow_gtts_fallback_now, _enable_gtts_fallback, _acquire_network_gate, _await_selected_voice_or_fallback."""

from __future__ import annotations

import os
import time
from videotranslator.config import EDGE_VOICE_PRESERVE_SEC, EDGE_VOICE_PROBE_INTERVAL_SEC
from videotranslator.core.diagnostics import compact_exception


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
            "      🔀 Edge TTS недоступен: ручная смена VPN не требуется; "
            "программа автоматически выберет рабочий локальный/сетевой резерв."
        )
        self._emit_problem(
            "tts_voice_preservation_started",
            level="warning",
            message=(
                "Edge TTS недоступен; ручное вмешательство не требуется, "
                "запущен автоматический выбор резервного TTS."
            ),
            voice=self.selected_voice,
            incident=incident,
            reason=reason,
            wait_sec=0,
            automatic_fallback_after_sec=0,
            user_action_required=False,
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
            message="Автоматический выбор доступного TTS-маршрута завершён.",
            voice=self.selected_voice,
            incident=incident,
            reason=reason,
            gtts_enabled=bool(self._tts_allow_gtts_fallback),
        )
        if notify_action:
            self._notify_network_notice(notify_action, incident=incident, reason=reason)
        return True


    def request_edge_probe_now(self):
        """Legacy hook for one immediate Edge health probe; never required by UI."""
        with self.edge_voice_lock:
            if not self.edge_voice_preservation_active:
                return False
            incident = self.edge_voice_incident
        self.edge_network_guard.request_probe_now()
        self.edge_voice_probe_requested.set()
        self._emit_problem(
            "edge_immediate_probe_requested",
            message="Запрошена немедленная фоновая проверка Edge TTS.",
            voice=self.selected_voice,
            incident=incident,
        )
        return True


    def allow_gtts_fallback_now(self, reason: str = "user_requested"):
        """Legacy compatibility hook; automatic mode normally enables this itself."""
        with self.edge_voice_lock:
            if not self.edge_voice_preservation_active:
                return False
            incident = self.edge_voice_incident
            self.edge_voice_fallback_request_reason = str(reason or "user_requested")
        self.edge_voice_fallback_requested.set()
        self._emit_problem(
            "tts_gtts_fallback_requested",
            message="Резервный gTTS разрешён без ожидания и без интерактивного подтверждения.",
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
            "      ↩️ Edge TTS недоступен. Резервный gTTS включён автоматически "
            "для оставшихся сегментов; тембр голоса может отличаться."
        )
        self._emit_problem(
            "tts_gtts_fallback_enabled",
            level="warning",
            message="Edge TTS недоступен; резервный gTTS автоматически разрешён для оставшихся сегментов.",
            voice=self.selected_voice,
            incident=incident,
            reason=self._tts_fallback_reason,
            voice_may_differ=True,
        )
        return True


    def _acquire_network_gate(self, gate):
        while not gate.acquire(timeout=0.25):
            self._check_cancel()


    def _probe_edge_voice_once(self, text: str, voice: str, segment_index: int) -> bool:
        """One bounded real Edge request used before final voice-consistency repair.

        This intentionally bypasses the storm cooldown only once.  It never falls back
        to gTTS and does not start another 120-second preservation window.
        """
        probe_path = os.path.join(self.temp_dir, f"edge_final_probe_{segment_index:05d}.mp3")
        parallel_acquired = False
        probe_acquired = False
        try:
            self._check_cancel()
            self._acquire_network_gate(self.edge_parallel_gate)
            parallel_acquired = True
            self._acquire_network_gate(self.edge_probe_gate)
            probe_acquired = True
            if os.path.exists(probe_path):
                os.remove(probe_path)
            self._run_edge_tts(text, probe_path, voice, rate="+0%")
            if not os.path.exists(probe_path) or os.path.getsize(probe_path) <= 200:
                raise RuntimeError("Edge TTS probe created an empty audio file")
            if self.edge_network_guard.is_active():
                self.edge_network_guard.force_recovery("edge_tts", reason="final_voice_repair_probe")
            else:
                self.edge_network_guard.record_success("edge_tts")
            self._emit_problem(
                "tts_final_voice_repair_probe_succeeded",
                message="Edge TTS отвечает; разрешена финальная замена резервных gTTS-фраз выбранным голосом.",
                tts_segment_index=segment_index,
                voice=voice,
            )
            return True
        except Exception as exc:
            self.edge_network_guard.record_failure("edge_tts", exc)
            self._emit_problem(
                "tts_final_voice_repair_probe_failed",
                level="warning",
                message="Финальная проверка Edge TTS не прошла; готовые gTTS-фразы сохраняются без дополнительного ожидания.",
                tts_segment_index=segment_index,
                voice=voice,
                exception={
                    "type": type(exc).__name__,
                    "message": compact_exception(exc, max_len=1000),
                },
            )
            return False
        finally:
            if probe_acquired:
                self.edge_probe_gate.release()
            if parallel_acquired:
                self.edge_parallel_gate.release()
            try:
                if os.path.exists(probe_path):
                    os.remove(probe_path)
            except OSError:
                pass


    def _await_selected_voice_or_fallback(self, text: str, voice: str,
                                           segment_index: int) -> str:
        """Select a working TTS route without asking the user to change VPN.

        The normal generation path already tries the selected Edge voice and then
        any installed compatible Piper voice.  If those routes cannot prepare the
        segment, recovery immediately enables gTTS.  A previously requested manual
        probe is still honoured once for backwards compatibility, but there is no
        countdown, popup-driven wait, or repeated VPN polling.
        """
        incident = self._begin_edge_voice_preservation(reason="edge_segments_deferred")

        # Preserve the old explicit API for callers/tests, but never require it.
        if self.edge_voice_fallback_requested.is_set():
            with self.edge_voice_lock:
                reason = self.edge_voice_fallback_request_reason or "user_requested"
            self._enable_gtts_fallback(reason)
            self._finish_edge_voice_preservation(reason="fallback_selected")
            return "gtts_allowed"

        # If an external caller explicitly requested an immediate Edge re-check, do
        # exactly one bounded probe.  Failure falls through to automatic fallback.
        if self.edge_voice_probe_requested.is_set():
            self.edge_voice_probe_requested.clear()
            self.edge_network_guard.request_probe_now()
            probe_path = os.path.join(self.temp_dir, f"edge_manual_probe_{segment_index:05d}.mp3")
            try:
                provider = self.generate_tts(
                    text,
                    probe_path,
                    voice,
                    rate_pct=0,
                    segment_index=segment_index,
                    allow_gtts=False,
                )
                if provider == "edge_tts":
                    self.edge_network_guard.force_recovery(
                        "edge_tts", reason="successful_manual_probe"
                    )
                    self._finish_edge_voice_preservation(
                        reason="successful_manual_probe",
                        notify_action="recovered",
                    )
                    self.log("      ✅ Edge TTS снова доступен; выбранный голос сохранён.")
                    return "edge_recovered"
            finally:
                try:
                    if os.path.exists(probe_path):
                        os.remove(probe_path)
                except OSError:
                    pass

        self._enable_gtts_fallback("automatic_edge_unavailable")
        self._finish_edge_voice_preservation(reason="automatic_fallback_selected")
        self._emit_problem(
            "tts_automatic_fallback_selected",
            level="warning",
            message=(
                "Выбранный Edge-голос и локальный Piper не подготовили сегмент; "
                "программа без участия пользователя перешла на рабочий сетевой резерв."
            ),
            tts_segment_index=segment_index,
            voice=voice,
            incident=incident,
            fallback_provider="gtts",
            user_action_required=False,
        )
        return "gtts_allowed"
