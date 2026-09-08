"""The blue-chip gate must track our claim, not the market's mood.

I1 used to assert `label == GOOD` for canonical assets. `label` folds together
rug risk -- our claim about the contract and its deployer -- and market
liquidity risk, a reading of the pool on the day of the scan. Only the first
is ours to hold stable. LINK.e proved the point: rug_status stayed LOW while
its pool drained to ~$78k, the combined label moved to WARN, and the gate
reported a scanner regression that had not happened.

Loosening a gate is dangerous in the obvious way, so these tests exist to pin
the other half: the new gate must still fail on every regression the old one
was there to catch. If someone deletes the rug_status check, several of these
go red.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest
import yaml

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "qa"))

from run_regression import evaluate_entry  # noqa: E402

SOURCE = "private_scoring_engine"


def _bluechip_entry(**overrides) -> dict:
    entry = {
        "symbol": "WAVAX",
        "address": "0xb31f66aa3c1e785363f0875a1b74e27b85fd66c7",
        "category": "bluechip",
        "expected_rug_status": ["LOW"],
        "forbidden_labels": ["DANGER", "INSUFFICIENT_DATA"],
        "gate": True,
    }
    entry.update(overrides)
    return entry


def _response(**overrides) -> dict:
    body = {"ok": True, "label": "GOOD", "rug_status": "LOW", "rug_score": 12, "source": SOURCE}
    body.update(overrides)
    return body


# --- what the gate must still catch ---

def test_rug_risk_rising_on_a_canonical_asset_fails():
    """The regression I1 exists for: we start calling a blue chip a rug."""
    result = evaluate_entry(_bluechip_entry(), _response(rug_status="HIGH", label="DANGER"), SOURCE)
    assert result["status"] == "FAIL"
    assert any("rug_status=HIGH" in p for p in result["problems"])


def test_moderate_rug_risk_also_fails():
    """LOW is the assertion; anything above it on a canonical asset is a defect."""
    result = evaluate_entry(_bluechip_entry(), _response(rug_status="MEDIUM", label="WARN"), SOURCE)
    assert result["status"] == "FAIL"


def test_danger_label_fails_even_if_rug_status_somehow_reads_low():
    """Belt and braces: a canonical asset must never reach the top alarm band."""
    result = evaluate_entry(_bluechip_entry(), _response(label="DANGER"), SOURCE)
    assert result["status"] == "FAIL"


def test_insufficient_data_on_a_blue_chip_fails():
    """Failing to read WAVAX is a broken scan, not an acceptable answer."""
    result = evaluate_entry(
        _bluechip_entry(), _response(label="INSUFFICIENT_DATA", rug_status="LOW"), SOURCE
    )
    assert result["status"] == "FAIL"


def test_missing_rug_status_fails_rather_than_passing_silently():
    """If the field disappears from the payload the gate must notice, not skip."""
    body = _response()
    del body["rug_status"]
    result = evaluate_entry(_bluechip_entry(), body, SOURCE)
    assert result["status"] == "FAIL"
    assert any("rug_status missing" in p for p in result["problems"])


def test_silent_fallback_to_another_scorer_still_fails():
    """I4 must keep biting on blue chips after the I1 rewrite."""
    result = evaluate_entry(_bluechip_entry(), _response(source="local_fallback"), SOURCE)
    assert result["status"] == "FAIL"


def test_failed_request_still_fails():
    result = evaluate_entry(
        _bluechip_entry(), {"ok": False, "error": "timeout", "source": None}, SOURCE
    )
    assert result["status"] == "FAIL"


# --- what the gate must now tolerate ---

def test_market_liquidity_drain_no_longer_reports_a_scanner_regression():
    """The LINK.e case: our claim held, the pool moved. Not our defect."""
    result = evaluate_entry(_bluechip_entry(symbol="LINK.e"), _response(label="WARN"), SOURCE)
    assert result["status"] == "PASS"
    assert result["problems"] == []


def test_healthy_blue_chip_passes():
    assert evaluate_entry(_bluechip_entry(), _response(), SOURCE)["status"] == "PASS"


# --- the golden set itself ---

def test_every_bluechip_entry_asserts_on_rug_status_not_label():
    """Guards against a future entry being added back in the old shape."""
    doc = yaml.safe_load((REPO_ROOT / "qa" / "golden_set.yaml").read_text(encoding="utf-8"))
    bluechips = [e for e in doc["entries"] if e["category"] == "bluechip"]
    assert bluechips, "golden set lost its blue-chip entries"
    for entry in bluechips:
        assert entry.get("expected_rug_status") == ["LOW"], entry["symbol"]
        assert "expected_labels" not in entry, (
            f"{entry['symbol']} asserts on label, which moves with the market"
        )
        assert entry.get("gate") is True, entry["symbol"]


def test_other_categories_were_not_loosened():
    """Only blue chips changed; scam/rug_factory keep asserting on label."""
    doc = yaml.safe_load((REPO_ROOT / "qa" / "golden_set.yaml").read_text(encoding="utf-8"))
    for entry in doc["entries"]:
        if entry["category"] == "bluechip":
            continue
        assert "expected_rug_status" not in entry, entry["symbol"]


def test_scam_entries_still_fail_on_a_false_good():
    """The rewrite must not have weakened the direction that matters most."""
    scam = {
        "symbol": "RUGGY",
        "address": "0xdead",
        "category": "scam",
        "forbidden_labels": ["GOOD"],
        "gate": True,
    }
    result = evaluate_entry(scam, _response(label="GOOD", rug_status="LOW"), SOURCE)
    assert result["status"] == "FAIL"


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-q"]))
