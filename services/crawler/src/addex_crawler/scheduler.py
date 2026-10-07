"""Decides which (addon, title) pairs are due and which Stremio ID to probe them with."""

from collections.abc import Collection, Iterable
from datetime import datetime
from urllib.parse import urlsplit

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from addex_core.ids import IdScheme, probe_id
from addex_core.manifest import StreamScope, stream_url
from addex_core.models import Addon, AddonStatus, Availability, Title
from addex_crawler.queue import Job

# Which external ID to try first. Anime addons key on Kitsu; IMDb is a fallback because
# its season numbering often disagrees with how anime is released.
ANIME_ID_ORDER = (IdScheme.KITSU, IdScheme.MAL, IdScheme.ANILIST, IdScheme.IMDB, IdScheme.TMDB)
ID_ORDER = (IdScheme.IMDB, IdScheme.TMDB, IdScheme.KITSU, IdScheme.MAL, IdScheme.ANILIST)


def pick_probe_id(
    scopes: Iterable[StreamScope], title_type: str, ids: dict[IdScheme, str], is_anime: bool
) -> str | None:
    """The first of the title's IDs, in preference order, that the addon accepts."""
    scopes = list(scopes)
    for scheme in ANIME_ID_ORDER if is_anime else ID_ORDER:
        if scheme not in ids:
            continue
        pid = probe_id(scheme, ids[scheme], title_type)
        if any(s.accepts(title_type, pid) for s in scopes):
            return pid
    return None


async def due_jobs(
    session: AsyncSession,
    now: datetime,
    top_titles: int | None = None,
    title_ids: Collection[int] | None = None,
) -> list[Job]:
    """Jobs for every active addon x title pair that was never checked or is past its
    `next_check_at`, most popular titles first. `title_ids` restricts it to those titles.

    Loads everything into memory: fine for the MVP (tens of addons, thousands of
    titles); push the matching into SQL when that stops being true.
    """
    addons = (
        await session.scalars(select(Addon).where(Addon.status == AddonStatus.ACTIVE))
    ).all()
    titles_q = (
        select(Title)
        .options(selectinload(Title.external_ids))
        .order_by(Title.popularity_rank.asc().nulls_last(), Title.id)
        .limit(top_titles)
    )
    checks_q = select(Availability.addon_id, Availability.title_id, Availability.next_check_at)
    if title_ids is not None:
        titles_q = titles_q.where(Title.id.in_(title_ids))
        checks_q = checks_q.where(Availability.title_id.in_(title_ids))
    titles = (await session.scalars(titles_q)).all()
    next_check = {(a, t): at for a, t, at in await session.execute(checks_q)}
    scopes = {a.id: [StreamScope.from_json(s) for s in a.stream_scopes] for a in addons}

    jobs = []
    for title in titles:
        ids = {x.scheme: x.value for x in title.external_ids}
        for addon in addons:
            at = next_check.get((addon.id, title.id))
            if at is not None and at > now:
                continue
            pid = pick_probe_id(scopes[addon.id], title.type, ids, title.is_anime)
            if pid is None:
                continue
            jobs.append(
                Job(
                    addon_id=addon.id,
                    title_id=title.id,
                    probe_id=pid,
                    url=stream_url(addon.base_url, title.type, pid),
                    host=urlsplit(addon.base_url).netloc,
                    popularity_rank=title.popularity_rank,
                )
            )
    return jobs
