"""Turns demand notes from the API into indexed titles and priority probes."""

import asyncio
import logging
import time
from dataclasses import dataclass, field, replace
from datetime import UTC, datetime

import httpx
from redis.asyncio import Redis
from redis.exceptions import RedisError
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from addex_core import animelists, cinemeta, kitsu
from addex_core.demand import DISCOVERABLE, UNKNOWN_TTL, DemandRequest, pop_demand, seen_key, \
    unknown_key
from addex_core.ids import IdScheme
from addex_core.linking import link_anime
from addex_core.models import Title, TitleId
from addex_core.titles import TitleSeed, upsert_titles
from addex_crawler import queue
from addex_crawler.queue import Job
from addex_crawler.scheduler import due_jobs

log = logging.getLogger("addex.crawler")


class AnimeMapping:
    """anime-lists mappings (Kitsu -> IMDb, MAL -> Kitsu), downloaded on first use and
    refreshed daily."""

    def __init__(self, max_age: float = 24 * 3600):
        self.max_age = max_age
        self._data = animelists.AnimeLists({}, {})
        self._fetched_at = float("-inf")

    async def get(self, client: httpx.AsyncClient) -> animelists.AnimeLists:
        if time.monotonic() - self._fetched_at > self.max_age:
            self._data = await animelists.fetch(client)
            self._fetched_at = time.monotonic()
        return self._data


class DiscoveryBudget:
    """At most `per_minute` new titles a minute, so a flood of made-up IDs can't turn
    into a flood of metadata lookups and probes."""

    def __init__(self, per_minute: int = 30):
        self.per_minute = per_minute
        self._window = 0
        self._used = 0

    def allow(self) -> bool:
        window = int(time.monotonic() // 60)
        if window != self._window:
            self._window, self._used = window, 0
        if self._used >= self.per_minute:
            return False
        self._used += 1
        return True


@dataclass
class DemandOutcome:
    status: str  # "known", "created", "unknown", "unsupported", "over_budget"
    jobs: list[Job] = field(default_factory=list)


async def _title_id(session: AsyncSession, scheme: IdScheme, value: str) -> int | None:
    return await session.scalar(
        select(TitleId.title_id).where(TitleId.scheme == scheme, TitleId.value == value)
    )


async def _discover(
    client: httpx.AsyncClient, req: DemandRequest, mapping: AnimeMapping
) -> TitleSeed | None:
    if req.scheme is IdScheme.KITSU:
        return await kitsu.fetch_anime(client, req.value)
    if req.scheme is IdScheme.MAL:
        # MAL has no API Addex uses; go through the anime's Kitsu entry, which also brings
        # its other IDs and its IMDb parent.
        kitsu_id = (await mapping.get(client)).mal_to_kitsu.get(req.value)
        seed = await kitsu.fetch_anime(client, kitsu_id) if kitsu_id else None
        if seed is not None and IdScheme.MAL not in seed.ids:
            # Kitsu doesn't list this MAL ID itself: attach it so the next request finds it.
            seed = replace(seed, ids={**seed.ids, IdScheme.MAL: req.value})
        return seed
    first = req.type if req.type in ("movie", "series") else "series"
    return await cinemeta.fetch_meta(
        client, req.value, (first, "movie" if first == "series" else "series")
    )


async def handle_demand(
    session: AsyncSession,
    redis: Redis,
    client: httpx.AsyncClient,
    req: DemandRequest,
    mapping: AnimeMapping,
    budget: DiscoveryBudget,
) -> DemandOutcome:
    """Make sure the requested title exists and return the due probes for its whole
    group. Jobs are returned rather than enqueued so the caller can commit first: a
    worker must never see a job for a title that isn't in the database yet."""
    status = "known"
    title_id = await _title_id(session, req.scheme, req.value)
    if title_id is None:
        if req.scheme not in DISCOVERABLE:
            return DemandOutcome("unsupported")
        if await redis.exists(unknown_key(req.scheme, req.value)):
            return DemandOutcome("unknown")
        if not budget.allow():
            # Let the next request for this title try again.
            await redis.delete(seen_key(req.scheme, req.value))
            return DemandOutcome("over_budget")
        seed = await _discover(client, req, mapping)
        if seed is None:
            await redis.set(unknown_key(req.scheme, req.value), 1, ex=UNKNOWN_TTL)
            return DemandOutcome("unknown")
        await upsert_titles(session, [seed])
        if IdScheme.KITSU in seed.ids:
            await link_anime(
                session, (await mapping.get(client)).kitsu_to_imdb,
                lambda wanted: cinemeta.fetch_metas(client, wanted),
            )
        title_id = await _title_id(session, req.scheme, req.value)
        if title_id is None and IdScheme.KITSU in seed.ids:
            # The Kitsu entry already lists a different MAL ID, which wins (one per title).
            title_id = await _title_id(session, IdScheme.KITSU, seed.ids[IdScheme.KITSU])
            log.warning("demand %s:%s: Kitsu %s lists another MAL ID; using the Kitsu entry",
                        req.scheme.value, req.value, seed.ids[IdScheme.KITSU])
        status = "created"

    title = await session.get(Title, title_id)
    root_id = title.parent_id or title.id
    group = [root_id, *await session.scalars(select(Title.id).where(Title.parent_id == root_id))]
    jobs = await due_jobs(session, datetime.now(UTC), title_ids=group)
    return DemandOutcome(status, jobs)


async def demand_loop(
    redis: Redis,
    sessionmaker: async_sessionmaker,
    client: httpx.AsyncClient,
    max_queue: int,
    budget: DiscoveryBudget | None = None,
    poll: float = 5.0,
) -> None:
    mapping, budget = AnimeMapping(), budget or DiscoveryBudget()
    while True:
        try:
            req = await pop_demand(redis, poll)
        except RedisError:
            log.exception("demand: redis pop failed, retrying")
            await asyncio.sleep(1.0)
            continue
        if req is None:
            continue
        try:
            async with sessionmaker.begin() as session:
                outcome = await handle_demand(session, redis, client, req, mapping, budget)
            pushed = await queue.enqueue(redis, outcome.jobs, max_queue, priority=True)
            log.info("demand %s:%s -> %s, %d probes queued",
                     req.scheme.value, req.value, outcome.status, pushed)
        except Exception:
            log.exception("demand %s:%s failed", req.scheme.value, req.value)
