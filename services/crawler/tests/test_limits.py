import asyncio

from addex_crawler import limits
from addex_crawler.limits import CircuitBreaker, RateLimiter


class FakeClock:
    def __init__(self):
        self.now = 1000.0

    def __call__(self) -> float:
        return self.now


def test_breaker_opens_after_threshold_and_doubles_cooldown():
    clock = FakeClock()
    b = CircuitBreaker(threshold=3, cooldown=10, max_cooldown=25, clock=clock)
    b.record_failure()
    b.record_failure()
    assert b.wait_time() == 0
    b.record_failure()
    assert b.wait_time() == 10

    clock.now += 10  # half-open: one more failure re-opens with double cooldown
    assert b.wait_time() == 0
    b.record_failure()
    assert b.wait_time() == 20

    clock.now += 20
    b.record_failure()
    assert b.wait_time() == 25  # capped

    clock.now += 25
    b.record_success()
    assert (b.failures, b.cooldown, b.wait_time()) == (0, 10, 0)


def test_breaker_pause_only_extends():
    clock = FakeClock()
    b = CircuitBreaker(clock=clock)
    b.pause(60)
    b.pause(5)
    assert b.wait_time() == 60


def test_rate_limiter_spaces_requests(monkeypatch):
    clock = FakeClock()
    slept = []

    async def fake_sleep(seconds):
        slept.append(seconds)

    monkeypatch.setattr(limits.asyncio, "sleep", fake_sleep)
    limiter = RateLimiter(rate=2, clock=clock)

    async def main():
        for _ in range(3):
            await limiter.acquire()
        clock.now += 5  # idle: next request goes straight through
        await limiter.acquire()

    asyncio.run(main())
    assert slept == [0.5, 1.0]
