"""Sliding-window throttle: at most N calls inside any `window_seconds` span.

TWSE bans clients that exceed 3 requests / 5 seconds. The clock is mocked so
this does not actually wait out the window.
"""

import time

from app.throttle import SlidingWindowThrottle


def test_acquire_sleeps_once_the_window_is_full(monkeypatch):
    clock = [100.0]
    slept: list[float] = []

    monkeypatch.setattr(time, "monotonic", lambda: clock[0])

    def fake_sleep(seconds: float) -> None:
        slept.append(seconds)
        clock[0] += seconds

    monkeypatch.setattr(time, "sleep", fake_sleep)

    throttle = SlidingWindowThrottle(max_calls=2, window_seconds=5.0)
    throttle.acquire()  # t=100
    clock[0] = 101.0
    throttle.acquire()  # t=101, window full
    throttle.acquire()  # must wait until the first call ages out (t=105)

    assert slept == [4.0]
    assert clock[0] == 105.0
    assert list(throttle._calls) == [101.0, 105.0]


def test_acquire_does_not_sleep_when_a_slot_has_aged_out(monkeypatch):
    clock = [100.0]
    monkeypatch.setattr(time, "monotonic", lambda: clock[0])
    monkeypatch.setattr(time, "sleep", lambda _s: (_ for _ in ()).throw(AssertionError("should not sleep")))

    throttle = SlidingWindowThrottle(max_calls=2, window_seconds=5.0)
    throttle.acquire()
    throttle.acquire()
    clock[0] = 105.0  # both calls are now exactly `window` old
    throttle.acquire()
    assert len(throttle._calls) == 1
