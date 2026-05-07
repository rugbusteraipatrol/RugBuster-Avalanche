"""Temporary deterministic risk engine for RugBuster Avalanche.

This module is intentionally simple and explainable for demos. The final version
can replace this scoring layer with a local fine-tuned RugBusterAI model while
keeping the adapter and registry interfaces stable.
"""

from __future__ import annotations

from dataclasses import dataclass, asdict
from typing import Any


@dataclass(frozen=True)
class RiskResult:
    score: int
    label: str
    reasons: list[str]

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def label_for_score(score: int) -> str:
    if score < 40:
        return "DANGER"
    if score < 70:
        return "WARN"
    return "GOOD"


def score_token(metadata: dict[str, Any]) -> RiskResult:
    """Return a 0-100 safety score from adapter metadata.

    Higher scores mean safer tokens. The rules favor transparency: verified-ish
    metadata, normal supply shape, and meaningful liquidity improve score;
    missing metadata and suspicious ownership signals reduce it.
    """

    score = 65
    reasons: list[str] = []

    symbol = str(metadata.get("symbol") or "").strip()
    name = str(metadata.get("name") or "").strip()
    decimals = metadata.get("decimals")
    total_supply = metadata.get("total_supply")
    liquidity_usd = metadata.get("liquidity_usd")
    deployer = metadata.get("deployer")

    if not name or name.lower() == "unknown":
        score -= 10
        reasons.append("Missing or unknown token name")
    else:
        score += 5
        reasons.append("Token name resolved")

    if not symbol or symbol.lower() == "unknown":
        score -= 10
        reasons.append("Missing or unknown symbol")
    else:
        score += 5
        reasons.append("Token symbol resolved")

    if decimals is None:
        score -= 5
        reasons.append("Decimals unavailable")
    elif int(decimals) > 24:
        score -= 10
        reasons.append("Unusual decimals value")
    else:
        score += 3
        reasons.append("Decimals within normal range")

    if total_supply is None:
        score -= 8
        reasons.append("Total supply unavailable")
    elif int(total_supply) <= 0:
        score -= 20
        reasons.append("Invalid total supply")
    else:
        score += 4
        reasons.append("Total supply readable")

    if liquidity_usd is None:
        reasons.append("Liquidity USD unavailable; neutral in demo mode")
    elif float(liquidity_usd) < 1_000:
        score -= 20
        reasons.append("Very low detected liquidity")
    elif float(liquidity_usd) < 10_000:
        score -= 8
        reasons.append("Thin detected liquidity")
    else:
        score += 10
        reasons.append("Meaningful detected liquidity")

    if not deployer:
        score -= 5
        reasons.append("Deployer unavailable")
    else:
        reasons.append("Deployer captured")

    lower_text = f"{name} {symbol}".lower()
    suspicious_terms = ("test", "rug", "scam", "100x", "pump", "airdrop", "claim")
    hits = [term for term in suspicious_terms if term in lower_text]
    if hits:
        score -= 10 + (5 * min(len(hits), 3))
        reasons.append(f"Suspicious naming terms: {', '.join(hits)}")

    score = max(0, min(100, score))
    return RiskResult(score=score, label=label_for_score(score), reasons=reasons)
