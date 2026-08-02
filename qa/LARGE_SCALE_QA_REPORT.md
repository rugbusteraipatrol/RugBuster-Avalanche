# RugBuster Avalanche — Large-Scale Scoring QA Report

**Date:** 2026-08-02 (final revision)
**Endpoint under test:** `GET https://web-production-376bf.up.railway.app/score?address=0x...`
**Deployed code:** `RugBuster-Avalanche@74670d9`, private engine `rugbuster-scoring-engine@0b3b71d`
**Sample size:** 257 Avalanche C-Chain addresses
**Test type:** read-only, independent verification

---

## 1. What this report does and does not claim

This is a **robustness and invariant** study, not an accuracy benchmark.

For 257 tokens there is no hand-verified ground truth, so we do not claim a
precision or recall figure over the whole sample. Instead we assert a small
number of properties that must hold for the product to be safe to ship, and we
report agreement against an independent third-party source (GoPlus token
security) wherever that source has data.

The hard assertions are made only on tiers where the correct answer is not in
dispute:

| Tier | n | Assertion |
|---|---|---|
| Canonical assets (whitelisted blue-chip) | 16 | must be `GOOD` |
| Rug-factory pattern (unverified source **and** ≤1 holder **and** ~100% concentration) | 93 | must never be `GOOD` |
| Dead liquidity (established but too thin to exit) | 10 | must never be `GOOD` |

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

No request crashed, timed out, or silently fell back to a cached or local
scorer.

### 3.2 Hard invariants

| Invariant | Result |
|---|---|
| **I1** — canonical assets return `GOOD` | **16 / 16** |
| **I5** — rug-factory pattern never returns `GOOD` | **93 / 93** |
| **I6** — tokens too thin to exit never return `GOOD` | **10 / 10** |
| **I3** — known-scam addresses return `WARN`/`DANGER` | **10 / 10** |
| **I4** — every response served by the private engine | **257 / 257** |

**I2 — fallback safety** was verified separately by running the API locally
with a deliberately unreachable `SCORING_ENGINE_URL`, and again with the engine
disabled. In both cases even WAVAX returned `INSUFFICIENT_DATA`, never `GOOD`.
This is the invariant that matters most: when the scoring engine is down, the
product refuses to reassure rather than guessing.

The gated suite (`qa/run_regression.py`, 142 entries) returns **GREEN**.

### 3.3 Verdict distribution

| Verdict | Count | Share |
|---|---|---|
| `DANGER` | 209 | 81.3% |
| `WARN` | 25 | 9.7% |
| `GOOD` | 23 | 9.0% |

### 3.4 Every `GOOD` verdict was manually reviewed

23 tokens received `GOOD`: 16 whitelisted canonical assets, plus USDe, KIMBO
(24,534 holders), gOHM, SUPER, COQ (110,060 holders), yyAVAX and NOCHILL — all
with verified source and a substantial holder base.

**Zero false `GOOD` verdicts were found.** Across 257 addresses, including 93
abandoned single-holder deployments, the product never told a user that a
dangerous token was safe.

### 3.5 Intelligence signals are live

The signals that separate an established asset from a fresh deployment now
reach scoring. In every earlier run these counts were zero:

| Signal | Occurrences |
|---|---|
| Bot-like transaction entropy | 184 |
| Bot farm holder cluster | 181 |
| Holder concentration | 168 |
| Rug velocity | 3 |

---

## 4. Findings

Listed including the closed ones. A report that claims no defects is less
credible than one that shows what it caught.

### P0 — Routescan/Snowtrace dependency was dead *(CLOSED)*

Every endpoint the collector depended on returned `"Phone verification
required"`: `txlist`, `tokentx`, `balance`, `getsourcecode`,
`tokenholderlist`. Everything routed through that API silently returned empty
defaults — deployer identity, contract age, creator history, holder
concentration, wash trading, rug velocity, funding origin. Only bytecode
scanning survived, because it goes straight to RPC.

Evidence at the time: across 257 scans, holder-concentration, holder-count,
deployer-history, wash-trading and rug-velocity reasons fired **zero** times,
while bytecode backdoor scores appeared normally.

**This was the root cause behind the whitelist.** With only bytecode
capabilities and liquidity depth as inputs, the engine could not separate an
established asset from a fresh rug — a stablecoin and a rug both expose
`mint()`. A hand-maintained list of 16 addresses was compensating for missing
evidence.

Now restored: `/health` reports `degraded: false`, and section 3.5 shows the
previously-dead signals firing across the sample.

### F1 — SUSHI.e whitelist address was wrong *(CLOSED)*

The whitelist carried an address that does not exist on-chain, so the real
SUSHI.e fell through to generic scoring and returned `DANGER`. The same class
of typo had already occurred on YAK — a typo'd address passes testing silently
precisely because it is never hit. Beyond the fix, the class is now blocked:
startup validation checks bytecode and `symbol()` for every whitelist entry
and surfaces mismatches through `/health`.

### F2 — Non-token addresses received a confident numeric score *(CLOSED)*

Router, factory, EOA, zero and burn addresses returned `WARN` with
`rug_score = 72`, because `totalSupply()` against a non-ERC-20 address returned
nothing and was read as "Total supply is zero or invalid". They now return
`NOT_A_TOKEN`, and across the full 257-token sample **zero legitimate tokens
were misclassified by the guard**.

### F3 — Mint capability penalised legitimate stablecoins *(CLOSED)*

JPYC (23,710 holders) and bridged SOL returned `DANGER` driven by `mint()` and
operator controls, which for a stablecoin or bridge token is the design rather
than evidence of fraud. Capability penalties are now weighted by a token track
record — holder count and age — so an established asset no longer needs to be
on a list to avoid a rug alarm. Both now return `GOOD`.

### F6 — Track-record weighting was over-applied, then corrected *(CLOSED)*

The first version of the track-record change introduced three regressions that
the 132-entry suite passed straight over:

- A maturity floor granted a discount to tokens with no real age, so a
  12-day-old contract with published source, 120 holders and a seeded pool
  scored 42 `LOW`; the same token with unpublished source scored 94 `HIGH`.
  Publishing source is free, so a 52-point swing on that signal alone is not
  defensible.
- A third-party "clean" verdict suppressed our own bytecode backdoor
  detection, hiding the flag entirely from the response.
- Market risk was capped below warning for any token with holder history,
  which produced `GOOD` for tokens carrying a few hundred dollars of
  liquidity — FITFI at $493, ALOT at $427 — while the same response printed
  "Very thin live liquidity".

The suite missed all three because every rug-factory address in the golden set
has one holder and no liquidity, so none of them reached the affected code
paths. Two profiles were added as permanent gates: the *prepared rug* (young
but with the cheap signals already bought) and *dead liquidity* (established
but impossible to exit). Four unit tests encode the guards, and all four failed
against the regressed engine, which is what made them worth adding.

### F7 — Holder concentration can exceed 100% *(OPEN)*

Six tokens report an impossible concentration figure, and four of them are
labelled `DANGER` on the strength of it:

| Token | Reported top-5 concentration | Verdict |
|---|---|---|
| WBTC | 473.3% | `DANGER` |
| AAVE.e | 112.1% | `WARN` |
| waAvaUSDT | 106.2% | `WARN` |
| bUSDC | 101.3% | `DANGER` |
| wSAC | 100.3% | `DANGER` |
| SOL | 100.1% | `DANGER` |

Cause: in `analyze_holder_concentration_avax`, holder quantities and total
supply are not on the same scale (one raw, one decimal-adjusted). WBTC has
1,525 holders and is called dangerous because its concentration computes to
473%.

This errs toward caution rather than false safety, so it is not a shipping
blocker, but it is visible to anyone reading the flags. The fix needs both a
unit correction and a hard guard: a computed concentration above 100% is
invalid data and must be treated as unknown, never scored as `CRITICAL`.

### F8 — Verdict distribution is heavily skewed *(OPEN, advisory)*

`DANGER` now covers 81% of the sample, where a previous revision had `WARN`
covering 75%. Given the deliberate skew toward fresh dust deployments this is
largely appropriate, but a verdict that applies to four fifths of everything
carries limited information. Worth measuring on a liquidity-weighted sample
before making public claims about verdict precision.

---

## 5. Reproducibility

- `qa/run_regression.py` + `qa/golden_set.yaml` — 142-address gated suite
  (blue-chip, scam, rug-factory, dead-liquidity, reference cases); exits
  non-zero on any gate failure and supports `--test-local-fallback` to
  reproduce the I2 fallback check.
- `tests/test_avax_risk_engine.py` — unit guards for the prepared-rug and
  dead-liquidity profiles, plus backdoor visibility.
- Sampling and large-run scripts (`sample_build.py`, `sample_supplement.py`,
  `sample_finalize.py`, `run_large_qa.py`) produce `sample_candidates.json`
  and `large_qa_results.json` with the full per-token response.

Recommended CI placement: run `qa/run_regression.py` on every push to `main`
touching `api/**` or `chains/avalanche/**`, after deploy completes. It is a
live-endpoint smoke test and should stay out of the unit-test suite.

---

## 6. Conclusion

Across 257 Avalanche addresses the scoring API was stable (257/257 successful,
zero errors), always served by the private scoring engine, and produced **no
false `GOOD` verdicts** — including across 93 abandoned single-holder
deployments. All hard invariants pass, and the fallback path was verified by
direct execution to degrade to `INSUFFICIENT_DATA` rather than reassure the
user when the engine is unavailable.

The data dependency that had been starving the holder, concentration, age and
deployer signals is restored, and those signals now appear across the sample.
Capability risk is weighted by a token track record rather than by membership
in a hand-maintained list, which is what allowed legitimate stablecoins and
bridged assets to stop reading as rug risk.

One finding remains open (F7, impossible concentration values) and one is
advisory (F8, verdict skew). Neither causes a dangerous token to be labelled
safe.

What this evidence supports claiming: the product is stable, it never labels a
dangerous token as safe, and it reports degradation honestly instead of
guessing. What it does not yet support claiming: that it is accurate or
finished.
