# Silent fallback audit — AVAX collector + scoring engine

Inventory only. Nothing in this repo was changed to produce this report.
Scope: `chains/avalanche/avax_collector_v6.py` (the collector every AVAX scan
runs through), `chains/avalanche/risk_engine.py` (the local scorer), and
`api/server.py` (the API boundary). Pulled from a fresh `origin/main`
worktree, commit `474bd77`, 2026-09-02.

## The one finding that matters most

`snowtrace_get()` (line 705) **already distinguishes fetch failure from a
confirmed-empty result** at the API layer:

```python
# line 725-733
if status == "1":
    return result                    # real data
msg = data.get("message", "")
if "No transactions" in msg or "No records" in msg:
    return []                        # confirmed empty -- this IS a finding
detail = str(result or msg or "unknown API error")
log.warning(...)
return None                          # fetch failed -- this is NOT a finding
...
return None                          # exhausted 3 retries -- also not a finding
```

That distinction is thrown away one line later, everywhere the function is
called. Every wrapper does the same thing:

| Wrapper | Line | Code |
|---|---|---|
| `get_contract_transactions` | 790 | `return result if result else []` |
| `get_token_transfers` | 800 | `return result if result else []` |
| `get_account_transactions` | 810 | `return result if result else []` |
| `get_token_holders` | 823 | `return result if result else []` |

`None` (API broke) and `[]` (API confirmed nothing exists) both become `[]`.
Everything downstream — all five modules named below — only ever sees `[]`
and cannot tell the two apart. **Fixing this one collapse point is most of
the actual work**; the five modules mostly just need to check which case
they got, once the wrappers stop hiding it.

## The five named modules

**Funding Origin** — `trace_funding_origin_avax`, line 915.
`get_account_transactions(current, limit=10)` returns `[]` at line 928 for
both "wallet genuinely has zero prior transactions" (a real signal —
`is_fresh_wallet = True` is deliberately set for this at line 930) and "API
call failed." No way to tell which happened. Same ambiguity for
`get_account_age_days_avax` (line 908), called per-hop at line 947.

**Deployment Latency** — `get_deployment_latency_avax`, line 956.
`get_token_transfers` returning `[]` (line 959) is read as "no first-buy
data" regardless of cause, and the function just returns the same
`{"latency_ms": -1, ...}` default either way. No FETCH_FAILED path exists at
all here — `-1` currently means both "token has no buys yet" and "we
couldn't check."

**TX Entropy** — `analyze_transaction_entropy_avax`, line 972. Same
`get_token_transfers` ambiguity at line 978. Additionally: the task's own
framing ("too few tx for statistics") isn't handled either — 1-4 real
transfers produce a numeric `entropy_score` with no confidence flag, no
different from 30 transfers.

**Wash Pattern** — `detect_wash_pattern_avax`, line 1007. Same
`get_token_transfers` call (line 1014); additionally reads
`get_account_transactions(deployer, limit=10)` at line 1024 and treats a
short/empty result as `linker_wallets_connected = True` (line 1025) —
meaning an API failure here doesn't just hide as "no data," it actively
flips toward a risk-positive reading.

**Holder Concentration** — `analyze_holder_concentration_avax`, line 570.
Three separate silent-empty paths, all returning the identical
`{"top5_pct": 0.0, ..., "concentration_risk": "LOW"}` default:
- line 578: `get_token_holders` returned nothing, or only 1 holder (page cap
  is 50 — line 748-750 — so a token with 51+ real holders where the top 5
  land past page 1 would also silently read wrong, not just an outright
  failure case)
- line 587: `total_supply` couldn't be resolved (RPC + Snowtrace both failed)
- line 626: caught exception during the percentage math itself

Every one of these currently reads as "top5 concentration is 0%, LOW risk"
— the single most dangerous line in this file, because it's not neutral,
it's a false-clean signal. (This is the exact bug class already fixed once
in this file for the *impossible-value* case — line 605's `not (0 <=
top5_pct <= 100)` guard — but that guard only catches values that are
mathematically impossible, not values that are merely unknown.)

**Contract Backdoor** — `detect_contract_backdoor_avax`, line 512. Bytecode
fetch (`eth_getCode` RPC call) failing is caught by a bare `except Exception`
(line 552, logged at `debug` level — the quietest level in the file) and
falls through to the same `has_backdoor: False, backdoor_risk_score: 0`
default as a contract that was actually read and found clean. **This is the
one the task description calls out specifically as needing its own status**
("neverifikovan izvor koda je poseban status, ne 'nije nađen backdoor'") —
right now there is no way to distinguish "we read this bytecode and it has
no dangerous functions" from "we never actually read the bytecode."

## Where it resurfaces one layer up: `risk_engine.py`

The collector's ambiguity feeds straight into scoring with a second round of
the same pattern. `score_avax_security` / `score_rug_risk`
(`chains/avalanche/risk_engine.py`):

```
line 80:  backdoor_score = int(metadata.get("v6_backdoor_risk_score") or metadata.get("backdoor_risk_score") or 0)
line 92:  holders = int(metadata.get("holders_count") or 0)
line 153: top5 = float(metadata.get("v6_top5_concentration_pct") or metadata.get("top5_holder_pct") or 0)
line 155: velocity = float(metadata.get("v6_rug_velocity_score") or metadata.get("rug_velocity_score") or 0)
line 156: creator_rug_rate = float(metadata.get("creator_rug_rate") or 0)
line 158: deployer_balance = float(metadata.get("deployer_balance_avax") or 0)
```

This is the second half of the same bug, and arguably worse: a missing
`v6_top5_concentration_pct` (because the collector never got holder data at
all) is read here as **the literal number zero percent concentration** —
which the scorer then treats as a positive signal, not an absence of one.
`or 0` is the pattern the task description asked to grep for; this is where
it actually bites.

## Mechanical inventory (counts, not full line lists — see below for how to regenerate)

| Pattern | `avax_collector_v6.py` | `risk_engine.py` | `api/server.py` |
|---|---:|---:|---:|
| `or 0` (any form: `or 0`, `or 0.0`, `or 0)`) | 75 | 10 | ~16 (mixed with unrelated numeric defaults) |
| `.get(x, 0)` / `.get(x, 0.0)` | 62 | 0 (uses `or 0` instead, see above) | 3 |
| `except Exception` (bare or near-bare) | 19 | 0 | 25 |
| `except:` / `except Exception: pass` (no logging at all) | 0 | 0 | 0 |

Zero bare `except: pass` blocks with no logging at all — every catch in
these three files at least logs something. The risk isn't silent code, it's
*silently-converted-to-clean* code: the log line explains the failure, but
the return value one line later throws that context away and reports back
as if nothing happened. That's the pattern worth fixing, not exception
handling per se.

`api/server.py`'s ~41 combined hits are almost entirely in unrelated code
paths (Dexscreener pair-field extraction, Telegram formatting, JSON config
parsing) — did not line-by-line audit those for this pass since the task
scoped the five named modules, which all live in `avax_collector_v6.py`
(collector) and `risk_engine.py` (scorer). Flagging that a full `api/server.py`
pass would still be worth a follow-up if the three-status work later expands
past these five modules.

To regenerate on a later commit:
```bash
grep -n "or 0\b" chains/avalanche/avax_collector_v6.py chains/avalanche/risk_engine.py api/server.py
grep -n "\.get([^,)]*, 0)" chains/avalanche/avax_collector_v6.py chains/avalanche/risk_engine.py api/server.py
grep -n "except Exception" chains/avalanche/avax_collector_v6.py chains/avalanche/risk_engine.py api/server.py
```

## What this means for the three-status design (not implemented here)

The fix has a natural chokepoint: `snowtrace_get` already knows the
difference between `FETCH_FAILED` (`None`) and `NOT_FOUND` (`[]` from a
confirmed-empty API response). The four thin wrappers around it
(`get_contract_transactions`, `get_token_transfers`,
`get_account_transactions`, `get_token_holders`) are what erase it, and
they're the only things standing between the fix and all five named
modules. `NOT_QUERIED` is a separate, simpler case — it's about a module
never being called at all (basic-tier scans, or a chain where a module
doesn't apply), which is already visible at the `run_cia_analysis_avax` /
`run_v6_analysis_avax` call sites (lines 1066, 1118) rather than inside the
collector functions themselves.

---

# What was fixed (2026-09-02)

The inventory above was the "before". This section records what changed in the
same branch, so the two are never read apart.

## The chokepoint

`snowtrace_get`'s None-vs-`[]` distinction now survives. `_as_fetched()` maps
it onto a `FetchedList` — a `list` subclass carrying `.fetch_status`, chosen so
every existing call site (`if not txs`, `len()`, indexing, iteration) behaves
identically and none of the ~2500 lines around it needed touching. The four
wrappers (`get_contract_transactions`, `get_token_transfers`,
`get_account_transactions`, `get_token_holders`) return it.

`fetch_status_of()` reads the status off any value, and deliberately treats a
plain untagged `[]` as `FETCH_FAILED` rather than `NOT_FOUND`: a caller that
did not preserve provenance cannot prove the source was reachable, and unknown
must never default to optimistic.

## Per-module status

Every module named in the audit now carries `status` + `status_reason`:

| Module | Was | Now |
|---|---|---|
| Holder Concentration | empty → `top5_pct: 0.0, LOW` | `FETCH_FAILED` (holder list unavailable / supply unresolvable / inconsistent data) vs `NOT_FOUND` (too few holder records) |
| Contract Backdoor | RPC failure → `has_backdoor: False` | `FETCH_FAILED` (bytecode unreadable) vs `NOT_FOUND` (no bytecode at address) |
| Funding Origin | broken hop → `is_fresh_wallet: True` | `FETCH_FAILED` naming the hop that broke; `is_fresh_wallet` stays False |
| Wash Pattern | failed deployer fetch → `linker_wallets_connected: True` | `FETCH_FAILED`; no longer manufactures a risk signal from an outage |
| TX Entropy | any sample size → confident `entropy_score` | `NOT_FOUND` below `MIN_TX_FOR_ENTROPY` (5) |
| Deployment Latency | `latency_ms: -1` for both causes | `FETCH_FAILED` vs `NOT_FOUND`, same `-1` but now with meaning attached |
| Holder Cluster | empty → zeroed result | `FETCH_FAILED` vs `NOT_FOUND` |

`NOT_QUERIED` is set where a module is deliberately skipped (no deployer known
→ funding trace and wash analysis are not attempted).

## API surface

`summarize_data_completeness()` folds the module statuses into what an API
consumer needs to decide whether to trust a verdict programmatically:

- `completeness_pct` / `modules_ok` / `modules_total`
- `missing_inputs[]` — `{module, status, reason}` per absent signal
- `verdict_is_conclusive` — false only when something actually broke.
  `NOT_FOUND` does **not** make a verdict inconclusive: a token with no pool
  anywhere is a finding about that token, not a gap in our scan.

`data_contract_version` (`2026.09.1`) is on every response, separate from the
scoring engine's own `engine_version`, and is now part of the scan cache key —
so a verdict computed under the previous rules cannot be served from cache for
the remainder of its TTL after a deploy.

## Deliberately NOT changed

The `or 0` reads in `risk_engine.py` (lines 80-158) still default missing
fields to zero. Those are now *detectable* — the status fields say when a zero
is a placeholder — but the scoring math has not been rewired to act on it,
because doing so moves verdicts and needs its own golden-set run. This is the
single most important follow-up: until it lands, a `FETCH_FAILED`
concentration is still *scored* as 0% concentration, even though the response
now honestly reports that it is unknown.

## Verification

- 29 local tests pass (10 pre-existing, 19 new in `tests/test_data_status.py`).
- Verified live against MAXI (`0x9e73...cab9`), a real token with no pool:
  reports `holder_concentration: NOT_FOUND` with reason, `has_fetch_failures:
  false`. Golden-set fixture added (`category: no_pool`, gate I7) asserting
  exactly that, plus the pre-existing "never GOOD" invariant.
- `qa/run_regression.py` gained `expected_module_status` /
  `expect_verdict_conclusive` support and gate I7; the 142 pre-existing
  entries are untouched and evaluate exactly as before.
- The golden set runs against the deployed endpoint, so gate I7 can only go
  green after this branch is deployed.
