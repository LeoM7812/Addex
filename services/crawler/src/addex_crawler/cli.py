"""addex-crawler: schedule and run stream availability probes.

  addex-crawler schedule [--once]     enqueue due (addon, title) pairs into Redis
  addex-crawler work [--until-empty]  consume the queues and record results
  addex-crawler run                   both, in one process
  addex-crawler status                queue sizes and results per addon
"""

import argparse
import asyncio
import logging
import os
import sys
from datetime import UTC, datetime

import httpx
from redis.asyncio import Redis
from sqlalchemy import func, select

from addex_core.db import make_engine, make_sessionmaker
from addex_core.models import Addon, Availability, CheckStatus
from addex_crawler import queue
from addex_crawler.scheduler import due_jobs
from addex_crawler.worker import WorkerConfig, db_sink, run_worker

log = logging.getLogger("addex.crawler")
ANSWERED = (CheckStatus.OK, CheckStatus.EMPTY)
USER_AGENT = "addex/0.1 (stream availability index; no streams are stored)"


def _redis() -> Redis:
    # redis-py's default 5 s socket timeout would race with the workers' blocking BRPOP.
    return Redis.from_url(
        os.environ.get("ADDEX_REDIS_URL", "redis://localhost:6379/0"),
        decode_responses=True,
        socket_timeout=30,
    )


async def schedule_loop(redis: Redis, sessionmaker, args: argparse.Namespace) -> None:
    while True:
        async with sessionmaker() as session:
            jobs = await due_jobs(session, datetime.now(UTC), top_titles=args.top)
        pushed = await queue.enqueue(redis, jobs, max_queue=args.max_queue)
        log.info("scheduler: %d pairs due, %d enqueued", len(jobs), pushed)
        if args.once:
            return
        await asyncio.sleep(args.interval)


def _worker_config(args: argparse.Namespace) -> WorkerConfig:
    return WorkerConfig(rate=args.rate, concurrency=args.concurrency, timeout=args.timeout)


async def cmd_schedule(args: argparse.Namespace) -> int:
    engine, redis = make_engine(), _redis()
    try:
        await schedule_loop(redis, make_sessionmaker(engine), args)
    finally:
        await redis.aclose()
        await engine.dispose()
    return 0


async def cmd_work(args: argparse.Namespace, schedule: bool = False) -> int:
    engine, redis = make_engine(), _redis()
    sessionmaker = make_sessionmaker(engine)
    limits = httpx.Limits(max_connections=200, max_keepalive_connections=50)
    try:
        async with httpx.AsyncClient(headers={"User-Agent": USER_AGENT}, limits=limits) as client:
            worker = run_worker(redis, client, db_sink(sessionmaker), _worker_config(args),
                                until_empty=getattr(args, "until_empty", False))
            if schedule:
                async with asyncio.TaskGroup() as tg:
                    tg.create_task(schedule_loop(redis, sessionmaker, args))
                    tg.create_task(worker)
            else:
                await worker
    finally:
        await redis.aclose()
        await engine.dispose()
    return 0


async def cmd_run(args: argparse.Namespace) -> int:
    return await cmd_work(args, schedule=True)


async def cmd_status(args: argparse.Namespace) -> int:
    engine, redis = make_engine(), _redis()
    try:
        print("queues:")
        for host in sorted(await redis.smembers(queue.HOSTS_KEY)):
            print(f"  {host:40} {await redis.llen(queue.queue_key(host)):>6}")
        q = (
            select(
                Addon.name,
                func.count().label("checked"),
                func.count().filter(Availability.has_streams.is_(True)).label("with_streams"),
                func.count().filter(Availability.status.not_in(ANSWERED)).label("failing"),
                func.percentile_cont(0.5).within_group(Availability.latency_ms).label("p50"),
            )
            .join(Availability)
            .group_by(Addon.name)
            .order_by(Addon.name)
        )
        async with make_sessionmaker(engine)() as session:
            rows = (await session.execute(q)).all()
        print(f"\n{'addon':32} {'checked':>8} {'streams':>8} {'failing':>8} {'p50 ms':>8}")
        for name, checked, with_streams, failing, p50 in rows:
            print(f"{name[:32]:32} {checked:>8} {with_streams:>8} {failing:>8} "
                  f"{(round(p50) if p50 is not None else '-'):>8}")
    finally:
        await redis.aclose()
        await engine.dispose()
    return 0


def main() -> None:
    parser = argparse.ArgumentParser(prog="addex-crawler", description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(required=True, metavar="command")

    def scheduling(p: argparse.ArgumentParser) -> None:
        p.add_argument("--interval", type=float, default=60, help="seconds between passes")
        p.add_argument("--max-queue", type=int, default=500, help="max queued jobs per host")
        p.add_argument("--top", type=int, help="only the N most popular titles")

    def working(p: argparse.ArgumentParser) -> None:
        p.add_argument("--rate", type=float, default=1.0, help="requests/s per host")
        p.add_argument("--concurrency", type=int, default=2, help="in-flight requests per host")
        p.add_argument("--timeout", type=float, default=10.0, help="seconds per request")

    p = sub.add_parser("schedule", help="enqueue due pairs")
    scheduling(p)
    p.add_argument("--once", action="store_true", help="one pass, then exit")
    p.set_defaults(func=cmd_schedule)

    p = sub.add_parser("work", help="consume queues and record results")
    working(p)
    p.add_argument("--until-empty", action="store_true", help="exit once queues are drained")
    p.set_defaults(func=cmd_work)

    p = sub.add_parser("run", help="schedule + work in one process")
    scheduling(p)
    working(p)
    p.set_defaults(func=cmd_run, once=False)

    p = sub.add_parser("status", help="queue sizes and results per addon")
    p.set_defaults(func=cmd_status)

    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    logging.getLogger("httpx").setLevel(logging.WARNING)
    # psycopg's async mode can't run on Windows' default Proactor event loop.
    loop_factory = asyncio.SelectorEventLoop if sys.platform == "win32" else None
    try:
        raise SystemExit(asyncio.run(args.func(args), loop_factory=loop_factory))
    except KeyboardInterrupt:
        raise SystemExit(130)


if __name__ == "__main__":
    main()
