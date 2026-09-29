"""Async token-bucket rate limiter (shared by both clients)."""

from __future__ import annotations

import asyncio
import time


class RateLimiter:
    """Token bucket limiting bytes/second.

    A single limiter can be shared by multiple coroutines (e.g. upload and
    download paths) to cap *combined* throughput.
    """

    def __init__(self, rate_bytes_per_sec: float, capacity: float | None = None):
        self.rate = float(rate_bytes_per_sec)
        # Burst size: default to 1 second worth of tokens.
        self.capacity = float(capacity) if capacity else self.rate
        self.tokens = self.capacity
        self._lock = asyncio.Lock()
        self._ts = time.monotonic()

    async def consume(self, n: int) -> None:
        """Block until *n* bytes worth of tokens are available, then take them."""
        if self.rate <= 0:
            return
        # A single chunk may exceed the burst capacity; never wait forever.
        n = min(n, self.capacity)
        while True:
            async with self._lock:
                now = time.monotonic()
                self.tokens = min(self.capacity, self.tokens + (now - self._ts) * self.rate)
                self._ts = now
                if self.tokens >= n:
                    self.tokens -= n
                    return
                deficit = (n - self.tokens) / self.rate
            # Release the lock while waiting so token math stays cheap.
            await asyncio.sleep(deficit)


def make_rate_limiter(mbps: float | None) -> RateLimiter | None:
    """Build a RateLimiter from a Mbps value, or None for unlimited.

    Bandwidth uses decimal megabits: 20 Mbps -> 20_000_000 / 8 bytes/s.
    """
    if not mbps or mbps <= 0:
        return None
    return RateLimiter(rate_bytes_per_sec=mbps * 1_000_000 / 8)
