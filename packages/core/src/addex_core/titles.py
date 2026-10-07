"""Upserting titles from external sources (Kitsu, IMDb, ...)."""

from dataclasses import dataclass, field

from sqlalchemy import select, tuple_
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from addex_core.ids import IdScheme
from addex_core.models import Title, TitleId


@dataclass(frozen=True)
class TitleSeed:
    type: str  # Stremio type: "movie" or "series"
    name: str
    # Ordered by preference; the first one is the source's own ID and is used to match
    # an existing title.
    ids: dict[IdScheme, str]
    year: int | None = None
    is_anime: bool = False
    poster: str | None = None
    popularity_rank: int | None = None
    aliases: tuple[str, ...] = ()


@dataclass
class UpsertReport:
    added: int = 0
    updated: int = 0
    # (scheme, value, owning title id): external IDs already attached to another title,
    # e.g. two Kitsu entries mapped to the same MAL ID. Left as they are.
    id_conflicts: list[tuple[IdScheme, str, int]] = field(default_factory=list)


async def upsert_titles(session: AsyncSession, seeds: list[TitleSeed]) -> UpsertReport:
    """Insert or update titles and their external IDs. Does not commit."""
    report = UpsertReport()
    keys = {(s, v) for seed in seeds for s, v in seed.ids.items()}
    owners: dict[tuple[IdScheme, str], int] = {}
    if keys:
        rows = await session.execute(
            select(TitleId.scheme, TitleId.value, TitleId.title_id).where(
                tuple_(TitleId.scheme, TitleId.value).in_(keys)
            )
        )
        owners = {(s, v): tid for s, v, tid in rows}
    titles = {
        t.id: t
        for t in await session.scalars(
            select(Title)
            .where(Title.id.in_(set(owners.values())))
            .options(selectinload(Title.external_ids))
        )
    }

    for seed in seeds:
        primary = next(iter(seed.ids.items()))
        title = titles.get(owners.get(primary, -1))
        if title is None:
            title = Title(external_ids=[])
            session.add(title)
            report.added += 1
        else:
            report.updated += 1

        title.type = seed.type
        title.name = seed.name
        title.year = seed.year
        title.is_anime = seed.is_anime
        title.poster = seed.poster
        title.popularity_rank = seed.popularity_rank
        title.aliases = sorted(set(seed.aliases) - {seed.name})

        have = {x.scheme for x in title.external_ids}
        for scheme, value in seed.ids.items():
            if scheme in have:
                continue
            owner = owners.get((scheme, value))
            if owner is not None and owner != title.id:
                report.id_conflicts.append((scheme, value, owner))
                continue
            title.external_ids.append(TitleId(scheme=scheme, value=value))
            # Claim it so a later seed in the same batch sees the conflict.
            owners[(scheme, value)] = title.id if title.id is not None else -2

    await session.flush()
    return report
