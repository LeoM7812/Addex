"""Consumes the per-host queues. One HostWorker per host, each with its own rate limiter,
circuit breaker and concurrency cap."""

import asyncio
import logging
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import UTC, datetime

import httpx
from redis.asyncio import Redis
from redis.exceptions import RedisError
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import async_sessionmaker

from addex_crawler import queue
from addex_crawler.limits import CircuitBreaker, RateLimiter
from addex_crawler.probe import ProbeResult, probe
from addex_crawler.queue import Job
from addex_crawler.results import record_result

log = logging.getLogger("addex.crawler")

Sink = Callable[[Job, ProbeResult], Awaitable[None]]


@dataclass(frozen=True)
class WorkerConfig:
    rate: float = 1.0  # requests per second, per host
    concurrency: int = 2  # in-flight requests, per host
    timeout: float = 10.0  # per request
    idle_poll: float = 5.0  # BRPOP timeout
    default_retry_after: float = 60.0  # 429 without a usable Retry-After


def db_sink(sessionmaker: async_sessionmaker) -> Sink:
    async def sink(job: Job, result: ProbeResult) -> None:
        try:
            async with sessionmaker.begin() as session:
                await record_result(session, job, result, datetime.now(UTC))
        except IntegrityError:
            log.warning("addon %s or title %s no longer exists, dropping result",
                        job.addon_id, job.title_id)

    return sink


class HostWorker:
    def __init__(
        self, host: str, redis: Redis, client: httpx.AsyncClient, sink: Sink,
        config: WorkerConfig,
    ):
        self.host = host
        self.redis = redis
        self.client = client
        self.sink = sink
        self.config = config
        self.limiter = RateLimiter(config.rate)
        self.breaker = CircuitBreaker()
        self.inflight = 0

    async def run(self, until_empty: bool = False) -> None:
        """Process jobs forever, or until the queue is empty and nothing is in flight."""
        sem = asyncio.Semaphore(self.config.concurrency)
        async with asyncio.TaskGroup() as tg:
            while True:
                if (wait := self.breaker.wait_time()) > 0:
                    log.warning("%s: circuit open, pausing %.0fs", self.host, wait)
                    await asyncio.sleep(wait)
                    continue
                await sem.acquire()
                try:
                    job = await queue.pop(self.redis, self.host, self.config.idle_poll)
                except RedisError:
                    log.exception("%s: redis pop failed, retrying", self.host)
                    sem.release()
                    await asyncio.sleep(1.0)
                    continue
                if job is None:
                    sem.release()
                    if until_empty and self.inflight == 0:
                        return
                    continue
                await self.limiter.acquire()
                self.inflight += 1
                tg.create_task(self._handle(job, sem))

    async def _handle(self, job: Job, sem: asyncio.Semaphore) -> None:
        try:
            result = await probe(self.client, job.url, self.config.timeout)
            if result.rate_limited:
                pause = result.retry_after or self.config.default_retry_after
                self.limiter.slow_down()
                log.warning("%s: 429, pausing %.0fs, then 1 request every %.1fs",
                            self.host, pause, self.limiter.interval)
                self.breaker.pause(pause)
                await queue.requeue(self.redis, job)
                return
            if result.host_failure:
                self.breaker.record_failure()
            else:
                self.breaker.record_success()
                self.limiter.record_ok()
            await self.sink(job, result)
            await queue.release(self.redis, job)
            log.info("%s %s %s streams=%s %sms", self.host, job.probe_id,
                     result.status.value, result.stream_count, result.latency_ms)
        except Exception:
            # The pair's lock expires on its own and the scheduler will retry it.
            log.exception("%s: job failed: %s", self.host, job)
        finally:
            self.inflight -= 1
            sem.release()


async def run_worker(
    redis: Redis, client: httpx.AsyncClient, sink: Sink, config: WorkerConfig,
    until_empty: bool = False, discover_every: float = 10.0,
) -> None:
    """Start a HostWorker for every host with a queue, picking up new hosts as they
    appear. With `until_empty`, returns once every host's queue is drained."""
    workers: dict[str, asyncio.Task] = {}
    async with asyncio.TaskGroup() as tg:
        while True:
            for host in sorted(await redis.smembers(queue.HOSTS_KEY)):
                if host not in workers:
                    worker = HostWorker(host, redis, client, sink, config)
                    workers[host] = tg.create_task(worker.run(until_empty))
            if until_empty and all(t.done() for t in workers.values()):
                return
            await asyncio.sleep(1.0 if until_empty else discover_every)
