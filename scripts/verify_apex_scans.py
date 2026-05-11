from __future__ import annotations

import json
import sys
from dataclasses import dataclass

import requests


API_URL = "https://web-production-376bf.up.railway.app/api/scan"


@dataclass(frozen=True)
class ScanExpectation:
    symbol: str
    address: str
    max_rug_score: int
    max_speculation_score: int


@dataclass(frozen=True)
class UnknownLiquidityExpectation:
    symbol: str
    address: str
    min_rug_score: int


KNOWN_TOKENS = [
    ScanExpectation("WAVAX", "0xB31f66AA3C1e785363F0875A1B74E27b85FD66c7", 35, 40),
    ScanExpectation("USDC", "0xB97EF9Ef8734C71904D8002F8b6Bc66Dd9c48a6E", 35, 40),
    ScanExpectation("USDT.e", "0x9702230A8Ea53601f5cD2dc00fDBc13d4dF4A8c7", 35, 40),
    ScanExpectation("WETH.e", "0x49D5c2BdFfac6CE2BFdB6640F4F80f226bc10bAB", 35, 45),
    ScanExpectation("JOE", "0x6e84a6216ea6dacc71ee8e6b0a5b7322eebc0fdd", 35, 45),
    ScanExpectation("QI", "0x8729438eb15e2c8b576fcc6aecda6a148776c0f5", 35, 50),
    ScanExpectation("COQ", "0x420fca0121dc28039145009570975747295f2329", 35, 55),
]

UNKNOWN_LIQUIDITY = [
    UnknownLiquidityExpectation("NO_PAIR_SAMPLE", "0x1111111111111111111111111111111111111111", 60),
]


def main() -> int:
    failures = 0
    print(f"Verifying Apex scanner against {API_URL}")

    for item in KNOWN_TOKENS:
        response = requests.post(API_URL, json={"address": item.address}, timeout=30)
        response.raise_for_status()
        payload = response.json()
        if not payload.get("ok") or not payload.get("report"):
            print(f"[FAIL] {item.symbol}: malformed response")
            failures += 1
            continue

        report = payload["report"]
        rug_score = report.get("rug_score")
        speculation_score = report.get("speculation_score")
        rug_status = str(report.get("rug_status") or "")
        speculation_status = str(report.get("speculation_status") or "")

        ok = (
            rug_score is not None
            and speculation_score is not None
            and int(rug_score) <= item.max_rug_score
            and int(speculation_score) <= item.max_speculation_score
            and rug_status == "LOW"
            and speculation_status == "LOW"
        )
        line = {
            "symbol": item.symbol,
            "rug_score": rug_score,
            "rug_status": rug_status,
            "speculation_score": speculation_score,
            "speculation_status": speculation_status,
            "liquidity_usd": report.get("liquidity_usd"),
            "dex": report.get("dex_id"),
            "pair": report.get("pair_address"),
            "ok": ok,
        }
        print(json.dumps(line, ensure_ascii=True))
        if not ok:
            failures += 1

    for item in UNKNOWN_LIQUIDITY:
        response = requests.post(API_URL, json={"address": item.address}, timeout=30)
        response.raise_for_status()
        payload = response.json()
        if not payload.get("ok") or not payload.get("report"):
            print(f"[FAIL] {item.symbol}: malformed response")
            failures += 1
            continue

        report = payload["report"]
        rug_score = report.get("rug_score")
        rug_status = str(report.get("rug_status") or "")
        speculation_score = report.get("speculation_score")
        speculation_status = str(report.get("speculation_status") or "")
        ok = (
            rug_score is not None
            and int(rug_score) >= item.min_rug_score
            and rug_status in {"ELEVATED", "HIGH"}
            and speculation_score is None
            and speculation_status == "UNKNOWN"
            and not report.get("has_liquidity_evidence")
        )
        line = {
            "symbol": item.symbol,
            "rug_score": rug_score,
            "rug_status": rug_status,
            "speculation_score": speculation_score,
            "speculation_status": speculation_status,
            "has_liquidity_evidence": report.get("has_liquidity_evidence"),
            "ok": ok,
        }
        print(json.dumps(line, ensure_ascii=True))
        if not ok:
            failures += 1

    if failures:
        print(f"Verification failed for {failures} token(s).")
        return 1

    print("All Apex verification checks passed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
