"""Shared network helpers for RugBuster Avalanche scripts."""

from __future__ import annotations

import os
from pathlib import Path

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parents[1]

NETWORKS = {
    "fuji": {
        "rpc_env": "FUJI_RPC_URL",
        "default_rpc": "https://api.avax-test.network/ext/bc/C/rpc",
        "chain_id": 43113,
        "label": "Avalanche Fuji",
    },
    "mainnet": {
        "rpc_env": "AVALANCHE_RPC_URL",
        "default_rpc": "https://api.avax.network/ext/bc/C/rpc",
        "chain_id": 43114,
        "label": "Avalanche C-Chain Mainnet",
    },
}


def load_env() -> None:
    load_dotenv(ROOT / ".env")


def resolve_network() -> str:
    raw = (os.getenv("RUGBUSTER_NETWORK") or "fuji").strip().lower()
    if raw not in NETWORKS:
        raise RuntimeError(f"Unsupported RUGBUSTER_NETWORK: {raw}")
    return raw


def resolve_rpc(network: str) -> str:
    config = NETWORKS[network]
    return os.getenv(config["rpc_env"]) or config["default_rpc"]
