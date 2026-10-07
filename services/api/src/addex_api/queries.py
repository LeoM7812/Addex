"""Read queries behind the HTTP routes.

Titles are shown as groups: a top-level title plus its children (Kitsu per-season
entries under an IMDb series). Only `active` addons are ever reported.
"""

from dataclasses import dataclass, field
from datetime import datetime

from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from addex_core.ids import IdScheme
from addex_core.manifest import install_url
from addex_core.models import Addon, AddonStatus, Availability, Title, TitleId

FUZZY_THRESHOLD = 0.6

SEARCH_SQL = text("""
WITH hits AS (
    SELECT
        coalesce(t.parent_id, t.id) AS root_id,
        greatest(
            word_similarity(:q, t.name),
            coalesce((SELECT max(word_similarity(:q, a)) FROM unnest(t.aliases) a), 0)
        ) AS score,
        lower(t.name) = lower(:q)
            OR EXISTS (SELECT 1 FROM unnest(t.aliases) a WHERE lower(a) = lower(:q)) AS exact,
        t.name ILIKE :prefix
            OR EXISTS (SELECT 1 FROM unnest(t.aliases) a WHERE a ILIKE :prefix) AS prefix
    FROM titles t
    WHERE t.name ILIKE :contains
       OR EXISTS (SELECT 1 FROM unnest(t.aliases) a WHERE a ILIKE :contains)
       OR word_similarity(:q, t.name) >= :fuzzy
       OR EXISTS (SELECT 1 FROM unnest(t.aliases) a WHERE word_similarity(:q, a) >= :fuzzy)
),
roots AS (
    SELECT root_id, max(score) AS score, bool_or(exact) AS exact, bool_or(prefix) AS prefix
    FROM hits GROUP BY root_id
)
SELECT
    r.id, r.type, r.name, r.year, r.poster, r.is_anime,
    (SELECT min(g.popularity_rank) FROM titles g
      WHERE g.id = r.id OR g.parent_id = r.id) AS popularity_rank,
    (SELECT count(*) FROM titles g WHERE g.parent_id = r.id) AS entry_count,
    (SELECT count(DISTINCT v.addon_id)
       FROM availability v
       JOIN titles g ON g.id = v.title_id
       JOIN addons a ON a.id = v.addon_id
      WHERE (g.id = r.id OR g.parent_id = r.id)
        AND v.has_streams AND a.status = 'active') AS addon_count
FROM roots JOIN titles r ON r.id = roots.root_id
ORDER BY roots.exact DESC, roots.prefix DESC, roots.score DESC,
         popularity_rank ASC NULLS LAST, r.id
LIMIT :limit
""")


def _escape_like(s: str) -> str:
    return s.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


@dataclass
class SearchHit:
    id: int
    type: str
    name: str
    year: int | None
    poster: str | None
    is_anime: bool
    popularity_rank: int | None
    entry_count: int  # child entries (seasons) grouped under this title
    addon_count: int  # active addons with streams for the title or any entry


async def search_titles(session: AsyncSession, q: str, limit: int = 20) -> list[SearchHit]:
    """Top-level titles whose name or aliases, or any child's, match `q`: substring
    match plus trigram similarity for typos. Exact and prefix matches rank first."""
    q = q.strip()
    esc = _escape_like(q)
    rows = await session.execute(
        SEARCH_SQL,
        {"q": q, "contains": f"%{esc}%", "prefix": f"{esc}%", "fuzzy": FUZZY_THRESHOLD,
         "limit": limit},
    )
    return [SearchHit(**row._mapping) for row in rows]


@dataclass
class EntryResult:
    title_id: int
    probe_id: str
    status: str
    has_streams: bool | None
    stream_count: int | None
    latency_ms: int | None
    last_checked: datetime
    last_answered: datetime | None


@dataclass
class AddonResult:
    id: int
    name: str
    description: str | None
    logo: str | None
    manifest_url: str
    install_url: str
    p2p: bool
    entries: list[EntryResult] = field(default_factory=list)

    @property
    def has_streams(self) -> bool:
        return any(e.has_streams for e in self.entries)

    @property
    def entries_with_streams(self) -> int:
        return sum(1 for e in self.entries if e.has_streams)

    @property
    def max_stream_count(self) -> int:
        return max((e.stream_count or 0 for e in self.entries), default=0)

    @property
    def last_checked(self) -> datetime:
        return max(e.last_checked for e in self.entries)

    @property
    def confirmed_at(self) -> datetime | None:
        """When streams were last confirmed, by any entry that has them. Not
        `last_checked`: that may be a newer check of a different entry that came back
        empty, or a failed probe."""
        return max((e.last_answered for e in self.entries if e.has_streams and e.last_answered),
                   default=None)


@dataclass
class TitleGroup:
    root: Title
    entries: list[Title]  # children, most popular first
    addons: list[AddonResult]  # addons with streams first

    @property
    def titles(self) -> list[Title]:
        return [self.root, *self.entries]


async def title_group(session: AsyncSession, title_id: int) -> TitleGroup | None:
    """The group `title_id` belongs to (its parent's group if it is a child), with
    per-addon results across every title in the group."""
    title = await session.get(Title, title_id)
    if title is None:
        return None
    root = await session.get(Title, title.parent_id) if title.parent_id else title
    entries = list(
        await session.scalars(
            select(Title)
            .where(Title.parent_id == root.id)
            .order_by(Title.popularity_rank.asc().nulls_last(), Title.year, Title.id)
        )
    )
    ids = [root.id, *(e.id for e in entries)]
    rows = await session.execute(
        select(Availability, Addon)
        .join(Addon)
        .where(Availability.title_id.in_(ids), Addon.status == AddonStatus.ACTIVE)
    )
    addons: dict[int, AddonResult] = {}
    for avail, addon in rows:
        result = addons.setdefault(addon.id, AddonResult(
            id=addon.id, name=addon.name, description=addon.description, logo=addon.logo,
            manifest_url=addon.manifest_url, install_url=install_url(addon.manifest_url),
            p2p=addon.p2p,
        ))
        result.entries.append(EntryResult(
            title_id=avail.title_id, probe_id=avail.probe_id, status=avail.status.value,
            has_streams=avail.has_streams, stream_count=avail.stream_count,
            latency_ms=avail.latency_ms, last_checked=avail.last_checked,
            last_answered=avail.last_answered,
        ))
    ordered = sorted(
        addons.values(),
        key=lambda a: (not a.has_streams, -a.entries_with_streams, -a.max_stream_count, a.name),
    )
    return TitleGroup(root=root, entries=entries, addons=ordered)


async def title_id_for(session: AsyncSession, scheme: IdScheme, value: str) -> int | None:
    return await session.scalar(
        select(TitleId.title_id).where(TitleId.scheme == scheme, TitleId.value == value)
    )

