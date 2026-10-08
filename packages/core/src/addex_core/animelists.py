"""Anime ID mappings from Fribb/anime-lists (https://github.com/Fribb/anime-lists).

One download gives both directions Addex needs:
- Kitsu -> IMDb, to group Kitsu's per-season entries under their IMDb series or movie;
- MAL -> Kitsu, to create a title when Stremio asks about a MyAnimeList ID.
"""

from dataclasses import dataclass
from typing import Any

import httpx

from addex_core.http import get_json

URL = "https://raw.githubusercontent.com/Fribb/anime-lists/master/anime-list-mini.json"


@dataclass(frozen=True)
class AnimeLists:
    kitsu_to_imdb: dict[str, str]
    mal_to_kitsu: dict[str, str]


def parse(entries: list[dict[str, Any]]) -> AnimeLists:
    """`imdb_id` is a list in the source; entries mapping to several IMDb titles are rare
    and ambiguous, so only the first is used. The first entry wins on duplicates."""
    kitsu_to_imdb: dict[str, str] = {}
    mal_to_kitsu: dict[str, str] = {}
    for entry in entries:
        kitsu, mal, imdb = entry.get("kitsu_id"), entry.get("mal_id"), entry.get("imdb_id")
        if isinstance(imdb, str):
            imdb = [imdb]
        if kitsu is None:
            continue
        if imdb:
            kitsu_to_imdb.setdefault(str(kitsu), imdb[0])
        if mal is not None:
            mal_to_kitsu.setdefault(str(mal), str(kitsu))
    return AnimeLists(kitsu_to_imdb=kitsu_to_imdb, mal_to_kitsu=mal_to_kitsu)


def parse_kitsu_to_imdb(entries: list[dict[str, Any]]) -> dict[str, str]:
    return parse(entries).kitsu_to_imdb


async def fetch(client: httpx.AsyncClient) -> AnimeLists:
    return parse(await get_json(client, URL, timeout=120))


async def fetch_kitsu_to_imdb(client: httpx.AsyncClient) -> dict[str, str]:
    return (await fetch(client)).kitsu_to_imdb
