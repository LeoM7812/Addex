import asyncio

import httpx

from addex_core import cinemeta
from addex_core.ids import IdScheme


def _meta(n, **extra):
    return {"id": f"tt{n:07d}", "imdb_id": f"tt{n:07d}", "name": f"Title {n}",
            "releaseInfo": "2020–", "poster": f"https://img.example/{n}", **extra}


def test_parse_meta():
    seed = cinemeta.parse_meta(_meta(1), "series", rank=3)
    assert seed.ids == {IdScheme.IMDB: "tt0000001"}
    assert (seed.type, seed.name, seed.year, seed.popularity_rank) == ("series", "Title 1", 2020, 3)
    assert not seed.is_anime
    assert cinemeta.parse_meta({"id": "kitsu:1", "name": "x"}, "series", 1) is None
    assert cinemeta.parse_meta(_meta(2, releaseInfo=None, year=None), "movie", 1).year is None


def test_anime_heuristic():
    assert cinemeta.is_anime({"genres": ["Animation", "Action"], "country": "Japan"})
    assert not cinemeta.is_anime({"genres": ["Animation"], "country": "United States"})
    assert not cinemeta.is_anime({"genres": ["Drama"], "country": "Japan"})
    assert cinemeta.is_anime({"genres": ["Animation"], "country": "Japan, United States"})
    assert not cinemeta.is_anime({"genres": ["Animation"], "country": "United States, Mexico, Japan"})


def test_fetch_top_paginates_dedupes_and_ranks():
    pages = {
        "/catalog/movie/top.json": {"metas": [_meta(i) for i in range(1, 4)], "hasMore": True},
        "/catalog/movie/top/skip=3.json": {
            "metas": [_meta(3), {"id": "bad"}, _meta(4), _meta(5)], "hasMore": False,
        },
    }
    seen = []

    def handler(request: httpx.Request):
        seen.append(request.url.path)
        return httpx.Response(200, json=pages[request.url.path])

    async def main():
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            return await cinemeta.fetch_top(client, "movie", limit=10, delay=0)

    seeds = asyncio.run(main())
    assert [(s.ids[IdScheme.IMDB], s.popularity_rank) for s in seeds] == [
        (f"tt{i:07d}", i) for i in range(1, 6)
    ]
    assert seen == list(pages)  # stopped on hasMore=False
