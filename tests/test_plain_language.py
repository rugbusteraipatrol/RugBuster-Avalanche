"""A warning must say which kind of warning it is -- Avalanche vocabulary.

Deliberately not shared with the Solana module. Copying field names between
services is how `withhold_verdict` came to blank a label here while leaving
every numeric verdict field populated.

Two things this has to get right that the Solana one does not.

`label` here mixes rug risk with market liquidity risk. LINK.e is the worked
example: rug_status stayed LOW while its pool drained, the label moved to WARN,
and a reader with only the label saw the scanner accusing a Chainlink bridge
asset of something.

And the backdoor detector matches function names by substring. `withdraw` sets
has_drain_function, so Wrapped AVAX -- where withdraw(uint256) burns the
caller's own wrapper and returns their own native token -- comes back with a
drain function and backdoor_risk_score 20. Repeating that word would have this
service call the chain's most canonical asset drainable in a response that
scores it GOOD.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "api"))

from plain_language import FINDING, GAP, REFUSAL, describe, not_established

REASSURANCE = "not evidence against the token"


def _payload(**overrides) -> dict:
    payload = {
        "label": "WARN",
        "rug_status": "LOW",
        "speculation_status": "LOW",
        "verdict_is_conclusive": True,
        "is_known_chain_asset": False,
        "v6_top1_concentration_pct": 28.5,
        "v6": {"backdoor": {}},
        "evidence": {
            "technical_controls": {"status": "OK", "admin_functions": []},
            "issuer_identity": {"status": "UNKNOWN", "recognised": False},
            "market": {"status": "LOW"},
            "creator_history": {"status": "OK",
                                "confirmed_incidents": {"status": "NOT_COLLECTED"}},
            "coverage": {"status": "OK", "missing_inputs": []},
        },
    }
    payload.update(overrides)
    return payload


# --- the detector's word is not repeated -----------------------------------

def test_a_wrapper_is_not_announced_as_holding_a_drain_function():
    result = describe(_payload(
        label="GOOD", is_known_chain_asset=True, known_asset_category="canonical_wrapped_native",
        v6={"backdoor": {"has_drain_function": True, "has_backdoor": True,
                         "backdoor_functions": ["withdraw(uint256)"]}},
    ))
    assert "drain" not in result["verdict_summary"].lower()
    assert "withdraw(uint256)" in result["verdict_summary"]


def test_an_unrecognised_contract_with_the_same_function_is_not_explained_away():
    """Identity is what makes the function expected. Without it, the honest
    statement is that we matched a name and did not read the code."""
    result = describe(_payload(
        v6={"backdoor": {"has_drain_function": True,
                         "backdoor_functions": ["withdraw(uint256)"]}},
    ))
    assert "has not been established" in result["verdict_summary"]
    assert "found by name, not by reading what it does" in result["verdict_summary"]


def test_a_burn_function_alone_is_not_a_controller_power():
    """has_backdoor is set by a plain ERC-20 burn. Leading with that would
    accuse every canonical asset on the chain."""
    result = describe(_payload(
        label="GOOD",
        v6={"backdoor": {"has_backdoor": True, "backdoor_risk_score": 0,
                         "backdoor_functions": ["burn(uint256)"],
                         "has_drain_function": False, "has_mint_function": False}},
    ))
    assert "expose" not in result["verdict_summary"]
    assert result["verdict_summary"].startswith("Nothing found against this token")


# --- the label mixes two readings, so say which one moved ------------------

def test_a_market_driven_warning_says_it_is_the_market():
    result = describe(_payload(label="WARN", rug_status="LOW", speculation_status="HIGH"))
    assert "market liquidity" in result["verdict_summary"]
    assert "not a claim about the contract" in result["verdict_summary"]


def test_a_rug_finding_is_not_blamed_on_the_market():
    result = describe(_payload(label="DANGER", rug_status="DANGER", speculation_status="LOW"))
    assert result["verdict_basis"] == FINDING
    assert "market" not in result["verdict_summary"]


# --- the sentence that must not appear where it does not belong ------------

def test_insufficient_data_is_never_softened():
    result = describe(_payload(label="INSUFFICIENT_DATA", verdict_is_conclusive=False))
    assert result["verdict_basis"] == REFUSAL
    assert REASSURANCE not in result["verdict_summary"]
    assert "not a clean bill of health" in result["verdict_summary"]


def test_an_inconclusive_verdict_is_a_refusal_whatever_the_label_says():
    result = describe(_payload(label="WARN", verdict_is_conclusive=False))
    assert result["verdict_basis"] == REFUSAL


# --- what we could not establish -------------------------------------------

def test_a_risk_level_is_not_mistaken_for_an_unread_dimension():
    """The market dimension reports LOW, not OK. Treating anything that is not
    literally OK as unread turned a good liquidity reading into a gap."""
    assert "how deep this token's market is" not in not_established(_payload())


def test_an_unread_dimension_is_listed():
    payload = _payload()
    payload["evidence"]["market"] = {"status": "UNKNOWN"}
    assert "how deep this token's market is" in not_established(payload)


def test_uncollected_deployer_history_is_stated():
    payload = _payload()
    payload["evidence"]["creator_history"] = {"status": "NOT_FOUND",
                                              "confirmed_incidents": {"status": "NOT_COLLECTED"}}
    gaps = not_established(payload)
    assert "what this deployer's previous tokens did" in gaps


def test_each_missing_input_is_named():
    payload = _payload()
    payload["evidence"]["coverage"] = {"status": "OK", "missing_inputs": ["routescan"]}
    assert "the reading from routescan, which could not be collected" in not_established(payload)


def test_unmeasured_concentration_is_a_different_gap_from_unidentified_holders():
    measured = not_established(_payload())
    unmeasured = not_established(_payload(v6_top1_concentration_pct=None))
    assert "who the largest holders are" in measured
    assert "how concentrated ownership is" in unmeasured


def test_describing_a_verdict_cannot_change_it():
    payload = _payload()
    before = dict(payload)
    result = describe(payload)
    assert payload == before
    assert set(result) == {"verdict_summary", "verdict_basis", "not_established"}
