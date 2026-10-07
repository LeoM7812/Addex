import os

import pytest
from redis import Redis as SyncRedis
from redis.exceptions import ConnectionError as RedisConnectionError

from addex_core.testing import _test_database, db, run  # noqa: F401

TEST_REDIS_URL = os.environ.get("ADDEX_TEST_REDIS_URL", "redis://localhost:6379/15")


@pytest.fixture
def redis_url() -> str:
    """A flushed Redis database for the test. Skips if Redis is unreachable."""
    client = SyncRedis.from_url(TEST_REDIS_URL)
    try:
        client.flushdb()
    except RedisConnectionError as e:
        pytest.skip(f"test redis unavailable: {e}")
    yield TEST_REDIS_URL
    client.flushdb()
    client.close()
