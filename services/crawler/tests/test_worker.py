import httpx
from redis.asyncio import Redis

from addex_core.models import CheckStatus
from addex_core.testing import run
from addex_crawler import queue
from addex_crawler.queue import Job
from addex_crawler.worker import WorkerConfig, run_worker

CONFIG = WorkerConfig(rate=1000, concurrency=2, timeout=1, idle_poll=0.1)


def _job(title_id, host="a.example"):
    addon_id = {"a.example": 1, "b.example": 2}[host]
    pid = f"tt{title_id}"
    return Job(addon_id, title_id, pid, f"https://{host}/stream/movie/{pid}.json", host)


async def _crawl(redis_url, jobs, handler, max_queue=100):
    redis = Redis.from_url(redis_url, decode_responses=True)
    recorded = []

    async def sink(job, result):
        recorded.append((job.host, job.title_id, result.status, result.stream_count))

    try:
        pushed = await queue.enqueue(redis, jobs, max_queue=max_queue)
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            await run_worker(redis, client, sink, CONFIG, until_empty=True)
        locks = await redis.keys("addex:lock:*")
        return pushed, sorted(recorded), locks
    finally:
        await redis.aclose()


def test_enqueue_dedupes_and_caps(redis_url):
    async def main():
        redis = Redis.from_url(redis_url, decode_responses=True)
        try:
            jobs = [_job(1), _job(2), _job(3), _job(1, host="b.example")]
            assert await queue.enqueue(redis, jobs, max_queue=2) == 3  # a.example capped at 2
            assert await queue.enqueue(redis, jobs, max_queue=10) == 1  # only title 3 is new
            assert await redis.llen(queue.queue_key("a.example")) == 3
            assert await redis.smembers(queue.HOSTS_KEY) == {"a.example", "b.example"}
            # FIFO: first enqueued is first popped.
            assert (await queue.pop(redis, "a.example", 0.1)).title_id == 1
        finally:
            await redis.aclose()

    run(main())


def test_worker_records_results_across_hosts(redis_url):
    def handler(request: httpx.Request):
        if request.url.host == "b.example":
            return httpx.Response(200, json={"streams": []})
        return httpx.Response(200, json={"streams": [{"infoHash": "x"}]})

    jobs = [_job(1), _job(2), _job(1, host="b.example")]
    pushed, recorded, locks = run(_crawl(redis_url, jobs, handler))
    assert pushed == 3
    assert recorded == [
        ("a.example", 1, CheckStatus.OK, 1),
        ("a.example", 2, CheckStatus.OK, 1),
        ("b.example", 1, CheckStatus.EMPTY, 0),
    ]
    assert locks == []  # released, so the pairs can be scheduled again


def test_worker_requeues_after_429(redis_url):
    calls = []

    def handler(request):
        calls.append(request.url.path)
        if len(calls) == 1:
            return httpx.Response(429, headers={"Retry-After": "0.2"})
        return httpx.Response(200, json={"streams": [{"url": "u"}]})

    _, recorded, _ = run(_crawl(redis_url, [_job(1)], handler))
    assert recorded == [("a.example", 1, CheckStatus.OK, 1)]  # the 429 itself isn't recorded
    assert len(calls) == 2
