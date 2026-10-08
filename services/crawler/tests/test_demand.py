from datetime import UTC, datetime, timedelta

import httpx
from redis.asyncio import Redis
from sqlalchemy import select

from addex_core.animelists import AnimeLists
from addex_core.demand import DemandRequest, note_demand, pop_demand, seen_key, unknown_key
from addex_core.ids import IdScheme
from addex_core.manifest import StreamScope
from addex_core.models import Addon, AddonStatus, Availability, CheckStatus, Title, TitleId
from addex_core.testing import run
from addex_crawler import queue
from addex_crawler.demand import DiscoveryBudget, handle_demand
from addex_crawler.queue import Job

TT_KITSU = StreamScope(frozenset({"movie", "series"}), ("tt", "kitsu"))
TT_ONLY = StreamScope(frozenset({"movie", "series"}), ("tt",))


class StubMapping:
    def __init__(self, kitsu_to_imdb=None, mal_to_kitsu=None):
        self.data = AnimeLists(kitsu_to_imdb or {}, mal_to_kitsu or {})

    async def get(self, client):
        return self.data


def _addon(host, scope):
    return Addon(
        manifest_url=f"https://{host}/manifest.json", base_url=f"https://{host}",
        manifest_id=host, name=host, version="1", stream_scopes=[scope.to_json()], p2p=False,
        adult=False, manifest={}, status=AddonStatus.ACTIVE,
    )


def _cinemeta_and_kitsu(request: httpx.Request) -> httpx.Response:
    path = request.url.path
    if path == "/meta/series/tt9999999.json":
        return httpx.Response(200, json={"meta": {
            "id": "tt9999999", "imdb_id": "tt9999999", "type": "series", "name": "New Show",
            "releaseInfo": "2026", "genres": ["Drama"], "country": "Portugal",
        }})
    if path == "/meta/series/tt5000000.json":
        return httpx.Response(200, json={"meta": {
            "id": "tt5000000", "imdb_id": "tt5000000", "type": "series", "name": "Anime Show",
            "releaseInfo": "2020", "genres": ["Animation"], "country": "Japan",
        }})
    if path == "/api/edge/anime/42":
        return httpx.Response(200, json={"data": {
            "id": "42", "type": "anime",
            "attributes": {"canonicalTitle": "Anime Show S2", "titles": {}, "subtype": "TV",
                           "startDate": "2022-01-01", "popularityRank": 900, "nsfw": False},
            "relationships": {"mappings": {"data": []}},
        }, "included": []})
    return httpx.Response(404)


def _scenario(redis_url, db, body, handler=_cinemeta_and_kitsu, budget=None):
    async def scenario(session):
        redis = Redis.from_url(redis_url, decode_responses=True)
        try:
            async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
                async def handle(req, mapping=StubMapping()):
                    return await handle_demand(
                        session, redis, client, req, mapping, budget or DiscoveryBudget()
                    )

                return await body(session, redis, handle)
        finally:
            await redis.aclose()

    return db(scenario)


def test_note_demand_dedupes(redis_url):
    async def main():
        redis = Redis.from_url(redis_url, decode_responses=True)
        try:
            req = DemandRequest(IdScheme.IMDB, "tt1", "movie")
            assert await note_demand(redis, req)
            assert not await note_demand(redis, req)
            assert await pop_demand(redis, 0.1) == req
            assert await pop_demand(redis, 0.1) is None
        finally:
            await redis.aclose()

    run(main())


def test_unknown_imdb_title_is_created_and_probed(db, redis_url):
    async def body(session, redis, handle):
        session.add_all([_addon("a.example", TT_KITSU), _addon("b.example", TT_ONLY)])
        await session.flush()
        outcome = await handle(DemandRequest(IdScheme.IMDB, "tt9999999", "series"))
        assert outcome.status == "created"
        assert sorted(j.url for j in outcome.jobs) == [
            "https://a.example/stream/series/tt9999999:1:1.json",
            "https://b.example/stream/series/tt9999999:1:1.json",
        ]
        title = await session.scalar(select(Title).where(Title.name == "New Show"))
        assert (title.type, title.year, title.popularity_rank) == ("series", 2026, None)

    _scenario(redis_url, db, body)


def test_unknown_id_is_negatively_cached(db, redis_url):
    calls = []

    def handler(request):
        calls.append(request.url.path)
        return httpx.Response(404)

    async def body(session, redis, handle):
        req = DemandRequest(IdScheme.IMDB, "tt0000404", "movie")
        assert (await handle(req)).status == "unknown"
        assert await redis.exists(unknown_key(IdScheme.IMDB, "tt0000404"))
        n = len(calls)
        assert (await handle(req)).status == "unknown"
        assert len(calls) == n  # no second lookup

    _scenario(redis_url, db, body, handler)


def test_unsupported_scheme_and_budget(db, redis_url):
    async def body(session, redis, handle):
        assert (await handle(DemandRequest(IdScheme.TMDB, "1", "movie"))).status == "unsupported"
        req = DemandRequest(IdScheme.IMDB, "tt9999999", "series")
        await redis.set(seen_key(req.scheme, req.value), 1)
        assert (await handle(req)).status == "over_budget"
        # Forgotten, so the next request for it can try again.
        assert not await redis.exists(seen_key(req.scheme, req.value))

    _scenario(redis_url, db, body, budget=DiscoveryBudget(per_minute=0))


def test_known_title_queues_only_due_pairs_of_its_group(db, redis_url):
    now = datetime.now(UTC)

    async def body(session, redis, handle):
        a = _addon("a.example", TT_KITSU)
        parent = Title(type="series", name="P", is_anime=True,
                       external_ids=[TitleId(scheme=IdScheme.IMDB, value="tt100")])
        fresh = Title(type="series", name="S1", is_anime=True, parent=parent,
                      external_ids=[TitleId(scheme=IdScheme.KITSU, value="1")])
        stale = Title(type="series", name="S2", is_anime=True, parent=parent,
                      external_ids=[TitleId(scheme=IdScheme.KITSU, value="2")])
        session.add_all([a, parent, fresh, stale])
        await session.flush()
        session.add(Availability(addon_id=a.id, title_id=fresh.id, probe_id="kitsu:1:1",
                                 status=CheckStatus.OK, last_checked=now,
                                 next_check_at=now + timedelta(hours=6), consecutive_failures=0))
        await session.flush()

        # Asking for one season covers the whole group, minus what is still fresh.
        outcome = await handle(DemandRequest(IdScheme.KITSU, "1", "series"))
        assert outcome.status == "known"
        assert sorted(j.probe_id for j in outcome.jobs) == ["kitsu:2:1", "tt100:1:1"]

    _scenario(redis_url, db, body)


def test_unknown_kitsu_title_is_linked_to_its_imdb_parent(db, redis_url):
    async def body(session, redis, handle):
        session.add(_addon("a.example", TT_KITSU))
        await session.flush()
        outcome = await handle(DemandRequest(IdScheme.KITSU, "42", "series"),
                               mapping=StubMapping(kitsu_to_imdb={"42": "tt5000000"}))
        assert outcome.status == "created"
        child = await session.scalar(select(Title).where(Title.name == "Anime Show S2"))
        parent = await session.get(Title, child.parent_id)
        assert (parent.name, parent.is_anime, child.popularity_rank) == ("Anime Show", True, 900)
        assert sorted(j.probe_id for j in outcome.jobs) == ["kitsu:42:1", "tt5000000:1:1"]

    _scenario(redis_url, db, body)


def test_priority_enqueue_jumps_the_queue(redis_url):
    def job(title_id):
        return Job(1, title_id, f"tt{title_id}", f"https://a.example/{title_id}", "a.example")

    async def main():
        redis = Redis.from_url(redis_url, decode_responses=True)
        try:
            assert await queue.enqueue(redis, [job(1), job(2), job(3)], max_queue=3) == 3
            # Over the cap, and title 3 is already waiting at the back: both go first.
            assert await queue.enqueue(redis, [job(3), job(4)], max_queue=3, priority=True) == 2
            order = [(await queue.pop(redis, "a.example", 0.1)).title_id for _ in range(4)]
            assert order == [4, 3, 1, 2]
            assert await redis.llen(queue.queue_key("a.example")) == 0
        finally:
            await redis.aclose()

    run(main())


def test_unknown_mal_title_is_created_through_kitsu(db, redis_url):
    """mal:7 -> Kitsu 42 (anime-lists) -> title from Kitsu, linked to its IMDb parent.
    Kitsu's own mappings for 42 don't list MAL 7, so it is attached from the request."""

    async def body(session, redis, handle):
        session.add(_addon("a.example", StreamScope(frozenset({"series"}), ("tt", "kitsu", "mal:"))))
        await session.flush()
        mapping = StubMapping(kitsu_to_imdb={"42": "tt5000000"}, mal_to_kitsu={"7": "42"})
        outcome = await handle(DemandRequest(IdScheme.MAL, "7", "series"), mapping=mapping)
        assert outcome.status == "created"

        child = await session.scalar(
            select(Title).join(TitleId).where(TitleId.scheme == IdScheme.MAL, TitleId.value == "7")
        )
        assert child.name == "Anime Show S2"
        assert (await session.get(Title, child.parent_id)).name == "Anime Show"
        # Kitsu is preferred for anime, so the season is probed by its Kitsu ID.
        assert sorted(j.probe_id for j in outcome.jobs) == ["kitsu:42:1", "tt5000000:1:1"]

        # Asked again by MAL ID, it is now known directly.
        again = await handle(DemandRequest(IdScheme.MAL, "7", "series"), mapping=mapping)
        assert again.status == "known"

    _scenario(redis_url, db, body)


def test_mal_id_without_kitsu_entry_is_unknown(db, redis_url):
    async def body(session, redis, handle):
        outcome = await handle(DemandRequest(IdScheme.MAL, "999", "series"),
                               mapping=StubMapping(mal_to_kitsu={"7": "42"}))
        assert outcome.status == "unknown"
        assert await redis.exists(unknown_key(IdScheme.MAL, "999"))

    _scenario(redis_url, db, body)
