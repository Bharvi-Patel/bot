"""Limits for the HTTP service (bot/server.py): per-visitor and per-store rate limits, and a cap on chats running at once.

All state is in this process's memory, so run the service as ONE process until these are moved to a shared store such as
Redis; with several workers each one would count on its own and the real limits would be several times higher.
"""
from __future__ import annotations

import threading
import time
from collections import deque
from typing import Callable


class SlidingWindowLimiter:
    """At most `limit` events per `window` seconds for each key. allow() returns (allowed, seconds_to_wait)."""

    def __init__(self, limit: int, window: float = 60.0, max_keys: int = 50_000, clock: Callable[[], float] = time.monotonic):
        if limit < 1 or window <= 0 or max_keys < 1:
            raise ValueError("limit, window and max_keys must be positive")
        self.limit, self.window, self.max_keys, self._clock = limit, window, max_keys, clock
        self._hits: dict[str, deque[float]] = {}
        self._lock = threading.Lock()

    def _purge(self, now: float) -> None:
        for key in [k for k, q in self._hits.items() if not q or q[-1] <= now - self.window]:
            del self._hits[key]

    def allow(self, key: str) -> tuple[bool, int]:
        with self._lock:
            now = self._clock()
            hits = self._hits.get(key)
            if hits is None:
                if len(self._hits) >= self.max_keys:
                    self._purge(now)
                    if len(self._hits) >= self.max_keys:          # table full of live keys: refuse rather than grow
                        return False, int(self.window)
                hits = self._hits[key] = deque()
            while hits and hits[0] <= now - self.window:
                hits.popleft()
            if len(hits) >= self.limit:
                return False, max(1, int(hits[0] + self.window - now) + 1)
            hits.append(now)
            return True, 0


class ConcurrencyGate:
    """At most `size` chats at the same time. try_enter() never waits; the caller answers 503 when it is full."""

    def __init__(self, size: int):
        if size < 1:
            raise ValueError("size must be positive")
        self._sem = threading.BoundedSemaphore(size)

    def try_enter(self) -> bool:
        return self._sem.acquire(blocking=False)

    def leave(self) -> None:
        self._sem.release()
