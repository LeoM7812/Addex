"""Configurable addon, addon catalogs, cache headers, manifest and pages."""

import pytest

from addex_api.userconfig import UserConfig


def _get(db, world, client_for, path):
    """GET `path`, or `path(world)` when it depends on IDs of the scenario's rows (which
    differ on every run: sequences don't roll back)."""

    async def scenario(session):
        w = await world(session)
        async with client_for(session) as client:
            return w, await client.get(path(w) if callable(path) else path)

    return db(scenario)


def _cfg(w, have=(), hide_p2p=False) -> str:
    return UserConfig(have=frozenset(w[name].id for name in have), hide_p2p=hide_p2p).encode()


def test_user_config_roundtrip():
    cfg = UserConfig(have=frozenset({3, 1}), hide_p2p=True)
    assert UserConfig.decode(cfg.encode()) == cfg
    assert UserConfig.decode(UserConfig().encode()) == UserConfig()
    assert UserConfig.decode("garbage") is None
    assert UserConfig.decode("WzFd") is None  # base64 of "[1]": JSON, but not an object


def test_manifest(db, world, client_for, monkeypatch):
    monkeypatch.setenv("ADDEX_CONTACT_EMAIL", "addex@example.org")
    _, resp = _get(db, world, client_for, "/manifest.json")
    body = resp.json()
    assert resp.headers["cache-control"] == "max-age=3600, public"
    assert "cacheMaxAge" not in body
    assert body["logo"] == "http://test/static/logo.png"
    assert body["contactEmail"] == "addex@example.org"
    assert body["behaviorHints"] == {"configurable": True}
    # Stremio requests addon catalogs by addonCatalogs (type + id).
    assert [(c["type"], c["id"]) for c in body["addonCatalogs"]] == [
        ("all", "addex-top"), ("all", "addex-anime")]
    # Prefixes only on the stream resource: stremio-core doesn't inherit them.
    assert "idPrefixes" not in body
    assert body["resources"][0] == {"name": "stream", "types": ["movie", "series"],
                                    "idPrefixes": ["tt", "kitsu:", "mal:"]}


def test_configured_manifest_and_bad_config(db, world, client_for):
    token = UserConfig(have=frozenset({1})).encode()
    assert _get(db, world, client_for, f"/{token}/manifest.json")[1].status_code == 200
    assert _get(db, world, client_for, "/not-a-config/manifest.json")[1].status_code == 404


def test_stream_cache_headers(db, world, client_for):
    _, hit = _get(db, world, client_for, "/stream/series/tt2560140:1:1.json")
    _, miss = _get(db, world, client_for, "/stream/movie/tt0000001.json")
    assert hit.headers["cache-control"] == \
        "max-age=3600, stale-while-revalidate=14400, stale-if-error=604800, public"
    assert hit.json()["staleError"] == 604800
    assert miss.headers["cache-control"] == "max-age=60, public"


def test_stream_respects_config(db, world, client_for):
    def installs(**cfg):
        def path(w):
            return f"/{_cfg(w, **cfg)}/stream/series/tt2560140:1:1.json"

        streams = _get(db, world, client_for, path)[1].json()["streams"]
        return [s["description"].splitlines()[0] for s in streams]

    assert installs() == ["Install Torrentio", "Install TPB Plus"]
    assert installs(have=["torrentio"]) == ["Install TPB Plus"]
    # Torrentio is P2P because the crawler saw torrents, not because it says so.
    assert installs(hide_p2p=True) == ["Install TPB Plus"]
    assert installs(have=["tpb"], hide_p2p=True) == []


@pytest.mark.parametrize("catalog", ["addex-top", "addex-anime"])
def test_addon_catalog(db, world, client_for, catalog):
    _, resp = _get(db, world, client_for, f"/addon_catalog/all/{catalog}.json")
    addons = resp.json()["addons"]
    # Torrentio answered 2 titles, both with streams; TPB 1 of 2. "Gone" is disabled.
    assert [a["transportUrl"] for a in addons] == [
        "https://torrentio.example/manifest.json", "https://tpbplus.example/manifest.json"]
    assert addons[0]["transportName"] == "http"
    what = "anime titles" if catalog == "addex-anime" else "titles"
    assert addons[0]["manifest"]["description"].splitlines() == [
        f"Addex: streams for 100% of 2 {what} checked.", "", "Torrentio addon"]
    assert addons[1]["manifest"]["description"].startswith("Addex: streams for 50% of 2")
    assert resp.headers["cache-control"].startswith("max-age=21600")


def test_addon_catalog_config_and_unknown(db, world, client_for):
    def path(w):
        return f"/{_cfg(w, have=['torrentio'])}/addon_catalog/all/addex-top.json"

    addons = _get(db, world, client_for, path)[1].json()["addons"]
    assert [a["manifest"]["name"] for a in addons] == ["TPB Plus"]
    for unknown in ["/addon_catalog/all/nope.json", "/addon_catalog/movie/addex-top.json"]:
        assert _get(db, world, client_for, unknown)[1].json()["addons"] == []


def test_api_addons(db, world, client_for):
    body = _get(db, world, client_for, "/api/addons")[1].json()
    assert [(a["name"], a["p2p"], a["coverage"], a["titles_checked"]) for a in body] == [
        ("Torrentio", True, 1.0, 2), ("TPB Plus", False, 0.5, 2)]
    assert body[0]["web_install_url"].startswith("https://web.stremio.com/#/addons?addon=")


def test_pages(db, world, client_for):
    landing = _get(db, world, client_for, "/")[1]
    assert landing.status_code == 200 and "text/html" in landing.headers["content-type"]
    assert "const initial = null;" in landing.text
    assert _get(db, world, client_for, "/configure")[1].status_code == 200

    def path(w):
        return f"/{_cfg(w, have=['torrentio'], hide_p2p=True)}/configure"

    w, configured = _get(db, world, client_for, path)
    expected = f'const initial = {{"have": [{w["torrentio"].id}], "hide_p2p": true}};'
    assert expected in configured.text
    assert _get(db, world, client_for, "/nope/configure")[1].status_code == 404


def test_static_images(db, world, client_for):
    for name in ("logo.png", "background.png"):
        resp = _get(db, world, client_for, f"/static/{name}")[1]
        assert resp.status_code == 200 and resp.content.startswith(b"\x89PNG")


def test_small_samples_rank_after_real_ones():
    from addex_api.queries import AddonStats
    from addex_core.models import Addon

    solid = AddonStats(Addon(name="Solid"), answered=100, with_streams=60,
                       anime_answered=0, anime_with_streams=0)
    lucky = AddonStats(Addon(name="Lucky"), answered=2, with_streams=2,
                       anime_answered=0, anime_with_streams=0)
    assert sorted([lucky, solid], key=lambda s: s.rank_key(anime=False)) == [solid, lucky]
