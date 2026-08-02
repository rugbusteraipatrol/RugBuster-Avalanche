"""Build a stratified Avalanche C-Chain token sample for the large-scale QA run.

Tier A (labelled ground truth)   -> supports precision/recall claims
Tier B (unlabelled bulk)         -> supports robustness + invariant + agreement claims

Sources: GeckoTerminal (pool discovery), GoPlus token-security (labels).
Nothing here touches the RugBuster API.
"""

import json
import time
from datetime import datetime, timezone

import requests

session = requests.Session()
session.headers.update({"Accept": "application/json", "User-Agent": "rugbuster-qa-sampler/1.0"})

GT = "https://api.geckoterminal.com/api/v2/networks/avax"
GOPLUS = "https://api.gopluslabs.io/api/v1/token_security/43114"
GT_SLEEP = 2.5   # GeckoTerminal allows ~30 req/min
GOPLUS_SLEEP = 1.2

QUOTE_SYMBOLS = {
    "WAVAX", "AVAX", "USDC", "USDC.E", "USDT", "USDT.E", "USDT0", "DAI", "DAI.E",
    "BUSD", "WETH.E", "WETH", "BTC.B", "MIM", "FRAX", "EURC", "EUROC", "AUSD",
}

now = datetime.now(timezone.utc)
candidates: dict[str, dict] = {}


def add_pool_page(path: str, params: dict, bucket: str) -> int:
    added = 0
    r = session.get(f"{GT}/{path}", params={**params, "include": "base_token"}, timeout=25)
    if r.status_code != 200:
        print(f"   [{r.status_code}] {path} {params}")
        return -1
    data = r.json()
    inc = {i["id"]: i["attributes"] for i in data.get("included", [])}
    for p in data.get("data", []):
        attrs = p["attributes"]
        tok = inc.get(p["relationships"]["base_token"]["data"]["id"], {})
        addr = (tok.get("address") or "").lower()
        sym = (tok.get("symbol") or "").upper()
        if not addr or sym in QUOTE_SYMBOLS:
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
        prev = candidates.get(addr)
        rec = {
            "symbol": tok.get("symbol"),
            "name": tok.get("name"),
            "address": addr,
            "reserve_usd": reserve,
            "age_hours": round(age_h, 1) if age_h is not None else None,
            "discovery": bucket,
        }
        if prev is None:
            candidates[addr] = rec
            added += 1
        elif (reserve or 0) > (prev.get("reserve_usd") or 0):
            prev.update(rec)
    return added


print("== Discovering pools ==")
for page in range(1, 11):
    n = add_pool_page("new_pools", {"page": page}, "new_pool")
    print(f"  new_pools p{page}: +{n} (total {len(candidates)})")
    time.sleep(GT_SLEEP)

for page in range(1, 11):
    n = add_pool_page("pools", {"page": page, "sort": "h24_volume_usd_desc"}, "top_volume")
    print(f"  top_volume p{page}: +{n} (total {len(candidates)})")
    time.sleep(GT_SLEEP)

for page in range(1, 6):
    n = add_pool_page("trending_pools", {"page": page, "duration": "24h"}, "trending")
    print(f"  trending p{page}: +{n} (total {len(candidates)})")
    time.sleep(GT_SLEEP)

print(f"\nTotal unique candidate tokens: {len(candidates)}")

print("\n== Labelling via GoPlus ==")
addrs = list(candidates.keys())
labelled = 0
for i in range(0, len(addrs), 20):
    batch = addrs[i:i + 20]
    try:
        r = session.get(GOPLUS, params={"contract_addresses": ",".join(batch)}, timeout=30)
        result = r.json().get("result", {}) or {}
    except Exception as exc:
        print(f"  batch {i//20}: FAIL {exc}")
        time.sleep(GOPLUS_SLEEP)
        continue
    for a in batch:
        info = result.get(a)
        if not info:
            candidates[a]["goplus"] = None
            continue
        labelled += 1

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
    print(f"  batch {i//20 + 1}/{(len(addrs)+19)//20} done (labelled {labelled})")
    time.sleep(GOPLUS_SLEEP)

with open("sample_candidates.json", "w", encoding="utf-8") as f:
    json.dump(list(candidates.values()), f, indent=2)

hard = [c for c in candidates.values() if (c.get("goplus") or {}).get("hard_scam")]
soft = [c for c in candidates.values() if (c.get("goplus") or {}).get("soft_risk") and not (c.get("goplus") or {}).get("hard_scam")]
clean = [c for c in candidates.values() if c.get("goplus") and not (c["goplus"].get("hard_scam") or c["goplus"].get("soft_risk"))]
nodata = [c for c in candidates.values() if not c.get("goplus")]

print(f"\n== Sample composition ==")
print(f"  GoPlus hard-scam signals : {len(hard)}")
print(f"  GoPlus soft-risk signals : {len(soft)}")
print(f"  GoPlus clean             : {len(clean)}")
print(f"  No GoPlus data           : {len(nodata)}")
print(f"  TOTAL                    : {len(candidates)}")
print("\nSaved sample_candidates.json")
