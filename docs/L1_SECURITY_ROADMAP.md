# RugBuster Apex L1 Security Roadmap

RugBuster Apex is being built in phases so each step is usable on Avalanche
before the project attempts a full L1 security layer.

## Phase 1 - Scanner And Registry

Status: live

- Avalanche token scanner
- Rug Score based on on-chain hard facts
- Speculation Score based on market and liquidity evidence
- UNKNOWN state when market evidence is too thin
- Verified mainnet registry contract
- Reviewer wallet authorization
- Reviewer-published on-chain reports

Outcome: RugBuster Apex can scan tokens and publish security attestations on
Avalanche C-Chain.

## Phase 2 - Evidence Corpus

Status: live

- AVAX collector worker running on Railway
- Full scan records stored in Postgres
- Duplicate contract addresses skipped
- Private raw evidence kept out of the public API
- Classifier upgraded to `weighted_v2`

Outcome: RugBuster Apex is building an Avalanche-specific risk dataset instead
of relying only on generic scanner output.

## Phase 3 - Builder API

Status: live

- Public `/health`
- Public `/score?address=0x...`
- Protected `/scan?address=0x...`
- API key control for deep scans
- Cache-first public score lookups

Outcome: wallets, launchpads, dashboards, and DEX tools can integrate compact
Avalanche risk labels without getting access to the raw private corpus.

## Phase 4 - Sentinel Monitoring

Status: planned

RugBuster Sentinel will convert the collector and API into proactive monitoring:

- Watch newly discovered AVAX tokens
- Re-score suspicious tokens periodically
- Send Telegram alerts for DANGER and severe WARN cases
- Track repeated deployer behavior
- Keep a public-safe activity feed for ecosystem visibility
- Preserve private evidence for model training and partner review

Outcome: RugBuster Apex becomes an always-on Avalanche threat-intelligence
service, not just a manual scanner.

## Phase 5 - L1 Security Layer

Status: long-term

The L1 path should be attempted only after the tooling proves demand:

- Dedicated Avalanche L1 or security-focused execution layer
- Builder-facing token risk oracle
- Registry and API integration for wallets and launchpads
- Validator or operator dashboard for security events
- Future ICM/ICTT-aware integrations where useful for Avalanche-native
  interoperability

Outcome: RugBuster Apex becomes a security layer for Avalanche builders that can
serve token intelligence, attestations, and monitoring signals across the
ecosystem.

## Principle

Do not jump straight to an L1 because it sounds bigger. Build the tooling first,
prove usage, collect data, earn integrations, then make the L1 decision from
evidence.
