"""Deploy RugBusterRegistry to Avalanche.

Usage:
    python scripts/deploy_fuji.py

Required .env values:
    RUGBUSTER_NETWORK=fuji or mainnet
    PRIVATE_KEY=your_test_wallet_private_key

The script compiles contracts/RugBusterRegistry.sol with solcx, deploys it,
and prints the contract address plus transaction gas used for the demo video.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

from dotenv import load_dotenv
from solcx import compile_standard, install_solc, set_solc_version
from web3 import Web3
from network_config import ROOT, NETWORKS, load_env, resolve_network, resolve_rpc

CONTRACT_PATH = ROOT / "contracts" / "RugBusterRegistry.sol"
ARTIFACT_DIR = ROOT / "artifacts"
SOLC_VERSION = "0.8.20"


def require_env(name: str) -> str:
    value = os.getenv(name)
    if not value:
        raise RuntimeError(f"Missing required environment variable: {name}")
    return value


def raw_transaction(signed_tx: Any) -> bytes:
    return getattr(signed_tx, "raw_transaction", None) or getattr(signed_tx, "rawTransaction")


def apply_fee_strategy(web3: Web3, tx: dict) -> dict:
    latest_block = web3.eth.get_block("latest")
    base_fee = int(latest_block.get("baseFeePerGas", 0) or 0)
    network_gas_price = int(web3.eth.gas_price)
    priority_fee = min(web3.to_wei(2, "gwei"), max(network_gas_price // 2, 1))
    max_fee = max(network_gas_price * 2, base_fee * 2 + priority_fee, priority_fee + 1)
    tx["maxPriorityFeePerGas"] = priority_fee
    tx["maxFeePerGas"] = max_fee
    return tx


def compile_contract() -> tuple[list[dict], str]:
    source = CONTRACT_PATH.read_text(encoding="utf-8")
    install_solc(SOLC_VERSION)
    set_solc_version(SOLC_VERSION)

    compiled = compile_standard(
        {
            "language": "Solidity",
            "sources": {"RugBusterRegistry.sol": {"content": source}},
            "settings": {
                "optimizer": {"enabled": True, "runs": 200},
                "outputSelection": {"*": {"*": ["abi", "evm.bytecode.object"]}},
            },
        }
    )

    contract = compiled["contracts"]["RugBusterRegistry.sol"]["RugBusterRegistry"]
    abi = contract["abi"]
    bytecode = contract["evm"]["bytecode"]["object"]

    ARTIFACT_DIR.mkdir(exist_ok=True)
    (ARTIFACT_DIR / "RugBusterRegistry.abi.json").write_text(json.dumps(abi, indent=2), encoding="utf-8")
    (ARTIFACT_DIR / "RugBusterRegistry.bytecode.txt").write_text(bytecode, encoding="utf-8")
    return abi, bytecode


def main() -> None:
    load_env()
    network = resolve_network()
    rpc_url = resolve_rpc(network)
    private_key = require_env("PRIVATE_KEY")

    web3 = Web3(Web3.HTTPProvider(rpc_url, request_kwargs={"timeout": 60}))
    if not web3.is_connected():
        raise RuntimeError(f"Could not connect to {NETWORKS[network]['label']} RPC: {rpc_url}")

    account = web3.eth.account.from_key(private_key)
    chain_id = web3.eth.chain_id
    balance = web3.eth.get_balance(account.address)
    if balance == 0:
        raise RuntimeError(f"Wallet {account.address} has no AVAX for gas on {NETWORKS[network]['label']}")

    abi, bytecode = compile_contract()
    registry = web3.eth.contract(abi=abi, bytecode=bytecode)

    tx = registry.constructor().build_transaction(
        {
            "from": account.address,
            "nonce": web3.eth.get_transaction_count(account.address),
            "chainId": chain_id,
        }
    )
    tx["gas"] = web3.eth.estimate_gas(tx)
    tx = apply_fee_strategy(web3, tx)

    signed = account.sign_transaction(tx)
    tx_hash = web3.eth.send_raw_transaction(raw_transaction(signed))
    print(f"Deploy sent: {tx_hash.hex()}")

    receipt = web3.eth.wait_for_transaction_receipt(tx_hash, timeout=180)
    if receipt.status != 1:
        raise RuntimeError(f"Deploy failed: {tx_hash.hex()}")

    print("RugBusterRegistry deployed")
    print(f"Network: {NETWORKS[network]['label']}")
    print(f"Contract: {receipt.contractAddress}")
    print(f"Deployer: {account.address}")
    print(f"Network chainId: {chain_id}")
    print(f"Gas used: {receipt.gasUsed}")
    print("Add this to .env:")
    print(f"REGISTRY_ADDRESS={receipt.contractAddress}")


if __name__ == "__main__":
    main()
