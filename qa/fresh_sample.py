"""Collect a fresh random Avalanche token sample, independent of the one used
for earlier runs, so a re-test cannot pass simply by memorising the old set.
"""

import json
import time
from datetime import datetime, timezone

import requests

session = requests.Session()
session.headers.update({"Accept": "application/json", "User-Agent": "rugbuster-qa-fresh/1.0"})
GT = "https://api.geckoterminal.com/api/v2/networks/avax"
GOPLUS = "https://api.gopluslabs.io/api/v1/token_security/43114"

QUOTES = {"WAVAX", "AVAX", "USDC", "USDC.E", "USDT", "USDT.E", "USDT0", "DAI", "DAI.E",
          "BUSD", "WETH.E", "WETH", "BTC.B", "MIM", "FRAX", "EURC", "EUROC", "AUSD"}

# Anything already scanned in a previous run is excluded, so this sample is new.
seen_before = set()
for path in ("sample_candidates.json", "run4_results.json"):
    try:
        for row in json.load(open(path, encoding="utf-8")):
            seen_before.add(row["address"].lower())
    except Exception:
        pass
print(f"excluding {len(seen_before)} previously seen addresses")

now = datetime.now(timezone.utc)
fresh: dict[str, dict] = {}

plans = (
    [("new_pools", {"page": p}, "new_pool") for p in range(1, 9)]
    + [("pools", {"page": p, "sort": "h24_volume_usd_desc"}, "top_volume") for p in range(1, 6)]
    + [("trending_pools", {"page": p, "duration": "6h"}, "trending_6h") for p in range(1, 4)]
    + [("pools", {"page": p, "sort": "h24_tx_count_desc"}, "top_tx") for p in range(1, 4)]
)

for path, params, bucket in plans:
    try:
        r = session.get(f"{GT}/{path}", params={**params, "include": "base_token"}, timeout=25)
    except Exception as exc:
        print(f"  {bucket} p{params.get('page')}: {exc}")
        time.sleep(10)
        continue
    if r.status_code != 200:
        print(f"  {bucket} p{params.get('page')}: HTTP {r.status_code}, backing off")
        time.sleep(20)
        continue
    data = r.json()
    inc = {i["id"]: i["attributes"] for i in data.get("included", [])}
    added = 0
    for p in data.get("data", []):
        attrs = p["attributes"]
        tok = inc.get(p["relationships"]["base_token"]["data"]["id"], {})
        addr = (tok.get("address") or "").lower()
        sym = (tok.get("symbol") or "").upper()
        if not addr or sym in QUOTES or addr in seen_before or addr in fresh:
            continue
        created = attrs.get("pool_created_at")
        age_h = None
        if created:
            try:
                age_h = (now - datetime.fromisoformat(created.replace("Z", "+00:00"))).total_seconds() / 3600
            except Exception:
                pass
        try:
            reserve = float(attrs.get("reserve_in_usd") or 0)
        except Exception:
            reserve = None
        fresh[addr] = {
            "symbol": tok.get("symbol"), "name": tok.get("name"), "address": addr,
            "reserve_usd": reserve, "age_hours": round(age_h, 1) if age_h is not None else None,
            "discovery": bucket,
        }
        added += 1
    print(f"  {bucket:12s} p{params.get('page')}: +{added} (total {len(fresh)})")
    time.sleep(6)

print(f"\nfresh unique tokens: {len(fresh)}")


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
        hc = int(info.get("holder_count") or 0)
    except Exception:
        hc = 0
    hard, soft = [], []
    if flag("is_honeypot"): hard.append("honeypot")
    if flag("cannot_sell_all"): hard.append("cannot_sell_all")
    if num("sell_tax") > 0.5: hard.append(f"sell_tax={num('sell_tax')}")
    if flag("hidden_owner"): hard.append("hidden_owner")
    if flag("owner_change_balance"): hard.append("owner_change_balance")
    if flag("can_take_back_ownership"): hard.append("can_take_back_ownership")
    if info.get("is_open_source") == "0": soft.append("unverified_source")
    if hc <= 1: soft.append(f"holder_count={hc}")
    if top_pct >= 0.9: soft.append(f"top_holder={top_pct:.2f}")
    if flag("is_mintable"): soft.append("mintable")
    if flag("is_blacklisted"): soft.append("blacklist_fn")
    return {"hard_scam": hard, "soft_risk": soft, "holder_count": hc,
            "top_holder_pct": top_pct, "is_open_source": info.get("is_open_source"),
            "token_symbol": info.get("token_symbol")}


addrs = list(fresh.keys())
print(f"\nlabelling {len(addrs)} tokens (small paced batches)")
empty = []
for i in range(0, len(addrs), 5):
    batch = addrs[i:i + 5]
    try:
        r = session.get(GOPLUS, params={"contract_addresses": ",".join(batch)}, timeout=30)
        res = r.json().get("result", {}) or {}
    except Exception:
        res = {}
    for a in batch:
        info = res.get(a)
        if info:
            fresh[a]["goplus"] = parse_goplus(info)
        else:
            empty.append(a)
            fresh[a]["goplus"] = None
    time.sleep(3)

print(f"retrying {len(empty)} empties individually")
rec = 0
for a in empty:
    try:
        r = session.get(GOPLUS, params={"contract_addresses": a}, timeout=30)
        info = (r.json().get("result", {}) or {}).get(a)
    except Exception:
        info = None
    if info:
        fresh[a]["goplus"] = parse_goplus(info)
        rec += 1
    time.sleep(2.5)
print(f"recovered {rec}")

json.dump(list(fresh.values()), open("fresh_candidates.json", "w", encoding="utf-8"), indent=2)
labelled = sum(1 for v in fresh.values() if v.get("goplus"))
print(f"\nsaved fresh_candidates.json: {len(fresh)} tokens, {labelled} labelled "
      f"({100*labelled/max(1,len(fresh)):.0f}%)")
