"""Expand the candidate pool and re-label it correctly.

Fixes a methodology bug found during QA: GoPlus batch queries silently return
partial results under throttling, which earlier made ~94% of the sample look
"unlabelled" when the data actually exists. This pass uses small batches with
pacing plus an individual retry for anything that comes back empty.
"""

import json
import os
import time
from datetime import datetime, timezone

import requests

session = requests.Session()
session.headers.update({"Accept": "application/json", "User-Agent": "rugbuster-qa-sampler/2.0"})

GT = "https://api.geckoterminal.com/api/v2/networks/avax"
GOPLUS = "https://api.gopluslabs.io/api/v1/token_security/43114"

QUOTE_SYMBOLS = {
    "WAVAX", "AVAX", "USDC", "USDC.E", "USDT", "USDT.E", "USDT0", "DAI", "DAI.E",
    "BUSD", "WETH.E", "WETH", "BTC.B", "MIM", "FRAX", "EURC", "EUROC", "AUSD",
}

TARGET_CANDIDATES = int(os.getenv("TARGET_CANDIDATES", "330"))
now = datetime.now(timezone.utc)

with open("sample_candidates.json", encoding="utf-8") as f:
    candidates = {c["address"].lower(): c for c in json.load(f)}
print(f"Loaded {len(candidates)} existing candidates")


def add_pool_page(path, params, bucket):
    r = session.get(f"{GT}/{path}", params={**params, "include": "base_token"}, timeout=25)
    if r.status_code != 200:
        return -1
    data = r.json()
    inc = {i["id"]: i["attributes"] for i in data.get("included", [])}
    added = 0
    for p in data.get("data", []):
        attrs = p["attributes"]
        tok = inc.get(p["relationships"]["base_token"]["data"]["id"], {})
        addr = (tok.get("address") or "").lower()
        sym = (tok.get("symbol") or "").upper()
        if not addr or sym in QUOTE_SYMBOLS or addr in candidates:
            continue
        created = attrs.get("pool_created_at")
        age_h = None
        if created:
            try:
                age_h = (now - datetime.fromisoformat(created.replace("Z", "+00:00"))).total_seconds() / 3600
            except Exception:
                pass
        reserve = attrs.get("reserve_in_usd")
        try:
            reserve = float(reserve) if reserve is not None else None
        except Exception:
            reserve = None
        candidates[addr] = {
            "symbol": tok.get("symbol"), "name": tok.get("name"), "address": addr,
            "reserve_usd": reserve, "age_hours": round(age_h, 1) if age_h is not None else None,
            "discovery": bucket,
        }
        added += 1
    return added


print("\n== Expanding discovery (paced for GeckoTerminal limits) ==")
plans = (
    [("new_pools", {"page": p}, "new_pool") for p in range(1, 11)]
    + [("pools", {"page": p, "sort": "h24_volume_usd_desc"}, "top_volume") for p in range(1, 11)]
    + [("trending_pools", {"page": p, "duration": "24h"}, "trending") for p in range(1, 6)]
    + [("pools", {"page": p, "sort": "h24_tx_count_desc"}, "top_tx") for p in range(1, 6)]
)
for path, params, bucket in plans:
    if len(candidates) >= TARGET_CANDIDATES:
        print(f"  reached target {TARGET_CANDIDATES}")
        break
    n = add_pool_page(path, params, bucket)
    if n == -1:
        print(f"  [429/err] {path} {params} - backing off 20s")
        time.sleep(20)
        continue
    print(f"  {bucket:11s} {params.get('page')}: +{n} (total {len(candidates)})")
    time.sleep(6)

print(f"\nCandidate pool: {len(candidates)}")


def parse_goplus(info):
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

    hard = []
    if flag("is_honeypot"):
        hard.append("honeypot")
    if flag("cannot_sell_all"):
        hard.append("cannot_sell_all")
    if num("sell_tax") > 0.5:
        hard.append(f"sell_tax={num('sell_tax')}")
    if flag("hidden_owner"):
        hard.append("hidden_owner")
    if flag("owner_change_balance"):
        hard.append("owner_change_balance")
    if flag("can_take_back_ownership"):
        hard.append("can_take_back_ownership")

    soft = []
    if info.get("is_open_source") == "0":
        soft.append("unverified_source")
    if holder_count <= 1:
        soft.append(f"holder_count={holder_count}")
    if top_pct >= 0.9:
        soft.append(f"top_holder={top_pct:.2f}")
    if flag("is_mintable"):
        soft.append("mintable")
    if flag("is_blacklisted"):
        soft.append("blacklist_fn")

    return {
        "hard_scam": hard, "soft_risk": soft, "holder_count": holder_count,
        "top_holder_pct": top_pct, "is_open_source": info.get("is_open_source"),
        "token_symbol": info.get("token_symbol"),
    }


# Re-label EVERYTHING (previous labels are untrustworthy due to the throttling bug)
addrs = list(candidates.keys())
print(f"\n== Re-labelling all {len(addrs)} candidates (batch=5, paced) ==")
empty = []
for i in range(0, len(addrs), 5):
    batch = addrs[i:i + 5]
    try:
        r = session.get(GOPLUS, params={"contract_addresses": ",".join(batch)}, timeout=30)
        result = r.json().get("result", {}) or {}
    except Exception:
        result = {}
    for a in batch:
        info = result.get(a)
        if info:
            candidates[a]["goplus"] = parse_goplus(info)
        else:
            empty.append(a)
            candidates[a]["goplus"] = None
    if (i // 5) % 10 == 0:
        print(f"  {i + len(batch)}/{len(addrs)} (empty so far: {len(empty)})")
    time.sleep(3)

print(f"\n== Individual retry for {len(empty)} empties ==")
recovered = 0
for j, a in enumerate(empty, 1):
    try:
        r = session.get(GOPLUS, params={"contract_addresses": a}, timeout=30)
        info = (r.json().get("result", {}) or {}).get(a)
    except Exception:
        info = None
    if info:
        candidates[a]["goplus"] = parse_goplus(info)
        recovered += 1
    if j % 25 == 0:
        print(f"  retried {j}/{len(empty)} (recovered {recovered})")
    time.sleep(2.5)

with open("sample_candidates.json", "w", encoding="utf-8") as f:
    json.dump(list(candidates.values()), f, indent=2)

hard = [c for c in candidates.values() if (c.get("goplus") or {}).get("hard_scam")]
soft = [c for c in candidates.values() if (c.get("goplus") or {}).get("soft_risk") and not (c.get("goplus") or {}).get("hard_scam")]
clean = [c for c in candidates.values() if c.get("goplus") and not (c["goplus"].get("hard_scam") or c["goplus"].get("soft_risk"))]
nodata = [c for c in candidates.values() if not c.get("goplus")]

print("\n== FINAL sample composition ==")
print(f"  hard-scam signals : {len(hard)}")
print(f"  soft-risk signals : {len(soft)}")
print(f"  clean             : {len(clean)}")
print(f"  no GoPlus data    : {len(nodata)}  ({100*len(nodata)/max(1,len(candidates)):.1f}%)")
print(f"  TOTAL             : {len(candidates)}")
print(f"  labelled coverage : {100*(len(candidates)-len(nodata))/max(1,len(candidates)):.1f}%")
