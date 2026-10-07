"""Groups Kitsu anime entries under their IMDb title.

Kitsu has one entry per season or cour ("Boku no Hero Academia 2"), IMDb one per series,
so the relation is many-to-one and rows are linked through `parent_id`, not merged:
each seed keeps owning its rows, and Kitsu entries keep being probed per season.
"""

from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field, replace

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from addex_core.ids import IdScheme
from addex_core.models import Title, TitleId
from addex_core.titles import TitleSeed, upsert_titles

# {imdb_id: types to try, most likely first} -> {imdb_id: seed, or None if not found}
ParentFetcher = Callable[[dict[str, tuple[str, ...]]], Awaitable[dict[str, TitleSeed | None]]]


@dataclass
class LinkReport:
    linked: int = 0
    unmapped: int = 0  # Kitsu titles with no IMDb mapping
    changed: int = 0  # parent_id values written this run
    parents_created: int = 0
    parents_missing: list[str] = field(default_factory=list)  # mapped, but not on Cinemeta


async def _ids(session: AsyncSession, scheme: IdScheme) -> dict[str, int]:
    rows = await session.execute(
        select(TitleId.value, TitleId.title_id).where(TitleId.scheme == scheme)
    )
    return dict(rows.all())


async def link_anime(
    session: AsyncSession, kitsu_to_imdb: dict[str, str], fetch_parents: ParentFetcher
) -> LinkReport:
    """Set `parent_id` on every Kitsu title, creating missing IMDb parents. Idempotent.
    Does not commit."""
    report = LinkReport()
    kitsu = await _ids(session, IdScheme.KITSU)
    imdb = await _ids(session, IdScheme.IMDB)
    titles = {
        t.id: t for t in await session.scalars(select(Title).where(Title.id.in_(kitsu.values())))
    }

    wanted: dict[str, tuple[str, ...]] = {}
    for kitsu_id, title_id in kitsu.items():
        target = kitsu_to_imdb.get(kitsu_id)
        if target and target not in imdb and target not in wanted:
            first = titles[title_id].type
            wanted[target] = (first, "series" if first == "movie" else "movie")

    if wanted:
        fetched = await fetch_parents(wanted)
        seeds = [replace(s, is_anime=True) for s in fetched.values() if s is not None]
        report.parents_missing = sorted(i for i, s in fetched.items() if s is None)
        report.parents_created = (await upsert_titles(session, seeds)).added
        imdb = await _ids(session, IdScheme.IMDB)

    parents = set()
    for kitsu_id, title_id in kitsu.items():
        parent = imdb.get(kitsu_to_imdb.get(kitsu_id, ""))
        if parent == title_id:  # a title can't be its own parent
            parent = None
        if parent is None:
            report.unmapped += kitsu_id not in kitsu_to_imdb
        else:
            report.linked += 1
            parents.add(parent)
        if titles[title_id].parent_id != parent:
            titles[title_id].parent_id = parent
            report.changed += 1

    if parents:
        await session.execute(update(Title).where(Title.id.in_(parents)).values(is_anime=True))
    await session.flush()
    return report
