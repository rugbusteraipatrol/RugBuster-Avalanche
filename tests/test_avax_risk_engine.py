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
