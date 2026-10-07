"""One request to an addon's /stream/ endpoint, reduced to counts.

The response body is inspected only to count streams and is then dropped: no URL,
magnet or info hash leaves this function.
"""

import time
from dataclasses import dataclass

import httpx

from addex_core.models import CheckStatus

# Keys that make a Stremio stream object playable. Streams with none of them are
# informational ("configure your debrid", "no results") and are not counted.
SOURCE_KEYS = frozenset({
    "url", "ytId", "infoHash", "externalUrl",
    "nzbUrl", "rarUrls", "zipUrls", "7zipUrls", "tgzUrls", "tarUrls",
})


@dataclass(frozen=True)
class ProbeResult:
    status: CheckStatus
    latency_ms: int | None = None
    http_status: int | None = None
    stream_count: int | None = None
    retry_after: float | None = None  # seconds, from a 429

    @property
    def answered(self) -> bool:
        """The addon gave a definitive yes/no for this title."""
        return self.status in (CheckStatus.OK, CheckStatus.EMPTY)

    @property
    def rate_limited(self) -> bool:
        return self.http_status == 429

    @property
    def host_failure(self) -> bool:
        """Failures that say the host is unhealthy (feeds the circuit breaker). A 404 or a
        malformed body is the addon answering badly, not the host being down."""
        if self.status in (CheckStatus.TIMEOUT, CheckStatus.ERROR):
            return True
        return self.status == CheckStatus.HTTP_ERROR and (
            self.rate_limited or (self.http_status or 0) >= 500
        )


def count_streams(body: object) -> int | None:
    """Playable streams in a /stream/ response, or None if it isn't one."""
    if not isinstance(body, dict) or not isinstance(body.get("streams"), list):
        return None
    return sum(1 for s in body["streams"] if isinstance(s, dict) and SOURCE_KEYS & s.keys())


def _retry_after(resp: httpx.Response) -> float | None:
    try:
        return float(resp.headers["retry-after"])
    except (KeyError, ValueError):
        return None


async def probe(client: httpx.AsyncClient, url: str, timeout: float = 10.0) -> ProbeResult:
    start = time.perf_counter()

    def elapsed() -> int:
        return round((time.perf_counter() - start) * 1000)

    try:
        resp = await client.get(url, timeout=timeout, follow_redirects=True)
    except httpx.TimeoutException:
        return ProbeResult(CheckStatus.TIMEOUT, elapsed())
    except httpx.HTTPError:
        return ProbeResult(CheckStatus.ERROR, elapsed())
    latency = elapsed()

    if resp.status_code != 200:
        return ProbeResult(
            CheckStatus.HTTP_ERROR, latency, resp.status_code,
            retry_after=_retry_after(resp) if resp.status_code == 429 else None,
        )
    try:
        count = count_streams(resp.json())
    except ValueError:
        count = None
    if count is None:
        return ProbeResult(CheckStatus.INVALID, latency, 200)
    status = CheckStatus.OK if count else CheckStatus.EMPTY
    return ProbeResult(status, latency, 200, stream_count=count)
