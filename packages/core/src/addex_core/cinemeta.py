"""Movie and series seed from Cinemeta, Stremio's official metadata addon.

Uses the "Popular" (`top`) catalog: IMDb IDs ranked by what Stremio users actually
browse, which is a better proxy for what people will search Addex for than all-time
IMDb vote counts.
"""

import asyncio
import re
from typing import Any

import httpx

from addex_core.http import get_json
from addex_core.ids import IdScheme
from addex_core.titles import TitleSeed

BASE = "https://v3-cinemeta.strem.io"
IMDB_ID = re.compile(r"^tt\d+$")
YEAR = re.compile(r"\d{4}")


def catalog_url(stremio_type: str, skip: int) -> str:
    extra = f"/skip={skip}" if skip else ""
    return f"{BASE}/catalog/{stremio_type}/top{extra}.json"


def is_anime(meta: dict[str, Any]) -> bool:
    """Japanese animation. Cinemeta has no anime flag, so this is a heuristic: animation
    whose first-listed country is Japan. Co-productions listed the other way round
    (Coco, Finding Nemo: "United States, ..., Japan") are excluded."""
    first_country = (meta.get("country") or "").split(",")[0].strip()
    return "Animation" in (meta.get("genres") or []) and first_country == "Japan"


def parse_meta(
    meta: dict[str, Any], stremio_type: str, rank: int | None
) -> TitleSeed | None:
    imdb_id = meta.get("imdb_id") or meta.get("id") or ""
    if not IMDB_ID.match(imdb_id) or not meta.get("name"):
        return None
    year = YEAR.search(str(meta.get("releaseInfo") or meta.get("year") or ""))
    return TitleSeed(
        type=stremio_type,
        name=meta["name"],
        ids={IdScheme.IMDB: imdb_id},
        year=int(year.group()) if year else None,
        is_anime=is_anime(meta),
        poster=meta.get("poster"),
        popularity_rank=rank,
    )


async def fetch_top(
    client: httpx.AsyncClient, stremio_type: str, limit: int = 500, delay: float = 0.5
) -> list[TitleSeed]:
    """The `limit` most popular titles of one type, ranked from 1."""
    seeds: dict[str, TitleSeed] = {}
    skip = 0
    while len(seeds) < limit:
        data = await get_json(client, catalog_url(stremio_type, skip))
        metas = data.get("metas") or []
        for meta in metas:
            seed = parse_meta(meta, stremio_type, rank=len(seeds) + 1)
            # The ranking is live, so pages can overlap.
            if seed and seed.ids[IdScheme.IMDB] not in seeds:
                seeds[seed.ids[IdScheme.IMDB]] = seed
        if not metas or not data.get("hasMore", True):
            break
        skip += len(metas)
        await asyncio.sleep(delay)
    return list(seeds.values())[:limit]


async def fetch_meta(
    client: httpx.AsyncClient, imdb_id: str, types: tuple[str, ...] = ("series", "movie")
) -> TitleSeed | None:
    """One title by IMDb ID, unranked. Tries each type in turn, since the caller may only
    guess whether it is a movie or a series."""
    for stremio_type in types:
        try:
            data = await get_json(client, f"{BASE}/meta/{stremio_type}/{imdb_id}.json", retries=2)
        except httpx.HTTPStatusError:
            continue
        meta = (data or {}).get("meta")
        if meta:
            return parse_meta(meta, meta.get("type") or stremio_type, rank=None)
    return None


async def fetch_metas(
    client: httpx.AsyncClient, wanted: dict[str, tuple[str, ...]], concurrency: int = 4
) -> dict[str, TitleSeed | None]:
    """Several titles by IMDb ID ({imdb_id: types to try}), a few at a time."""
    sem = asyncio.Semaphore(concurrency)

    async def one(imdb_id: str, types: tuple[str, ...]) -> tuple[str, TitleSeed | None]:
        async with sem:
            return imdb_id, await fetch_meta(client, imdb_id, types)

    return dict(await asyncio.gather(*(one(i, t) for i, t in wanted.items())))
