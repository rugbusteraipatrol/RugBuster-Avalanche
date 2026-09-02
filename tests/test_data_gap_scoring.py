"""The local fallback scorer must honour the same rule as the remote engine.

`risk_engine.py` runs when the private scoring engine is unreachable. If it
kept scoring an unread contract as a clean one, the fallback would be exactly
the path most likely to hit an outage and most likely to lie about it.
"""

from __future__ import annotations

from chains.avalanche.risk_engine import score_rug_risk


def _readable_token(**overrides) -> dict:
    base = {
        "name": "Solid Token",
        "symbol": "SOLID",
        "decimals": 18,
        "total_supply": 1_000_000 * 10**18,
        "holders_count": 5_000,
        "token_age_days": 400,
        "has_liquidity_evidence": True,
        "liquidity_usd": 2_000_000,
    }
    base.update(overrides)
    return base


def test_baseline_is_scored_not_withheld():
    """Guards the fixture: the gap tests below are meaningless if this is already None."""
    result = score_rug_risk(_readable_token())
    assert result.score is not None
    assert result.status != "INSUFFICIENT_DATA"


def test_unread_bytecode_withholds_a_score_instead_of_scoring_it_clean():
    result = score_rug_risk(_readable_token(v6_backdoor_status="FETCH_FAILED"))
    assert result.score is None
    assert result.status == "INSUFFICIENT_DATA"
    assert any("bytecode could not be read" in reason.lower() for reason in result.reasons)


def test_unread_bytecode_does_not_inflate_risk():
    """Withhold the clean claim; never invent a dangerous one."""
    result = score_rug_risk(_readable_token(v6_backdoor_status="FETCH_FAILED"))
    assert result.status != "HIGH"


def test_successful_backdoor_scan_scores_normally():
    result = score_rug_risk(_readable_token(v6_backdoor_status="OK"))
    assert result.score is not None


def test_not_found_status_does_not_withhold_a_score():
    """No bytecode at the address is a fact about the address, not a failed read."""
    result = score_rug_risk(_readable_token(v6_backdoor_status="NOT_FOUND"))
    assert result.score is not None


def test_known_chain_asset_is_exempt():
    result = score_rug_risk(
        _readable_token(v6_backdoor_status="FETCH_FAILED", is_known_chain_asset=True)
    )
    assert result.score is not None


def test_real_finding_still_wins_over_the_gap():
    """A hard risk signal we did find is not erased by one we could not.

    The token still scores rather than being withheld, and the finding is
    carried in the reasons. It does not have to clear the ELEVATED band --
    suspicious naming alone is deliberately weighted below that -- only to
    survive the gap guard instead of being swallowed by it.
    """
    result = score_rug_risk(
        _readable_token(v6_backdoor_status="FETCH_FAILED", name="claim airdrop now", symbol="SCAM")
    )
    assert result.score is not None
    assert result.status != "INSUFFICIENT_DATA"
    assert any("suspicious terms" in reason for reason in result.reasons)
    # and it is scored above the clean baseline, not silently equal to it
    assert result.score > score_rug_risk(_readable_token()).score


def test_missing_status_field_behaves_exactly_as_before():
    """Payloads predating the status fields must score identically."""
    with_status = score_rug_risk(_readable_token(v6_backdoor_status="OK"))
    without_status = score_rug_risk(_readable_token())
    assert with_status.score == without_status.score
    assert with_status.status == without_status.status
