import threading

import pytest

from bot.service_limits import ConcurrencyGate, SlidingWindowLimiter


class Clock:
    def __init__(self):
        self.now = 1000.0

    def __call__(self):
        return self.now


def test_the_window_slides():
    clock = Clock()
    lim = SlidingWindowLimiter(2, window=60, clock=clock)
    assert lim.allow("a") == (True, 0) and lim.allow("a") == (True, 0)
    ok, wait = lim.allow("a")
    assert not ok and wait == 61
    clock.now += 30
    assert lim.allow("a")[0] is False
    clock.now += 31
    assert lim.allow("a") == (True, 0)


def test_keys_are_counted_separately():
    lim = SlidingWindowLimiter(1, clock=Clock())
    assert lim.allow("a")[0] and lim.allow("b")[0] and not lim.allow("a")[0]


def test_the_table_cannot_grow_without_limit():
    clock = Clock()
    lim = SlidingWindowLimiter(5, window=60, max_keys=3, clock=clock)
    assert all(lim.allow(k)[0] for k in "abc")
    assert lim.allow("d") == (False, 60)                 # full of live keys: refused, nothing added
    clock.now += 61                                       # all expired: purged, room again
    assert lim.allow("d")[0] and len(lim._hits) == 1


def test_denied_attempts_are_not_counted():
    clock = Clock()
    lim = SlidingWindowLimiter(1, window=60, clock=clock)
    lim.allow("a")
    for _ in range(50):
        lim.allow("a")
    clock.now += 61
    assert lim.allow("a")[0]                              # only the first hit counted, so it is free again after the window


@pytest.mark.parametrize("args", [(0,), (1, 0), (1, 60, 0)])
def test_bad_limiter_settings(args):
    with pytest.raises(ValueError):
        SlidingWindowLimiter(*args)


def test_limiter_is_safe_across_threads():
    lim = SlidingWindowLimiter(100, window=60)
    results = []
    threads = [threading.Thread(target=lambda: results.append(lim.allow("k")[0])) for _ in range(300)]
    [t.start() for t in threads]
    [t.join() for t in threads]
    assert results.count(True) == 100


def test_gate_never_waits_and_gives_slots_back():
    gate = ConcurrencyGate(2)
    assert gate.try_enter() and gate.try_enter() and not gate.try_enter()
    gate.leave()
    assert gate.try_enter()


def test_gate_refuses_a_size_below_one():
    with pytest.raises(ValueError):
        ConcurrencyGate(0)
