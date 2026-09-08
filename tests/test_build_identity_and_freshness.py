"""Which build answered, and how old is what it answered with.

Ported from the Solana service after an independent review found `/score` there
returning a stored row of any age as a current verdict.

**That specific defect is not live here, and the difference is worth stating
rather than papering over.** AVAX `/score` consults only the 180-second
in-memory cache and otherwise recomputes, so it cannot serve year-old evidence.
`lookup_cached_score` does query `avax_scans` with no age bound, but nothing
calls it and it did not even select `created_at` -- it could not have aged a row
if it tried. It is bounded here so that wiring it up later cannot recreate the
Solana failure.

What was genuinely missing on this service:

* no build commit anywhere, so a review could not tell which code answered;
* no `observed_at` / `fetched_at`, so a caller could not tell a just-computed
  verdict from one at the end of its window;
* `cache_key` carried only `DATA_CONTRACT_VERSION`, which describes the response
  shape -- editing `risk_engine.py` changed scores while the key stayed
  identical, and the cache kept serving the previous rules.
"""

from __future__ import annotations

import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest import mock

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "api"))
sys.path.insert(0, str(REPO_ROOT / "chains" / "avalanche"))

import server  # noqa: E402
from freshness import FRESH, INVALID, STALE, UNDATED, assess  # noqa: E402

WAVAX = "0xb31f66aa3c1e785363f0875a1b74e27b85fd66c7"
NOW = datetime(2026, 9, 8, 12, 0, 0, tzinfo=timezone.utc)


@pytest.fixture(autouse=True)
def _clear_cache():
    server.SCAN_CACHE.clear()
    yield
    server.SCAN_CACHE.clear()


def _report(label="GOOD", score=12):
    return {
        "ok": True,
        "address": server.Web3.to_checksum_address(WAVAX),
        "label": label,
        "rug_score": score,
        "source": "private_scoring_engine",
        "engine_version": "2026.09.1",
        "completeness_pct": 100,
        "missing_inputs": [],
        "verdict_is_conclusive": True,
    }


def _get(query=""):
    client = server.app.test_client()
    return client.get(f"/score?address={WAVAX}{query}").get_json()


# --- build identity --------------------------------------------------------

def test_health_reports_the_build_and_both_versions():
    with mock.patch.object(server, "validate_known_token_metadata", return_value={"ok": True}), \
         mock.patch.object(server, "collector_routescan_api_health", return_value={"ok": True}), \
         mock.patch.object(server, "get_web3"):
        body = server.app.test_client().get("/health").get_json()
    assert "build_commit" in body
    assert body["data_contract_version"] == server.DATA_CONTRACT_VERSION
    assert body["local_engine_version"] == server.LOCAL_ENGINE_VERSION


def test_an_unknown_build_stays_unknown_rather_than_being_guessed():
    with mock.patch.dict("os.environ", {}, clear=True), \
         mock.patch("subprocess.run", side_effect=OSError("no git")):
        import build_identity

        build_identity.build_commit.cache_clear()
        assert build_identity.build_commit() == "unknown"
        build_identity.build_commit.cache_clear()


def test_a_scored_response_carries_identity():
    with mock.patch.object(server, "score_with_private_engine", return_value=_report()):
        body = _get()
    assert body["build_commit"]
    assert body["local_engine_version"] == server.LOCAL_ENGINE_VERSION


def test_an_invalid_address_response_carries_identity():
    body = server.app.test_client().get("/score?address=notanaddress").get_json()
    assert body["ok"] is False
    assert "build_commit" in body


# --- the cache key must move when the scoring rules move -------------------

def test_the_cache_key_changes_with_the_local_engine_version():
    before = server.cache_key(WAVAX)
    with mock.patch.object(server, "LOCAL_ENGINE_VERSION", "9999.99.9"):
        after = server.cache_key(WAVAX)
    assert before != after, (
        "a scoring change must not keep serving the previous rules' verdicts"
    )


def test_the_cache_key_changes_with_the_data_contract_version():
    before = server.cache_key(WAVAX)
    with mock.patch.object(server, "DATA_CONTRACT_VERSION", "9999.99.9"):
        assert server.cache_key(WAVAX) != before


# --- observed_at vs fetched_at ---------------------------------------------

def test_a_fresh_score_reports_both_timestamps():
    with mock.patch.object(server, "score_with_private_engine", return_value=_report()):
        body = _get()
    assert body["data_freshness"] == FRESH
    assert body["observed_at"] <= body["fetched_at"]
    assert body["age_seconds"] == 0


def test_serving_from_cache_does_not_move_the_observation_forward():
    """The reason these two fields are separate."""
    with mock.patch.object(server, "score_with_private_engine", return_value=_report()):
        first = _get()
    time.sleep(0.01)
    with mock.patch.object(server, "score_with_private_engine") as engine:
        second = _get()
    assert engine.call_count == 0, "the second request should have been a cache hit"
    assert second["observed_at"] == first["observed_at"]
    assert second["fetched_at"] > first["fetched_at"]
    assert second["age_seconds"] >= 0


def test_fresh_equals_one_forces_a_recompute_and_a_new_observation():
    with mock.patch.object(server, "score_with_private_engine", return_value=_report()):
        first = _get()
        time.sleep(0.01)
        second = _get("&fresh=1")
    assert second["observed_at"] > first["observed_at"]


# --- an entry whose age cannot be established ------------------------------

def test_a_cache_entry_without_a_usable_timestamp_is_not_served_as_current():
    """Belt and braces behind TTL eviction: if an entry ever reaches the
    response path without a usable timestamp, it must not read as current."""
    with mock.patch.object(server, "score_with_private_engine", return_value=_report()):
        _get()
    key = server.cache_key(WAVAX)
    server.SCAN_CACHE[key]["observed_at"] = None
    with mock.patch.object(server, "score_with_private_engine") as engine:
        body = _get()
        assert engine.call_count == 0
    assert body["label"] == "UNKNOWN"
    assert body["last_known_label"] == "GOOD"
    assert body["data_freshness"] == UNDATED


# --- the freshness primitive, on this repo's constants ---------------------

def test_the_memory_window_boundary_is_defined_on_both_sides():
    inside = assess(NOW - server.MEMORY_CACHE_MAX_AGE, now=NOW)
    outside = assess(NOW - server.MEMORY_CACHE_MAX_AGE - timedelta(seconds=1), now=NOW)
    assert inside["freshness"] == FRESH
    assert outside["freshness"] == STALE


def test_a_future_timestamp_is_invalid_not_fresh():
    assert assess(NOW + timedelta(days=1), now=NOW)["freshness"] == INVALID


# --- the dead stored-scan path, bounded so it cannot be wired up unsafely ---

def test_the_stored_scan_lookup_is_still_unused():
    """If this starts failing, the path below is live and needs real coverage."""
    server_source = (REPO_ROOT / "api" / "server.py").read_text(encoding="utf-8")
    calls = server_source.count("lookup_cached_score(")
    assert calls == 1, (
        "lookup_cached_score is now called somewhere; it was dead code when "
        "these bounds were added, and a live caller needs tests of its own."
    )


def test_an_old_stored_scan_row_would_be_withheld_not_served():
    old = datetime.now(timezone.utc) - timedelta(days=400)
    cursor = mock.MagicMock()
    cursor.fetchone.return_value = ({"label": "GOOD", "rug_score": 5}, old)
    cursor.__enter__ = mock.Mock(return_value=cursor)
    cursor.__exit__ = mock.Mock(return_value=False)
    conn = mock.MagicMock()
    conn.cursor.return_value = cursor
    conn.__enter__ = mock.Mock(return_value=conn)
    conn.__exit__ = mock.Mock(return_value=False)

    with mock.patch.object(server, "DATABASE_URL", "postgres://x"), \
         mock.patch.object(server, "psycopg2", mock.Mock(connect=mock.Mock(return_value=conn))):
        result = server.lookup_cached_score(WAVAX)

    assert result["label"] == "UNKNOWN"
    assert result["data_freshness"] == STALE


def test_a_recent_stored_scan_row_would_be_served():
    recent = datetime.now(timezone.utc) - timedelta(hours=1)
    cursor = mock.MagicMock()
    cursor.fetchone.return_value = ({"label": "GOOD", "rug_score": 5}, recent)
    cursor.__enter__ = mock.Mock(return_value=cursor)
    cursor.__exit__ = mock.Mock(return_value=False)
    conn = mock.MagicMock()
    conn.cursor.return_value = cursor
    conn.__enter__ = mock.Mock(return_value=conn)
    conn.__exit__ = mock.Mock(return_value=False)

    with mock.patch.object(server, "DATABASE_URL", "postgres://x"), \
         mock.patch.object(server, "psycopg2", mock.Mock(connect=mock.Mock(return_value=conn))):
        result = server.lookup_cached_score(WAVAX)

    assert result["data_freshness"] == FRESH
    assert result["label"] != "UNKNOWN"
