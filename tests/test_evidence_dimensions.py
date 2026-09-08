"""The five dimensions must each answer only their own question.

Step 3 of the agreed repair order is deliberately additive: it separates what
the scanner claims without changing any verdict. Most of this file is therefore
about what must *not* happen -- a dimension inferring from another, a missing
reading defaulting to something reassuring, or our own past labels being dressed
up as confirmed events.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "api"))

from evidence import (  # noqa: E402
    NOT_COLLECTED,
    OK,
    UNKNOWN,
    build_evidence,
    coverage,
    creator_history,
    issuer_identity,
    market,
    technical_controls,
)


def _report(**overrides):
    report = {
        "label": "WARN",
        "rug_score": 20,
        "rug_status": "LOW",
        "speculation_score": 63,
        "speculation_status": "ELEVATED",
        "deployer": "0xdeadbeef",
        "is_known_chain_asset": False,
        "liquidity_usd": 120_642.87,
        "fdv": 8_953_055.0,
        "has_liquidity_evidence": True,
        "market_liquidity_risk": {
            "status": "ELEVATED",
            "score": 63,
            "reasons": ["Liquidity to FDV ratio is under 3%"],
        },
        "v6": {
            "backdoor": {
                "status": "OK",
                "has_backdoor": True,
                "admin_functions": ["owner()", "transferOwnership(address)"],
                "backdoor_functions": ["pause()"],
                "status_reason": "",
            }
        },
        "creator_stats": {"total": 40, "danger": 2, "rug_rate": 5.0, "status": "OK"},
        "completeness_pct": 100,
        "missing_inputs": [],
        "verdict_is_conclusive": True,
    }
    report.update(overrides)
    return report


# --- the step's own constraint: no verdict may move ------------------------

VERDICT_FIELDS = ("label", "rug_score", "rug_status", "speculation_score", "speculation_status")


def test_building_evidence_changes_no_verdict_field():
    report = _report()
    before = {field: report[field] for field in VERDICT_FIELDS}
    build_evidence(report)
    assert {field: report[field] for field in VERDICT_FIELDS} == before


def test_building_evidence_does_not_mutate_the_report_at_all():
    report = _report()
    snapshot = repr(sorted(report.items(), key=str))
    build_evidence(report)
    assert repr(sorted(report.items(), key=str)) == snapshot


# --- technical controls ----------------------------------------------------

def test_authorities_are_reported_for_a_recognised_issuer_too():
    """Identity contextualises an authority; it must not cancel it."""
    result = technical_controls(_report(is_known_chain_asset=True))
    assert result["admin_functions"]
    assert result["backdoor_functions"] == ["pause()"]
    assert result["has_backdoor"] is True


def test_an_unread_contract_is_unknown_not_clean():
    result = technical_controls(_report(v6={}))
    assert result["status"] == UNKNOWN
    assert result["has_backdoor"] is None


def test_a_module_failure_status_is_carried_through():
    report = _report(v6={"backdoor": {"status": "FETCH_FAILED", "status_reason": "rpc down"}})
    result = technical_controls(report)
    assert result["status"] == "FETCH_FAILED"
    assert result["reason"] == "rpc down"


# --- market ----------------------------------------------------------------

def test_market_reports_its_own_reading():
    result = market(_report())
    assert result["risk_status"] == "ELEVATED"
    assert result["liquidity_usd"] == 120_642.87
    assert "under 3%" in result["reasons"][0]


def test_absent_market_data_is_unknown():
    result = market(_report(market_liquidity_risk=None))
    assert result["status"] == UNKNOWN
    assert result["reasons"] == []


def test_market_does_not_speak_for_the_contract():
    """An elevated market reading must not appear in technical controls."""
    report = _report(market_liquidity_risk={"status": "ELEVATED", "score": 90, "reasons": ["thin"]})
    assert "thin" not in str(technical_controls(report))


# --- issuer identity -------------------------------------------------------

def test_a_curated_asset_is_recognised():
    result = issuer_identity(_report(is_known_chain_asset=True, known_asset_category="canonical"))
    assert result["recognised"] is True
    assert result["status"] == OK
    assert result["category"] == "canonical"


def test_an_unrecognised_issuer_is_unknown_not_an_accusation():
    result = issuer_identity(_report())
    assert result["status"] == UNKNOWN
    assert result["recognised"] is False
    assert "not that it is illegitimate" in result["note"]


def test_size_is_not_identity():
    """Holder counts and liquidity must not promote a mint to recognised.

    Treating scale as issuer verification is exactly what let an unverified,
    concentrated mint be waved through in the frozen PR #4.
    """
    huge = _report(holders_count=5_000_000, liquidity_usd=900_000_000)
    assert issuer_identity(huge)["recognised"] is False


# --- creator history -------------------------------------------------------

def test_our_own_labels_are_named_as_ours():
    result = creator_history(_report())
    assert result["prior_tokens_scanned_by_us"] == 40
    assert result["prior_tokens_we_labelled_danger"] == 2
    assert "Not independently confirmed" in result["note"].replace("\n", " ") or \
           "not\nindependently confirmed" in result["note"]


def test_confirmed_incidents_is_uncollected_not_zero():
    """"We have not looked" and "there were none" are different answers."""
    result = creator_history(_report())
    assert result["confirmed_incidents"]["status"] == NOT_COLLECTED
    assert result["confirmed_incidents"]["count"] is None


def test_a_failed_history_lookup_is_not_a_clean_record():
    report = _report(creator_stats={"total": 0, "danger": 0, "rug_rate": 0.0,
                                    "status": "FETCH_FAILED", "status_reason": "db down"})
    result = creator_history(report)
    assert result["status"] == "FETCH_FAILED"
    assert result["reason"] == "db down"


def test_no_history_at_all_is_unknown():
    assert creator_history(_report(creator_stats={}))["status"] == UNKNOWN


# --- coverage --------------------------------------------------------------

def test_coverage_carries_completeness_and_freshness():
    report = _report(data_freshness="FRESH", observed_at="2026-09-08T12:00:00+00:00")
    result = coverage(report)
    assert result["completeness_pct"] == 100
    assert result["data_freshness"] == "FRESH"
    assert result["observed_at"].startswith("2026-09-08")


def test_missing_completeness_is_unknown():
    report = _report()
    del report["completeness_pct"]
    assert coverage(report)["status"] == UNKNOWN


def test_missing_inputs_are_listed_not_summarised_away():
    report = _report(missing_inputs=[{"module": "holder_concentration", "status": "FETCH_FAILED"}])
    assert coverage(report)["missing_inputs"][0]["module"] == "holder_concentration"


# --- the whole block -------------------------------------------------------

def test_every_dimension_is_present_even_on_an_empty_report():
    result = build_evidence({})
    assert set(result) == {
        "technical_controls", "market", "issuer_identity", "creator_history", "coverage"
    }
    for name, block in result.items():
        assert block["status"] in {OK, UNKNOWN, "FETCH_FAILED", "NOT_FOUND", "NOT_QUERIED"}, name


@pytest.mark.parametrize("dimension", ["technical_controls", "market", "issuer_identity", "creator_history", "coverage"])
def test_an_empty_report_never_produces_a_reassuring_dimension(dimension):
    """Nothing read must never come out looking like something checked."""
    block = build_evidence({})[dimension]
    assert block["status"] != OK


# --- wiring: additive at the endpoint, not only in the builder -------------

def test_the_endpoint_carries_the_evidence_block_without_moving_the_verdict():
    """The step's constraint, checked where it can actually be violated."""
    from unittest import mock

    sys.path.insert(0, str(REPO_ROOT / "chains" / "avalanche"))
    import server

    server.SCAN_CACHE.clear()
    wavax = "0xb31f66aa3c1e785363f0875a1b74e27b85fd66c7"
    report = {
        "ok": True,
        "address": server.Web3.to_checksum_address(wavax),
        "label": "WARN",
        "rug_score": 20,
        "rug_status": "LOW",
        "speculation_score": 63,
        "speculation_status": "ELEVATED",
        "source": "private_scoring_engine",
        "market_liquidity_risk": {"status": "ELEVATED", "score": 63, "reasons": ["thin"]},
        "is_known_chain_asset": False,
        "creator_stats": {"total": 40, "danger": 2, "rug_rate": 5.0, "status": "OK"},
        "completeness_pct": 100,
        "missing_inputs": [],
    }
    with mock.patch.object(server, "score_with_private_engine", return_value=report):
        body = server.app.test_client().get(f"/score?address={wavax}").get_json()
    server.SCAN_CACHE.clear()

    # the verdict is exactly what the engine produced
    assert body["label"] == "WARN"
    assert body["rug_score"] == 20
    assert body["rug_status"] == "LOW"
    assert body["speculation_score"] == 63

    # and the dimensions are alongside it, not instead of it
    assert set(body["evidence"]) == {
        "technical_controls", "market", "issuer_identity", "creator_history", "coverage"
    }
    assert body["evidence"]["market"]["risk_status"] == "ELEVATED"
    assert body["evidence"]["issuer_identity"]["recognised"] is False
    assert body["evidence"]["coverage"]["data_freshness"] == "FRESH"
    assert body["evidence"]["creator_history"]["confirmed_incidents"]["status"] == NOT_COLLECTED


# --- timestamps must each name what they actually are ----------------------

def test_coverage_separates_computation_from_observation():
    """A scalar timestamp must not imply every provider observed at that
    instant. Routescan, DexScreener, Glacier and the chain each look at their
    own moment, and none returns a time we have verified."""
    report = _report(
        computed_at="2026-09-08T12:00:00+00:00",
        served_at="2026-09-08T12:00:05+00:00",
        observed_at=None,
        observation_coverage="COMPUTATION_TIME_ONLY",
        data_freshness="FRESH",
    )
    result = coverage(report)
    assert result["computed_at"].startswith("2026-09-08")
    assert result["served_at"] > result["computed_at"]
    assert result["observed_at"] is None
    assert result["observation_coverage"] == "COMPUTATION_TIME_ONLY"


def test_coverage_never_substitutes_computation_time_for_observation():
    report = _report(computed_at="2026-09-08T12:00:00+00:00", data_freshness="FRESH")
    assert coverage(report)["observed_at"] is None, (
        "computation time was reported as observation time"
    )


def test_the_danger_rate_is_read_under_either_field_name():
    """Two independent branches touch this: one renames rug_rate to say whose
    labels it counts. Reading a single name would empty the field depending on
    which lands first."""
    old = creator_history(_report(creator_stats={"total": 10, "danger": 9, "rug_rate": 90.0}))
    new = creator_history(_report(creator_stats={"total": 10, "danger": 9, "prior_danger_rate_pct": 90.0}))
    assert old["prior_danger_rate_pct"] == 90.0
    assert new["prior_danger_rate_pct"] == 90.0
