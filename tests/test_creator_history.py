"""Deployer history must come from the database, and say when it cannot.

`avax_collector_v6.get_creator_stats` reads a dict the collector fills while it
crawls. The API runs in a different process, so that dict is always empty there
and every scan reported `total: 0, danger: 0, rug_rate: 0.0` -- identical for a
deployer with 573 DANGER results out of 593 tokens and for one nobody has ever
seen. The product's central claim was reading from the wrong place.

The second half is the failure this whole day was about: `total: 0` could not
say *why*. An unreachable database and a genuinely new deployer produced the
same three numbers, and the scorer treats a 0% rug rate as reassuring, so an
outage read as a clean record.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from unittest import mock

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "api"))
sys.path.insert(0, str(REPO_ROOT / "chains" / "avalanche"))

import server  # noqa: E402


@pytest.fixture(autouse=True)
def _clear_cache():
    server.CREATOR_HISTORY_CACHE.clear()
    yield
    server.CREATOR_HISTORY_CACHE.clear()


def _db_module(row):
    """A stand-in psycopg2 whose query returns `row`."""
    cursor = mock.MagicMock()
    cursor.fetchone.return_value = row
    cursor.__enter__ = mock.Mock(return_value=cursor)
    cursor.__exit__ = mock.Mock(return_value=False)
    conn = mock.MagicMock()
    conn.cursor.return_value = cursor
    conn.__enter__ = mock.Mock(return_value=conn)
    conn.__exit__ = mock.Mock(return_value=False)
    return mock.Mock(connect=mock.Mock(return_value=conn))


def _with_db(row):
    """Patch psycopg2 so the query returns `row`."""
    return mock.patch.object(server, "psycopg2", _db_module(row))


# --- the signal that was missing ---

def test_a_deployer_we_have_repeatedly_flagged_is_now_visible():
    """0x1f6908b7... in production: 573 DANGER out of 593 tokens."""
    with mock.patch.object(server, "DATABASE_URL", "postgres://x"), _with_db((593, 573)):
        stats = server.lookup_creator_stats("0x1f6908b79ae1f2c87c16f0facc9084d93601c8eb")
    assert stats["total"] == 593
    assert stats["danger"] == 573
    assert stats["prior_danger_rate_pct"] == 96.6  # our own DANGER labels, not confirmed rugs
    assert stats["status"] == "OK"


def test_a_clean_deployer_with_a_real_record_reads_as_clean():
    with mock.patch.object(server, "DATABASE_URL", "postgres://x"), _with_db((40, 0)):
        stats = server.lookup_creator_stats("0xabc")
    assert stats["prior_danger_rate_pct"] == 0.0
    assert stats["status"] == "OK"


# --- an absent record must not read as a clean one ---

def test_database_unreachable_is_not_a_clean_history():
    failing = mock.Mock(connect=mock.Mock(side_effect=OSError("connection refused")))
    with mock.patch.object(server, "DATABASE_URL", "postgres://x"), \
         mock.patch.object(server, "psycopg2", failing):
        stats = server.lookup_creator_stats("0xabc")
    assert stats["status"] == "FETCH_FAILED"
    assert stats["prior_danger_rate_pct"] == 0.0        # shape unchanged...
    assert stats["status_reason"]          # ...but no longer a claim


def test_database_not_configured_is_not_a_clean_history():
    with mock.patch.object(server, "DATABASE_URL", ""):
        stats = server.lookup_creator_stats("0xabc")
    assert stats["status"] == "FETCH_FAILED"


def test_a_deployer_with_no_prior_tokens_is_not_found_not_failed():
    """A first-time deployer is a finding. It is not a hole in the scan."""
    with mock.patch.object(server, "DATABASE_URL", "postgres://x"), _with_db((0, 0)):
        stats = server.lookup_creator_stats("0xabc")
    assert stats["status"] == "NOT_FOUND"
    assert stats["total"] == 0


def test_missing_deployer_is_not_queried():
    assert server.lookup_creator_stats("")["status"] == "NOT_QUERIED"


# --- things that would make the rate lie ---

def test_a_factory_contract_is_excluded():
    """Trader Joe deploys for whoever calls it; its record is the chain's."""
    stats = server.lookup_creator_stats("0x9AD6c38BE94206cA50bb0d90783181662f0Cfa10")
    assert stats["status"] == "NOT_QUERIED"
    assert "factory" in stats["status_reason"]
    assert stats["prior_danger_rate_pct"] == 0.0


def test_one_prior_token_does_not_become_a_100_percent_danger_rate():
    """One DANGER out of one is arithmetic, not a record."""
    with mock.patch.object(server, "DATABASE_URL", "postgres://x"), _with_db((1, 1)):
        stats = server.lookup_creator_stats("0xabc")
    assert stats["total"] == 1
    assert stats["danger"] == 1
    assert stats["prior_danger_rate_pct"] == 0.0
    assert stats["status"] == "NOT_FOUND"
    assert "below the" in stats["status_reason"]


def test_the_rate_starts_counting_at_the_documented_threshold():
    with mock.patch.object(server, "DATABASE_URL", "postgres://x"), _with_db((3, 3)):
        stats = server.lookup_creator_stats("0xabc")
    assert stats["prior_danger_rate_pct"] == 100.0
    assert stats["status"] == "OK"


# --- caching must not outlive its usefulness or mask failures ---

def test_a_successful_lookup_is_cached():
    """A second scan of the same deployer must not hit the database again."""
    module = _db_module((10, 5))
    with mock.patch.object(server, "DATABASE_URL", "postgres://x"),          mock.patch.object(server, "psycopg2", module):
        server.lookup_creator_stats("0xabc")
        server.lookup_creator_stats("0xabc")
    assert module.connect.call_count == 1


def test_a_failed_lookup_is_not_cached():
    """An outage must not pin a FETCH_FAILED verdict for the whole TTL."""
    failing = mock.Mock(connect=mock.Mock(side_effect=OSError("down")))
    with mock.patch.object(server, "DATABASE_URL", "postgres://x"), \
         mock.patch.object(server, "psycopg2", failing):
        server.lookup_creator_stats("0xabc")
    assert "0xabc" not in server.CREATOR_HISTORY_CACHE


def test_lookup_is_case_insensitive_on_the_deployer_address():
    with mock.patch.object(server, "DATABASE_URL", "postgres://x"), _with_db((20, 4)):
        upper = server.lookup_creator_stats("0xABCDEF")
    with mock.patch.object(server, "DATABASE_URL", "postgres://x"), _with_db((20, 4)):
        lower = server.lookup_creator_stats("0xabcdef")
    assert upper["prior_danger_rate_pct"] == lower["prior_danger_rate_pct"] == 20.0


# --- the name must keep saying whose labels these are ----------------------

def test_the_field_is_not_called_a_rug_rate():
    """An independent review named this: counting our own earlier DANGER
    verdicts and calling the ratio a rug rate presents the scanner's own
    guesses as confirmed events, so one mistake can harden into a record and
    justify the next. The name is the only thing carrying that distinction."""
    with mock.patch.object(server, "DATABASE_URL", "postgres://x"), _with_db((100, 90)):
        stats = server.lookup_creator_stats("0xabc")
    assert "prior_danger_rate_pct" in stats
    assert "rug_rate" not in stats


def test_the_user_facing_reason_does_not_claim_confirmed_rugs():
    import sys as _sys
    _sys.path.insert(0, str(REPO_ROOT / "chains" / "avalanche"))
    from risk_engine import score_rug_risk

    result = score_rug_risk({
        "name": "Example", "symbol": "EX", "decimals": 18,
        "total_supply": 10**24, "holders_count": 5000, "token_age_days": 400,
        "has_liquidity_evidence": True, "liquidity_usd": 2_000_000,
        "creator_prior_danger_rate": 90.0,
    })
    joined = " ".join(result.reasons).lower()
    assert "rug rate" not in joined, "the reason still claims confirmed rug events"
    assert "flagged by this scanner" in joined


# --- the collector says it too, or the rename only half happened -----------
#
# The rename landed in `api/` and in the risk engine, but the collector keeps
# its own deployer figure and its own wording, and the API serves the text the
# collector stored (`reasons` on the scan record). So after the rename a reader
# could still be told "96.6% rug rate" -- by the other producer. These pin the
# collector to the same words, and to the same name for the same number.

def _collector():
    import sys as _sys
    _sys.path.insert(0, str(REPO_ROOT / "chains" / "avalanche"))
    import avax_collector_v6

    return avax_collector_v6


def test_the_collector_reports_the_figure_under_the_honest_name():
    collector = _collector()
    stats = collector.get_creator_stats("")
    assert "prior_danger_rate_pct" in stats
    # `rug_rate` survives only as an alias for existing readers.
    assert stats["rug_rate"] == stats["prior_danger_rate_pct"]


def test_the_collector_reason_does_not_claim_confirmed_rugs():
    collector = _collector()
    _score, reasons = collector.calculate_rugbuster_avax_risk(
        token_info={"holders_count": 5000},
        cia_intel={},
        v5={},
        v6={},
        creator_stats={"total": 30, "danger": 27, "prior_danger_rate_pct": 90.0,
                       "rug_rate": 90.0, "status": "OK"},
        deployer_balance=5.0,
    )
    joined = " ".join(reasons).lower()
    assert "rug rate" not in joined, "the collector still claims confirmed rug events"
    assert "flagged by this scanner" in joined


def test_the_stored_training_record_does_not_claim_confirmed_rugs():
    collector = _collector()
    record = collector.build_training_record_v6(
        contract_address="0xdead",
        token_info={"name": "Example", "symbol": "EX", "holders_count": 10},
        deployer="0xabc",
        deploy_timestamp=0,
        creator_stats={"total": 30, "danger": 27, "prior_danger_rate_pct": 90.0,
                       "rug_rate": 90.0, "status": "OK"},
        cia_intel={},
        v5={},
        v6={},
        label="DANGER",
        risk_flags=[],
        risk_percent=88,
    )
    blob = json.dumps(record).lower()
    assert "rug rate" not in blob, "the stored record still claims confirmed rug events"


def test_the_collector_reads_the_new_name_when_the_alias_is_gone():
    """The API's lookup returns no `rug_rate` at all. A collector that only
    knew the old name would read every deployer as 0%."""
    collector = _collector()
    _score, reasons = collector.calculate_rugbuster_avax_risk(
        token_info={"holders_count": 5000},
        cia_intel={},
        v5={},
        v6={},
        creator_stats={"total": 30, "danger": 27, "prior_danger_rate_pct": 90.0,
                       "status": "OK"},
        deployer_balance=5.0,
    )
    assert any("90.0%" in reason for reason in reasons)
