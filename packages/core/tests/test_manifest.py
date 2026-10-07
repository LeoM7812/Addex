import asyncio

import httpx
import pytest

from addex_core.manifest import (
    ManifestError,
    StreamScope,
    fetch_manifest,
    normalize_manifest_url,
    parse_manifest,
)

# Shaped like Torrentio: short resource names inheriting top-level types/prefixes.
TORRENTIO_LIKE = {
    "id": "com.stremio.torrentio.addon",
    "version": "0.0.14",
    "name": "Torrentio",
    "resources": ["stream"],
    "types": ["movie", "series", "anime", "other"],
    "catalogs": [],
    "idPrefixes": ["tt", "kitsu"],
    "behaviorHints": {"configurable": True, "configurationRequired": False},
}

# Shaped like Anime Kitsu: catalog + meta only.
KITSU_LIKE = {
    "id": "community.anime.kitsu",
    "version": "0.0.10",
    "name": "Anime Kitsu",
    "resources": ["catalog", {"name": "meta", "types": ["anime"], "idPrefixes": ["kitsu"]}],
    "types": ["anime", "movie", "series"],
    "catalogs": [{"type": "anime", "id": "kitsu-anime-trending"}],
}

URL = "https://addon.example/manifest.json"


def test_short_resource_inherits_top_level_scope():
    m = parse_manifest(TORRENTIO_LIKE, URL)
    assert m.stream_scopes == (
        StreamScope(frozenset({"movie", "series", "anime", "other"}), ("tt", "kitsu")),
    )
    assert m.supports_stream("movie", "tt0111161")
    assert m.supports_stream("series", "kitsu:1376:1")
    assert not m.supports_stream("series", "mal:5114:1")
    assert not m.supports_stream("tv", "tt0111161")


def test_meta_only_addon_has_no_stream_scopes():
    m = parse_manifest(KITSU_LIKE, URL)
    assert not m.provides_streams
    assert not m.supports_stream("anime", "kitsu:1")


def test_object_resource_overrides_and_falls_back():
    data = {
        **TORRENTIO_LIKE,
        "resources": [
            {"name": "stream", "types": ["movie"], "idPrefixes": ["tmdb:"]},
            {"name": "stream", "types": ["series"]},  # prefixes fall back to top level
            {"name": "subtitles", "types": ["movie"]},
        ],
    }
    m = parse_manifest(data, URL)
    assert m.supports_stream("movie", "tmdb:550")
    assert not m.supports_stream("movie", "tt0137523")
    assert m.supports_stream("series", "tt0944947:1:1")
    assert m.stream_types == {"movie", "series"}


def test_missing_id_prefixes_accepts_any_id_but_empty_list_accepts_none():
    no_prefixes = {**TORRENTIO_LIKE}
    del no_prefixes["idPrefixes"]
    assert parse_manifest(no_prefixes, URL).supports_stream("movie", "whatever:1")

    empty = {**TORRENTIO_LIKE, "idPrefixes": []}
    assert not parse_manifest(empty, URL).supports_stream("movie", "tt0111161")


def test_flags_and_urls():
    data = {**TORRENTIO_LIKE, "behaviorHints": {"p2p": True, "configurationRequired": True}}
    m = parse_manifest(data, "https://addon.example/providers=yts/manifest.json")
    assert m.p2p and m.configuration_required and not m.adult
    assert m.base_url == "https://addon.example/providers=yts"
    assert m.install_url == "stremio://addon.example/providers=yts/manifest.json"
    assert (
        m.stream_url("series", "kitsu:1376:1")
        == "https://addon.example/providers=yts/stream/series/kitsu:1376:1.json"
    )


def test_scope_json_roundtrip():
    for scope in parse_manifest(TORRENTIO_LIKE, URL).stream_scopes:
        assert StreamScope.from_json(scope.to_json()) == scope
    any_id = StreamScope(frozenset({"movie"}), None)
    assert StreamScope.from_json(any_id.to_json()) == any_id


@pytest.mark.parametrize(
    "raw, expected",
    [
        ("https://a.example/manifest.json", "https://a.example/manifest.json"),
        ("stremio://a.example/manifest.json", "https://a.example/manifest.json"),
        ("https://a.example/", "https://a.example/manifest.json"),
        ("  https://a.example/cfg/manifest.json?x=1 ", "https://a.example/cfg/manifest.json"),
    ],
)
def test_normalize_manifest_url(raw, expected):
    assert normalize_manifest_url(raw) == expected


def test_normalize_rejects_non_http():
    with pytest.raises(ManifestError):
        normalize_manifest_url("ftp://a.example/manifest.json")


@pytest.mark.parametrize(
    "data",
    [
        [],
        {"id": "x", "name": "x", "resources": []},  # no version
        {**TORRENTIO_LIKE, "resources": "stream"},
    ],
)
def test_invalid_manifests(data):
    with pytest.raises(ManifestError):
        parse_manifest(data, URL)


def _fetch(handler) -> object:
    async def run():
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            return await fetch_manifest(client, "stremio://addon.example")

    return asyncio.run(run())


def test_fetch_manifest():
    def handler(request: httpx.Request) -> httpx.Response:
        assert str(request.url) == "https://addon.example/manifest.json"
        return httpx.Response(200, json=TORRENTIO_LIKE)

    assert _fetch(handler).name == "Torrentio"


@pytest.mark.parametrize(
    "response",
    [
        httpx.Response(404),
        httpx.Response(200, text="<html>"),
        httpx.Response(200, json={"a": "x" * 600_000}),
    ],
)
def test_fetch_manifest_errors(response):
    with pytest.raises(ManifestError):
        _fetch(lambda request: response)


def test_fetch_manifest_network_error():
    def handler(request):
        raise httpx.ConnectTimeout("timed out")

    with pytest.raises(ManifestError, match="ConnectTimeout"):
        _fetch(handler)
