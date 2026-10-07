"""Addex as a Stremio addon.

Instead of streams it answers with one entry per indexed addon that has the title,
each linking to that addon's install page. It never returns a playable source.
"""

import re
from datetime import UTC, datetime
from typing import Annotated

from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from addex_api import __version__
from addex_api.deps import get_session
from addex_api.queries import AddonResult, title_group, title_id_for
from addex_core.ids import IdScheme

router = APIRouter()
Session = Annotated[AsyncSession, Depends(get_session)]

CACHE_MAX_AGE = 3600

MANIFEST = {
    "id": "org.addex.index",
    "version": __version__,
    "name": "Addex",
    "description": "Shows which addons have streams for what you're watching, with a link "
    "to install them. Addex itself hosts and returns no streams.",
    "resources": ["stream"],
    "types": ["movie", "series"],
    "idPrefixes": ["tt", "kitsu:", "mal:"],
    "catalogs": [],
}

IMDB_ID = re.compile(r"^(tt\d+)(?::\d+:\d+)?$")
PREFIXED_ID = re.compile(r"^(kitsu|mal|anilist|tmdb):(\d+)(?::\d+)*$")


def parse_stremio_id(stremio_id: str) -> tuple[IdScheme, str] | None:
    """Title-level ID from a Stremio ID, dropping any season/episode suffix."""
    if m := IMDB_ID.match(stremio_id):
        return IdScheme.IMDB, m.group(1)
    if m := PREFIXED_ID.match(stremio_id):
        return IdScheme(m.group(1)), m.group(2)
    return None


def _ago(when: datetime, now: datetime) -> str:
    minutes = int((now - when).total_seconds() // 60)
    if minutes < 60:
        return f"{max(minutes, 1)} min ago"
    if minutes < 48 * 60:
        return f"{minutes // 60} h ago"
    return f"{minutes // (24 * 60)} days ago"


def stream_entry(addon: AddonResult, now: datetime) -> dict:
    count = addon.max_stream_count
    details = [f"{count} stream{'s' if count != 1 else ''}"]
    if addon.p2p:
        details.append("P2P")
    if addon.confirmed_at:
        details.append(f"confirmed {_ago(addon.confirmed_at, now)}")
    text = f"Install {addon.name}\n" + " · ".join(details)
    return {
        "name": "Addex",
        "description": text,
        "title": text,  # pre-v5 clients read `title`
        "externalUrl": addon.install_url,
    }


@router.get("/manifest.json")
async def manifest():
    return MANIFEST


@router.get("/stream/{stremio_type}/{stremio_id}.json")
async def stream(session: Session, stremio_type: str, stremio_id: str):
    streams: list[dict] = []
    parsed = parse_stremio_id(stremio_id)
    title_id = await title_id_for(session, *parsed) if parsed else None
    group = await title_group(session, title_id) if title_id else None
    if group:
        now = datetime.now(UTC)
        streams = [stream_entry(a, now) for a in group.addons if a.has_streams]
    return {"streams": streams, "cacheMaxAge": CACHE_MAX_AGE}
