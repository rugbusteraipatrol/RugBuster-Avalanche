"""Avalanche C-Chain DEX pair monitor for RugBusterAI.

The adapter watches Uniswap V2-style PairCreated events from configured DEX
factories, enriches token metadata through Web3, scores candidates through the
temporary risk engine, and appends rows to a local CSV dataset.

This is a grant/demo-safe skeleton: it is designed to run with public RPCs, but
production usage should use a dedicated RPC provider and persistent indexing.
"""

from __future__ import annotations

import csv
import json
import os
import time
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

from dotenv import load_dotenv
from web3 import Web3
from web3.contract import Contract

from risk_engine import score_token

load_dotenv()

AVALANCHE_RPC_URL = os.getenv("AVALANCHE_RPC_URL", "https://api.avax.network/ext/bc/C/rpc")
POLL_SECONDS = int(os.getenv("POLL_SECONDS", "12"))
START_BLOCK_LOOKBACK = int(os.getenv("START_BLOCK_LOOKBACK", "250"))
OUTPUT_CSV = Path(os.getenv("OUTPUT_CSV", "data/avalanche_tokens.csv"))

# Uniswap V2-style Avalanche factories. Override or extend through DEX_FACTORIES_JSON.
DEFAULT_FACTORIES = {
    "trader_joe_v1": "0x9Ad6C38BE94206cA50bb0d90783181662f0Cfa10",
    "pangolin_v1": "0xE54Ca86531e17Ef3616d22Ca28b0D458b6C89106",
}

PAIR_CREATED_ABI = json.loads(
    """
    [{
      "anonymous": false,
      "inputs": [
        {"indexed": true, "internalType": "address", "name": "token0", "type": "address"},
        {"indexed": true, "internalType": "address", "name": "token1", "type": "address"},
        {"indexed": false, "internalType": "address", "name": "pair", "type": "address"},
        {"indexed": false, "internalType": "uint256", "name": "allPairsLength", "type": "uint256"}
      ],
      "name": "PairCreated",
      "type": "event"
    }]
    """
)

ERC20_ABI = json.loads(
    """
    [
      {"constant": true, "inputs": [], "name": "name", "outputs": [{"name": "", "type": "string"}], "type": "function"},
      {"constant": true, "inputs": [], "name": "symbol", "outputs": [{"name": "", "type": "string"}], "type": "function"},
      {"constant": true, "inputs": [], "name": "decimals", "outputs": [{"name": "", "type": "uint8"}], "type": "function"},
      {"constant": true, "inputs": [], "name": "totalSupply", "outputs": [{"name": "", "type": "uint256"}], "type": "function"}
    ]
    """
)

PAIR_ABI = json.loads(
    """
    [
      {"constant": true, "inputs": [], "name": "getReserves", "outputs": [
        {"name": "_reserve0", "type": "uint112"},
        {"name": "_reserve1", "type": "uint112"},
        {"name": "_blockTimestampLast", "type": "uint32"}
      ], "type": "function"}
    ]
    """
)


@dataclass(frozen=True)
class TokenCandidate:
    observed_at: str
    dex: str
    block_number: int
    tx_hash: str
    pair: str
    token: str
    other_token: str
    name: str | None
    symbol: str | None
    decimals: int | None
    total_supply: int | None
    deployer: str | None
    liquidity_usd: float | None
    score: int
    label: str
    reasons: str


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def load_factories() -> dict[str, str]:
    raw = os.getenv("DEX_FACTORIES_JSON")
    if not raw:
        return DEFAULT_FACTORIES
    custom = json.loads(raw)
    return {str(name): Web3.to_checksum_address(address) for name, address in custom.items()}


def call_optional(contract: Contract, fn_name: str) -> Any | None:
    try:
        return getattr(contract.functions, fn_name)().call()
    except Exception:
        return None


def get_token_metadata(web3: Web3, token_address: str) -> dict[str, Any]:
    token = web3.eth.contract(address=Web3.to_checksum_address(token_address), abi=ERC20_ABI)
    tx_count = web3.eth.get_transaction_count(Web3.to_checksum_address(token_address))

    return {
        "token": Web3.to_checksum_address(token_address),
        "name": call_optional(token, "name") or "Unknown",
        "symbol": call_optional(token, "symbol") or "Unknown",
        "decimals": call_optional(token, "decimals"),
        "total_supply": call_optional(token, "totalSupply"),
        # Contract deployer is not directly available from JSON-RPC without trace APIs.
        # A production indexer should resolve this from creation transaction traces.
        "deployer": None,
        "contract_tx_count": tx_count,
        "liquidity_usd": None,
    }


def enrich_pair(web3: Web3, event: Any, dex_name: str) -> Iterable[TokenCandidate]:
    args = event["args"]
    token0 = Web3.to_checksum_address(args["token0"])
    token1 = Web3.to_checksum_address(args["token1"])
    pair = Web3.to_checksum_address(args["pair"])
    tx_hash = event["transactionHash"].hex()
    block_number = int(event["blockNumber"])

    for token_address, other_token in ((token0, token1), (token1, token0)):
        metadata = get_token_metadata(web3, token_address)
        metadata.update({"pair": pair, "other_token": other_token, "dex": dex_name})
        risk = score_token(metadata)

        yield TokenCandidate(
            observed_at=utc_now(),
            dex=dex_name,
            block_number=block_number,
            tx_hash=tx_hash,
            pair=pair,
            token=token_address,
            other_token=other_token,
            name=metadata.get("name"),
            symbol=metadata.get("symbol"),
            decimals=metadata.get("decimals"),
            total_supply=metadata.get("total_supply"),
            deployer=metadata.get("deployer"),
            liquidity_usd=metadata.get("liquidity_usd"),
            score=risk.score,
            label=risk.label,
            reasons="; ".join(risk.reasons),
        )


def append_candidates(path: Path, candidates: Iterable[TokenCandidate]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    rows = [asdict(candidate) for candidate in candidates]
    if not rows:
        return

    exists = path.exists()
    with path.open("a", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0].keys()))
        if not exists:
            writer.writeheader()
        writer.writerows(rows)


def build_contracts(web3: Web3, factories: dict[str, str]) -> dict[str, Contract]:
    return {
        name: web3.eth.contract(address=Web3.to_checksum_address(address), abi=PAIR_CREATED_ABI)
        for name, address in factories.items()
    }


def run_once(web3: Web3, contracts: dict[str, Contract], from_block: int, to_block: int) -> int:
    total = 0
    for dex_name, factory in contracts.items():
        try:
            events = factory.events.PairCreated().get_logs(fromBlock=from_block, toBlock=to_block)
        except Exception as exc:
            print(f"[{dex_name}] log fetch failed: {exc}")
            continue

        for event in events:
            candidates = list(enrich_pair(web3, event, dex_name))
            append_candidates(OUTPUT_CSV, candidates)
            total += len(candidates)
            for candidate in candidates:
                print(f"{candidate.label} {candidate.score:03d} {candidate.symbol} {candidate.token}")

    return total


def main() -> None:
    web3 = Web3(Web3.HTTPProvider(AVALANCHE_RPC_URL, request_kwargs={"timeout": 30}))
    if not web3.is_connected():
        raise RuntimeError(f"Could not connect to Avalanche RPC: {AVALANCHE_RPC_URL}")

    factories = load_factories()
    contracts = build_contracts(web3, factories)
    latest = web3.eth.block_number
    from_block = max(0, latest - START_BLOCK_LOOKBACK)

    print("RugBuster Avalanche adapter online")
    print(f"RPC: {AVALANCHE_RPC_URL}")
    print(f"Factories: {', '.join(factories)}")
    print(f"Starting from block {from_block}")

    while True:
        latest = web3.eth.block_number
        if from_block <= latest:
            found = run_once(web3, contracts, from_block, latest)
            if found:
                print(f"Saved {found} token candidate rows to {OUTPUT_CSV}")
            from_block = latest + 1
        time.sleep(POLL_SECONDS)


if __name__ == "__main__":
    main()
