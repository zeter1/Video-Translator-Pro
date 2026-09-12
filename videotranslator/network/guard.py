"""Owner module for: NetworkStormGuard, _SessionRequestsProxy, ReusableGoogleTranslator."""

from __future__ import annotations

from collections import deque
import importlib
import threading
import time
from videotranslator.config import NETWORK_CONNECT_TIMEOUT_SEC, NETWORK_READ_TIMEOUT_SEC, NETWORK_STORM_THRESHOLD, NETWORK_STORM_WINDOW_SEC
from videotranslator.core.diagnostics import classify_exception


class NetworkStormGuard:
    """Останавливает новые сетевые запросы, когда за короткое время накопился шквал ошибок."""

    def __init__(self, log=None, event_cb=None, clock=None, *, service: str = "network",
                 threshold: int = NETWORK_STORM_THRESHOLD,
                 window_sec: int = NETWORK_STORM_WINDOW_SEC,
                 initial_pause_sec: int = 30, recovery_successes: int = 3,
                 recovery_min_sec: int = 0, probe_interval_sec: int = 0):
        self.log = log
        self.event_cb = event_cb
        self.clock = clock or time.monotonic
        self.service = service
        self.threshold = max(2, int(threshold))
        self.window_sec = max(10, int(window_sec))
        self.initial_pause_sec = max(1, min(60, int(initial_pause_sec)))
        self.required_recovery_successes = max(1, int(recovery_successes))
        self.recovery_min_sec = max(0, int(recovery_min_sec))
        self.probe_interval_sec = max(0, int(probe_interval_sec))
        self.lock = threading.Lock()
        self.failures = deque()
        self.active = False
        self.cooldown_until = 0.0
        self.storm_number = 0
        self.recovery_successes = 0
        self.recovery_started_at = 0.0
        self.suppressed_failures = 0
        self.started_at = 0.0
        self.component = service
        self.cooldown_rounds = 0
        self.probe_in_flight = False

    def _event(self, event: str, message: str, **details):
        if self.event_cb:
            try:
                self.event_cb(event, level="warning" if event.endswith("started") else "info",
                              message=message, **details)
            except Exception:
                pass

    def before_request(self, sleep_or_cancel):
        while True:
            with self.lock:
                remaining = self.cooldown_until - self.clock() if self.active else 0.0
            if remaining <= 0:
                return
            sleep_or_cancel(remaining)

    def is_active(self) -> bool:
        with self.lock:
            return bool(self.active)

    def seconds_until_probe(self) -> float:
        with self.lock:
            if not self.active:
                return 0.0
            return max(0.0, self.cooldown_until - self.clock())

    def request_probe_now(self) -> bool:
        """Разрешает ближайшему ожидающему потоку немедленно проверить новый VPN-маршрут."""
        with self.lock:
            if not self.active:
                return False
            self.cooldown_until = self.clock()
            self.probe_in_flight = False
            return True

    def claim_probe_or_bypass(self) -> str:
        """
        Для резервируемого сервиса возвращает normal/probe/bypass.
        Во время шторма только один редкий запрос проверяет восстановление,
        остальные сразу используют резервный сервис и не ждут всей паузы.
        """
        now = self.clock()
        with self.lock:
            if not self.active:
                return "normal"
            if now < self.cooldown_until or self.probe_in_flight:
                return "bypass"
            self.probe_in_flight = True
            return "probe"

    def record_failure(self, component: str, exc: Exception) -> bool:
        """Возвращает True, если подробность этой ошибки ещё следует записать."""
        now = self.clock()
        started = False
        pause_sec = 0
        failure_count = 0
        with self.lock:
            self.failures.append(now)
            cutoff = now - self.window_sec
            while self.failures and self.failures[0] < cutoff:
                self.failures.popleft()

            if not self.active and len(self.failures) >= self.threshold:
                self.active = True
                self.storm_number += 1
                pause_sec = min(60, self.initial_pause_sec + (self.storm_number - 1) * 15)
                self.cooldown_until = now + pause_sec
                self.started_at = now
                self.recovery_successes = 0
                self.recovery_started_at = 0.0
                self.suppressed_failures = 0
                self.component = component
                self.cooldown_rounds = 1
                self.probe_in_flight = False
                started = True
            elif self.active:
                self.suppressed_failures += 1
                self.recovery_successes = 0
                self.recovery_started_at = 0.0
                self.probe_in_flight = False
                if now >= self.cooldown_until:
                    self.cooldown_rounds += 1
                    pause_sec = min(60, self.initial_pause_sec + (self.cooldown_rounds - 1) * 15)
                    self.cooldown_until = now + pause_sec
            failure_count = len(self.failures)

        if started:
            if self.log:
                self.log(
                    f"      🌩️ Сетевой шторм начался: {failure_count} ошибок за "
                    f"{self.window_sec}с; сервис {self.service}; передышка {pause_sec}с"
                )
            self._event(
                "network_storm_started",
                "Накопилось много сетевых ошибок; новые запросы временно приостановлены.",
                component=component,
                service=self.service,
                failures_in_window=failure_count,
                window_sec=self.window_sec,
                pause_sec=pause_sec,
                exception_category=classify_exception(exc),
            )
        return not self.active or started

    def record_success(self, component: str):
        finished = None
        now = self.clock()
        with self.lock:
            if not self.active:
                cutoff = now - self.window_sec
                while self.failures and self.failures[0] < cutoff:
                    self.failures.popleft()
                return
            self.probe_in_flight = False
            if now < self.cooldown_until:
                return
            if self.recovery_started_at <= 0:
                self.recovery_started_at = now
            self.recovery_successes += 1
            stable_for = now - self.recovery_started_at
            recovered = (
                self.recovery_successes >= self.required_recovery_successes
                and stable_for >= self.recovery_min_sec
            )
            if recovered:
                finished = {
                    "component": component or self.component,
                    "service": self.service,
                    "duration_sec": round(now - self.started_at, 3),
                    "suppressed_failures": self.suppressed_failures,
                    "successful_requests": self.recovery_successes,
                    "stable_for_sec": round(stable_for, 3),
                    "cooldown_rounds": self.cooldown_rounds,
                }
                self.active = False
                self.cooldown_until = 0.0
                self.failures.clear()
                self.recovery_successes = 0
                self.recovery_started_at = 0.0
                self.suppressed_failures = 0
                self.cooldown_rounds = 0
            elif self.probe_interval_sec > 0:
                self.cooldown_until = now + self.probe_interval_sec

        if finished:
            if self.log:
                self.log(
                    "      🌤️ Сетевой шторм закончился: сервис снова отвечает "
                    f"({finished['service']}, {finished['successful_requests']} успешных проверок, "
                    f"стабильно {finished['stable_for_sec']:.0f}с)"
                )
            self._event(
                "network_storm_finished",
                "Сетевой сервис снова стабильно отвечает.",
                **finished,
            )

    def force_recovery(self, component: str, reason: str = "manual_probe") -> bool:
        """
        Завершает шторм после успешной целевой проверки.

        Используется только тогда, когда программа специально дождалась смены VPN
        и успешно создала реальный TTS-фрагмент выбранным голосом.
        """
        now = self.clock()
        with self.lock:
            if not self.active:
                return False
            finished = {
                "component": component or self.component,
                "service": self.service,
                "duration_sec": round(now - self.started_at, 3),
                "suppressed_failures": self.suppressed_failures,
                "successful_requests": max(1, self.recovery_successes),
                "stable_for_sec": round(max(0.0, now - self.recovery_started_at), 3)
                if self.recovery_started_at > 0 else 0.0,
                "cooldown_rounds": self.cooldown_rounds,
                "recovery_reason": reason,
            }
            self.active = False
            self.cooldown_until = 0.0
            self.failures.clear()
            self.recovery_successes = 0
            self.recovery_started_at = 0.0
            self.suppressed_failures = 0
            self.cooldown_rounds = 0
            self.probe_in_flight = False

        if self.log:
            self.log(
                "      🌤️ Edge TTS снова отвечает: успешна проверка после смены VPN; "
                "возвращаю выбранный голос"
            )
        self._event(
            "network_storm_finished",
            "Сетевой сервис успешно проверен после ожидания или смены VPN.",
            **finished,
        )
        return True


class _SessionRequestsProxy:
    """Минимальный requests-совместимый объект для deep-translator."""

    def __init__(self, requests_module, session):
        self._requests = requests_module
        self._session = session

    def get(self, url, **kwargs):
        if kwargs.get("timeout") is None:
            kwargs["timeout"] = (NETWORK_CONNECT_TIMEOUT_SEC, NETWORK_READ_TIMEOUT_SEC)
        return self._session.get(url, **kwargs)

    def __getattr__(self, name):
        return getattr(self._requests, name)


class ReusableGoogleTranslator:
    """GoogleTranslator с одним HTTP-сеансом и повторным использованием TLS-соединения."""

    def __init__(self, target: str):
        from deep_translator import GoogleTranslator

        try:
            import requests
        except ModuleNotFoundError:
            # Нужен для изолированных тестов с подменённым deep-translator.
            self.requests_module = None
            self.session = None
            self.translator = GoogleTranslator(source="auto", target=target)
            self.google_module = None
            self.original_requests = None
            self.proxy = None
            return

        self.requests_module = requests
        self.session = requests.Session()
        adapter = requests.adapters.HTTPAdapter(pool_connections=2, pool_maxsize=4, max_retries=0)
        self.session.mount("https://", adapter)
        self.session.mount("http://", adapter)
        self.translator = GoogleTranslator(source="auto", target=target)
        self.google_module = None
        self.original_requests = None
        self.proxy = _SessionRequestsProxy(requests, self.session)
        try:
            self.google_module = importlib.import_module("deep_translator.google")
            self.original_requests = getattr(self.google_module, "requests", None)
            self.google_module.requests = self.proxy
        except Exception:
            # Совместимость с тестовыми/старыми сборками deep-translator.
            self.google_module = None

    def translate(self, text: str) -> str:
        return self.translator.translate(text)

    def close(self):
        try:
            if self.google_module is not None and getattr(self.google_module, "requests", None) is self.proxy:
                self.google_module.requests = self.original_requests
        finally:
            if self.session is not None:
                self.session.close()
