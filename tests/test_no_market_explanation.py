"""A verdict withheld for want of a market says so, not "too little was readable".

DYP, 2026-09-11: completeness_pct 100, confidence NORMAL, no blocking gap, and
label INSUFFICIENT_DATA -- the engine had no market score because no live pool
was found. The summary said "Too little was readable to judge this token".
Wording only: the label and verdict_basis are unchanged.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "api"))

from plain_language import REFUSAL, describe  # noqa: E402

DYP_LIKE = {
    "label": "INSUFFICIENT_DATA",
    "rug_status": "LOW",
    "speculation_status": "UNKNOWN",
    "has_liquidity_evidence": False,
    "blocking_data_gaps": [],
    "verdict_is_conclusive": True,
    "v6": {"backdoor": {}},
    "evidence": {},
}


def test_no_live_market_is_named_as_the_reason():
    result = describe(dict(DYP_LIKE))
    assert "no live market was found" in result["verdict_summary"]
    assert "Too little was readable" not in result["verdict_summary"]
    assert result["verdict_basis"] == REFUSAL
    assert "not a clean bill of health" in result["verdict_summary"]


def test_a_blocking_gap_keeps_the_old_sentence():
    result = describe({**DYP_LIKE, "blocking_data_gaps": ["contract_capability"]})
    assert result["verdict_summary"].startswith("Too little was readable")


def test_liquidity_evidence_present_keeps_the_old_sentence():
    result = describe({**DYP_LIKE, "has_liquidity_evidence": True})
    assert result["verdict_summary"].startswith("Too little was readable")


def test_other_labels_are_untouched():
    result = describe({**DYP_LIKE, "label": "WARN"})
    assert "no live market was found" not in result["verdict_summary"]
