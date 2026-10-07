import asyncio
from typing import Any

import httpx


async def get_json(
    client: httpx.AsyncClient,
    url: str,
    params: dict[str, str] | None = None,
    retries: int = 4,
    timeout: float = 20.0,
) -> Any:
    """GET a JSON document, retrying network errors, 429 and 5xx with exponential backoff."""
    for attempt in range(retries + 1):
        can_retry = attempt < retries
        try:
            resp = await client.get(url, params=params, timeout=timeout, follow_redirects=True)
        except httpx.TransportError:
            if not can_retry:
                raise
        else:
            if not (can_retry and (resp.status_code == 429 or resp.status_code >= 500)):
                resp.raise_for_status()
                return resp.json()
        await asyncio.sleep(2**attempt)
    raise AssertionError("unreachable")
