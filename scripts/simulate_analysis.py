"""Simulate RugBusterAI batch analysis on Avalanche Fuji.

This sends one batchUpdate transaction with 5 demo token addresses. It is built
for recording a simple demo: scores are generated locally, written on-chain, and
the script prints gas used so the Avalanche activity is visible.

Usage:
    python scripts/simulate_analysis.py

Required .env values:
    FUJI_RPC_URL=https://api.avax-test.network/ext/bc/C/rpc
    PRIVATE_KEY=your_test_wallet_private_key
    REGISTRY_ADDRESS=deployed_registry_address
"""

from __future__ import annotations

import hashlib
import json
import os
import sys
from pathlib import Path

from dotenv import load_dotenv
from web3 import Web3

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "chains" / "avalanche"))

from risk_engine import score_token  # noqa: E402

ABI_PATH = ROOT / "artifacts" / "RugBusterRegistry.abi.json"

DEMO_TOKENS = [
    {
        "token": "0x1111111111111111111111111111111111111111",
        "name": "Culture Catalyst Coin",
        "symbol": "CCC",
        "decimals": 18,
        "total_supply": 1_000_000_000 * 10**18,
        "deployer": "0xAa000000000000000000000000000000000000Aa",
        "liquidity_usd": 85_000,
    },
    {
        "token": "0x2222222222222222222222222222222222222222",
        "name": "Avalanche Meme Index",
        "symbol": "AMI",
        "decimals": 18,
        "total_supply": 100_000_000 * 10**18,
        "deployer": "0xBb000000000000000000000000000000000000Bb",
        "liquidity_usd": 25_000,
    },
    {
        "token": "0x3333333333333333333333333333333333333333",
        "name": "Claim Airdrop 100x",
        "symbol": "CLAIM",
        "decimals": 18,
        "total_supply": 5_000_000_000 * 10**18,
        "deployer": "0xCc000000000000000000000000000000000000Cc",
        "liquidity_usd": 450,
    },
    {
        "token": "0x4444444444444444444444444444444444444444",
        "name": "Unknown",
        "symbol": "Unknown",
        "decimals": None,
        "total_supply": None,
        "deployer": None,
        "liquidity_usd": None,
    },
    {
        "token": "0x5555555555555555555555555555555555555555",
        "name": "Builder Safety Token",
        "symbol": "BST",
        "decimals": 18,
        "total_supply": 10_000_000 * 10**18,
        "deployer": "0xDd000000000000000000000000000000000000Dd",
        "liquidity_usd": 120_000,
    },
]


def require_env(name: str) -> str:
    value = os.getenv(name)
    if not value:
        raise RuntimeError(f"Missing required environment variable: {name}")
    return value


def metadata_hash(payload: dict) -> bytes:
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(encoded).digest()


def load_abi() -> list[dict]:
    if ABI_PATH.exists():
        return json.loads(ABI_PATH.read_text(encoding="utf-8"))

    # Minimal ABI fallback for users who only copied the source without artifacts.
    return [
        {
            "inputs": [
                {"internalType": "address[]", "name": "tokens", "type": "address[]"},
                {"internalType": "uint8[]", "name": "scores", "type": "uint8[]"},
                {"internalType": "bytes32[]", "name": "metadataHashes", "type": "bytes32[]"},
            ],
            "name": "batchUpdate",
            "outputs": [],
            "stateMutability": "nonpayable",
            "type": "function",
        }
    ]


def main() -> None:
    load_dotenv(ROOT / ".env")
    rpc_url = os.getenv("FUJI_RPC_URL") or os.getenv("AVALANCHE_RPC_URL") or "https://api.avax-test.network/ext/bc/C/rpc"
    private_key = require_env("PRIVATE_KEY")
    registry_address = Web3.to_checksum_address(require_env("REGISTRY_ADDRESS"))

    web3 = Web3(Web3.HTTPProvider(rpc_url, request_kwargs={"timeout": 60}))
    if not web3.is_connected():
        raise RuntimeError(f"Could not connect to Fuji RPC: {rpc_url}")

    account = web3.eth.account.from_key(private_key)
    registry = web3.eth.contract(address=registry_address, abi=load_abi())

    tokens: list[str] = []
    scores: list[int] = []
    hashes: list[bytes] = []

    print("Simulated RugBusterAI analysis")
    for item in DEMO_TOKENS:
        risk = score_token(item)
        tokens.append(Web3.to_checksum_address(item["token"]))
        scores.append(risk.score)
        hashes.append(metadata_hash({"token": item, "risk": risk.to_dict()}))
        print(f"{risk.label:7} {risk.score:03d} {item['symbol']:8} {item['token']}")

    tx = registry.functions.batchUpdate(tokens, scores, hashes).build_transaction(
        {
            "from": account.address,
            "nonce": web3.eth.get_transaction_count(account.address),
            "chainId": web3.eth.chain_id,
        }
    )
    tx["gas"] = web3.eth.estimate_gas(tx)
    tx["maxFeePerGas"] = web3.eth.gas_price * 2
    tx["maxPriorityFeePerGas"] = web3.to_wei(2, "gwei")

    signed = account.sign_transaction(tx)
    tx_hash = web3.eth.send_raw_transaction(signed.rawTransaction)
    print(f"Batch update sent: {tx_hash.hex()}")

    receipt = web3.eth.wait_for_transaction_receipt(tx_hash, timeout=180)
    if receipt.status != 1:
        raise RuntimeError(f"Batch update failed: {tx_hash.hex()}")

    print("Batch update confirmed")
    print(f"Registry: {registry_address}")
    print(f"Reviewer: {account.address}")
    print(f"Tokens scored: {len(tokens)}")
    print(f"Gas used: {receipt.gasUsed}")
    print(f"Effective gas price: {receipt.effectiveGasPrice}")


if __name__ == "__main__":
    main()
