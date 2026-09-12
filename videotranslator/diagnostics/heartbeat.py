"""Owner module for: ActivityHeartbeat."""

from __future__ import annotations

import threading
import time
from videotranslator.core.timefmt import fmt_time


class ActivityHeartbeat:
    """Пишет редкий признак жизни во время долгой блокирующей операции."""

    def __init__(self, log, label: str, interval: int = 60, event_cb=None, **event_details):
        self.log = log
        self.label = label
        self.interval = max(15, int(interval))
        self.event_cb = event_cb
        self.event_details = dict(event_details or {})
        self.started_at = 0.0
        self.stop_event = threading.Event()
        self.thread = None

    def __enter__(self):
        self.started_at = time.monotonic()
        self.thread = threading.Thread(target=self._run, name=f"heartbeat-{self.label}", daemon=True)
        self.thread.start()
        return self

    def _run(self):
        while not self.stop_event.wait(self.interval):
            elapsed = int(time.monotonic() - self.started_at)
            try:
                self.log(f"   ⏳ {self.label}: работа продолжается, прошло {fmt_time(elapsed)}...")
                if self.event_cb:
                    self.event_cb(
                        "activity_heartbeat",
                        message="Долгая операция продолжает выполняться.",
                        operation=self.label,
                        elapsed_sec=elapsed,
                        **self.event_details,
                    )
            except Exception:
                return

    def __exit__(self, exc_type, exc_value, exc_tb):
        self.stop_event.set()
        return False
