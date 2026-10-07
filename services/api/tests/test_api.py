import pytest


def _search(db, world, client_for, q):
    async def scenario(session):
        w = await world(session)
        async with client_for(session) as client:
            resp = await client.get("/api/search", params={"q": q})
        return w, resp

    return db(scenario)


@pytest.mark.parametrize("q", ["attack", "Shingeki no", "atack on titan", "ATTACK ON TITAN"])
def test_search_returns_parent_once(db, world, client_for, q):
    w, resp = _search(db, world, client_for, q)
    assert resp.status_code == 200
    [hit] = resp.json()
    assert hit["id"] == w["aot"].id
    assert hit["entry_count"] == 2
    assert hit["popularity_rank"] == 1  # best rank across the group
    assert hit["addon_count"] == 2  # Torrentio + TPB; the disabled addon is not counted


def test_search_counts_only_answers_with_streams(db, world, client_for):
    _, resp = _search(db, world, client_for, "death note")
    assert [(h["name"], h["addon_count"]) for h in resp.json()] == [("Death Note", 0)]


@pytest.mark.parametrize("q", ["%%", "__", "zzzzzz"])
def test_search_no_match_and_like_escaping(db, world, client_for, q):
    assert _search(db, world, client_for, q)[1].json() == []


def test_search_validates_query(db, world, client_for):
    assert _search(db, world, client_for, "a")[1].status_code == 422


def test_title_detail_from_child_returns_group(db, world, client_for):
    async def scenario(session):
        w = await world(session)
        async with client_for(session) as client:
            body = (await client.get(f"/api/titles/{w['s2'].id}")).json()
            missing = await client.get("/api/titles/999999")
        return w, body, missing

    w, body, missing = db(scenario)
    assert missing.status_code == 404
    assert body["title"]["id"] == w["aot"].id
    assert [e["name"] for e in body["entries"]] == ["Attack on Titan", "Attack on Titan Season 2"]

    torrentio, tpb = body["addons"]  # "Gone" is disabled and not listed
    assert (torrentio["name"], torrentio["entries_with_streams"], torrentio["max_stream_count"]) \
        == ("Torrentio", 2, 30)
    assert torrentio["install_url"] == "stremio://torrentio.example/manifest.json"
    assert torrentio["web_install_url"] == (
        "https://web.stremio.com/#/addons?addon=https%3A%2F%2Ftorrentio.example%2Fmanifest.json"
    )
    assert (tpb["name"], tpb["has_streams"], tpb["entries_with_streams"]) == ("TPB Plus", True, 1)
    assert len(tpb["entries"]) == 2
