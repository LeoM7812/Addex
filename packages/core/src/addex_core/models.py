"""Postgres schema.

Ground rule: nothing here may hold stream URLs, magnets or info hashes. `availability` keeps
only whether an addon answered, how many streams it returned and how fast.
"""

from datetime import datetime
from enum import StrEnum

from sqlalchemy import (
    Boolean,
    DateTime,
    Enum,
    ForeignKey,
    Index,
    Integer,
    MetaData,
    SmallInteger,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship

from addex_core.ids import IdScheme

NAMING_CONVENTION = {
    "ix": "ix_%(table_name)s_%(column_0_N_name)s",
    "uq": "uq_%(table_name)s_%(column_0_N_name)s",
    "ck": "ck_%(table_name)s_%(constraint_name)s",
    "fk": "fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s",
    "pk": "pk_%(table_name)s",
}


class Base(DeclarativeBase):
    metadata = MetaData(naming_convention=NAMING_CONVENTION)


def _enum(cls: type[StrEnum], name: str) -> Enum:
    return Enum(cls, name=name, values_callable=lambda e: [m.value for m in e])


class AddonStatus(StrEnum):
    ACTIVE = "active"
    # Manifest declares no `stream` resource (catalog/meta/subtitles only).
    NO_STREAMS = "no_streams"
    # behaviorHints.configurationRequired and the registered URL carries no config.
    NEEDS_CONFIG = "needs_config"
    # Manifest could not be fetched or parsed on the last attempt.
    BROKEN = "broken"
    # Manually excluded from crawling.
    DISABLED = "disabled"


class CheckStatus(StrEnum):
    OK = "ok"  # 200 with at least one stream
    EMPTY = "empty"  # 200 with `streams: []`
    TIMEOUT = "timeout"
    HTTP_ERROR = "http_error"  # non-2xx
    INVALID = "invalid"  # 200 but body is not a valid stream response
    ERROR = "error"  # connection refused, DNS, TLS, ...


class Addon(Base):
    __tablename__ = "addons"

    id: Mapped[int] = mapped_column(primary_key=True)
    manifest_url: Mapped[str] = mapped_column(Text, unique=True)
    base_url: Mapped[str] = mapped_column(Text)
    # `id` field from the manifest. Not unique: the same addon can be registered with
    # different configs, each under its own manifest URL.
    manifest_id: Mapped[str] = mapped_column(Text, index=True)
    name: Mapped[str] = mapped_column(Text)
    version: Mapped[str] = mapped_column(Text)
    description: Mapped[str | None] = mapped_column(Text)
    logo: Mapped[str | None] = mapped_column(Text)
    # Normalized `stream` resource scopes, see addex_core.manifest.StreamScope:
    # [{"types": ["movie", "series"], "id_prefixes": ["tt", "kitsu"] | null}]
    stream_scopes: Mapped[list[dict]] = mapped_column(JSONB, default=list)
    p2p: Mapped[bool] = mapped_column(Boolean, default=False)
    adult: Mapped[bool] = mapped_column(Boolean, default=False)
    # Raw manifest as last fetched. Manifests describe capabilities, not content.
    manifest: Mapped[dict] = mapped_column(JSONB)
    status: Mapped[AddonStatus] = mapped_column(
        _enum(AddonStatus, "addon_status"), default=AddonStatus.ACTIVE
    )
    last_fetched_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_error: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )

    availability: Mapped[list["Availability"]] = relationship(back_populates="addon")


class Title(Base):
    __tablename__ = "titles"

    id: Mapped[int] = mapped_column(primary_key=True)
    # Stremio type used when probing: "movie" or "series".
    type: Mapped[str] = mapped_column(Text)
    name: Mapped[str] = mapped_column(Text)
    year: Mapped[int | None] = mapped_column(SmallInteger)
    is_anime: Mapped[bool] = mapped_column(Boolean, default=False)
    poster: Mapped[str | None] = mapped_column(Text)
    # Lower is more popular. Drives the adaptive refresh TTL. Null = not ranked.
    popularity_rank: Mapped[int | None] = mapped_column(Integer, index=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )

    external_ids: Mapped[list["TitleId"]] = relationship(
        back_populates="title", cascade="all, delete-orphan"
    )
    availability: Mapped[list["Availability"]] = relationship(back_populates="title")


class TitleId(Base):
    """External IDs for a title. An anime usually has kitsu + mal (+ imdb sometimes);
    the crawler picks whichever one an addon's id prefixes accept."""

    __tablename__ = "title_ids"
    __table_args__ = (UniqueConstraint("scheme", "value"),)

    title_id: Mapped[int] = mapped_column(
        ForeignKey("titles.id", ondelete="CASCADE"), primary_key=True
    )
    scheme: Mapped[IdScheme] = mapped_column(_enum(IdScheme, "id_scheme"), primary_key=True)
    value: Mapped[str] = mapped_column(Text)

    title: Mapped[Title] = relationship(back_populates="external_ids")


class Availability(Base):
    """Latest probe result for one (addon, title) pair."""

    __tablename__ = "availability"
    __table_args__ = (
        Index(None, "title_id"),
        Index(None, "next_check_at"),
    )

    addon_id: Mapped[int] = mapped_column(
        ForeignKey("addons.id", ondelete="CASCADE"), primary_key=True
    )
    title_id: Mapped[int] = mapped_column(
        ForeignKey("titles.id", ondelete="CASCADE"), primary_key=True
    )
    # The exact Stremio ID sent, e.g. "kitsu:1376:1".
    probe_id: Mapped[str] = mapped_column(Text)
    status: Mapped[CheckStatus] = mapped_column(_enum(CheckStatus, "check_status"))
    # True for OK, False for EMPTY, null when the addon failed to answer: a timeout says
    # nothing about whether the title is there.
    has_streams: Mapped[bool | None] = mapped_column(Boolean)
    stream_count: Mapped[int | None] = mapped_column(Integer)
    latency_ms: Mapped[int | None] = mapped_column(Integer)
    http_status: Mapped[int | None] = mapped_column(SmallInteger)
    last_checked: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    # Last time the addon gave a definitive answer (OK or EMPTY). Lets the front-end keep
    # showing a known result while the addon is temporarily down.
    last_answered: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    next_check_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    consecutive_failures: Mapped[int] = mapped_column(Integer, default=0)

    addon: Mapped[Addon] = relationship(back_populates="availability")
    title: Mapped[Title] = relationship(back_populates="availability")
