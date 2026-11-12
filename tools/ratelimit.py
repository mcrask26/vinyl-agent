"""A small thread-safe rate limiter shared by all calls to one API."""

import threading
import time
from collections import deque


class RateLimiter:
    """Allows at most `calls` calls in any `period`-second window. Callers wait their turn."""

    def __init__(self, calls: int, period: float):
        self.calls = calls
        self.period = period
        self._times = deque()
        self._lock = threading.Lock()

    def _prune(self, now: float) -> None:
        while self._times and now - self._times[0] >= self.period:
            self._times.popleft()

    def wait(self) -> None:
        with self._lock:  # holding the lock while sleeping makes callers queue up in order
            now = time.monotonic()
            self._prune(now)
            if len(self._times) >= self.calls:
                time.sleep(self.period - (now - self._times[0]))
                now = time.monotonic()
                self._prune(now)
            self._times.append(now)
