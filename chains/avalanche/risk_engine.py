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


def add_reason(reasons: list[str], points: int, reason: str) -> int:
    if points:
        reasons.append(reason)
    return points


def score_avax_security(metadata: dict[str, Any]) -> ScoreResult:
    """RugBuster's Avalanche-native RugCheck-style score.

    This combines C-Chain hard evidence when available: deployer behavior,
    bytecode/backdoor hints, holder concentration, funding, and market depth.
    It is intentionally deterministic so reviewers can reproduce the verdict.
    """

    score = 8
    reasons: list[str] = []

    backdoor_score = int(metadata.get("v6_backdoor_risk_score") or metadata.get("backdoor_risk_score") or 0)
    top5 = float(metadata.get("v6_top5_concentration_pct") or metadata.get("top5_holder_pct") or 0)
    concentration = str(metadata.get("v6_concentration_risk") or "").upper()
    velocity = float(metadata.get("v6_rug_velocity_score") or metadata.get("rug_velocity_score") or 0)
    creator_rug_rate = float(metadata.get("creator_rug_rate") or 0)
    holders = int(metadata.get("holders_count") or 0)
    deployer_balance = float(metadata.get("deployer_balance_avax") or 0)
    liquidity_usd = metadata.get("liquidity_usd")
    fdv = metadata.get("fdv")

    if metadata.get("v6_has_backdoor") or backdoor_score >= 40:
        score += add_reason(reasons, min(35, max(12, backdoor_score // 2)), f"Bytecode backdoor risk score {backdoor_score}/100")
    if metadata.get("v6_is_proxy"):
        score += add_reason(reasons, 18, "Upgradeable proxy contract")
    if metadata.get("v6_has_mint"):
        score += add_reason(reasons, 18, "Mint function detected in bytecode")
    if metadata.get("v6_has_blacklist"):
        score += add_reason(reasons, 12, "Blacklist function detected")

    if concentration == "CRITICAL" or top5 >= 90:
        score += add_reason(reasons, 30, f"Critical holder concentration top5={top5:.1f}%")
    elif concentration == "HIGH" or top5 >= 75:
        score += add_reason(reasons, 22, f"High holder concentration top5={top5:.1f}%")
    elif concentration == "MEDIUM" or top5 >= 55:
        score += add_reason(reasons, 10, f"Moderate holder concentration top5={top5:.1f}%")

    if metadata.get("cia_all_fresh_wallets"):
        score += add_reason(reasons, 12, "Fresh funding chain")
    if metadata.get("cia_bot_pattern"):
        score += add_reason(reasons, 10, "Bot-like transaction entropy")
    if metadata.get("cia_wash_detected"):
        score += add_reason(reasons, 18, "Wash trading pattern detected")
    if metadata.get("cia_bot_farm"):
        score += add_reason(reasons, 15, "Bot farm holder cluster")
    if metadata.get("v6_is_fast_rug") or velocity >= 0.65:
        score += add_reason(reasons, 20, f"High rug velocity score {velocity}")

    if creator_rug_rate >= 80:
        score = max(score, 88)
        reasons.append(f"Deployer history: {creator_rug_rate:.1f}% rug rate")
    elif creator_rug_rate >= 40:
        score = max(score, 72)
        reasons.append(f"Deployer history: {creator_rug_rate:.1f}% rug rate")

    if holders and holders < 10:
        score += add_reason(reasons, 8, f"Very few holders ({holders})")
    if deployer_balance and deployer_balance < 0.1:
        score += add_reason(reasons, 6, f"Near-zero deployer balance ({deployer_balance:.4f} AVAX)")

    if liquidity_usd is None:
        reasons.append("Liquidity evidence unavailable")
    else:
        liq = float(liquidity_usd)
        if liq < 5_000:
            score += add_reason(reasons, 16, f"Very thin live liquidity at ${liq:,.0f}")
        elif liq < 25_000:
            score += add_reason(reasons, 9, f"Thin live liquidity at ${liq:,.0f}")
    if liquidity_usd and fdv:
        ratio = float(liquidity_usd) / max(float(fdv), 1.0)
        if ratio < 0.01:
            score += add_reason(reasons, 18, "Liquidity to FDV ratio under 1%")
        elif ratio < 0.03:
            score += add_reason(reasons, 12, "Liquidity to FDV ratio under 3%")

    if not reasons:
        reasons.append("No hard Avalanche rug signals detected")

    final = clamp(round(score))
    return ScoreResult(score=final, status=risk_status(final), reasons=reasons[:8])


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

    native = score_avax_security(metadata)
    if native.score is not None and native.score > score:
        score = native.score
        reasons = native.reasons + reasons[:3]

    return ScoreResult(score=clamp(score), status=risk_status(score), reasons=reasons[:8])


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

    score = 20
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
            score += 42
            reasons.append(f"Very thin live liquidity at ${liq:,.0f}")
        elif liq < 25_000:
            score += 24
            reasons.append(f"Thin live liquidity at ${liq:,.0f}")
        elif liq < 100_000:
            score += 8
            reasons.append(f"Shallow live liquidity at ${liq:,.0f}")
        elif liq >= 500_000:
            score -= 10
            reasons.append(f"Deep live liquidity at ${liq:,.0f}")
        else:
            score -= 2
            reasons.append(f"Meaningful live liquidity at ${liq:,.0f}")

    if fdv is None:
        reasons.append("FDV unavailable from market sources")
    else:
        fdv_value = float(fdv)
        if liquidity_usd and fdv_value > 0:
            ratio = float(liquidity_usd) / fdv_value
            if ratio < 0.01:
                score += 45
                reasons.append("Liquidity to FDV ratio is under 1% - exit liquidity risk is extreme")
            elif ratio < 0.03:
                score += 35
                reasons.append("Liquidity to FDV ratio is under 3% - market depth is dangerously thin")
            elif ratio < 0.05:
                score += 28
                reasons.append("Liquidity to FDV ratio is under 5% - exit liquidity looks fragile")
            elif ratio < 0.15:
                score += 14
                reasons.append("Liquidity to FDV ratio is under 15% - market depth is shallow")
            elif ratio >= 0.3:
                score -= 8
                reasons.append("Liquidity to FDV ratio is very healthy")
            elif ratio >= 0.15:
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
