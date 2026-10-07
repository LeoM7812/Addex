"""Redis job queue: one list per host, so a slow or broken host never blocks the others.

Keys:
  addex:hosts               set of hosts that have (or had) a queue
  addex:jobs:{host}         list of JSON jobs; LPUSH to enqueue, BRPOP to consume
  addex:lock:{addon}:{title} set while a job for that pair is queued or running, so the
                            scheduler does not enqueue it twice. Expires on its own if a
                            worker dies mid-job.
"""

import json
from dataclasses import asdict, dataclass

from redis.asyncio import Redis

HOSTS_KEY = "addex:hosts"
LOCK_TTL = 6 * 3600


def queue_key(host: str) -> str:
    return f"addex:jobs:{host}"


def lock_key(addon_id: int, title_id: int) -> str:
    return f"addex:lock:{addon_id}:{title_id}"


@dataclass(frozen=True)
class Job:
    addon_id: int
    title_id: int
    probe_id: str
    url: str
    host: str
    # Snapshot at scheduling time; drives the refresh TTL when the result is recorded.
    popularity_rank: int | None = None

    def dumps(self) -> str:
        return json.dumps(asdict(self))

    @classmethod
    def loads(cls, raw: str | bytes) -> "Job":
        return cls(**json.loads(raw))


async def enqueue(redis: Redis, jobs: list[Job], max_queue: int) -> int:
    """Push jobs whose pair isn't already queued, keeping each host's queue at or below
    `max_queue`. Jobs should come ordered by priority. Returns how many were pushed."""
    room: dict[str, int] = {}
    pushed = 0
    for job in jobs:
        if job.host not in room:
            room[job.host] = max_queue - await redis.llen(queue_key(job.host))
            await redis.sadd(HOSTS_KEY, job.host)
        if room[job.host] <= 0:
            continue
        if not await redis.set(lock_key(job.addon_id, job.title_id), 1, nx=True, ex=LOCK_TTL):
            continue
        await redis.lpush(queue_key(job.host), job.dumps())
        room[job.host] -= 1
        pushed += 1
    return pushed


async def pop(redis: Redis, host: str, timeout: float) -> Job | None:
    item = await redis.brpop([queue_key(host)], timeout=timeout)
    return Job.loads(item[1]) if item else None


async def requeue(redis: Redis, job: Job) -> None:
    """Put a job back at the consuming end, so it is the next one taken."""
    await redis.rpush(queue_key(job.host), job.dumps())


async def release(redis: Redis, job: Job) -> None:
    await redis.delete(lock_key(job.addon_id, job.title_id))
