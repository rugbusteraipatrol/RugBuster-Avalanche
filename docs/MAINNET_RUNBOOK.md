# RugBuster Avalanche Mainnet Runbook

## Goal

Deploy `RugBusterRegistry.sol` to Avalanche C-Chain mainnet, publish one or more
real `batchUpdate` transactions, and verify the contract on Snowtrace before the
Retro9000 submission deadline.

## Preconditions

- Wallet has AVAX on Avalanche C-Chain mainnet
- Local `.env` contains the deployer `PRIVATE_KEY`
- Dependencies are already installed in `.venv`

## 1. Switch scripts to mainnet

Edit `.env` and set:

```txt
RUGBUSTER_NETWORK=mainnet
AVALANCHE_RPC_URL=https://api.avax.network/ext/bc/C/rpc
REGISTRY_ADDRESS=
```

Keep `FUJI_RPC_URL` in the file for later, but the active deploy flow will use
`RUGBUSTER_NETWORK=mainnet`.

## 2. Deploy the registry

```powershell
.\.venv\Scripts\python.exe scripts\deploy_fuji.py
```

This script now works for both Fuji and mainnet. On success it prints:

- network
- contract address
- deployer address
- gas used

Copy the printed contract address into `.env`:

```txt
REGISTRY_ADDRESS=0x...
```

## 3. Publish a real batch write

```powershell
.\.venv\Scripts\python.exe scripts\simulate_analysis.py
```

This sends one `batchUpdate` transaction using five demo token addresses and
prints:

- batch tx hash
- gas used
- effective gas price

## 4. Verify on Snowtrace

Open your mainnet contract page on Snowtrace and go to `Contract` -> `Verify & Publish`.

Use:

- Compiler Type: `Solidity (Single file)`
- Compiler Version: `v0.8.20`
- Open Source License Type: `MIT`
- Optimization: `Yes`
- Runs: `200`

Paste the source from:

`contracts/RugBusterRegistry.sol`

Constructor arguments are empty for this contract.

Official guide:

https://build.avax.network/docs/primary-network/verify-contract/snowtrace

## 5. Capture proof for grant

Collect these links:

- mainnet contract address page
- deploy transaction page
- first batch update transaction page

Record:

- contract address
- gas used on deploy
- gas used on batch update
- wallet address used for publishing

## Notes

- Avalanche burns both the base fee and the priority fee.
- Our measured Fuji costs were well below 1 AVAX total, so a small mainnet AVAX
  balance should be enough for deploy plus several writes.
- Keep the wallet lightweight; do not use a treasury wallet for early testing.
