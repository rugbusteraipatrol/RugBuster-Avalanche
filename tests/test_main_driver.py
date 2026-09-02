"""The headline reason must describe the token, not our plumbing.

Live on 2026-09-02, WAVAX read: "Main driver: Symbol matches protected WAVAX
but no address was supplied to verify it." The verdict was correct (GOOD) but
the one sentence a user reads announced an internal complaint -- about an
address the user had just supplied -- which reads as a broken scanner.
"""

from __future__ import annotations

import importlib

import pytest


@pytest.fixture()
def server(monkeypatch):
    monkeypatch.setenv("RUGBUSTER_NETWORK", "mainnet")
    import api.server as srv
    importlib.reload(srv)
    return srv


def test_skips_our_own_diagnostics_for_a_real_finding(server):
    reasons = [
        "Symbol matches protected WAVAX but no address was supplied to verify it",
        "Identity reputation source status is UNKNOWN",
        "Known Avalanche canonical/established asset; new-token heuristics suppressed",
        "Token name readable on-chain",
        "Decimals value is within normal ERC-20 range",
        "Deep live liquidity at $9,858,429",
    ]
    assert server.main_driver(reasons) == "Deep live liquidity at $9,858,429"


def test_a_real_risk_finding_still_leads(server):
    reasons = [
        "Token name readable on-chain",
        "Deployer sold within 40 seconds of launch",
        "Deep live liquidity at $2,000,000",
    ]
    assert server.main_driver(reasons) == "Deployer sold within 40 seconds of launch"


def test_falls_back_rather_than_inventing_reassurance(server):
    """All-diagnostic input must not become a manufactured positive finding."""
    reasons = [
        "Token name readable on-chain",
        "Token symbol readable on-chain",
    ]
    assert server.main_driver(reasons) == "Token name readable on-chain"


def test_empty_reasons(server):
    assert "No dominant hard-risk driver" in server.main_driver([])


def test_verdict_headline_no_longer_leads_with_the_address_complaint(server):
    report = {
        "rug_status": "LOW",
        "rug_score": 12,
        "speculation_status": "LOW",
        "speculation_score": 6,
        "missing_inputs": [],
        "risk_flags": [
            "Symbol matches protected WAVAX but no address was supplied to verify it",
            "Deep live liquidity at $9,858,429",
        ],
    }
    text = server.syndicate_verdict_from_report(report)
    assert "no address was supplied" not in text
    assert "Deep live liquidity" in text


def test_payload_now_carries_the_address(server):
    """Without this the engine's confusable check cannot run at all."""
    import inspect
    source = inspect.getsource(server.build_remote_scoring_payload)
    assert '"address": checksum' in source


def test_all_markers_are_lowercase(server):
    """The filter lowercases the text but not the markers, so an uppercase
    marker silently never matches -- which is exactly how "within normal
    ERC-20 range" slipped through and became a headline."""
    for marker in server.NON_FINDING_REASON_MARKERS:
        assert marker == marker.lower(), f"marker must be lowercase: {marker!r}"
