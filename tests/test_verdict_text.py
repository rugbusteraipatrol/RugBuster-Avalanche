"""The sentence a customer reads must not contradict the engine.

The engine now refuses to call a token clean when a load-bearing check did
not run. The human-facing sentence is generated separately -- by DeepSeek,
with a deterministic template as fallback -- and neither knew about data
gaps. An incomplete scan could therefore be summarised as "looks safe" under
a verdict of INSUFFICIENT_DATA, which is worse than having no sentence at all.
"""

from __future__ import annotations

import importlib
from unittest import mock

import pytest


@pytest.fixture()
def server(monkeypatch):
    monkeypatch.setenv("RUGBUSTER_NETWORK", "mainnet")
    import api.server as srv
    importlib.reload(srv)
    return srv


def _incomplete_report() -> dict:
    return {
        "address": "0x1111111111111111111111111111111111111111",
        "token_name": "Solid Token",
        "symbol": "SOLID",
        "label": "INSUFFICIENT_DATA",
        "rug_status": "LOW",
        "rug_score": 12,
        "speculation_status": "LOW",
        "speculation_score": 6,
        "completeness_pct": 12,
        "verdict_is_conclusive": False,
        "missing_inputs": [
            {"module": "contract_backdoor", "status": "FETCH_FAILED", "reason": "bytecode unreadable"},
            {"module": "holder_concentration", "status": "FETCH_FAILED", "reason": "holder list unavailable"},
        ],
    }


def _complete_report() -> dict:
    report = _incomplete_report()
    report.update({
        "label": "GOOD",
        "completeness_pct": 100,
        "verdict_is_conclusive": True,
        "missing_inputs": [],
    })
    return report


# --- the deterministic template ---

def test_template_says_not_enough_data_and_names_what_failed(server):
    text = server.syndicate_verdict_from_report(_incomplete_report())
    assert "Not enough data" in text
    assert "the contract's code" in text
    assert "who holds the supply" in text
    assert "12%" in text


def test_template_does_not_claim_safety_when_incomplete(server):
    text = server.syndicate_verdict_from_report(_incomplete_report()).lower()
    assert "low" not in text.split("could not check")[0]
    assert not server._reads_as_reassuring(text)


def test_template_is_unchanged_for_a_complete_scan(server):
    text = server.syndicate_verdict_from_report(_complete_report())
    assert text.startswith("RugBuster verdict:")
    assert "Not enough data" not in text


def test_not_found_alone_does_not_trigger_the_incomplete_message(server):
    """A token with no pool is a finding, not a failed scan."""
    report = _complete_report()
    report["missing_inputs"] = [
        {"module": "holder_concentration", "status": "NOT_FOUND", "reason": "no holder records"}
    ]
    text = server.syndicate_verdict_from_report(report)
    assert "Not enough data" not in text


# --- what the model is told ---

def test_ai_context_carries_the_gaps(server):
    context = server.build_ai_scan_context(_incomplete_report())
    assert context["checks_completed_pct"] == 12
    assert context["verdict_is_conclusive"] is False
    assert "the contract's code" in context["checks_that_could_not_run"]


def test_ai_context_is_clean_for_a_complete_scan(server):
    context = server.build_ai_scan_context(_complete_report())
    assert context["checks_that_could_not_run"] == []


# --- the safety net, because a prompt instruction is not a guarantee ---

def _mock_deepseek(server, text: str):
    response = mock.Mock()
    response.json.return_value = {"choices": [{"message": {"content": text}}]}
    response.raise_for_status.return_value = None
    return mock.patch.object(server.requests, "post", return_value=response)


def test_reassuring_ai_text_is_discarded_when_the_scan_was_incomplete(server, monkeypatch):
    monkeypatch.setattr(server, "DEEPSEEK_API_KEY", "test-key")
    with _mock_deepseek(server, "This token looks safe with no red flags."):
        verdict = server.fetch_deepseek_verdict(_incomplete_report())
    assert "Not enough data" in verdict
    assert "looks safe" not in verdict


def test_honest_ai_text_is_kept_when_the_scan_was_incomplete(server, monkeypatch):
    monkeypatch.setattr(server, "DEEPSEEK_API_KEY", "test-key")
    honest = "Not enough data to judge: contract code and holder distribution could not be read."
    with _mock_deepseek(server, honest):
        verdict = server.fetch_deepseek_verdict(_incomplete_report())
    assert verdict == honest


def test_reassuring_ai_text_is_kept_when_the_scan_was_complete(server, monkeypatch):
    """The safety net must not fire on scans that genuinely completed."""
    monkeypatch.setattr(server, "DEEPSEEK_API_KEY", "test-key")
    text = "Deep liquidity and clean contract; low risk."
    with _mock_deepseek(server, text):
        verdict = server.fetch_deepseek_verdict(_complete_report())
    assert verdict == text


def test_safety_net_path_does_not_crash(server, monkeypatch):
    """Regression guard: this branch calls log.warning, and server.py had no
    logger defined when the branch was first written."""
    monkeypatch.setattr(server, "DEEPSEEK_API_KEY", "test-key")
    with _mock_deepseek(server, "Token is clean and secure."):
        verdict = server.fetch_deepseek_verdict(_incomplete_report())
    assert verdict


@pytest.mark.parametrize(
    "text,expected",
    [
        ("This token looks safe", True),
        ("No red flags detected", True),
        ("Low risk profile overall", True),
        ("Not enough data to judge this token", False),
        ("Deployer dumped supply within minutes", False),
    ],
)
def test_reassuring_detector(server, text, expected):
    assert server._reads_as_reassuring(text) is expected


# --- the public boundary: an allowlist silently drops anything not named ---

def test_public_score_response_exposes_the_data_status_fields(server):
    """`/score` returns an explicit allowlist. The status work is invisible to
    every caller unless these are named in it -- which they were not, until a
    live deploy showed the fields missing from the response."""
    compact = server.compact_score_response(_incomplete_report(), "private_scoring_engine")
    assert compact["completeness_pct"] == 12
    assert compact["verdict_is_conclusive"] is False
    assert [item["module"] for item in compact["missing_inputs"]] == [
        "contract_backdoor",
        "holder_concentration",
    ]
    assert compact["data_contract_version"] == server.DATA_CONTRACT_VERSION


def test_public_score_response_is_sane_for_a_complete_scan(server):
    compact = server.compact_score_response(_complete_report(), "private_scoring_engine")
    assert compact["completeness_pct"] == 100
    assert compact["verdict_is_conclusive"] is True
    assert compact["missing_inputs"] == []
