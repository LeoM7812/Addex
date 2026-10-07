from datetime import datetime

from pydantic import BaseModel

from addex_api.queries import AddonResult, SearchHit, TitleGroup
from addex_core.models import Title


class SearchHitOut(BaseModel):
    id: int
    type: str
    name: str
    year: int | None
    poster: str | None
    is_anime: bool
    popularity_rank: int | None
    entry_count: int
    addon_count: int

    @classmethod
    def of(cls, hit: SearchHit) -> "SearchHitOut":
        return cls(**hit.__dict__)


class TitleOut(BaseModel):
    id: int
    type: str
    name: str
    aliases: list[str]
    year: int | None
    poster: str | None
    is_anime: bool
    popularity_rank: int | None

    @classmethod
    def of(cls, t: Title) -> "TitleOut":
        return cls(id=t.id, type=t.type, name=t.name, aliases=t.aliases, year=t.year,
                   poster=t.poster, is_anime=t.is_anime, popularity_rank=t.popularity_rank)


class EntryOut(BaseModel):
    title_id: int
    probe_id: str
    status: str
    has_streams: bool | None
    stream_count: int | None
    latency_ms: int | None
    last_checked: datetime
    last_answered: datetime | None


class AddonOut(BaseModel):
    id: int
    name: str
    description: str | None
    logo: str | None
    manifest_url: str
    install_url: str
    p2p: bool
    has_streams: bool
    entries_with_streams: int
    max_stream_count: int
    last_checked: datetime
    confirmed_at: datetime | None
    entries: list[EntryOut]

    @classmethod
    def of(cls, a: AddonResult) -> "AddonOut":
        return cls(
            id=a.id, name=a.name, description=a.description, logo=a.logo,
            manifest_url=a.manifest_url, install_url=a.install_url, p2p=a.p2p,
            has_streams=a.has_streams, entries_with_streams=a.entries_with_streams,
            max_stream_count=a.max_stream_count, last_checked=a.last_checked,
            confirmed_at=a.confirmed_at,
            entries=[EntryOut(**e.__dict__) for e in a.entries],
        )


class TitleDetailOut(BaseModel):
    title: TitleOut
    entries: list[TitleOut]  # per-season entries grouped under `title`
    addons: list[AddonOut]

    @classmethod
    def of(cls, g: TitleGroup) -> "TitleDetailOut":
        return cls(title=TitleOut.of(g.root), entries=[TitleOut.of(e) for e in g.entries],
                   addons=[AddonOut.of(a) for a in g.addons])
