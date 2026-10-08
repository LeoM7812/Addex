"""Writing probe results to `availability`, and the adaptive refresh policy."""

from datetime import datetime, timedelta

from sqlalchemy import update
from sqlalchemy.ext.asyncio import AsyncSession

from addex_core.models import Addon, Availability, CheckStatus
from addex_crawler.probe import ProbeResult
from addex_crawler.queue import Job

# Popularity rank ceiling -> how long a definitive answer stays fresh.
REFRESH_TTL = (
    (50, timedelta(hours=6)),
    (200, timedelta(hours=12)),
    (1000, timedelta(days=1)),
)
UNRANKED_TTL = timedelta(days=3)
FAILURE_BACKOFF = timedelta(minutes=15)


def next_check_delay(rank: int | None, answered: bool, consecutive_failures: int) -> timedelta:
    ttl = next((t for ceiling, t in REFRESH_TTL if rank is not None and rank <= ceiling),
               UNRANKED_TTL)
    if answered:
        return ttl
    # 15 min, 30 min, 1 h, ... never longer than a normal refresh.
    return min(FAILURE_BACKOFF * 2 ** (consecutive_failures - 1), ttl)


async def record_result(
    session: AsyncSession, job: Job, result: ProbeResult, now: datetime
) -> Availability:
    row = await session.get(Availability, (job.addon_id, job.title_id), with_for_update=True)
    if row is None:
        row = Availability(addon_id=job.addon_id, title_id=job.title_id, consecutive_failures=0)
        session.add(row)

    row.probe_id = job.probe_id
    row.status = result.status
    row.http_status = result.http_status
    row.latency_ms = result.latency_ms
    row.last_checked = now
    if result.answered:
        row.has_streams = result.status == CheckStatus.OK
        row.stream_count = result.stream_count
        row.last_answered = now
        row.consecutive_failures = 0
    else:
        row.consecutive_failures += 1
    row.next_check_at = now + next_check_delay(
        job.popularity_rank, result.answered, row.consecutive_failures
    )
    if result.torrent:
        await session.execute(
            update(Addon)
            .where(Addon.id == job.addon_id, Addon.p2p_observed.is_(False))
            .values(p2p_observed=True)
        )
    await session.flush()
    return row
