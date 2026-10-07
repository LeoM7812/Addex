"""Per-host politeness: a request rate limit and a circuit breaker.

Both live in the worker process. With more than one worker process the effective rate
per host multiplies; move them to Redis before scaling out.
"""

import asyncio
import time
from collections.abc import Callable

Clock = Callable[[], float]


class RateLimiter:
    """Spaces request starts at least 1/rate seconds apart."""

    def __init__(self, rate: float, clock: Clock = time.monotonic):
        self.interval = 1.0 / rate
        self.clock = clock
        self._next = 0.0

    async def acquire(self) -> None:
        now = self.clock()
        slot = max(now, self._next)
        self._next = slot + self.interval
        if slot > now:
            await asyncio.sleep(slot - now)


class CircuitBreaker:
    """Stops sending requests to a host after `threshold` consecutive failures.

    Open for `cooldown` seconds, doubling on each re-open up to `max_cooldown`. Once the
    cooldown passes, requests flow again (half-open); the next failure re-opens it
    straight away, the next success closes it and resets the cooldown.
    """

    def __init__(
        self,
        threshold: int = 5,
        cooldown: float = 30.0,
        max_cooldown: float = 1800.0,
        clock: Clock = time.monotonic,
    ):
        self.threshold = threshold
        self.base_cooldown = cooldown
        self.max_cooldown = max_cooldown
        self.clock = clock
        self.failures = 0
        self.cooldown = cooldown
        self.open_until = 0.0

    def wait_time(self) -> float:
        """Seconds until requests may be sent; 0 when closed or half-open."""
        return max(0.0, self.open_until - self.clock())

    def record_success(self) -> None:
        self.failures = 0
        self.cooldown = self.base_cooldown
        self.open_until = 0.0

    def record_failure(self) -> None:
        self.failures += 1
        if self.failures >= self.threshold:
            self.open_until = self.clock() + self.cooldown
            self.cooldown = min(self.cooldown * 2, self.max_cooldown)

    def pause(self, seconds: float) -> None:
        """Explicit back-off requested by the host (429 Retry-After)."""
        self.open_until = max(self.open_until, self.clock() + seconds)
