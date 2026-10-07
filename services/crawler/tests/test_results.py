from datetime import UTC, datetime, timedelta

import pytest

from addex_core.models import Addon, AddonStatus, CheckStatus, Title
from addex_crawler.probe import ProbeResult
from addex_crawler.queue import Job
from addex_crawler.results import next_check_delay, record_result


@pytest.mark.parametrize(
    "rank, expected",
    [(1, timedelta(hours=6)), (150, timedelta(hours=12)), (900, timedelta(days=1)),
     (5000, timedelta(days=3)), (None, timedelta(days=3))],
)
def test_ttl_by_popularity(rank, expected):
    assert next_check_delay(rank, answered=True, consecutive_failures=0) == expected


def test_failure_backoff_is_exponential_and_capped():
    delays = [next_check_delay(1, False, n) for n in range(1, 7)]
    assert delays[:4] == [timedelta(minutes=m) for m in (15, 30, 60, 120)]
    assert delays[-1] == timedelta(hours=6)


def test_failure_keeps_last_answer(db):
    t0 = datetime(2026, 10, 7, 12, tzinfo=UTC)

    async def scenario(session):
        addon = Addon(manifest_url="https://a.example/manifest.json", base_url="https://a.example",
                      manifest_id="x", name="A", version="1", stream_scopes=[], p2p=False,
                      adult=False, manifest={}, status=AddonStatus.ACTIVE)
        title = Title(type="movie", name="T", is_anime=False, popularity_rank=1)
        session.add_all([addon, title])
        await session.flush()
        job = Job(addon.id, title.id, "tt1", "https://a.example/stream/movie/tt1.json",
                  "a.example", popularity_rank=1)

        row = await record_result(
            session, job, ProbeResult(CheckStatus.OK, 300, 200, stream_count=12), t0
        )
        assert (row.has_streams, row.stream_count, row.last_answered) == (True, 12, t0)
        assert row.next_check_at == t0 + timedelta(hours=6)

        t1 = t0 + timedelta(hours=7)
        for n in (1, 2):
            row = await record_result(session, job, ProbeResult(CheckStatus.TIMEOUT, 10000), t1)
        assert row.status == CheckStatus.TIMEOUT and row.consecutive_failures == 2
        assert (row.has_streams, row.stream_count, row.last_answered) == (True, 12, t0)
        assert row.next_check_at == t1 + timedelta(minutes=30)

        row = await record_result(
            session, job, ProbeResult(CheckStatus.EMPTY, 200, 200, stream_count=0), t1
        )
        assert (row.has_streams, row.stream_count, row.consecutive_failures) == (False, 0, 0)

    db(scenario)
