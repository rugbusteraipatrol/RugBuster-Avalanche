# RugBuster Avalanche — Large-Scale Scoring QA Report

**Date:** 2026-08-01
**Endpoint under test:** `GET https://web-production-376bf.up.railway.app/score?address=0x...`
**Deployed code:** `RugBuster-Avalanche@8304ad3`, private engine `rugbuster-scoring-engine@d0c7834`
**Sample size:** 257 Avalanche C-Chain addresses
**Test type:** read-only, independent verification (no engine code was modified for this run)

---

## 1. What this report does and does not claim

This is a **robustness and invariant** study, not an accuracy benchmark.

For 257 tokens there is no hand-verified ground truth, so we do not claim a
precision or recall figure over the whole sample. Instead we assert a small
number of properties that must hold for the product to be safe to ship, and we
report agreement against an independent third-party source (GoPlus token
security) wherever that source has data.

Two hard assertions are made, on tiers where the correct answer is not in
dispute:

| Tier | n | Assertion |
|---|---|---|
| Canonical assets (whitelisted blue-chip) | 16 | must be `GOOD` |
| Rug-factory pattern (unverified source **and** ≤1 holder **and** ~100% concentration) | 93 | must never be `GOOD` |

Everything else is measured, not graded.

### Why GoPlus flags are not treated as ground truth

During sample construction, GoPlus flagged **GMX** — an established perpetuals
protocol token — with `hidden_owner` and `owner_change_balance`. Treating raw
third-party flags as truth would have scored RugBuster as "wrong" for not
calling GMX a scam. Third-party flags are therefore used as an *independent
signal for agreement analysis only*.

### A methodology bug we caught in ourselves

The first labelling pass reported that 94% of the sample had no GoPlus data.
That was false: GoPlus batch queries **silently return partial results under
throttling** rather than an error. Single-address queries returned full data for
the same tokens (JOE: 150,994 holders; COQ: 110,060 holders). Re-labelling with
small paced batches plus per-address retry raised real coverage from ~6% to
**53.7%**. Had this gone unnoticed, the labelled tier would have been nearly
empty and every conclusion drawn from it worthless.

---

## 2. Sample construction

Discovery sources: GeckoTerminal (`new_pools`, `pools` by 24h volume,
`trending_pools`, `pools` by 24h tx count) and DexScreener search across 44
query terms. Labelling: GoPlus token-security API (chain 43114).

| Tier | n | Basis |
|---|---|---|
| `A_bluechip` | 16 | canonical assets, addresses re-verified against DexScreener/GeckoTerminal |
| `A_rug_factory` | 93 | unverified source + ≤1 holder + ~100% top-holder concentration |
| `B_unlabelled` | 110 | no GoPlus data available |
| `B_clean` | 19 | GoPlus reports no risk signals |
| `B_soft_risk` | 15 | partial GoPlus risk signals |
| `B_goplus_flagged` | 4 | high-severity GoPlus flags (incl. the GMX false positive) |

**Sample skew (stated openly):** 78% of tokens with known liquidity hold under
$1,000, and 82% of tokens with a known age are ≤72 hours old. This sample is
deliberately weighted toward fresh, thin deployments — the population where the
original incident occurred. It is **not** representative of what a typical
retail user pastes into the demo, and conclusions should not be generalised to
that population.

---

## 3. Results

### 3.1 Robustness

| Metric | Result |
|---|---|
| HTTP 200 | 257 / 257 |
| Transport or parse errors | 0 |
| `source == private_scoring_engine` | 257 / 257 |
| Latency p50 | 4.63 s |
| Latency p95 | 5.00 s |
| Latency max | 5.65 s |

No request crashed, timed out, or silently fell back to a cached or local
scorer.

### 3.2 Hard invariants

| Invariant | Result |
|---|---|
| **I1** — canonical assets return `GOOD` | **16 / 16 pass** |
| **I2** — rug-factory pattern never returns `GOOD` | **93 / 93 pass** (all returned `WARN`) |
| **I3** — every response served by the private engine | **257 / 257 pass** |

**I4 — fallback safety** was verified separately by running the API locally with
a deliberately unreachable `SCORING_ENGINE_URL`, and again with the engine
disabled. In both cases even WAVAX returned `INSUFFICIENT_DATA`, never `GOOD`.
This is the invariant that matters most: when the scoring engine is down, the
product refuses to reassure rather than guessing.

### 3.3 Verdict distribution

| Verdict | Count | Share |
|---|---|---|
| `WARN` | 193 | 75.1% |
| `DANGER` | 39 | 15.2% |
| `GOOD` | 24 | 9.3% |
| `INSUFFICIENT_DATA` | 1 | 0.4% |

### 3.4 Every `GOOD` verdict was manually reviewed

24 tokens received `GOOD`. 16 are whitelisted canonical assets. The remaining 8
were inspected individually:

| Token | Holders | Source verified | Assessment |
|---|---|---|---|
| AAVE.e | 9,008 | yes | legitimate bridged asset |
| COQ | 110,060 | yes | established Avalanche token |
| KIMBO | 24,534 | yes | established Avalanche token |
| SUPER | 4,108 | yes | legitimate |
| BRO | 4,401 | yes | legitimate |
| USDe | 610 | yes | legitimate (Ethena) |
| yyAVAX | — | — | Yield Yak liquid staking, $336k liquidity |
| NOCHILL | — | — | established Avalanche token |

**Zero false `GOOD` verdicts were found in this sample.** This is the single
most important safety result in the report: across 257 addresses, including 93
abandoned single-holder deployments, the product never told a user that a
dangerous token was safe.

### 3.5 Agreement with GoPlus (labelled subset, n=132)

| Comparison | Result |
|---|---|
| GoPlus reports risk → RugBuster returns non-`GOOD` | 111 / 113 (98.2%) |
| GoPlus reports clean → RugBuster returns `GOOD` | 5 / 19 (26.3%) |

The second number is low, and is explained rather than hidden below.

---

## 4. Findings

Listed deliberately, including the ones already closed: a report that claims no
defects is less credible than one that shows what it caught.

### P0 — The Routescan/Snowtrace API key was dead *(disclosed, not yet closed)*

Every endpoint the collector depends on returned
`"Phone verification required"`: `txlist`, `tokentx`, `balance`,
`getsourcecode`, `tokenholderlist`.

Consequence: everything routed through that API silently returned empty
defaults — deployer identity, contract age, creator history, holder
concentration, wash-trading detection, rug velocity, funding origin. Only
bytecode scanning survived, because it goes straight to RPC.

Direct evidence across 257 scans — number of times any of these reasons fired:

| Signal | Occurrences |
|---|---|
| Holder concentration | 0 |
| Holder count | 0 |
| Deployer history | 0 |
| Wash trading / bot farm / fresh funding | 0 |
| Rug velocity | 0 |

Meanwhile "Bytecode backdoor risk score 20/100" and "40/100" *do* appear (15×
and 10×), confirming the RPC path works and the Routescan path does not.

**This is the root cause behind the whitelist.** With only bytecode capabilities
and liquidity depth as inputs, the engine cannot separate an established asset
from a fresh rug — a stablecoin and a rug both expose `mint()`; a forgotten
bridged asset and a rug both look thin. A hand-maintained list of 16 addresses
was compensating for the missing evidence.

Status after `1d19062`: the dead hardcoded key was removed and `/health` now
reports `degraded: true`, `routescan: missing_key` instead of silently
fabricating defaults. The dependency still needs rebinding to AvaCloud (the
service already holds `AVACLOUD_API_KEY`) or a valid Routescan key.

### F1 — `SUSHI.e` whitelist address was wrong *(FIXED in `1d19062`)*

`api/server.py` carried `0x37b60851c570232f6f6a2a15d8b8f692ec4e08d5`, which does
not exist on-chain; the real SUSHI.e is
`0x37b608519f91f70f2eeb0e5ed9af4061722e4f76`. The entry could never match, so
real SUSHI.e fell through to generic scoring and returned `DANGER`. The same
class of typo had already occurred on YAK — a typo'd address passes testing
silently precisely because it is never hit.

Fixed, and more importantly the class is now structurally blocked: startup
validation checks bytecode and `symbol()` for every whitelist entry and surfaces
mismatches through `/health`.

### F2 — Non-token addresses received a confident numeric score *(FIXED in `1d19062`)*

Router, factory, EOA, zero and burn addresses returned `WARN` with
`rug_score = 72`, because `totalSupply()` against a non-ERC-20 address returned
nothing and was read as "Total supply is zero or invalid" (+60). They now return
`NOT_A_TOKEN`. Verified across the full 257-token sample: **zero legitimate
tokens were misclassified by the new guard.**

### F3 — Mint capability penalises legitimate stablecoins and bridged assets

`JPYC` (23,710 holders) and bridged `SOL` return `DANGER`, driven by
`mint(address,uint256)` and operator controls. For a stablecoin or a bridge
token, mint authority is inherent to the design, not evidence of fraud. This is
the same root cause as the original incident — correctness depends on whitelist
membership — and it will keep recurring for every legitimate asset outside the
current 16-entry whitelist. This explains most of the low "GoPlus clean →
`GOOD`" agreement in §3.5.

### F4 — `WARN` saturation

75% of the sample returns `WARN`. Given the deliberate skew toward fresh dust
deployments this is largely appropriate, but a verdict that applies to three
quarters of everything carries limited information. Worth tracking on a
liquidity-weighted sample before making public claims about verdict precision.

### F5 — Latency

Median 4.63 s per scan. Functionally fine, but it is what a user waits after
clicking "Check", and it constrains any batch or portfolio feature built on
this endpoint.

---

## 5. Reproducibility

All numbers above can be regenerated:

- `qa/run_regression.py` + `qa/golden_set.yaml` — 51-address gated regression suite (blue-chip, scam, reference cases); exits non-zero on any gate failure and supports `--test-local-fallback` to reproduce the I4 fallback check.
- Sampling and large-run scripts (`sample_build.py`, `sample_supplement.py`, `sample_finalize.py`, `run_large_qa.py`) produce `sample_candidates.json` and `large_qa_results.json`, which contain the full per-token response including flags, latency and source.

Recommended CI placement: run `qa/run_regression.py` on every push to `main`
touching `api/**` or `chains/avalanche/**`, after deploy completes. It is a
live-endpoint smoke test and should stay out of the unit-test suite.

---

## 6. Re-verification after `1d19062`

The full 257-address sample was replayed against the hardened deploy:

| Metric | Result |
|---|---|
| HTTP 200 / errors | 257 / 257, zero errors |
| `source == private_scoring_engine` | 257 / 257 |
| Canonical assets `GOOD` | 16 / 16 |
| Rug-factory never `GOOD` | 93 / 93 |
| Legitimate tokens misread as `NOT_A_TOKEN` | **0 / 257** |
| `GOOD` set membership | unchanged (24, none added or removed) |
| Verdict changes | 3, all improvements (`DANGER` → `WARN`) |

The gated suite (`qa/run_regression.py`, 132 entries) returns **GREEN**: I1
blue-chip 10/10, I2b reference case 1/1, I3 scam 10/10, I4 source 132/132, I5
rug-factory 81/81.

## 7. Conclusion

Across 257 Avalanche addresses the scoring API was stable (257/257 successful,
zero errors), always served by the private scoring engine, and produced **no
false `GOOD` verdicts** — including across 93 abandoned single-holder
deployments. Both hard invariants passed at 100%, and the fallback path was
verified by direct execution to degrade to `INSUFFICIENT_DATA` rather than
reassure the user when the engine is unavailable.

F1 and F2 are closed, and the whitelist-typo class that produced F1 twice is now
blocked by startup validation. The P0 data-dependency remains open but is no
longer silent: `/health` reports `degraded` rather than letting empty defaults
pass as analysis.

F3 (mint capability penalising legitimate stablecoins and bridged assets) cannot
be closed until that data dependency is restored, because the signals that would
distinguish an established asset from a fresh deployment — holder count, holder
concentration, contract age, deployer history — are exactly the ones the dead
API key was starving.

What this evidence supports claiming: the product is stable, it never labels a
dangerous token as safe, and it reports degradation honestly instead of
guessing. What it does not yet support claiming: that it is accurate or
finished.
