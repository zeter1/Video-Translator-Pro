"""Batch-scoped Google repair circuit shared by per-video translators."""
from __future__ import annotations

import threading
import time


class TranslationProviderCoolingDownError(RuntimeError):
    """Raised when a batch-level provider cooldown intentionally blocks a request."""


class SharedTranslationCircuit:
    def __init__(self, clock=None):
        self.clock = clock or time.monotonic
        self.lock = threading.Lock()
        self.cooldown_until = 0.0
        self.reason = ""
        self.failures = 0
        self.failure_streak = 0

    def remaining(self) -> float:
        with self.lock:
            return max(0.0, self.cooldown_until - self.clock())

    def available(self) -> bool:
        return self.remaining() <= 0.0

    def mark_failure(self, reason: str, cooldown_sec: float) -> float:
        """Record a real provider failure and return the effective cooldown.

        Repeated 429 responses across a long batch back off exponentially so we do
        not probe a blocked unofficial endpoint every minute for hours. Network
        failures keep their caller-provided fixed cooldown.
        """
        with self.lock:
            now = self.clock()
            normalized_reason = str(reason or "provider_failure")
            self.failures += 1
            if normalized_reason == self.reason:
                self.failure_streak += 1
            else:
                self.failure_streak = 1
            self.reason = normalized_reason

            effective_cooldown = max(0.0, float(cooldown_sec))
            if normalized_reason == "rate_limit":
                multiplier = 2 ** min(max(0, self.failure_streak - 1), 4)
                effective_cooldown = min(effective_cooldown * multiplier, 15.0 * 60.0)

            self.cooldown_until = max(self.cooldown_until, now + effective_cooldown)
            return max(0.0, self.cooldown_until - now)

    def mark_success(self) -> None:
        with self.lock:
            self.cooldown_until = 0.0
            self.reason = ""
            self.failures = 0
            self.failure_streak = 0

    def assert_available(self) -> None:
        remaining = self.remaining()
        if remaining > 0:
            raise TranslationProviderCoolingDownError(
                f"Google repair временно отключён ещё на {remaining:.0f}с после сетевого/rate-limit сбоя."
            )
