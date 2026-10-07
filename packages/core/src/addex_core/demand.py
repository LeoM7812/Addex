"""On-demand checks: titles people open in Stremio get indexed and probed first.

The API notes every stream request; the crawler picks the notes up, creates the title if
Addex doesn't know it yet, and enqueues its due probes ahead of the scheduled backlog.

Keys:
  addex:demand                       list of JSON requests (LPUSH / BRPOP)
  addex:demand:seen:{scheme}:{value} set for DEDUPE_TTL after a request is queued, so a
                                     title opened many times only queues once
  addex:demand:unknown:{scheme}:{value} set when no metadata source knows the ID
"""

import json
from dataclasses import asdict, dataclass

from redis.asyncio import Redis

from addex_core.ids import IdScheme

QUEUE_KEY = "addex:demand"
DEDUPE_TTL = 10 * 60
UNKNOWN_TTL = 24 * 3600
# IDs the crawler can create titles from.
DISCOVERABLE = (IdScheme.IMDB, IdScheme.KITSU)


@dataclass(frozen=True)
class DemandRequest:
    scheme: IdScheme
    value: str
    type: str  # Stremio type the client asked for

    def dumps(self) -> str:
        return json.dumps(asdict(self))

    @classmethod
    def loads(cls, raw: str | bytes) -> "DemandRequest":
        data = json.loads(raw)
        return cls(scheme=IdScheme(data["scheme"]), value=data["value"], type=data["type"])


def seen_key(scheme: IdScheme, value: str) -> str:
    return f"addex:demand:seen:{scheme.value}:{value}"


def unknown_key(scheme: IdScheme, value: str) -> str:
    return f"addex:demand:unknown:{scheme.value}:{value}"


async def note_demand(redis: Redis, request: DemandRequest) -> bool:
    """Queue a check for this title unless one was queued recently. Returns whether it
    was queued."""
    if not await redis.set(seen_key(request.scheme, request.value), 1, nx=True, ex=DEDUPE_TTL):
        return False
    await redis.lpush(QUEUE_KEY, request.dumps())
    return True


async def pop_demand(redis: Redis, timeout: float) -> DemandRequest | None:
    item = await redis.brpop([QUEUE_KEY], timeout=timeout)
    return DemandRequest.loads(item[1]) if item else None
