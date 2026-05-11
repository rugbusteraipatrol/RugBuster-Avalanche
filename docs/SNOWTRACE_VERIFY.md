## Snowtrace Verification

Use this guide to verify the mainnet RugBusterRegistry deployment on Snowtrace.

### Mainnet Contract

- Contract address: `0x5F30276B3A5079E088Ec3072884286de5a868355`
- Deploy tx: `0xec2e38d4dfcb0037b263d0d1f496de2ebf4e4895e04cee1a94f5c8c860f720fc`
- First batch tx: `0x22b6006598b1774d86844e7cf794398934b18a6044c989a84c719b5248f84282`

### Source File

- Source file: `contracts/RugBusterRegistry.sol`
- SPDX: `MIT`
- Solidity version: `0.8.20`
- Optimization: `enabled`
- Optimization runs: `200`
- Constructor arguments: none
- External libraries: none

### Snowtrace Flow

1. Open the contract page on Snowtrace mainnet.
2. Open the `Contract` tab.
3. Click `Verify & Publish`.
4. If Snowtrace asks for chain, choose Avalanche C-Chain mainnet.
5. Use single-file Solidity verification and paste the full contents of `contracts/RugBusterRegistry.sol`.
6. Fill the compiler settings exactly:
   - Compiler type: Solidity (single file)
   - Compiler version: `v0.8.20`
   - Optimization: `Yes`
   - Runs: `200`
   - License: `MIT`
7. Leave constructor arguments empty.
8. Submit verification.

### Expected Notes

- This contract is a single file, so no flattening step is needed.
- The constructor has no parameters, so there is no ABI-encoded constructor payload to provide.
- The deployment was made from `0x66065488Af8FbeB34705f966FA43b1BEb4015E83`.

### Useful Links

- Contract: `https://snowtrace.io/address/0x5F30276B3A5079E088Ec3072884286de5a868355`
- Deploy tx: `https://snowtrace.io/tx/0xec2e38d4dfcb0037b263d0d1f496de2ebf4e4895e04cee1a94f5c8c860f720fc`
- Batch tx: `https://snowtrace.io/tx/0x22b6006598b1774d86844e7cf794398934b18a6044c989a84c719b5248f84282`
- Avalanche docs: `https://build.avax.network/docs/primary-network/verify-contract/snowtrace`
