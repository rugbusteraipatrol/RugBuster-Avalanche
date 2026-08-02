"""Large-scale QA run: replay a stratified Avalanche sample through the live
/score endpoint and compute robustness / invariant / agreement metrics.

Read-only: issues GET /score only. Results are written incrementally so the run
is resumable if interrupted.
"""

import json
import os
import random
import statistics
import sys
import time

import requests

ENDPOINT = "https://web-production-376bf.up.railway.app/score"
TARGET_N = int(os.getenv("TARGET_N", "300"))
SLEEP = float(os.getenv("SCAN_SLEEP", "0.8"))
OUT = "large_qa_results.json"

session = requests.Session()
session.headers.update({"Accept": "application/json", "User-Agent": "rugbuster-shield-demo-qa/3.0"})

# Canonical assets whose address is asserted by the product itself (whitelist),
# independently re-verified against DexScreener/GeckoTerminal during QA.
BLUECHIP = {
    "0xb31f66aa3c1e785363f0875a1b74e27b85fd66c7": "WAVAX",
    "0xb97ef9ef8734c71904d8002f8b6bc66dd9c48a6e": "USDC",
    "0xa7d7079b0fead91f3e65f86e8915cb59c1a4c664": "USDC.e",
    "0x9702230a8ea53601f5cd2dc00fdbc13d4df4a8c7": "USDt",
    "0x49d5c2bdffac6ce2bfdb6640f4f80f226bc10bab": "WETH.e",
    "0x152b9d0fdc40c096757f570a51e494bd4b943e50": "BTC.b",
    "0x2b2c81e08f1af8835a78bb2a90ae924ace0ea4be": "sAVAX",
    "0x5947bb275c521040051d82396192181b413227a3": "LINK.e",
    "0xd586e7f844cea2f87f50152665bcbc2c279d8d70": "DAI.e",
    "0x50b7545627a5162f82a992c33b87adc75187b218": "WBTC.e",
    "0x6e84a6216ea6dacc71ee8e6b0a5b7322eebc0fdd": "JOE",
    "0x8729438eb15e2c8b576fcc6aecda6a148776c0f5": "QI",
    "0x60781c2586d68229fde47564546784ab3faca982": "PNG",
    "0x130966628846bfd36ff31a822705796e8cb8c18d": "MIM",
    "0xd24c2ad096400b6fbcd2ad8b24e7acbc21a1da64": "FRAX",
    "0xc891eb4cbdeff6e073e859e987815ed1505c2acd": "EURC",
}


def stratify(candidates, target):
    hard, soft, clean, nodata = [], [], [], []
    for c in candidates:
        g = c.get("goplus") or {}
        if not c.get("goplus"):
            nodata.append(c)
        elif g.get("hard_scam"):
            hard.append(c)
        elif g.get("soft_risk"):
            soft.append(c)
        else:
            clean.append(c)

    random.seed(20260801)
    for bucket in (hard, soft, clean, nodata):
        random.shuffle(bucket)

    # Take every hard-scam token we found (they are the scarcest and most
    # informative), then fill the rest proportionally.
    chosen = list(hard)
    remaining = max(0, target - len(chosen))
    quota = {
        "soft": int(remaining * 0.35),
        "clean": int(remaining * 0.35),
        "nodata": remaining - int(remaining * 0.35) - int(remaining * 0.35),
    }
    chosen += soft[: quota["soft"]]
    chosen += clean[: quota["clean"]]
    chosen += nodata[: quota["nodata"]]

    # Always include the canonical assets as an embedded control group.
    have = {c["address"].lower() for c in chosen}
    for addr, sym in BLUECHIP.items():
        if addr not in have:
            chosen.append({
                "symbol": sym, "name": sym, "address": addr,
                "reserve_usd": None, "age_hours": None,
                "discovery": "bluechip_control", "goplus": None,
            })
    return chosen


def classify_expectation(entry):
    """Return (tier, expectation).

    Tiering note: GoPlus flags are treated as independent *signals*, not as
    ground-truth verdicts. GMX (an established protocol token) carries
    hidden_owner/owner_change_balance flags, which shows a raw GoPlus flag is
    not by itself proof of a scam. So only two tiers carry hard assertions:

      A_bluechip    canonical assets the product itself whitelists -> must be GOOD
      A_rug_factory unverified source AND a single holder AND ~100% concentration
                    -> the abandoned/fresh-deployment pattern that caused the
                       original incident; must never be labelled GOOD

    Everything else is measured for robustness and agreement, not scored
    against an assumed correct answer.
    """
    addr = entry["address"].lower()
    if addr in BLUECHIP:
        return "A_bluechip", "must_be_GOOD"

    g = entry.get("goplus") or {}
    soft = g.get("soft_risk") or []
    unverified = any(s.startswith("unverified_source") for s in soft)
    single_holder = any(s.startswith("holder_count") and s.endswith(("=0", "=1")) for s in soft)
    concentrated = any(s.startswith("top_holder") for s in soft)
    if unverified and single_holder and concentrated:
        return "A_rug_factory", "must_not_be_GOOD"

    if g.get("hard_scam"):
        return "B_goplus_flagged", "agreement_only"
    if soft:
        return "B_soft_risk", "agreement_only"
    if entry.get("goplus"):
        return "B_clean", "no_assertion"
    return "B_unlabelled", "no_assertion"


def main():
    with open("sample_candidates.json", encoding="utf-8") as f:
        candidates = json.load(f)

    sample = stratify(candidates, TARGET_N)
    print(f"Sample size: {len(sample)}")

    done = {}
    if os.path.exists(OUT):
        with open(OUT, encoding="utf-8") as f:
            for row in json.load(f):
                done[row["address"].lower()] = row
        print(f"Resuming: {len(done)} already scanned")

    results = list(done.values())
    for i, entry in enumerate(sample, 1):
        addr = entry["address"].lower()
        if addr in done:
            continue
        tier, expectation = classify_expectation(entry)
        t0 = time.time()
        try:
            r = session.get(ENDPOINT, params={"address": entry["address"]}, timeout=45)
            elapsed = time.time() - t0
            status = r.status_code
            try:
                body = r.json()
            except Exception:
                body = {"ok": False, "error": f"non-JSON: {r.text[:120]}"}
        except Exception as exc:
            elapsed = time.time() - t0
            status = None
            body = {"ok": False, "error": f"{type(exc).__name__}: {exc}"}

        row = {
            "symbol": entry.get("symbol"),
            "address": entry["address"],
            "tier": tier,
            "expectation": expectation,
            "discovery": entry.get("discovery"),
            "reserve_usd": entry.get("reserve_usd"),
            "age_hours": entry.get("age_hours"),
            "goplus": entry.get("goplus"),
            "http_status": status,
            "ok": body.get("ok"),
            "label": body.get("label"),
            "rug_score": body.get("rug_score"),
            "risk_percent": body.get("risk_percent"),
            "source": body.get("source"),
            "risk_flags": body.get("risk_flags"),
            "error": body.get("error"),
            "latency_s": round(elapsed, 2),
        }
        results.append(row)

        if i % 10 == 0 or row["label"] is None:
            print(f"[{i}/{len(sample)}] {tier:15s} {str(entry.get('symbol'))[:12]:12s} "
                  f"-> {row['label']} ({row['latency_s']}s)")
            sys.stdout.flush()
        with open(OUT, "w", encoding="utf-8") as f:
            json.dump(results, f, indent=2)
        time.sleep(SLEEP)

    print(f"\nDone. {len(results)} results -> {OUT}")

    lat = [r["latency_s"] for r in results if r.get("latency_s")]
    if lat:
        lat_sorted = sorted(lat)
        print(f"latency p50={statistics.median(lat):.2f}s "
              f"p95={lat_sorted[int(len(lat_sorted)*0.95)-1]:.2f}s max={max(lat):.2f}s")


if __name__ == "__main__":
    main()
