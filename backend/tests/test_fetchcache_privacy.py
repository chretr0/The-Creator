"""Credential handling at the cache/API boundary; no network or database required."""

import json
import os
import urllib.error

import pytest
from fastapi import HTTPException

from app import fetchcache
from app.config import settings
from app.routers import hazards

KEY = "unit-" + "test-" + "only"
URL = f"https://example.invalid/api/{KEY}/data"
CACHE_KEY = "public-test-cache-key"


@pytest.fixture(autouse=True)
def isolated_cache(monkeypatch, tmp_path):
    monkeypatch.setattr(settings, "cache_dir", str(tmp_path))
    fetchcache._memory.clear()
    yield
    fetchcache._memory.clear()


def fetch():
    return fetchcache.fetch("test", URL, ttl_s=60, cache_key=CACHE_KEY, sensitive_values=(KEY,))


def test_cache_does_not_persist_request_url_or_echoed_credentials(monkeypatch):
    monkeypatch.setattr(fetchcache, "_get", lambda *args: f"Upstream echoed {URL}")
    got = fetch()
    path = fetchcache._cache_path("test", CACHE_KEY)
    saved = json.loads(path.read_text())
    assert "url" not in saved
    assert KEY not in path.read_text()
    assert KEY not in got.body and "[REDACTED]" in got.body
    assert fetch().body == got.body  # memory hit
    fetchcache._memory.clear()
    assert fetch().body == got.body  # disk hit


@pytest.mark.parametrize("stale", [False, True])
def test_legacy_cached_body_and_stale_error_are_redacted(monkeypatch, stale):
    path = fetchcache._cache_path("test", CACHE_KEY)
    path.parent.mkdir(parents=True)
    path.write_text(json.dumps({"url": URL, "retrievedAt": "2026-01-01T00:00:00Z", "body": f"echo {KEY}"}))
    if stale:
        os.utime(path, (1, 1))

    def offline(*args):
        raise urllib.error.URLError(f"failed {URL}")

    monkeypatch.setattr(fetchcache, "_get", offline)
    got = fetch()
    assert got.stale is stale
    assert KEY not in got.body
    assert KEY not in (got.error or "")
    assert "[REDACTED]" in got.body


def test_successful_fire_csv_is_unchanged(monkeypatch):
    csv = "latitude,longitude,acq_date,acq_time\n51.5,-0.1,2026-01-01,1200\n"
    monkeypatch.setattr(fetchcache, "_get", lambda *args: csv)
    assert fetch().body == csv


def test_fires_does_not_return_upstream_error_text(monkeypatch):
    monkeypatch.setattr(settings, "firms_map_key", KEY)

    def response(*args, **kwargs):
        assert kwargs["sensitive_values"] == (KEY,)
        return fetchcache.Fetched(f"Rejected credential {KEY}", "2026-01-01T00:00:00Z", False)

    monkeypatch.setattr(fetchcache, "fetch", response)
    with pytest.raises(HTTPException) as error:
        hazards.fires(west=0, south=0, east=1, north=1, days=1)
    assert error.value.status_code == 502
    assert KEY not in error.value.detail
    assert "Rejected credential" not in error.value.detail
