# RugBuster Scoring Roadmap

## Phase 2: shared scoring-engine improvements

These items belong in the main multi-chain scoring engine, not in an Avalanche-only fork.

- Token identity classification: proxy, LP token, bridge asset, router, factory, EOA, registry contract, and standard ERC-20.
- Multi-pair liquidity aggregation across supported DEXs instead of relying on a single discovered pair.
- Honeypot and sell-simulation checks where chain and RPC safety allow read-only simulation.
- Owner-renounced and privileged-role state checks.
- Shared canonical-asset discovery from verified token lists and bridge registries, with manual registry entries only as overrides.

