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


def parse_meta(meta: dict[str, Any], stremio_type: str, rank: int) -> TitleSeed | None:
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
