"""Kitsu -> IMDb mapping from Fribb/anime-lists (https://github.com/Fribb/anime-lists)."""

from typing import Any

import httpx

from addex_core.http import get_json

URL = "https://raw.githubusercontent.com/Fribb/anime-lists/master/anime-list-mini.json"


def parse_kitsu_to_imdb(entries: list[dict[str, Any]]) -> dict[str, str]:
    """Kitsu ID -> IMDb ID. `imdb_id` is a list in the source; entries mapping to several
    IMDb titles are rare and ambiguous, so only the first is used."""
    mapping: dict[str, str] = {}
    for entry in entries:
        kitsu, imdb = entry.get("kitsu_id"), entry.get("imdb_id")
        if isinstance(imdb, str):
            imdb = [imdb]
        if kitsu is None or not imdb:
            continue
        mapping.setdefault(str(kitsu), imdb[0])
    return mapping


async def fetch_kitsu_to_imdb(client: httpx.AsyncClient) -> dict[str, str]:
    return parse_kitsu_to_imdb(await get_json(client, URL, timeout=120))
