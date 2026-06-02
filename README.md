# RugBuster Avalanche

> AI-powered token security registry and on-chain risk certification layer for Avalanche C-Chain.

RugBuster Avalanche extends the RugBusterAI security engine into the Avalanche ecosystem as a public, verifiable risk layer for tokens, launchpads, wallets, DEX interfaces, and Culture Catalyst participants.

The Solana prototype proved the core thesis: real-time token monitoring, external risk signals, structured datasets, and AI-assisted fraud classification can protect users before liquidity disappears. RugBuster Avalanche adapts that experience to an EVM-native flow: scan Avalanche tokens, produce a transparent safety score, and publish security attestations directly on-chain.

## Grant Thesis

Avalanche does not only need another off-chain scanner. It needs a public security registry that applications can query, auditors can inspect, and the ecosystem can build on.

RugBuster Avalanche is designed for the Retro9000 and Culture Catalyst context:

- Scan new and relevant Avalanche C-Chain tokens from DEX pair creation events.
- Enrich tokens with contract, liquidity, supply, and deployer metadata.
- Score projects with a temporary rules engine today and a dedicated RugBusterAI model later.
- Publish safety scores on-chain through a gas-efficient registry contract.
- Emit real-time events for monitoring and integrations.

## Product Model

On Solana, RugBuster acts like an emergency alert system for retail traders.

On Avalanche, RugBuster acts as a security certification layer:

1. The Avalanche adapter detects token liquidity events and collects metadata.
2. The risk engine assigns a safety score from 0 to 100.
3. The registry contract records the score on Avalanche C-Chain.
4. Wallets, dashboards, DEX tools, and grant reviewers can query the public registry.

## Architecture

```txt
RugBuster-Avalanche/
  contracts/
    RugBusterRegistry.sol        # On-chain safety score registry
  chains/
    avalanche/
      adapter.py                 # DEX pair monitor and metadata collector
      risk_engine.py             # Temporary deterministic risk rules
  scripts/
    deploy_fuji.py               # Deploy registry to Avalanche Fuji
    simulate_analysis.py         # Send demo batchUpdate transaction
  data/
    .gitkeep                     # Local CSV output directory
  .env.example
  .gitignore
  requirements.txt
  README.md
```

## On-Chain Registry

`RugBusterRegistry.sol` stores public token safety reports:

- token address
- score from 0 to 100
- risk label: GOOD, WARN, or DANGER
- metadata hash for off-chain evidence
- reviewer address
- timestamp

The contract includes `batchUpdate` so the scanner can publish multiple token reports in one transaction. This keeps writes efficient while still producing meaningful Avalanche C-Chain activity.

## Fuji Demo Workflow

```bash
python -m venv .venv
. .venv/Scripts/activate  # Windows PowerShell: .venv\Scripts\Activate.ps1
pip install -r requirements.txt
copy .env.example .env
```

Edit `.env` and set a Fuji-funded test wallet private key:

```txt
PRIVATE_KEY=your_fuji_test_wallet_private_key
FUJI_RPC_URL=https://api.avax-test.network/ext/bc/C/rpc
```

Deploy the registry:

```bash
python scripts/deploy_fuji.py
```

Copy the printed contract address into `.env`:

```txt
REGISTRY_ADDRESS=0x...
```

Run the batch scoring demo:

```bash
python scripts/simulate_analysis.py
```

The simulation scores five demo token addresses, sends one `batchUpdate` transaction to Fuji, and prints gas used for the video.

## Live Monitor

`chains/avalanche/adapter.py` monitors DEX factory `PairCreated` events and writes enriched token candidates to CSV.

```bash
python chains/avalanche/adapter.py
```

Initial supported factory targets:

- Trader Joe V1-style pair factory
- Pangolin V1-style pair factory

The adapter is intentionally configurable through `.env`, so additional Avalanche DEX factories can be added without changing core logic.

## 24/7 AVAX Collector Worker

`chains/avalanche/avax_collector_v6.py` is the long-running Avalanche worker for
Retro9000/Culture Catalyst activity. It polls Avalanche C-Chain RPC every 60
seconds, queues new contract deployments, scans one queued token every random
15-90 minutes, and publishes six module payloads as separate zero-value EVM
transactions with JSON in `tx.data`:

- `funding_origin`
- `holder_concentration`
- `backdoor_check`
- `liquidity_status`
- `rug_velocity`
- `final_verdict`

Do not paste private keys into chat. Set secrets in Railway variables or local
`.env`:

```txt
AVAX_LOG_PRIVATE_KEY=verified_wallet_private_key
AVAX_RPC=https://api.avax.network/ext/bc/C/rpc
ONCHAIN_LOG_ENABLED=true
ACTIVITY_LOGGER_ADDRESS=deployed_activity_logger_contract
ONCHAIN_LOG_TO_ADDRESS=
REQUIRE_ERC20_METADATA=true
DEX_FACTORIES_JSON=
LB_DEX_FACTORIES_JSON=

AVAX_TELEGRAM_BOT_TOKEN=telegram_bot_token
AVAX_TELEGRAM_CHAT_ID=@RugBusterAvax

MAX_TOKENS_PER_DAY=20
MAX_EUR_TOTAL=20
MAX_AVAX_TOTAL=2
TARGET_AVAX_PER_SCAN=0.001
EVIDENCE_BYTES_TARGET=2048
RUN_UNTIL_DATE=2026-06-17
AVAX_EUR_PRICE_FALLBACK=30
MIN_SCAN_DELAY_MINUTES=5
MAX_SCAN_DELAY_MINUTES=5
```

Deploy the activity logger with the verified wallet:

```bash
npm run deploy:activity:mainnet
```

Then set the printed `ACTIVITY_LOGGER_ADDRESS` in Railway. With
`ACTIVITY_LOGGER_ADDRESS` set, each module is a valid contract call from the
verified wallet to the RugBuster activity contract. If it is not set, the worker
falls back to raw `tx.data` writes, which is less useful for project attribution.

State is stored in `avax_collector_state.json`; token count resets daily, while
total AVAX/EUR spend is tracked until `RUN_UNTIL_DATE`. Transaction hashes are
appended to `avax_scan_log.md`.

## Local Scan API

To power the website and local demos with a real RugBuster backend, start the local API:

```bash
python api/server.py
```

Endpoints:

- `GET /health`
- `POST /api/scan`

Example request:

```json
{
  "address": "0xB31f66AA3C1e785363F0875A1B74E27b85FD66c7",
  "publish": false,
  "publish_modules": false,
  "notify": false
}
```

The API:

- reads Avalanche token metadata through RPC
- reads live market structure from DexScreener
- scores the token through `risk_engine.py`
- can optionally publish the result to `RugBusterRegistry`
- can optionally publish each scan module as its own on-chain registry event
- can optionally send a Telegram alert

For deeper on-chain proof-of-work per real scan, set `publish_modules` to `true`.
The API will send one Avalanche C-Chain transaction for each module:

```json
{
  "address": "0xB31f66AA3C1e785363F0875A1B74E27b85FD66c7",
  "publish_modules": true,
  "notify": true
}
```

Current module writes:

- `token_metadata`
- `liquidity`
- `market_activity`
- `rug_risk`
- `speculation_risk`
- `final_verdict`

Each transaction emits a `ScoreUpdated` registry event with a module-specific
metadata hash. This keeps the activity tied to real scanner output while making
the Avalanche activity visible on-chain.

If the website is open locally, its Apex scanner will try this API first at `http://127.0.0.1:8787`, then fall back to direct DexScreener reads if the API is not running.

## Railway Deploy

The API is ready for Railway deployment.

Files already included:

- `Procfile`
- `requirements.txt`
- `api/server.py`

Recommended Railway setup:

1. Create a new Railway service from the `RugBuster-Avalanche` repo.
2. Railway should detect the Python app automatically.
3. Start command:

```txt
gunicorn --bind 0.0.0.0:$PORT api.server:app
```

4. Add environment variables:

```txt
RUGBUSTER_NETWORK=mainnet
AVALANCHE_RPC_URL=https://api.avax.network/ext/bc/C/rpc
REGISTRY_ADDRESS=0x5F30276B3A5079E088Ec3072884286de5a868355
PRIVATE_KEY=your_registry_reviewer_key
PUBLISH_TO_REGISTRY=false
PUBLISH_MODULES_TO_REGISTRY=false
TELEGRAM_ALERTS=false
TELEGRAM_BOT_TOKEN=
TELEGRAM_CHAT_ID=
```

Set `PUBLISH_MODULES_TO_REGISTRY=true` when the backend should automatically
publish module-level on-chain writes for every real API scan, including scans
started from a Telegram bot or external worker.

5. After deploy, test:

```txt
GET /health
POST /api/scan
```

When the public Railway URL is live, point the website scanner to that URL instead of `127.0.0.1`.

## Temporary AI Bridge

The first version uses deterministic risk rules in `risk_engine.py` so demos can run end-to-end immediately.

Later phases replace or augment this with a fine-tuned RugBusterAI model trained on Avalanche-specific token data.

## Roadmap

- [x] Avalanche grant repository skeleton
- [x] On-chain safety score registry
- [x] DEX pair monitoring adapter
- [x] Temporary risk rules engine
- [x] Fuji deployment script
- [x] Batch publisher simulation
- [ ] Avalanche-specific dataset collection
- [ ] Evaluation harness for AI scoring
- [ ] Wallet and dashboard API integrations

## Positioning

RugBusterAI brings proven Solana fraud-detection experience to Avalanche as an AI-native security certification layer. The long-term goal is a multi-chain cyber-security protocol where each supported chain has its own adapter, dataset, scoring model, and public risk registry.
