"""Addex as a Stremio addon.

- `stream`: for a movie or episode, one entry per indexed addon that has the title,
  linking to that addon's install page. Never a playable source.
- `addon_catalog`: Addex's indexed addons, ranked by measured coverage, so they can be
  browsed and installed from Stremio's own Addons screen.
- Every route also exists under `/{config}/...` for per-user settings (see userconfig).
"""

import os
import re
from datetime import UTC, datetime
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import JSONResponse
from sqlalchemy.ext.asyncio import AsyncSession

from addex_api import __version__
from addex_api.deps import NoteDemand, get_note_demand, get_session
from addex_api.queries import AddonResult, AddonStats, addon_stats, title_group, title_id_for
from addex_api.userconfig import DEFAULT, UserConfig
from addex_core.demand import DemandRequest
from addex_core.ids import IdScheme

router = APIRouter()
Session = Annotated[AsyncSession, Depends(get_session)]

DAY = 24 * 3600
ID_PREFIXES = ["tt", "kitsu:", "mal:"]
ADDON_CATALOGS = {
    "addex-top": "Addex: most coverage",
    "addex-anime": "Addex: anime",
}
ADDON_CATALOG_TYPE = "all"

IMDB_ID = re.compile(r"^(tt\d+)(?::\d+:\d+)?$")
PREFIXED_ID = re.compile(r"^(kitsu|mal|anilist|tmdb):(\d+)(?::\d+)*$")


def parse_stremio_id(stremio_id: str) -> tuple[IdScheme, str] | None:
    """Title-level ID from a Stremio ID, dropping any season/episode suffix."""
    if m := IMDB_ID.match(stremio_id):
        return IdScheme.IMDB, m.group(1)
    if m := PREFIXED_ID.match(stremio_id):
        return IdScheme(m.group(1)), m.group(2)
    return None


def user_config(config: str | None = None) -> UserConfig:
    if config is None:
        return DEFAULT
    cfg = UserConfig.decode(config)
    if cfg is None:
        raise HTTPException(404, "unknown config")
    return cfg


Config = Annotated[UserConfig, Depends(user_config)]


def addon_response(
    body: dict, max_age: int, stale_revalidate: int | None = None, stale_error: int | None = None,
    in_body: bool = True,
) -> JSONResponse:
    """JSON with cache hints in the body (read by Stremio) and as Cache-Control (read by
    proxies and browsers), the way the official SDK sends them."""
    hints = {"cacheMaxAge": max_age, "staleRevalidate": stale_revalidate,
             "staleError": stale_error}
    hints = {k: v for k, v in hints.items() if v is not None}
    header = {"cacheMaxAge": "max-age", "staleRevalidate": "stale-while-revalidate",
              "staleError": "stale-if-error"}
    cache_control = ", ".join(f"{header[k]}={v}" for k, v in hints.items()) + ", public"
    content = {**body, **hints} if in_body else body
    return JSONResponse(content, headers={"Cache-Control": cache_control})


def build_manifest(base_url: str) -> dict:
    manifest = {
        "id": "org.addex.index",
        "version": __version__,
        "name": "Addex",
        "description": "Finds which addons have streams for what you're watching, and "
        "lists them with an install link. Browse the best addons under Addons > Addex. "
        "Addex itself hosts and returns no streams.",
        "logo": f"{base_url}/static/logo.png",
        "background": f"{base_url}/static/background.png",
        "resources": [
            {"name": "stream", "types": ["movie", "series"], "idPrefixes": ID_PREFIXES},
            "addon_catalog",
        ],
        "types": ["movie", "series"],
        "catalogs": [],
        "addonCatalogs": [
            {"type": ADDON_CATALOG_TYPE, "id": cid, "name": name}
            for cid, name in ADDON_CATALOGS.items()
        ],
        "behaviorHints": {"configurable": True},
    }
    if email := os.environ.get("ADDEX_CONTACT_EMAIL"):
        manifest["contactEmail"] = email  # enables the Report button in Stremio
    if signature := os.environ.get("ADDEX_STREMIO_ADDONS_SIGNATURE"):
        # Proves to stremio-addons.net that this deployment owns its listing. Issued per
        # addon URL, so it comes from the environment rather than the code.
        manifest["stremioAddonsConfig"] = {
            "issuer": "https://stremio-addons.net",
            "signature": signature,
        }
    return manifest


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
    # Only `description`: stremio-core aliases `title` to it, and sending both is a
    # duplicate-field error that makes the client drop the whole response.
    # Stremio hands externalUrl to the system browser, so the stremio:// link would
    # break; the Stremio Web install page works from every client.
    return {"name": "Addex", "description": text, "externalUrl": addon.web_install_url}


def catalog_entry(stats: AddonStats, anime: bool) -> dict:
    """An addon as Stremio's Addons screen lists it, with Addex's numbers prepended to
    its own description."""
    coverage = stats.anime_coverage if anime else stats.coverage
    checked = stats.anime_answered if anime else stats.answered
    what = "anime titles" if anime else "titles"
    blurb = f"Addex: streams for {round(coverage * 100)}% of {checked} {what} checked."
    manifest = dict(stats.addon.manifest)
    manifest["description"] = f"{blurb}\n\n{manifest.get('description') or ''}".strip()
    return {"transportName": "http", "transportUrl": stats.addon.manifest_url,
            "manifest": manifest}


@router.get("/manifest.json")
@router.get("/{config}/manifest.json")
async def manifest(request: Request, cfg: Config):
    return addon_response(build_manifest(str(request.base_url).rstrip("/")), 3600, in_body=False)


@router.get("/stream/{stremio_type}/{stremio_id}.json")
@router.get("/{config}/stream/{stremio_type}/{stremio_id}.json")
async def stream(
    session: Session,
    cfg: Config,
    note_demand: Annotated[NoteDemand, Depends(get_note_demand)],
    stremio_type: str,
    stremio_id: str,
):
    streams: list[dict] = []
    parsed = parse_stremio_id(stremio_id)
    if parsed:
        # Unknown titles get indexed and stale ones re-checked, ahead of the backlog.
        await note_demand(DemandRequest(*parsed, type=stremio_type))
    title_id = await title_id_for(session, *parsed) if parsed else None
    group = await title_group(session, title_id) if title_id else None
    if group:
        now = datetime.now(UTC)
        streams = [stream_entry(a, now) for a in group.addons
                   if a.has_streams and cfg.wants(a.id, a.p2p)]
    if not streams:
        # Nothing known yet usually means a check was just queued: ask again soon.
        return addon_response({"streams": []}, 60)
    return addon_response({"streams": streams}, 3600, stale_revalidate=4 * 3600,
                          stale_error=7 * DAY)


@router.get("/addon_catalog/{catalog_type}/{catalog_id}.json")
@router.get("/{config}/addon_catalog/{catalog_type}/{catalog_id}.json")
async def addon_catalog(session: Session, cfg: Config, catalog_type: str, catalog_id: str):
    if catalog_type != ADDON_CATALOG_TYPE or catalog_id not in ADDON_CATALOGS:
        return addon_response({"addons": []}, 60)
    anime = catalog_id == "addex-anime"
    stats = await addon_stats(session)
    if anime:
        stats = sorted((s for s in stats if s.anime_with_streams),
                       key=lambda s: s.rank_key(anime=True))
    else:
        stats = [s for s in stats if s.with_streams]
    addons = [catalog_entry(s, anime) for s in stats if cfg.wants(s.addon.id, s.p2p)]
    return addon_response({"addons": addons}, 6 * 3600, stale_revalidate=DAY,
                          stale_error=7 * DAY)
