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

## Avalanche Adapter

`chains/avalanche/adapter.py` monitors DEX factory `PairCreated` events and writes enriched token candidates to CSV.

Initial supported factory targets:

- Trader Joe V1-style pair factory
- Pangolin V1-style pair factory

The adapter is intentionally configurable through `.env`, so additional Avalanche DEX factories can be added without changing core logic.

## Temporary AI Bridge

The first version uses deterministic risk rules in `risk_engine.py` so demos can run end-to-end immediately.

Later phases replace or augment this with a fine-tuned RugBusterAI model trained on Avalanche-specific token data.

## Local Setup

```bash
python -m venv .venv
. .venv/Scripts/activate  # Windows
pip install -r requirements.txt
copy .env.example .env
python chains/avalanche/adapter.py
```

## Roadmap

- [x] Avalanche grant repository skeleton
- [x] On-chain safety score registry
- [x] DEX pair monitoring adapter
- [x] Temporary risk rules engine
- [ ] Fuji deployment script
- [ ] Batch publisher for registry writes
- [ ] Avalanche-specific dataset collection
- [ ] Evaluation harness for AI scoring
- [ ] Wallet and dashboard API integrations

## Positioning

RugBusterAI brings proven Solana fraud-detection experience to Avalanche as an AI-native security certification layer. The long-term goal is a multi-chain cyber-security protocol where each supported chain has its own adapter, dataset, scoring model, and public risk registry.
