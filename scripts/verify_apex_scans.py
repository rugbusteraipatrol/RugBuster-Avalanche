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
    expected_label: str
    max_risk: int


KNOWN_TOKENS = [
    ScanExpectation("WAVAX", "0xB31f66AA3C1e785363F0875A1B74E27b85FD66c7", "GOOD", 30),
    ScanExpectation("USDC", "0xB97EF9Ef8734C71904D8002F8b6Bc66Dd9c48a6E", "GOOD", 30),
    ScanExpectation("USDT.e", "0x9702230A8Ea53601f5cD2dc00fDBc13d4dF4A8c7", "GOOD", 30),
    ScanExpectation("WETH.e", "0x49D5c2BdFfac6CE2BFdB6640F4F80f226bc10bAB", "GOOD", 35),
    ScanExpectation("JOE", "0x6e84a6216ea6dacc71ee8e6b0a5b7322eebc0fdd", "GOOD", 35),
    ScanExpectation("QI", "0x8729438eb15e2c8b576fcc6aecda6a148776c0f5", "GOOD", 40),
    ScanExpectation("COQ", "0x420fca0121dc28039145009570975747295f2329", "GOOD", 40),
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
        safety_score = int(report.get("score") or 0)
        risk_score = 100 - safety_score
        label = str(report.get("label") or "")

        ok = label == item.expected_label and risk_score <= item.max_risk
        line = {
            "symbol": item.symbol,
            "label": label,
            "safety": safety_score,
            "risk": risk_score,
            "liquidity_usd": report.get("liquidity_usd"),
            "dex": report.get("dex_id"),
            "pair": report.get("pair_address"),
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
