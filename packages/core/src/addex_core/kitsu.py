"""Anime seed from the Kitsu API, ordered by Kitsu's popularity rank.

Kitsu IDs are what Stremio anime addons key on (`kitsu:1376`), and its `mappings`
relationship gives MAL and AniList IDs in the same request.
"""

import asyncio
from typing import Any

import httpx

from addex_core.http import get_json
from addex_core.ids import IdScheme
from addex_core.titles import TitleSeed

API = "https://kitsu.app/api/edge/anime"
PAGE_SIZE = 20  # Kitsu's maximum
MAPPING_SCHEMES = {"myanimelist/anime": IdScheme.MAL, "anilist/anime": IdScheme.ANILIST}
SKIPPED_SUBTYPES = {"music"}

PARAMS = {
    "sort": "popularityRank",
    "include": "mappings",
    "fields[anime]": "canonicalTitle,titles,abbreviatedTitles,subtype,startDate,"
    "popularityRank,posterImage,nsfw,mappings",
    "fields[mappings]": "externalSite,externalId",
    "page[limit]": str(PAGE_SIZE),
}


def parse_page(data: dict[str, Any]) -> list[TitleSeed]:
    mappings = {
        m["id"]: m["attributes"] for m in data.get("included", []) if m["type"] == "mappings"
    }
    seeds = []
    for item in data["data"]:
        a = item["attributes"]
        if a.get("nsfw") or a.get("subtype") in SKIPPED_SUBTYPES:
            continue
        ids = {IdScheme.KITSU: item["id"]}
        for ref in item.get("relationships", {}).get("mappings", {}).get("data") or []:
            m = mappings.get(ref["id"])
            scheme = m and MAPPING_SCHEMES.get(m["externalSite"])
            if scheme and scheme not in ids:
                ids[scheme] = str(m["externalId"])
        start = a.get("startDate")
        aliases = [*(a.get("titles") or {}).values(), *(a.get("abbreviatedTitles") or [])]
        seeds.append(
            TitleSeed(
                type="movie" if a.get("subtype") == "movie" else "series",
                name=a["canonicalTitle"],
                ids=ids,
                year=int(start[:4]) if start else None,
                is_anime=True,
                poster=(a.get("posterImage") or {}).get("medium"),
                popularity_rank=a.get("popularityRank"),
                # Kitsu returns nulls inside both titles and abbreviatedTitles.
                aliases=tuple(dict.fromkeys(t for t in aliases if t)),
            )
        )
    return seeds


async def fetch_top_anime(
    client: httpx.AsyncClient, limit: int = 500, delay: float = 0.5
) -> list[TitleSeed]:
    """The `limit` most popular anime, skipping music videos and NSFW entries."""
    seeds: dict[str, TitleSeed] = {}
    offset = 0
    while len(seeds) < limit:
        data = await get_json(client, API, {**PARAMS, "page[offset]": str(offset)})
        if not data["data"]:
            break
        # Offset paging over a live ranking can repeat entries across pages.
        for seed in parse_page(data):
            seeds.setdefault(seed.ids[IdScheme.KITSU], seed)
        offset += PAGE_SIZE
        await asyncio.sleep(delay)
    return list(seeds.values())[:limit]
