from pathlib import Path

from sqlalchemy import select

from addex_core.manifest import ManifestError, parse_manifest
from addex_core.models import Addon, AddonStatus
from addex_core.registry import load_registry, sync_registry
from test_manifest import KITSU_LIKE, TORRENTIO_LIKE

A = "https://a.example/manifest.json"
B = "https://b.example/manifest.json"


def test_load_registry_normalizes_and_dedupes(tmp_path: Path):
    path = tmp_path / "addons.yaml"
    path.write_text(
        "addons:\n"
        "  - url: https://a.example/\n"
        "  - stremio://a.example/manifest.json\n"
        "  - url: https://b.example/manifest.json\n"
    )
    assert load_registry(path) == [A, B]


def test_sync_inserts_updates_and_disables(db):
    async def scenario(session):
        first = await sync_registry(
            session, [A, B], [parse_manifest(TORRENTIO_LIKE, A), parse_manifest(KITSU_LIKE, B)]
        )
        assert (first.added, first.updated) == ([A, B], [])

        addons = {a.manifest_url: a for a in await session.scalars(select(Addon))}
        assert addons[A].status == AddonStatus.ACTIVE
        assert addons[A].stream_scopes[0]["id_prefixes"] == ["tt", "kitsu"]
        assert addons[B].status == AddonStatus.NO_STREAMS

        # A fails to fetch, B is dropped from the registry.
        second = await sync_registry(session, [A], [ManifestError("boom")])
        assert second.failed == [(A, "boom")] and second.disabled == [B]
        assert addons[A].status == AddonStatus.BROKEN and addons[A].last_error == "boom"
        assert addons[A].name == "Torrentio"  # last good manifest kept
        assert addons[B].status == AddonStatus.DISABLED

        # Both come back.
        third = await sync_registry(
            session, [A, B], [parse_manifest(TORRENTIO_LIKE, A), parse_manifest(KITSU_LIKE, B)]
        )
        assert third.updated == [A, B]
        assert addons[A].status == AddonStatus.ACTIVE and addons[A].last_error is None
        assert addons[B].status == AddonStatus.NO_STREAMS

    db(scenario)


def test_sync_skips_new_addon_that_fails(db):
    async def scenario(session):
        report = await sync_registry(session, [A], [ManifestError("down")])
        assert report.failed and not report.added
        assert (await session.scalars(select(Addon))).all() == []

    db(scenario)


def test_needs_config_status(db):
    async def scenario(session):
        data = {**TORRENTIO_LIKE, "behaviorHints": {"configurationRequired": True}}
        await sync_registry(session, [A], [parse_manifest(data, A)])
        addon = await session.scalar(select(Addon))
        assert addon.status == AddonStatus.NEEDS_CONFIG

    db(scenario)
