# RugBuster Avalanche API

Live API:

```txt
https://web-production-376bf.up.railway.app
```

RugBuster Apex exposes a cache-first Avalanche risk API for wallets,
launchpads, DEX interfaces, dashboards, and L1 builder tooling. The public
endpoint returns compact labels from collected evidence, while private scan
access stays protected to control cost and prevent dataset scraping.

## Public Endpoints

```bash
curl https://web-production-376bf.up.railway.app/
curl https://web-production-376bf.up.railway.app/health
curl "https://web-production-376bf.up.railway.app/score?address=0x2ce7788de3a177f8be43df6376a8513ef9182032"
```

`GET /score?address=0x...` returns a compact Avalanche C-Chain risk score.
It uses cache when available and falls back to a live read-only score without
Telegram alerts or on-chain writes.

Example response shape:

```json
{
  "address": "0x2ce7788de3a177f8be43df6376a8513ef9182032",
  "chain": "avalanche",
  "label": "DANGER",
  "classifier": "weighted_v2",
  "source": "live_score"
}
```

## Protected Endpoint

```bash
curl \
  -H "X-API-Key: <partner_or_internal_key>" \
  "https://rugbuster-api-production.up.railway.app/scan?address=0x..."
```

`GET /scan?address=0x...` is protected because it can perform deeper scoring
work and should not be freely scraped or abused. API keys are issued privately
for trusted integrations and internal workers.

## Data Model

The Avalanche collector stores full scan records in Postgres. The API exposes
risk labels and compact integration responses, not the private raw corpus.

Current storage table:

```txt
avax_scans(full_record JSONB)
```

Current classifier:

```txt
weighted_v2
```

## On-Chain Registry

Verified Avalanche mainnet registry:

```txt
0x5F30276B3A5079E088Ec3072884286de5a868355
```

Snowtrace:

```txt
https://snowtrace.io/address/0x5F30276B3A5079E088Ec3072884286de5a868355
```

The API layer and the registry serve different jobs:

- API: fast builder-facing lookups and protected deep scans.
- Registry: public on-chain attestations and reviewer-published proof.
- Collector: private evidence corpus used for scoring and model improvement.

Together they form the first RugBuster Apex security tooling layer for
Avalanche builders and future L1 security infrastructure.
