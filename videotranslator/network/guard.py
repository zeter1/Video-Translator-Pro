"""Owner module for: NetworkStormGuard, _SessionRequestsProxy, ReusableGoogleTranslator."""

from __future__ import annotations

from collections import deque
import importlib
import threading
import time
from videotranslator.config import NETWORK_CONNECT_TIMEOUT_SEC, NETWORK_READ_TIMEOUT_SEC, NETWORK_STORM_THRESHOLD, NETWORK_STORM_WINDOW_SEC, TRANSLATION_MIN_REQUEST_INTERVAL_SEC
from videotranslator.core.diagnostics import classify_exception, compact_exception


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
        """Allow the next waiting worker to run one immediate service health probe."""
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

        Используется после успешной целевой проверки сервиса, когда можно снять
        временный circuit-breaker без ожидания обычного окна восстановления.
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
                "      🌤️ Edge TTS снова отвечает: контрольная проверка успешна; "
                "возвращаю выбранный голос"
            )
        self._event(
            "network_storm_finished",
            "Сетевой сервис успешно прошёл фоновую контрольную проверку.",
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
    """Устойчивый Google-переводчик с автоматическим переключением endpoint.

    Основной маршрут использует JSON endpoint ``translate.googleapis.com``.
    Старый deep-translator/mobile endpoint остаётся резервом. Это важно для
    длинных видео: блокировка одного endpoint не должна останавливать весь
    перевод, если второй маршрут Google продолжает отвечать.
    """

    JSON_ENDPOINT = "https://translate.googleapis.com/translate_a/single"
    BACKEND_JSON = "google_json_gtx"
    BACKEND_MOBILE = "deep_translator_google_mobile"

    def __init__(self, target: str):
        from deep_translator import GoogleTranslator

        self.target = str(target or "ru")
        self._pace_lock = threading.Lock()
        self._last_request_at = 0.0
        self._min_request_interval = max(0.0, float(TRANSLATION_MIN_REQUEST_INTERVAL_SEC))
        self._preferred_backend = self.BACKEND_JSON
        self._route_event = None

        try:
            import requests
        except ModuleNotFoundError:
            # Нужен для изолированных тестов с подменённым deep-translator.
            self.requests_module = None
            self.session = None
            self.translator = GoogleTranslator(source="auto", target=self.target)
            self.google_module = None
            self.original_requests = None
            self.proxy = None
            self._preferred_backend = self.BACKEND_MOBILE
            return

        self.requests_module = requests
        self.session = requests.Session()
        try:
            self.session.headers.update({
                "User-Agent": (
                    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                    "AppleWebKit/537.36 (KHTML, like Gecko) "
                    "Chrome/129.0 Safari/537.36"
                ),
                "Accept": "application/json,text/plain,*/*",
                "Accept-Language": "en-US,en;q=0.9",
            })
        except Exception:
            pass
        adapter = requests.adapters.HTTPAdapter(pool_connections=2, pool_maxsize=4, max_retries=0)
        self.session.mount("https://", adapter)
        self.session.mount("http://", adapter)

        # deep-translator остаётся резервом для случаев, когда JSON endpoint
        # временно недоступен на конкретном маршруте/VPN.
        self.translator = GoogleTranslator(source="auto", target=self.target)
        # Изолированные тесты/расширения иногда подменяют GoogleTranslator своей
        # реализацией. В этом случае не обходим подмену реальным HTTP JSON-запросом.
        google_cls_module = str(getattr(GoogleTranslator, "__module__", ""))
        if not google_cls_module.startswith("deep_translator"):
            self._preferred_backend = self.BACKEND_MOBILE
        self.google_module = None
        self.original_requests = None
        self.proxy = _SessionRequestsProxy(requests, self.session)
        try:
            self.google_module = importlib.import_module("deep_translator.google")
            self.original_requests = getattr(self.google_module, "requests", None)
            self.google_module.requests = self.proxy
        except Exception:
            self.google_module = None

    def _pace(self):
        with self._pace_lock:
            now = time.monotonic()
            remaining = self._min_request_interval - (now - self._last_request_at)
            if remaining > 0:
                time.sleep(remaining)
            self._last_request_at = time.monotonic()

    def _translate_json(self, text: str) -> str:
        if self.session is None:
            raise RuntimeError("requests session unavailable for google json endpoint")
        self._pace()
        response = self.session.get(
            self.JSON_ENDPOINT,
            params={
                "client": "gtx",
                "sl": "auto",
                "tl": self.target,
                "dt": "t",
                "q": text,
            },
            timeout=(NETWORK_CONNECT_TIMEOUT_SEC, NETWORK_READ_TIMEOUT_SEC),
        )
        response.raise_for_status()
        payload = response.json()
        chunks = payload[0] if isinstance(payload, list) and payload else None
        if not isinstance(chunks, list):
            raise RuntimeError("Google JSON endpoint returned unexpected payload")
        translated = "".join(
            str(item[0])
            for item in chunks
            if isinstance(item, (list, tuple)) and item and item[0] is not None
        ).strip()
        if not translated:
            raise RuntimeError("Google JSON endpoint returned empty translation")
        return translated

    def _translate_mobile(self, text: str) -> str:
        self._pace()
        return self.translator.translate(text)

    def _backend_order(self):
        if self._preferred_backend == self.BACKEND_MOBILE:
            return (self.BACKEND_MOBILE, self.BACKEND_JSON)
        return (self.BACKEND_JSON, self.BACKEND_MOBILE)

    def translate(self, text: str) -> str:
        failures = []
        first_backend = self._backend_order()[0]
        for backend in self._backend_order():
            try:
                if backend == self.BACKEND_JSON:
                    translated = self._translate_json(text)
                else:
                    translated = self._translate_mobile(text)
                if backend != first_backend:
                    failed_backend, failed_exc = failures[-1]
                    self._route_event = {
                        "from": failed_backend,
                        "to": backend,
                        "reason": classify_exception(failed_exc),
                        "exception": compact_exception(failed_exc, max_len=500),
                    }
                    # Успешный резерв становится предпочтительным до закрытия клиента:
                    # не продолжаем долбить endpoint, который только что получил 429/5xx.
                    self._preferred_backend = backend
                return translated
            except Exception as exc:
                failures.append((backend, exc))

        summary = "; ".join(
            f"{backend}={compact_exception(exc, max_len=240)}"
            for backend, exc in failures
        )
        # В тексте сохраняются 429/timeout-маркеры из первичных исключений,
        # чтобы classify_exception выше по стеку корректно выбрал rate_limit/network.
        raise RuntimeError(f"Все маршруты Google Translate недоступны: {summary}") from failures[-1][1]

    def consume_route_event(self):
        event = self._route_event
        self._route_event = None
        return event

    @property
    def active_backend(self) -> str:
        return self._preferred_backend

    def close(self):
        try:
            if self.google_module is not None and getattr(self.google_module, "requests", None) is self.proxy:
                self.google_module.requests = self.original_requests
        finally:
            if self.session is not None:
                self.session.close()
