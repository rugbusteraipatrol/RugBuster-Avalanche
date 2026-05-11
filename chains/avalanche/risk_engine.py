"""Deterministic dual-score engine for RugBuster Avalanche.

Rug Score uses only on-chain facts. Speculation Score uses only market data.
If market liquidity evidence is missing, speculation is reported as UNKNOWN.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any


@dataclass(frozen=True)
class ScoreResult:
    score: int | None
    status: str
    reasons: list[str]

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class DualScoreResult:
    rug: ScoreResult
    speculation: ScoreResult

    def to_dict(self) -> dict[str, Any]:
        return {
            "rug": self.rug.to_dict(),
            "speculation": self.speculation.to_dict(),
        }


def risk_status(score: int | None) -> str:
    if score is None:
        return "UNKNOWN"
    if score >= 75:
        return "HIGH"
    if score >= 45:
        return "ELEVATED"
    return "LOW"


def clamp(score: int) -> int:
    return max(0, min(100, score))


def score_rug_risk(metadata: dict[str, Any]) -> ScoreResult:
    """Score rug risk from hard on-chain facts only."""

    score = 12
    reasons: list[str] = []

    name = str(metadata.get("name") or "").strip()
    symbol = str(metadata.get("symbol") or "").strip()
    decimals = metadata.get("decimals")
    total_supply = metadata.get("total_supply")

    if not name or name.lower() == "unknown":
        score += 14
        reasons.append("Token name unavailable on-chain")
    else:
        reasons.append("Token name readable on-chain")

    if not symbol or symbol.lower() == "unknown":
        score += 14
        reasons.append("Token symbol unavailable on-chain")
    else:
        reasons.append("Token symbol readable on-chain")

    if decimals is None:
        score += 18
        reasons.append("Decimals unavailable on-chain")
    else:
        decimals_value = int(decimals)
        if decimals_value < 0 or decimals_value > 24:
            score += 28
            reasons.append(f"Decimals value {decimals_value} is unusual")
        else:
            reasons.append("Decimals value is within normal ERC-20 range")

    if total_supply is None:
        score += 22
        reasons.append("Total supply unavailable on-chain")
    else:
        supply_value = int(total_supply)
        if supply_value <= 0:
            score += 60
            reasons.append("Total supply is zero or invalid")
        else:
            reasons.append("Total supply readable on-chain")

    lower_text = f"{name} {symbol}".lower()
    suspicious_terms = ("claim", "airdrop", "scam", "rug", "test")
    hits = [term for term in suspicious_terms if term in lower_text]
    if hits:
        score += 10 + (4 * min(len(hits), 3))
        reasons.append(f"On-chain naming includes suspicious terms: {', '.join(hits)}")

    return ScoreResult(score=clamp(score), status=risk_status(score), reasons=reasons)


def score_speculation_risk(metadata: dict[str, Any]) -> ScoreResult:
    """Score speculation risk from market structure only.

    If we do not have evidence of live liquidity, return UNKNOWN instead of
    inventing a number.
    """

    if not metadata.get("has_liquidity_evidence"):
        return ScoreResult(
            score=None,
            status="UNKNOWN",
            reasons=["No live liquidity evidence found on supported Avalanche venues"],
        )

    score = 28
    reasons: list[str] = []

    liquidity_usd = metadata.get("liquidity_usd")
    fdv = metadata.get("fdv")
    volume24h = metadata.get("volume24h")
    price_change24h = metadata.get("price_change_24h")
    buys24h = metadata.get("buys24h")
    sells24h = metadata.get("sells24h")

    if liquidity_usd is None:
        score += 14
        reasons.append("Pair exists but USD liquidity could not be priced")
    else:
        liq = float(liquidity_usd)
        if liq < 5_000:
            score += 38
            reasons.append(f"Very thin live liquidity at ${liq:,.0f}")
        elif liq < 25_000:
            score += 20
            reasons.append(f"Thin live liquidity at ${liq:,.0f}")
        elif liq >= 100_000:
            score -= 10
            reasons.append(f"Deep live liquidity at ${liq:,.0f}")
        else:
            score -= 4
            reasons.append(f"Meaningful live liquidity at ${liq:,.0f}")

    if fdv is None:
        reasons.append("FDV unavailable from market sources")
    else:
        fdv_value = float(fdv)
        if liquidity_usd and fdv_value > 0:
            ratio = float(liquidity_usd) / fdv_value
            if ratio < 0.01:
                score += 20
                reasons.append("Liquidity to FDV ratio is under 1%")
            elif ratio < 0.03:
                score += 10
                reasons.append("Liquidity to FDV ratio is low")
            elif ratio >= 0.1:
                score -= 4
                reasons.append("Liquidity to FDV ratio is healthy")

    if volume24h is None:
        reasons.append("24h volume unavailable from market sources")
    else:
        vol = float(volume24h)
        if vol < 10_000:
            score += 10
            reasons.append(f"Low 24h volume at ${vol:,.0f}")
        elif vol >= 100_000:
            score -= 4
            reasons.append(f"Strong 24h volume at ${vol:,.0f}")

    if price_change24h is None:
        reasons.append("24h price change unavailable from market sources")
    else:
        move = abs(float(price_change24h))
        if move >= 60:
            score += 18
            reasons.append(f"Very high 24h volatility at {float(price_change24h):.1f}%")
        elif move >= 25:
            score += 8
            reasons.append(f"Elevated 24h volatility at {float(price_change24h):.1f}%")
        else:
            reasons.append(f"24h volatility is moderate at {float(price_change24h):.1f}%")

    if buys24h is None or sells24h is None:
        reasons.append("24h buy/sell flow unavailable from market sources")
    else:
        buys = int(buys24h)
        sells = int(sells24h)
        total = buys + sells
        if total < 20:
            score += 6
            reasons.append("Sparse 24h trading activity")
        if sells > buys * 3 and sells > 20:
            score += 8
            reasons.append(f"Heavy sell pressure: {sells} sells vs {buys} buys")
        elif buys > sells * 2 and buys > 20:
            score -= 2
            reasons.append(f"Buy-side demand leads: {buys} buys vs {sells} sells")

    return ScoreResult(score=clamp(score), status=risk_status(score), reasons=reasons)


def score_token(metadata: dict[str, Any]) -> DualScoreResult:
    """Return separated Rug Score and Speculation Score."""

    return DualScoreResult(
        rug=score_rug_risk(metadata),
        speculation=score_speculation_risk(metadata),
    )
