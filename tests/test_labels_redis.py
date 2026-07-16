"""
Integration test for the RedisCache implementation.

Skipped without WARDEN_TEST_REDIS_URL. Verifies the same asymmetric-
invalidation semantics that InMemoryCache exercises in test_labels.py.
"""

from __future__ import annotations

import os

import pytest

pytest.importorskip("redis")

import redis

from core.algebra import Subject
from core.labels import RedisCache

REDIS_URL = os.environ.get("WARDEN_TEST_REDIS_URL")
pytestmark = pytest.mark.skipif(
    REDIS_URL is None,
    reason="Set WARDEN_TEST_REDIS_URL to run Redis integration tests. "
    "See docker-compose.yml for a local dev Redis.",
)


@pytest.fixture()
def cache():
    client = redis.Redis.from_url(REDIS_URL, decode_responses=True)
    client.flushdb()
    yield RedisCache(client, ttl_seconds=60)
    client.flushdb()


def test_set_and_get(cache):
    principal = Subject("user", "alice")
    cache.set(principal, {1, 2, 3}, {10, 20})
    got = cache.get(principal)
    assert got == ({1, 2, 3}, {10, 20})


def test_get_miss_returns_none(cache):
    assert cache.get(Subject("user", "nobody")) is None


def test_invalidate_removes_entry(cache):
    principal = Subject("user", "alice")
    cache.set(principal, {1}, set())
    cache.invalidate(principal)
    assert cache.get(principal) is None


def test_ttl_applied(cache):
    """The TTL must be positive — this catches a bug where set() forgets
    to pass ex= and entries never expire (revocation lag becomes unbounded)."""
    principal = Subject("user", "alice")
    cache.set(principal, {1}, set())
    # Access the underlying client to verify TTL was set.
    ttl = cache._client.ttl(cache._key(principal))
    assert 0 < ttl <= 60
