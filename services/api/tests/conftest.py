from datetime import UTC, datetime, timedelta

import httpx
import pytest

from addex_api.app import create_app
from addex_api.deps import get_note_demand, get_session
from addex_core.ids import IdScheme
from addex_core.models import Addon, AddonStatus, Availability, CheckStatus, Title, TitleId
from addex_core.testing import _test_database, db  # noqa: F401

NOW = datetime.now(UTC)


def _client_for(session, demand: list | None = None) -> httpx.AsyncClient:
    """An HTTP client for the app, bound to the test's session. Demand notes are
    appended to `demand` instead of going to Redis."""
    app = create_app()

    async def override():
        yield session

    def note_demand():
        async def note(req):
            if demand is not None:
                demand.append(req)

        return note

    app.dependency_overrides[get_session] = override
    app.dependency_overrides[get_note_demand] = note_demand
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test")


def _addon(name, status=AddonStatus.ACTIVE, p2p=False, p2p_observed=False):
    host = name.lower().replace(" ", "")
    return Addon(
        manifest_url=f"https://{host}.example/manifest.json", base_url=f"https://{host}.example",
        manifest_id=host, name=name, version="1", stream_scopes=[], p2p=p2p,
        p2p_observed=p2p_observed, adult=False,
        manifest={"id": host, "name": name, "description": f"{name} addon"}, status=status,
    )


def _title(name, type_="series", rank=None, parent=None, aliases=(), **ids):
    return Title(
        type=type_, name=name, aliases=list(aliases), popularity_rank=rank, is_anime=True,
        parent=parent,
        external_ids=[TitleId(scheme=IdScheme(s), value=v) for s, v in ids.items()],
    )


def _check(addon, title, has_streams, count, status=CheckStatus.OK, hours_ago=1):
    checked = NOW - timedelta(hours=hours_ago)
    answered = status in (CheckStatus.OK, CheckStatus.EMPTY)
    return Availability(
        addon=addon, title=title, probe_id="-", status=status, has_streams=has_streams,
        stream_count=count, latency_ms=100, last_checked=checked,
        last_answered=checked if answered else None,
        next_check_at=NOW + timedelta(hours=6), consecutive_failures=0,
    )


@pytest.fixture
def client_for():
    return _client_for


@pytest.fixture
def world():
    """Builds a small catalogue in the session and returns its objects by name."""

    async def build(session):
        # Torrentio doesn't declare P2P but the crawler saw torrents; TPB is direct links.
        torrentio, tpb = _addon("Torrentio", p2p_observed=True), _addon("TPB Plus")
        gone = _addon("Gone", status=AddonStatus.DISABLED)
        aot = _title("Attack on Titan", rank=40, imdb="tt2560140")
        s1 = _title("Attack on Titan", rank=1, parent=aot,
                    aliases=["Shingeki no Kyojin"], kitsu="7442")
        s2 = _title("Attack on Titan Season 2", rank=20, parent=aot,
                    aliases=["Shingeki no Kyojin 2"], kitsu="8671")
        death_note = _title("Death Note", rank=6, kitsu="1376")
        session.add_all([
            torrentio, tpb, gone, aot, s1, s2, death_note,
            _check(torrentio, s1, True, 30),
            _check(torrentio, s2, True, 12),
            _check(tpb, aot, True, 5, hours_ago=50),
            _check(tpb, s1, False, 0, CheckStatus.EMPTY),
            _check(gone, aot, True, 99),
            _check(torrentio, death_note, None, None, CheckStatus.TIMEOUT),
        ])
        await session.flush()
        return {"torrentio": torrentio, "tpb": tpb, "gone": gone, "aot": aot, "s1": s1,
                "s2": s2, "death_note": death_note}

    return build
