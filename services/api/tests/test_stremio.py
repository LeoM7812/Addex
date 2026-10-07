import pytest

from addex_api.routes_stremio import parse_stremio_id
from addex_core.demand import DemandRequest
from addex_core.ids import IdScheme


@pytest.mark.parametrize(
    "sid, expected",
    [
        ("tt2560140", (IdScheme.IMDB, "tt2560140")),
        ("tt2560140:2:5", (IdScheme.IMDB, "tt2560140")),
        ("kitsu:7442", (IdScheme.KITSU, "7442")),
        ("kitsu:7442:12", (IdScheme.KITSU, "7442")),
        ("mal:16498:1", (IdScheme.MAL, "16498")),
        ("yt_id:abc", None),
        ("tt12x", None),
    ],
)
def test_parse_stremio_id(sid, expected):
    assert parse_stremio_id(sid) == expected


def _get(db, world, client_for, path):
    async def scenario(session):
        await world(session)
        async with client_for(session) as client:
            return await client.get(path)

    return db(scenario)


def test_manifest(db, world, client_for):
    body = _get(db, world, client_for, "/manifest.json").json()
    assert body["resources"] == ["stream"] and body["catalogs"] == []


@pytest.mark.parametrize(
    "path",
    ["/stream/series/tt2560140:3:1.json", "/stream/series/kitsu:8671:4.json",
     "/stream/series/kitsu%3A7442%3A1.json"],
)
def test_stream_lists_addons_to_install(db, world, client_for, path):
    body = _get(db, world, client_for, path).json()
    assert body["cacheMaxAge"] == 3600
    streams = body["streams"]
    assert [s["externalUrl"] for s in streams] == [
        "https://web.stremio.com/#/addons?addon=https%3A%2F%2Ftorrentio.example%2Fmanifest.json",
        "https://web.stremio.com/#/addons?addon=https%3A%2F%2Ftpbplus.example%2Fmanifest.json",
    ]
    assert streams[0]["description"] == "Install Torrentio\n30 streams · P2P · confirmed 1 h ago"
    # TPB's newest check (1 h ago) was an empty entry; its streams were confirmed 50 h ago.
    assert streams[1]["description"].endswith("5 streams · P2P · confirmed 2 days ago")
    # Addex never hands out a playable source.
    for s in streams:
        assert not {"url", "infoHash", "ytId"} & s.keys()
        # stremio-core treats `title` as an alias of `description`; both = parse error.
        assert not {"title", "description"} <= s.keys()


@pytest.mark.parametrize(
    "path", ["/stream/movie/tt0000001.json", "/stream/series/kitsu:1376:1.json",
             "/stream/series/garbage.json"],
)
def test_stream_without_results(db, world, client_for, path):
    # Unknown title, a title whose only check timed out, an unparseable ID.
    body = _get(db, world, client_for, path).json()
    assert body["streams"] == []
    assert body["cacheMaxAge"] == 60  # a check may have just been queued


def test_cors_header(db, world, client_for):
    async def scenario(session):
        await world(session)
        async with client_for(session) as client:
            return await client.get("/manifest.json", headers={"Origin": "https://web.stremio.com"})

    assert db(scenario).headers["access-control-allow-origin"] == "*"


def test_private_network_preflight(db, world, client_for):
    """Stremio's UI is a public https page; Chromium preflights its requests to a
    localhost addon and blocks them unless the server opts in."""

    async def scenario(session):
        async with client_for(session) as client:
            return await client.options("/manifest.json", headers={
                "Origin": "https://web.stremio.com",
                "Access-Control-Request-Method": "GET",
                "Access-Control-Request-Private-Network": "true",
            })

    resp = db(scenario)
    assert resp.status_code == 200
    assert resp.headers["access-control-allow-private-network"] == "true"


def test_stream_requests_note_demand(db, world, client_for):
    noted = []

    async def scenario(session):
        await world(session)
        async with client_for(session, noted) as client:
            await client.get("/stream/series/tt2560140:3:1.json")
            await client.get("/stream/movie/tt0000001.json")  # unknown: noted too
            await client.get("/stream/series/garbage.json")  # unparseable: not noted

    db(scenario)
    assert noted == [
        DemandRequest(IdScheme.IMDB, "tt2560140", "series"),
        DemandRequest(IdScheme.IMDB, "tt0000001", "movie"),
    ]
