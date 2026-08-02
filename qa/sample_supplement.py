"""Supplementary discovery via DexScreener search (more generous rate limits
than GeckoTerminal) + GoPlus labelling, merged into sample_candidates.json.
"""

import json
import os
import string
import time

import requests

session = requests.Session()
session.headers.update({"Accept": "application/json", "User-Agent": "rugbuster-qa-sampler/1.1"})

DEX_SEARCH = "https://api.dexscreener.com/latest/dex/search"
GOPLUS = "https://api.gopluslabs.io/api/v1/token_security/43114"

QUOTE_SYMBOLS = {
    "WAVAX", "AVAX", "USDC", "USDC.E", "USDT", "USDT.E", "USDT0", "DAI", "DAI.E",
    "BUSD", "WETH.E", "WETH", "BTC.B", "MIM", "FRAX", "EURC", "EUROC", "AUSD",
}

existing = []
if os.path.exists("sample_candidates.json"):
    with open("sample_candidates.json", encoding="utf-8") as f:
        existing = json.load(f)
candidates = {c["address"].lower(): c for c in existing}
print(f"Starting from {len(candidates)} existing candidates")

# Broad query terms: quote pairings pull the widest set of AVAX pairs, plus
# common meme/defi word fragments to reach the long tail.
QUERIES = [
    "WAVAX", "AVAX USDC", "AVAX USDT", "avalanche", "joe", "pangolin", "arena",
    "coq", "inu", "dog", "cat", "pepe", "moon", "safe", "baby", "meme", "ai",
    "gold", "bank", "swap", "yield", "farm", "stake", "vault", "dao", "protocol",
    "finance", "token", "coin", "capital", "labs", "network", "chain", "world",
] + [f"{c}avax" for c in string.ascii_lowercase[:10]]

print(f"\n== DexScreener discovery ({len(QUERIES)} queries) ==")
for qi, q in enumerate(QUERIES, 1):
    try:
        r = session.get(DEX_SEARCH, params={"q": q}, timeout=25)
        if r.status_code != 200:
            print(f"  [{r.status_code}] q={q}")
            time.sleep(2)
            continue
        pairs = r.json().get("pairs") or []
    except Exception as exc:
        print(f"  FAIL q={q}: {exc}")
        time.sleep(2)
        continue

    added = 0
    for p in pairs:
        if (p.get("chainId") or "").lower() != "avalanche":
            continue
        base = p.get("baseToken") or {}
        addr = (base.get("address") or "").lower()
        sym = (base.get("symbol") or "").upper()
        if not addr or sym in QUOTE_SYMBOLS:
            continue
        liq = (p.get("liquidity") or {}).get("usd") or 0
        try:
            liq = float(liq)
        except Exception:
            liq = 0.0
        if addr not in candidates:
            candidates[addr] = {
                "symbol": base.get("symbol"),
                "name": base.get("name"),
                "address": addr,
                "reserve_usd": liq,
                "age_hours": None,
                "discovery": "dexscreener_search",
            }
            added += 1
    print(f"  [{qi:02d}/{len(QUERIES)}] q={q:14s} +{added} (total {len(candidates)})")
    time.sleep(0.7)

print(f"\nTotal unique candidates: {len(candidates)}")

# Label anything not yet labelled
unlabelled = [a for a, c in candidates.items() if "goplus" not in c]
print(f"\n== GoPlus labelling of {len(unlabelled)} new candidates ==")
for i in range(0, len(unlabelled), 20):
    batch = unlabelled[i:i + 20]
    try:
        r = session.get(GOPLUS, params={"contract_addresses": ",".join(batch)}, timeout=30)
        result = r.json().get("result", {}) or {}
    except Exception as exc:
        print(f"  batch {i//20 + 1}: FAIL {exc}")
        for a in batch:
            candidates[a]["goplus"] = None
        time.sleep(1.5)
        continue

    for a in batch:
        info = result.get(a)
        if not info:
            candidates[a]["goplus"] = None
            continue

        def flag(k):
            return info.get(k) == "1"

        def num(k):
            try:
                return float(info.get(k) or 0)
            except Exception:
                return 0.0

        holders = info.get("holders") or []
        top_pct = max([float(h.get("percent") or 0) for h in holders], default=0.0)
        try:
            holder_count = int(info.get("holder_count") or 0)
        except Exception:
            holder_count = 0

        hard_scam = []
        if flag("is_honeypot"):
            hard_scam.append("honeypot")
        if flag("cannot_sell_all"):
            hard_scam.append("cannot_sell_all")
        if num("sell_tax") > 0.5:
            hard_scam.append(f"sell_tax={num('sell_tax')}")
        if flag("hidden_owner"):
            hard_scam.append("hidden_owner")
        if flag("owner_change_balance"):
            hard_scam.append("owner_change_balance")
        if flag("can_take_back_ownership"):
            hard_scam.append("can_take_back_ownership")

        soft_risk = []
        if info.get("is_open_source") == "0":
            soft_risk.append("unverified_source")
        if holder_count <= 1:
            soft_risk.append(f"holder_count={holder_count}")
        if top_pct >= 0.9:
            soft_risk.append(f"top_holder={top_pct:.2f}")
        if flag("is_mintable"):
            soft_risk.append("mintable")
        if flag("is_blacklisted"):
            soft_risk.append("blacklist_fn")

        candidates[a]["goplus"] = {
            "hard_scam": hard_scam,
            "soft_risk": soft_risk,
            "holder_count": holder_count,
            "top_holder_pct": top_pct,
            "is_open_source": info.get("is_open_source"),
            "token_symbol": info.get("token_symbol"),
        }
    print(f"  batch {i//20 + 1}/{(len(unlabelled)+19)//20}")
    time.sleep(1.2)

with open("sample_candidates.json", "w", encoding="utf-8") as f:
    json.dump(list(candidates.values()), f, indent=2)

hard = [c for c in candidates.values() if (c.get("goplus") or {}).get("hard_scam")]
soft = [c for c in candidates.values() if (c.get("goplus") or {}).get("soft_risk") and not (c.get("goplus") or {}).get("hard_scam")]
clean = [c for c in candidates.values() if c.get("goplus") and not (c["goplus"].get("hard_scam") or c["goplus"].get("soft_risk"))]
nodata = [c for c in candidates.values() if not c.get("goplus")]

print("\n== Final sample composition ==")
print(f"  hard-scam signals : {len(hard)}")
print(f"  soft-risk signals : {len(soft)}")
print(f"  clean             : {len(clean)}")
print(f"  no GoPlus data    : {len(nodata)}")
print(f"  TOTAL             : {len(candidates)}")
