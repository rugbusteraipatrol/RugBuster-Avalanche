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

def test_a_fresh_score_names_each_timestamp_for_what_it_is():
    """Three moments, three names. A scalar timestamp must not imply that every
    upstream provider looked at the token at that instant."""
    with mock.patch.object(server, "score_with_private_engine", return_value=_report()):
        body = _get()
    assert body["data_freshness"] == FRESH
    assert body["computed_at"] <= body["served_at"]
    assert body["verdict_age_seconds"] == 0
    assert body["observed_at"] is None, "no upstream gives us a verified observation time"
    assert body["observation_coverage"] == "COMPUTATION_TIME_ONLY"
    assert body["age_seconds"] is None, "age_seconds means evidence age, which we do not have"


def test_serving_from_cache_does_not_move_the_computation_time_forward():
    """The reason these fields are separate."""
    with mock.patch.object(server, "score_with_private_engine", return_value=_report()):
        first = _get()
    time.sleep(0.01)
    with mock.patch.object(server, "score_with_private_engine") as engine:
        second = _get()
    assert engine.call_count == 0, "the second request should have been a cache hit"
    assert second["computed_at"] == first["computed_at"]
    assert second["served_at"] > first["served_at"]
    assert second["verdict_age_seconds"] >= 0


def test_fresh_equals_one_forces_a_recompute_and_a_new_computation_time():
    with mock.patch.object(server, "score_with_private_engine", return_value=_report()):
        first = _get()
        time.sleep(0.01)
        second = _get("&fresh=1")
    assert second["computed_at"] > first["computed_at"]


# --- an entry whose age cannot be established ------------------------------

def test_a_cache_entry_without_a_usable_timestamp_is_not_served_as_current():
    """Belt and braces behind TTL eviction: if an entry ever reaches the
    response path without a usable timestamp, it must not read as current."""
    with mock.patch.object(server, "score_with_private_engine", return_value=_report()):
        _get()
    key = server.cache_key(WAVAX)
    server.SCAN_CACHE[key]["computed_at"] = None
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


# --- withholding must neutralise every field that carries a verdict --------

def test_withholding_blanks_every_verdict_field_not_just_the_label():
    """Found by self-review, and it was live: this module was written for the
    Solana response shape (`label`/`risk_score`) and reused here, where the
    verdict also lives in `rug_score`, `rug_status` and the speculation pair.
    Blanking only the label left a caller reading `rug_score` the stale number.
    """
    from freshness import withhold_verdict

    stale = {
        "label": "GOOD",
        "rug_score": 12,
        "rug_status": "LOW",
        "speculation_score": 30,
        "speculation_status": "LOW",
        "token_name": "Example",
    }
    out = withhold_verdict(stale, assess(None, now=NOW))

    assert out["label"] == "UNKNOWN"
    for field in ("rug_score", "rug_status", "speculation_score", "speculation_status"):
        assert out[field] is None, f"{field} still carries the withheld verdict"
        assert out[f"last_known_{field}"] == stale[field]
    assert out["token_name"] == "Example", "non-verdict fields must survive"


def test_no_verdict_field_survives_withholding_on_a_real_response():
    """The generic version: whatever the response shape is, nothing that could
    be read as a current verdict may keep its value."""
    from freshness import VERDICT_FIELDS, withhold_verdict

    with mock.patch.object(server, "score_with_private_engine", return_value=_report()):
        served = _get()
    withheld = withhold_verdict(served, assess(None, now=NOW))

    for field in VERDICT_FIELDS:
        if field not in served or served[field] is None:
            continue
        assert withheld[field] in (None, "UNKNOWN"), (
            f"{field} survived withholding with {withheld[field]!r}"
        )


# --- every route that returns a verdict must say which build produced it ----

def test_the_scan_route_carries_identity():
    with mock.patch.object(server, "score_with_private_engine", return_value=_report()), \
         mock.patch.object(server, "fetch_deepseek_verdict", return_value=None):
        body = server.app.test_client().post(
            "/api/scan", json={"address": WAVAX}
        ).get_json()
    assert body.get("build_commit"), "/api/scan returned a verdict with no build identity"


def test_the_portfolio_route_carries_identity():
    with mock.patch.object(server, "fetch_portfolio_tokens", return_value=[]), \
         mock.patch.object(server, "build_portfolio_reports", return_value=[]):
        body = server.app.test_client().post(
            "/api/portfolio", json={"address": WAVAX}
        ).get_json()
    assert body.get("build_commit"), "/api/portfolio returned a verdict with no build identity"


def test_an_invalid_wallet_response_carries_identity():
    body = server.app.test_client().post(
        "/api/portfolio", json={"address": "nonsense"}
    ).get_json()
    assert body["ok"] is False
    assert "build_commit" in body


# --- a read failure is not a verdict, and a withheld verdict says why -------

def test_a_failed_chain_read_is_never_reported_as_not_a_token():
    """A rate-limited RPC used to reach the caller as NOT_A_TOKEN -- a claim
    about the contract, made on the strength of our failure to read it.

    The two are opposite claims: one is about our reach, the other about the
    address. `call_optional` returned None for both, and the caller could not
    tell them apart."""
    import server

    class _RaisingCall:
        @staticmethod
        def call():
            raise ConnectionError("rpc")

    class _Failing:
        class functions:
            @staticmethod
            def name():
                return _RaisingCall

    value, error = server.call_optional(_Failing, "name")
    assert value is None
    assert error == "ConnectionError", "the reason must survive the call"


def test_the_read_error_names_the_field_that_failed():
    import server

    report = server.insufficient_data_report(
        "0x0000000000000000000000000000000000000001",
        "The chain could not be read for this address: name=ConnectionError",
    )
    assert report["label"] == "INSUFFICIENT_DATA"
    assert report["rug_status"] == "INSUFFICIENT_DATA"
    assert any("ConnectionError" in gap for gap in report["blocking_data_gaps"])
    assert report.get("source") != "not_a_token_guard"


def test_every_withheld_verdict_carries_its_reason():
    """A caller seeing INSUFFICIENT_DATA had no way to tell which check was
    missing: the engine computed blocking_data_gaps and the API dropped it."""
    import server

    report = server.insufficient_data_report(
        "0x0000000000000000000000000000000000000001", "engine unreachable")
    assert report["blocking_data_gaps"] == ["engine unreachable"]


def test_a_partly_throttled_read_is_retried_before_any_claim():
    """The first version of this fix required all four metadata calls to fail
    before it would say "unavailable". A partly-throttled read -- two calls
    through, two not -- slipped back into NOT_A_TOKEN, which is the claim it
    was written to prevent.

    One attempt cannot separate them: web3 raises the same error when a
    contract has no such function and when the node returns nothing. What
    separates them is repetition, so the read is retried once and only a second
    failure may become a finding.
    """
    import server

    calls = {"n": 0}

    class _Call:
        @staticmethod
        def call():
            calls["n"] += 1
            if calls["n"] == 1:
                raise ConnectionError("throttled")
            return 18

    class _Recovers:
        class functions:
            @staticmethod
            def decimals():
                return _Call

    first, first_error = server.call_optional(_Recovers, "decimals")
    assert first is None and first_error == "ConnectionError"
    second, second_error = server.call_optional(_Recovers, "decimals")
    assert second == 18 and second_error is None, (
        "a retried read must be able to succeed, or the retry proves nothing"
    )


def test_the_retry_pause_is_short_enough_to_serve_a_caller():
    import server
    assert 0 < server.RETRY_PAUSE_SECONDS <= 3


def test_a_failure_on_a_deciding_field_is_unavailable_not_a_finding():
    """`is_probable_erc20` rests on decimals and totalSupply. An earlier
    version of this rule asked whether *any* field had been read, so a contract
    whose name came through and whose decimals did not was still declared "not
    a token" -- on two reads that failed. Seen in the local run."""
    import server
    from web3 import Web3

    class _Field:
        def __init__(self, value=None, error=None):
            self.value, self.error = value, error

        def call(self):
            if self.error:
                raise self.error
            return self.value

    class _Token:
        class functions:
            name = staticmethod(lambda: _Field("Some Token"))
            symbol = staticmethod(lambda: _Field("SOME"))
            decimals = staticmethod(lambda: _Field(error=ConnectionError("throttled")))
            totalSupply = staticmethod(lambda: _Field(error=ConnectionError("throttled")))

    class _Eth:
        @staticmethod
        def get_code(_address):
            return b"\x60\x80"

        @staticmethod
        def contract(address=None, abi=None):
            return _Token

    class _Web3:
        eth = _Eth

    original_sleep = server.time.sleep
    server.time.sleep = lambda _s: None
    try:
        metadata = server.get_onchain_metadata(
            _Web3, Web3.to_checksum_address("0x0000000000000000000000000000000000000001"))
    finally:
        server.time.sleep = original_sleep

    assert metadata["read_failed"] is True, (
        "decimals and totalSupply both failed; nothing about the address was established"
    )
    assert metadata["read_errors"]["decimals"] == "ConnectionError"


def test_a_field_the_contract_simply_does_not_expose_is_an_answer():
    """None with no error is the contract answering. That is a finding and
    must stay one, or every non-token becomes 'unavailable'."""
    import server
    from web3 import Web3

    class _Empty:
        def call(self):
            return None

    class _Token:
        class functions:
            name = staticmethod(lambda: _Empty())
            symbol = staticmethod(lambda: _Empty())
            decimals = staticmethod(lambda: _Empty())
            totalSupply = staticmethod(lambda: _Empty())

    class _Eth:
        @staticmethod
        def get_code(_address):
            return b"\x60\x80"

        @staticmethod
        def contract(address=None, abi=None):
            return _Token

    class _Web3:
        eth = _Eth

    metadata = server.get_onchain_metadata(
        _Web3, Web3.to_checksum_address("0x0000000000000000000000000000000000000002"))
    assert metadata["read_failed"] is False
    assert metadata["is_probable_erc20"] is False


def test_the_public_response_carries_the_reason_a_verdict_was_withheld():
    """Wiring it into the report was not enough: /score returns a projection,
    and the field stopped there. A caller saw INSUFFICIENT_DATA with no reason,
    which is the whole complaint."""
    import server

    report = {
        "address": "0x0000000000000000000000000000000000000001",
        "label": "INSUFFICIENT_DATA",
        "blocking_data_gaps": ["holder_concentration"],
        "rug_status": "INSUFFICIENT_DATA",
    }
    response = server.compact_score_response(report, "private_scoring_engine")
    assert response["blocking_data_gaps"] == ["holder_concentration"]


def test_the_field_is_present_even_when_nothing_was_blocking():
    """Absent and empty are different answers, and a caller should not have to
    tell them apart by whether a key exists."""
    import server

    response = server.compact_score_response(
        {"address": "0x0000000000000000000000000000000000000001", "label": "GOOD"},
        "private_scoring_engine")
    assert response["blocking_data_gaps"] == []
