"""Sliding-window rate limiter for upstream TWSE/TPEX requests.

TWSE bans clients that exceed 3 requests per 5 seconds. Every outbound call in
this app (history fetch, realtime quote) goes through `twse_throttle.acquire()`,
which blocks the calling worker thread until a slot is free.
"""

import threading
import time
from collections import deque

from app.config import get_settings


class SlidingWindowThrottle:
    def __init__(self, max_calls: int, window_seconds: float):
        self._max_calls = max_calls
        self._window = window_seconds
        self._calls: deque[float] = deque()
        self._lock = threading.Lock()

    def acquire(self) -> None:
        while True:
            with self._lock:
                now = time.monotonic()
                while self._calls and now - self._calls[0] >= self._window:
                    self._calls.popleft()

                if len(self._calls) < self._max_calls:
                    self._calls.append(now)
                    return

                sleep_for = self._window - (now - self._calls[0])

            # Sleep outside the lock so other threads can drain the window too.
            time.sleep(max(sleep_for, 0.01))


_settings = get_settings()
twse_throttle = SlidingWindowThrottle(
    max_calls=_settings.throttle_max_calls,
    window_seconds=_settings.throttle_window_seconds,
)
