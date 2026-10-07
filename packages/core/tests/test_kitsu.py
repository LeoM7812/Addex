import asyncio

import httpx
import pytest

from addex_core import http, kitsu
from addex_core.ids import IdScheme


def _anime(kid, rank, subtype="TV", nsfw=False, mapping_ids=()):
    return {
        "id": kid,
        "type": "anime",
        "attributes": {
            "canonicalTitle": f"Anime {kid}",
            "titles": {"en": f"Anime {kid}", "en_jp": f"Anime-chan {kid}", "ja_jp": None},
            "abbreviatedTitles": [f"A{kid}", None],
            "subtype": subtype,
            "startDate": "2013-04-07",
            "popularityRank": rank,
            "posterImage": {"medium": f"https://media.example/{kid}.jpg"},
            "nsfw": nsfw,
        },
        "relationships": {"mappings": {"data": [{"type": "mappings", "id": m} for m in mapping_ids]}},
    }


MAPPINGS = [
    {"id": "m1", "type": "mappings", "attributes": {"externalSite": "myanimelist/anime", "externalId": "16498"}},
    {"id": "m2", "type": "mappings", "attributes": {"externalSite": "anilist/anime", "externalId": "16498"}},
    {"id": "m3", "type": "mappings", "attributes": {"externalSite": "thetvdb", "externalId": "267440"}},
]


def test_parse_page():
    page = {
        "data": [
            _anime("7442", 1, mapping_ids=("m1", "m2", "m3")),
            _anime("2", 2, subtype="movie"),
            _anime("3", 3, subtype="music"),
            _anime("4", 4, nsfw=True),
        ],
        "included": MAPPINGS,
    }
    aot, movie = kitsu.parse_page(page)
    assert aot.ids == {IdScheme.KITSU: "7442", IdScheme.MAL: "16498", IdScheme.ANILIST: "16498"}
    assert (aot.type, aot.year, aot.popularity_rank, aot.is_anime) == ("series", 2013, 1, True)
    assert aot.aliases == ("Anime 7442", "Anime-chan 7442", "A7442")
    assert aot.poster == "https://media.example/7442.jpg"
    assert movie.type == "movie" and movie.ids == {IdScheme.KITSU: "2"}


def _fetch(handler, limit):
    async def main():
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            return await kitsu.fetch_top_anime(client, limit=limit, delay=0)

    return asyncio.run(main())


def _paged(pages):
    def handler(request: httpx.Request):
        offset = int(request.url.params["page[offset]"])
        return httpx.Response(200, json={"data": pages.get(offset, [])})

    return handler


def test_fetch_dedupes_across_pages_and_respects_limit():
    pages = {
        0: [_anime(str(i), i) for i in range(1, 21)],
        20: [_anime("20", 20)] + [_anime(str(i), i) for i in range(21, 40)],
    }
    seeds = _fetch(_paged(pages), limit=30)
    ids = [s.ids[IdScheme.KITSU] for s in seeds]
    assert ids == [str(i) for i in range(1, 31)]


def test_fetch_stops_when_pages_run_out():
    assert len(_fetch(_paged({0: [_anime("1", 1)]}), limit=500)) == 1


def test_fetch_retries_rate_limit(monkeypatch):
    async def no_sleep(_):
        pass

    monkeypatch.setattr(http.asyncio, "sleep", no_sleep)
    calls = []

    def handler(request):
        calls.append(1)
        if len(calls) == 1:
            return httpx.Response(429)
        return _paged({0: [_anime("1", 1)]})(request)

    assert len(_fetch(handler, limit=1)) == 1
    assert len(calls) == 2


def test_fetch_gives_up_after_retries(monkeypatch):
    async def no_sleep(_):
        pass

    monkeypatch.setattr(http.asyncio, "sleep", no_sleep)
    with pytest.raises(httpx.HTTPStatusError):
        _fetch(lambda request: httpx.Response(503), limit=1)
