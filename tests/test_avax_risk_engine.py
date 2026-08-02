from chains.avalanche.risk_engine import score_token


def test_missing_metadata_is_insufficient_data_not_elevated():
    result = score_token(
        {
            "name": "Unknown",
            "symbol": "Unknown",
            "decimals": None,
            "total_supply": None,
            "has_liquidity_evidence": False,
        }
    )

    assert result.rug.score is None
    assert result.rug.status == "INSUFFICIENT_DATA"


def test_wavax_like_metadata_scores_low_rug_risk():
    result = score_token(
        {
            "name": "Wrapped AVAX",
            "symbol": "WAVAX",
            "decimals": 18,
            "total_supply": 400_000_000 * 10**18,
            "has_liquidity_evidence": True,
            "liquidity_usd": 10_000_000,
            "fdv": 8_000_000_000,
            "volume24h": 5_000_000,
            "price_change_24h": 2.5,
            "buys24h": 1_000,
            "sells24h": 1_100,
            "is_known_chain_asset": True,
        }
    )

    assert result.rug.status == "LOW"
    assert result.rug.score < 45
    assert result.speculation.status == "LOW"


def test_usdce_like_metadata_scores_low_rug_risk():
    result = score_token(
        {
            "name": "USD Coin Bridged",
            "symbol": "USDC.e",
            "decimals": 6,
            "total_supply": 300_000_000 * 10**6,
            "has_liquidity_evidence": True,
            "liquidity_usd": 2_000_000,
            "fdv": 300_000_000,
            "volume24h": 200_000,
            "price_change_24h": 0.1,
            "buys24h": 100,
            "sells24h": 120,
            "is_known_chain_asset": True,
        }
    )

    assert result.rug.status == "LOW"
    assert result.rug.score < 45


def _prepared_rug_metadata(**overrides):
    """A rug dressed to pass: young, but with everything cheap already bought.

    Publishing verified source costs nothing, and a hundred holders is one
    airdrop. Age is the only ingredient that cannot be faked, which is why a
    track record must not be granted on the cheap ingredients alone.
    """
    metadata = {
        "name": "Prepared",
        "symbol": "PREP",
        "decimals": 18,
        "total_supply": 10**27,
        "holders_count": 120,
        "token_age_days": 12,
        "has_liquidity_evidence": True,
        "liquidity_usd": 40_000,
        "fdv": 5_000_000,
        "goplus_is_open_source": "1",
        "goplus_is_mintable": "0",
        "goplus_is_proxy": "0",
        "goplus_hidden_owner": "0",
        "goplus_owner_change_balance": "0",
        "v6_has_mint": True,
        "v6_has_operator_controls": True,
        "v6_admin_control_functions": ["mint(address,uint256)", "transferOwnership(address)"],
        "v6_top5_concentration_pct": 99.0,
        "v6_concentration_risk": "CRITICAL",
    }
    metadata.update(overrides)
    return metadata


def test_young_token_does_not_earn_a_track_record_discount():
    result = score_token(_prepared_rug_metadata())

    assert result.rug.status != "LOW", (
        "a 12-day-old token with mint plus admin controls and 99% of supply in five "
        "wallets must not read as low rug risk just because its source is published"
    )


def test_publishing_source_does_not_flip_the_verdict():
    verified = score_token(_prepared_rug_metadata()).rug.score
    unverified = score_token(_prepared_rug_metadata(goplus_is_open_source="0")).rug.score

    assert verified >= unverified - 15, (
        f"verifying source moved the score {unverified} -> {verified}; publishing "
        "source is free, so it must not be worth a large discount on its own"
    )


def test_bytecode_backdoor_is_never_hidden_by_a_third_party_clean_bill():
    result = score_token(
        _prepared_rug_metadata(v6_has_backdoor=True, v6_backdoor_risk_score=30)
    )

    assert any("backdoor" in reason.lower() for reason in result.rug.reasons), (
        "our own bytecode backdoor detection must stay visible even when an "
        "external scanner reports the contract as clean"
    )


def test_established_token_with_dead_liquidity_is_not_low_market_risk():
    result = score_token(
        {
            "name": "Old And Thin",
            "symbol": "THIN",
            "decimals": 18,
            "total_supply": 10**27,
            "holders_count": 64_000,
            "token_age_days": 1_550,
            "has_liquidity_evidence": True,
            "liquidity_usd": 521,
            "fdv": 20_000_000,
            "volume24h": 523,
            "price_change_24h": 10.2,
            "buys24h": 3,
            "sells24h": 4,
        }
    )

    assert result.speculation.status != "LOW", (
        "a long track record means the owner has not stolen; it does not mean a "
        "holder can exit through $521 of liquidity"
    )
