# Deploy runbook — three-state data status

Prepared 2026-09-02. **Nothing is deployed yet.** Two PRs are open and waiting.

| Repo | PR | Branch | How it deploys |
|---|---|---|---|
| RugBuster-Avalanche | [#2](https://github.com/rugbusteraipatrol/RugBuster-Avalanche/pull/2) | `audit/silent-fallbacks` | **auto-deploys on merge to `main`** |
| rugbuster-scoring-engine | [#5](https://github.com/rugbusteraipatrol/rugbuster-scoring-engine/pull/5) | `feat/act-on-data-status` | merge is safe; deploy is a separate manual `railway up` |

## Order does not matter

Verified in both directions, so there is no coordinated-release risk:

- **new engine + old collector** → payload carries no `status` keys → engine falls back to presence-based behaviour → identical to today
- **old engine + new collector** → a `FETCH_FAILED` dict is truthy → old engine counts it as available → identical to today

Deploying only one is a no-op, not a break. The behaviour change appears once both are live.

## Steps

**1. Scoring engine** (`rugbuster-scoring-engine`)

Merge PR #5, then from a clean checkout of `main`:

```bash
git checkout main && git pull && python -m pytest tests/ -q
```

Expect `117 passed`. Then deploy:

```bash
railway up
```

Railway project `blissful-cat`, service `rugbuster-scoring-engine`. Note this repo deploys by CLI, so Railway holds no commit metadata — record the commit SHA yourself.

Confirm it is live:

```bash
curl -s https://rugbuster-scoring-engine-production.up.railway.app/health
```

**2. Collector + API** (`RugBuster-Avalanche`)

Merging PR #2 **is** the deploy — this repo auto-deploys from `main`. Before merging, from the branch:

```bash
python -m pytest tests/ -q
```

Expect `37 passed`. Then merge, and wait for the Railway build on `web-production-376bf` to finish.

**3. Verify against production**

```bash
curl -s "https://web-production-376bf.up.railway.app/score?address=0xB31f66AA3C1e785363F0875A1B74E27b85FD66c7" | python -m json.tool
```

Check, in order:

- `label` is still `GOOD` — WAVAX is on the whitelist, so the new rules must not move it
- `data_contract_version` is `2026.09.1` — proves the new code is actually serving, not a cached old response
- `completeness_pct` is present and plausible (expect 75-100 on a healthy run)
- `missing_inputs` is a list; any entry has `module`, `status`, `reason`
- `verdict_is_conclusive` is `true`

Then the no-pool fixture:

```bash
curl -s "https://web-production-376bf.up.railway.app/score?address=0x9e7338d762d7a82273a3e4dc6994e586dc97cab9" | python -m json.tool
```

- `label` must **not** be `GOOD`
- `missing_inputs` must contain `holder_concentration` with status `NOT_FOUND` **and a non-empty reason** — if it says `FETCH_FAILED` instead, that means our own Snowtrace access broke, not that the token has no holders. Check `SNOWTRACE_API_KEY` before concluding anything about the token.

**4. Full regression gate**

```bash
python qa/run_regression.py
```

143 entries against the live endpoint. Expect `RESULT: GREEN`, including the new `I7` line:

```
[PASS] I7 tokens with no pool report NOT_FOUND with a reason, never a silent clean read: 1/1
```

This is the first run that can exercise I7 — the harness hits the deployed endpoint, so it could not be verified before deploy.

## What "green" does and does not prove

The regression set runs while upstream APIs happen to be healthy, so it exercises the `OK` and `NOT_FOUND` paths. It does **not** exercise `FETCH_FAILED` — that only appears during a real outage, which is exactly when the old behaviour was dangerous and the new behaviour matters.

That path is covered by unit tests in both repos and by a cross-repo integration check (same token: API healthy → `GOOD`, API down → `INSUFFICIENT_DATA` with both gaps named). Do not expect to see it in production traffic on a good day.

## Expected change in production behaviour

| Situation | Before | After |
|---|---|---|
| Everything healthy | unchanged | unchanged |
| Load-bearing fetch fails, whitelisted token | `GOOD` | `GOOD` (whitelist is curated ground truth) |
| Load-bearing fetch fails, other token | `GOOD` | `INSUFFICIENT_DATA` + named gaps |
| Token genuinely has no pool | verdict as before | verdict as before, plus `NOT_FOUND` + reason |
| Real risk found | `WARN`/`DANGER` | unchanged — gaps never downgrade a real finding |

Expect a rise in `INSUFFICIENT_DATA` **only** correlated with upstream trouble. If it rises on a quiet day, that is a signal worth chasing: something is failing that used to be silently absorbed.

## Rollback

Both are ordinary reverts, and either can be rolled back alone thanks to the order-independence above.

- **RugBuster-Avalanche**: revert the merge commit on `main`; auto-deploy takes it back.
- **rugbuster-scoring-engine**: revert on `main`, then `railway up` again.

Note the scan cache is keyed on `data_contract_version`, so a rollback also naturally invalidates verdicts produced under the new rules rather than serving them for the rest of the TTL.

## Not included in this deploy

- **The `or 0` reads in `risk_engine.py`** (lines 80-158) still default other missing fields to zero. The two load-bearing ones are now guarded; the rest are detectable but not yet acted on.
- **`api/server.py`'s remaining ~41 silent-fallback hits** in unrelated paths (Dexscreener extraction, Telegram formatting, config parsing) were inventoried but not audited line by line.
- **No liquidity data source** in the x402/MCP gateway — separate track, unrelated to this deploy.
