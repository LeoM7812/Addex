import logging
from collections.abc import AsyncIterator, Awaitable, Callable

from fastapi import Request
from redis.exceptions import RedisError
from sqlalchemy.ext.asyncio import AsyncSession

from addex_core.demand import DemandRequest, note_demand

log = logging.getLogger("addex.api")

NoteDemand = Callable[[DemandRequest], Awaitable[None]]


async def get_session(request: Request) -> AsyncIterator[AsyncSession]:
    async with request.app.state.sessionmaker() as session:
        yield session


def get_note_demand(request: Request) -> NoteDemand:
    """Records that someone asked for a title, so the crawler checks it soon. Best
    effort: without Redis the API still answers from what it already knows."""
    redis = getattr(request.app.state, "redis", None)

    async def note(req: DemandRequest) -> None:
        if redis is None:
            return
        try:
            await note_demand(redis, req)
        except RedisError as e:
            log.warning("could not note demand for %s:%s: %s", req.scheme.value, req.value, e)

    return note
