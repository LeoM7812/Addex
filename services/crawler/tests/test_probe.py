import asyncio

import httpx
import pytest

from addex_core.models import CheckStatus
from addex_crawler.probe import count_streams, probe

URL = "https://addon.example/stream/movie/tt0111161.json"


def _probe(handler):
    async def main():
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            return await probe(client, URL, timeout=1)

    return asyncio.run(main())


def test_counts_only_playable_streams():
    body = {"streams": [
        {"infoHash": "abc", "fileIdx": 0},
        {"url": "https://cdn.example/x.mp4"},
        {"externalUrl": "https://netflix.example/title/1"},
        {"name": "[RD] configure me", "title": "Add your debrid key"},
        "garbage",
    ]}
    assert count_streams(body) == 3


@pytest.mark.parametrize("body", [[], {"streams": "nope"}, {"metas": []}, None])
def test_count_streams_rejects_non_stream_bodies(body):
    assert count_streams(body) is None


@pytest.mark.parametrize(
    "response, status, count",
    [
        (httpx.Response(200, json={"streams": [{"infoHash": "a"}, {"infoHash": "b"}]}),
         CheckStatus.OK, 2),
        (httpx.Response(200, json={"streams": []}), CheckStatus.EMPTY, 0),
        (httpx.Response(200, json={"streams": [{"title": "configure me"}]}),
         CheckStatus.EMPTY, 0),
        (httpx.Response(200, text="<html>"), CheckStatus.INVALID, None),
        (httpx.Response(200, json={"error": "x"}), CheckStatus.INVALID, None),
        (httpx.Response(404), CheckStatus.HTTP_ERROR, None),
    ],
)
def test_probe_statuses(response, status, count):
    result = _probe(lambda request: response)
    assert (result.status, result.stream_count) == (status, count)
    assert result.latency_ms is not None


def test_torrent_flag():
    torrent = _probe(lambda r: httpx.Response(200, json={"streams": [{"url": "u"}, {"infoHash": "a"}]}))
    direct = _probe(lambda r: httpx.Response(200, json={"streams": [{"url": "u"}]}))
    assert torrent.torrent and not direct.torrent


def test_probe_timeout_and_connection_error():
    def timeout(request):
        raise httpx.ReadTimeout("slow")

    def refused(request):
        raise httpx.ConnectError("refused")

    assert _probe(timeout).status == CheckStatus.TIMEOUT
    assert _probe(refused).status == CheckStatus.ERROR
    assert _probe(timeout).host_failure and _probe(refused).host_failure


def test_host_failure_classification():
    assert _probe(lambda r: httpx.Response(503)).host_failure
    assert not _probe(lambda r: httpx.Response(404)).host_failure
    assert not _probe(lambda r: httpx.Response(200, text="x")).host_failure


def test_rate_limited_reads_retry_after():
    result = _probe(lambda r: httpx.Response(429, headers={"Retry-After": "120"}))
    assert result.rate_limited and result.retry_after == 120
    assert _probe(lambda r: httpx.Response(429)).retry_after is None
