from __future__ import annotations

import hashlib
import json
import os
from typing import Any

import requests
from web3 import Web3


MINIMAL_REGISTRY_ABI = [
    {
        "inputs": [
            {"internalType": "address", "name": "token", "type": "address"},
            {"internalType": "uint8", "name": "score", "type": "uint8"},
            {"internalType": "bytes32", "name": "metadataHash", "type": "bytes32"},
        ],
        "name": "updateScore",
        "outputs": [],
        "stateMutability": "nonpayable",
        "type": "function",
    }
]


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


def metadata_hash(payload: dict) -> bytes:
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(encoded).digest()


def _send_update_score(
    *,
    web3: Web3,
    account: Any,
    registry: Any,
    token: str,
    score: int,
    payload: dict,
    nonce: int | None = None,
) -> dict[str, Any]:
    tx = registry.functions.updateScore(
        Web3.to_checksum_address(token),
        int(score),
        metadata_hash(payload),
    ).build_transaction(
        {
            "from": account.address,
            "nonce": web3.eth.get_transaction_count(account.address) if nonce is None else nonce,
            "chainId": web3.eth.chain_id,
        }
    )
    estimated_gas = int(web3.eth.estimate_gas(tx))
    tx["gas"] = max(int(estimated_gas * 1.5), estimated_gas + 25_000)
    tx = apply_fee_strategy(web3, tx)

    signed = account.sign_transaction(tx)
    tx_hash = web3.eth.send_raw_transaction(raw_transaction(signed))
    receipt = web3.eth.wait_for_transaction_receipt(tx_hash, timeout=180)
    if receipt.status != 1:
        raise RuntimeError(f"Registry publish failed: {tx_hash.hex()}")

    return {
        "tx_hash": tx_hash.hex(),
        "gas_used": int(receipt.gasUsed),
        "effective_gas_price": int(getattr(receipt, "effectiveGasPrice", 0) or 0),
        "publisher": account.address,
    }


def publish_score(
    *,
    web3: Web3,
    private_key: str,
    registry_address: str,
    token: str,
    score: int,
    payload: dict,
) -> dict[str, Any]:
    account = web3.eth.account.from_key(private_key)
    registry = web3.eth.contract(
        address=Web3.to_checksum_address(registry_address),
        abi=MINIMAL_REGISTRY_ABI,
    )
    return _send_update_score(
        web3=web3,
        account=account,
        registry=registry,
        token=token,
        score=score,
        payload=payload,
    )


def publish_score_modules(
    *,
    web3: Web3,
    private_key: str,
    registry_address: str,
    token: str,
    modules: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    account = web3.eth.account.from_key(private_key)
    registry = web3.eth.contract(
        address=Web3.to_checksum_address(registry_address),
        abi=MINIMAL_REGISTRY_ABI,
    )
    next_nonce = web3.eth.get_transaction_count(account.address)
    receipts: list[dict[str, Any]] = []

    for module in modules:
        module_name = str(module.get("module") or "unknown")
        module_score = int(module.get("score") or 0)
        module_payload = dict(module.get("payload") or {})
        module_payload.setdefault("module", module_name)
        module_payload.setdefault("token", Web3.to_checksum_address(token))

        receipt = _send_update_score(
            web3=web3,
            account=account,
            registry=registry,
            token=token,
            score=module_score,
            payload=module_payload,
            nonce=next_nonce,
        )
        receipt["module"] = module_name
        receipt["score"] = module_score
        receipts.append(receipt)
        next_nonce += 1

    return receipts


def send_telegram_alert(
    *,
    bot_token: str,
    chat_id: str,
    message: str,
    parse_mode: str = "Markdown",
) -> dict[str, Any]:
    response = requests.post(
        f"https://api.telegram.org/bot{bot_token}/sendMessage",
        json={
            "chat_id": chat_id,
            "text": message,
            "parse_mode": parse_mode,
            "disable_web_page_preview": True,
        },
        timeout=20,
    )
    response.raise_for_status()
    return response.json()


def should_publish() -> bool:
    return (os.getenv("PUBLISH_TO_REGISTRY") or "").strip().lower() in {"1", "true", "yes", "on"}


def should_alert() -> bool:
    return (os.getenv("TELEGRAM_ALERTS") or "").strip().lower() in {"1", "true", "yes", "on"}
