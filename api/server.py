from __future__ import annotations

import json
import hashlib
import hmac
import os
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Any

import requests
from flask import Flask, jsonify, request
from web3 import Web3

try:
    import psycopg2
except ImportError:  # pragma: no cover - optional when DATABASE_URL is absent
    psycopg2 = None

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "chains" / "avalanche"))
sys.path.insert(0, str(ROOT / "scripts"))

from bridge import publish_score, publish_score_modules, send_telegram_alert  # noqa: E402
from risk_engine import score_token  # noqa: E402
from network_config import NETWORKS, load_env, resolve_network, resolve_rpc  # noqa: E402
from avax_collector_v6 import (  # noqa: E402
    get_avax_balance as collector_get_avax_balance,
    get_contract_transactions as collector_get_contract_transactions,
    get_creator_stats as collector_get_creator_stats,
    get_token_info_avax as collector_get_token_info_avax,
    run_cia_analysis_avax as collector_run_cia_analysis_avax,
    run_v5_analysis_avax as collector_run_v5_analysis_avax,
    run_v6_analysis_avax as collector_run_v6_analysis_avax,
    routescan_api_health as collector_routescan_api_health,
)

load_env()

DEXSCREENER_API = "https://api.dexscreener.com/latest/dex/tokens"
GLACIER_API = "https://glacier-api.avax.network"
STABLE_QUOTES = {
    "0xB97EF9Ef8734C71904D8002F8b6Bc66Dd9c48a6E": 1.0,  # USDC
    "0xA7D7079b0FEaD91F3e65f86E8915Cb59c1a4C664": 1.0,  # USDC.e
    "0x9702230A8Ea53601f5cD2dc00fDBc13d4dF4A8c7": 1.0,  # USDT.e
}
COMMON_QUOTES = [
    "0xB31f66AA3C1e785363F0875A1B74E27b85FD66c7",  # WAVAX
    "0xB97EF9Ef8734C71904D8002F8b6Bc66Dd9c48a6E",  # USDC
    "0xA7D7079b0FEaD91F3e65f86E8915Cb59c1a4C664",  # USDC.e
    "0x9702230A8Ea53601f5cD2dc00fDBc13d4dF4A8c7",  # USDT.e
    "0x49D5c2BdFfac6CE2BFdB6640F4F80f226bc10bAB",  # WETH.e
]
KNOWN_TOKEN_METADATA = {
    "0xb31f66aa3c1e785363f0875a1b74e27b85fd66c7": {
        "name": "Wrapped AVAX",
        "symbol": "WAVAX",
        "decimals": 18,
        "category": "canonical_wrapped_native",
    },
    "0xb97ef9ef8734c71904d8002f8b6bc66dd9c48a6e": {
        "name": "USD Coin",
        "symbol": "USDC",
        "decimals": 6,
        "category": "canonical_stablecoin",
    },
    "0xa7d7079b0fead91f3e65f86e8915cb59c1a4c664": {
        "name": "USD Coin Bridged",
        "symbol": "USDC.e",
        "decimals": 6,
        "category": "canonical_stablecoin",
    },
    "0x9702230a8ea53601f5cd2dc00fdbc13d4df4a8c7": {
        "name": "Tether USD",
        "symbol": "USDT",
        "decimals": 6,
        "category": "canonical_stablecoin",
    },
    "0x49d5c2bdffac6ce2bfdb6640f4f80f226bc10bab": {
        "name": "Wrapped Ether",
        "symbol": "WETH.e",
        "decimals": 18,
        "category": "canonical_wrapped_asset",
    },
    "0x5947bb275c521040051d82396192181b413227a3": {
        "name": "Chainlink Token",
        "symbol": "LINK.e",
        "decimals": 18,
        "category": "canonical_bridged_asset",
    },
    "0xd586e7f844cea2f87f50152665bcbc2c279d8d70": {
        "name": "Dai Stablecoin",
        "symbol": "DAI.e",
        "decimals": 18,
        "category": "canonical_bridged_asset",
    },
    "0x50b7545627a5162f82a992c33b87adc75187b218": {
        "name": "Wrapped BTC",
        "symbol": "WBTC.e",
        "decimals": 8,
        "category": "canonical_bridged_asset",
    },
    "0x8ebaf22b6f053dffeaf46f4dd9efa95d89ba8580": {
        "name": "Uniswap",
        "symbol": "UNI.e",
        "decimals": 18,
        "category": "canonical_bridged_asset",
    },
    "0x37b608519f91f70f2eeb0e5ed9af4061722e4f76": {
        "name": "SushiToken",
        "symbol": "SUSHI.e",
        "decimals": 18,
        "category": "canonical_bridged_asset",
    },
    "0x152b9d0fdc40c096757f570a51e494bd4b943e50": {
        "name": "Bitcoin",
        "symbol": "BTC.b",
        "decimals": 8,
        "category": "canonical_bridged_asset",
    },
    "0xc891eb4cbdeff6e073e859e987815ed1505c2acd": {
        "name": "Euro Coin",
        "symbol": "EURC",
        "decimals": 6,
        "category": "canonical_stablecoin",
    },
    "0xd24c2ad096400b6fbcd2ad8b24e7acbc21a1da64": {
        "name": "Frax",
        "symbol": "FRAX",
        "decimals": 18,
        "category": "canonical_stablecoin",
    },
    "0x130966628846bfd36ff31a822705796e8cb8c18d": {
        "name": "Magic Internet Money",
        "symbol": "MIM",
        "decimals": 18,
        "category": "canonical_stablecoin",
    },
    "0x2b2c81e08f1af8835a78bb2a90ae924ace0ea4be": {
        "name": "Staked AVAX",
        "symbol": "sAVAX",
        "decimals": 18,
        "category": "established_liquid_staking_token",
    },
    "0x6e84a6216ea6dacc71ee8e6b0a5b7322eebc0fdd": {
        "name": "JoeToken",
        "symbol": "JOE",
        "decimals": 18,
        "category": "established_protocol_token",
    },
    "0x8729438eb15e2c8b576fcc6aecda6a148776c0f5": {
        "name": "BENQI",
        "symbol": "QI",
        "decimals": 18,
        "category": "established_protocol_token",
    },
    "0x60781c2586d68229fde47564546784ab3faca982": {
        "name": "Pangolin",
        "symbol": "PNG",
        "decimals": 18,
        "category": "established_protocol_token",
    },
    "0x62edc0692bd897d2295872a9ffcac5425011c661": {
        "name": "GMX",
        "symbol": "GMX",
        "decimals": 18,
        "category": "established_protocol_token",
    },
    "0x2f6f07cdcf3588944bf4c42ac74ff24bf56e7590": {
        "name": "StargateToken",
        "symbol": "STG",
        "decimals": 18,
        "category": "established_protocol_token",
    },
    "0x59414b3089ce2af0010e7523dea7e2b35d776ec7": {
        "name": "Yak Token",
        "symbol": "YAK",
        "decimals": 18,
        "category": "established_protocol_token",
    },
}
ADMIN_FUNCTION_SIGNATURES = {
    "715018a6": "renounceOwnership()",
    "8da5cb5b": "owner()",
    "f2fde38b": "transferOwnership(address)",
    "ac7475ed": "updateOperator(address)",
    "6d44a3b2": "updateOperator(address,bool)",
    "b3ab15fb": "setOperator(address)",
    "558a7297": "setOperator(address,bool)",
    "14fc2812": "setAuthorized(address)",
    "711bf9b2": "setAuthorized(address,bool)",
    "eecea000": "setAuthorization(address,bool)",
    "b6a5d7de": "authorize(address)",
    "fca3b5aa": "setMinter(address)",
    "cf456ae7": "setMinter(address,bool)",
    "40c10f19": "mint(address,uint256)",
    "9dc29fac": "burn(address,uint256)",
    "f9f92be4": "blacklist(address)",
    "153b0d1e": "setBlacklist(address,bool)",
}
EIP1967_IMPLEMENTATION_SLOT = "0x360894a13ba1a3210667c828492db98dca3e2076cc3735a920a3ca505d382bbc"
MAINNET_FACTORIES = {
    "TRADERJOE": "0x9Ad6C38BE94206cA50bb0d90783181662f0Cfa10",
    "PANGOLIN": "0xE54Ca86531e17Ef3616d22Ca28b0D458b6C89106",
}
FUJI_FACTORIES = {
    "TRADERJOE_FUJI": "0xFf06D441D352F33041926D451a5118742880017D",
    "PANGOLIN_FUJI": "0xefa94DE7a4659D7836704329a8ca30E89e599d14",
}

FACTORY_ABI = json.loads(
    """
    [
      {
        "constant": true,
        "inputs": [
          {"name": "tokenA", "type": "address"},
          {"name": "tokenB", "type": "address"}
        ],
        "name": "getPair",
        "outputs": [{"name": "pair", "type": "address"}],
        "type": "function"
      }
    ]
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
      {"constant": true, "inputs": [], "name": "token0", "outputs": [{"name": "", "type": "address"}], "type": "function"},
      {"constant": true, "inputs": [], "name": "token1", "outputs": [{"name": "", "type": "address"}], "type": "function"},
      {"constant": true, "inputs": [], "name": "getReserves", "outputs": [
        {"name": "_reserve0", "type": "uint112"},
        {"name": "_reserve1", "type": "uint112"},
        {"name": "_blockTimestampLast", "type": "uint32"}
      ], "type": "function"}
    ]
    """
)

app = Flask(__name__)
SCAN_CACHE_TTL_SECONDS = 180
SCAN_CACHE: dict[str, dict[str, Any]] = {}
PORTFOLIO_SCAN_WORKERS = 3
KNOWN_TOKEN_VALIDATION: dict[str, Any] = {"checked": False, "ok": False, "errors": []}
DATABASE_URL = os.getenv("DATABASE_URL")
RECENT_SCAN_LIMIT = int(os.getenv("RECENT_SCAN_LIMIT", "10"))
RECENT_SCAN_INGEST_TOKEN = os.getenv("RECENT_SCAN_INGEST_TOKEN", "").strip()
RECENT_SCANS: list[dict[str, Any]] = []
DEEPSEEK_API_KEY = os.getenv("DEEPSEEK_API_KEY", "").strip()
DEEPSEEK_MODEL = os.getenv("DEEPSEEK_MODEL", "deepseek-chat").strip()
DEEPSEEK_API_URL = os.getenv("DEEPSEEK_API_URL", "https://api.deepseek.com/chat/completions").strip()
DEEPSEEK_TIMEOUT_SECONDS = int(os.getenv("DEEPSEEK_TIMEOUT_SECONDS", "20"))
USE_REMOTE_SCORING_ENGINE = os.getenv("USE_REMOTE_SCORING_ENGINE", "true").strip().lower() in {"1", "true", "yes", "on"}
SCORING_ENGINE_URL = os.getenv("SCORING_ENGINE_URL", "").strip().rstrip("/")
SCORING_ENGINE_HMAC_SECRET = os.getenv("SCORING_ENGINE_HMAC_SECRET", "").strip()
SCORING_ENGINE_TIMEOUT_SECONDS = float(os.getenv("SCORING_ENGINE_TIMEOUT_SECONDS", "8"))


def cache_key(address: str) -> str:
    return Web3.to_checksum_address(address)


def get_cached_report(address: str) -> dict[str, Any] | None:
    entry = SCAN_CACHE.get(cache_key(address))
    if not entry:
        return None
    if time.time() - entry["ts"] > SCAN_CACHE_TTL_SECONDS:
        SCAN_CACHE.pop(cache_key(address), None)
        return None
    return entry["report"]


def put_cached_report(address: str, report: dict[str, Any]) -> None:
    SCAN_CACHE[cache_key(address)] = {"ts": time.time(), "report": report}


def is_known_asset_address(address: str) -> bool:
    if not Web3.is_address(address):
        return False
    return Web3.to_checksum_address(address).lower() in KNOWN_TOKEN_METADATA


def validate_known_token_metadata(web3: Web3) -> dict[str, Any]:
    if KNOWN_TOKEN_VALIDATION["checked"]:
        return KNOWN_TOKEN_VALIDATION
    errors: list[str] = []
    for raw_address, expected in KNOWN_TOKEN_METADATA.items():
        try:
            checksum = Web3.to_checksum_address(raw_address)
            if not web3.eth.get_code(checksum):
                errors.append(f"{expected.get('symbol')} {checksum}: no bytecode")
                continue
            token = web3.eth.contract(address=checksum, abi=ERC20_ABI)
            symbol = call_optional(token, "symbol")
            expected_symbol = str(expected.get("symbol") or "").lower()
            if expected_symbol and str(symbol or "").lower() != expected_symbol:
                errors.append(f"{expected.get('symbol')} {checksum}: symbol={symbol!r}")
        except Exception as exc:
            errors.append(f"{expected.get('symbol')} {raw_address}: {type(exc).__name__}")
    KNOWN_TOKEN_VALIDATION.update({"checked": True, "ok": not errors, "errors": errors[:12]})
    if errors:
        app.logger.error("Known Avalanche token metadata validation failed: %s", errors[:12])
    return KNOWN_TOKEN_VALIDATION


def implementation_address(web3: Web3, address: str) -> str | None:
    try:
        raw = web3.eth.get_storage_at(Web3.to_checksum_address(address), EIP1967_IMPLEMENTATION_SLOT)
    except Exception:
        return None
    if not raw or int.from_bytes(raw, "big") == 0:
        return None
    candidate = "0x" + raw.hex()[-40:]
    if int(candidate, 16) == 0:
        return None
    try:
        checksum = Web3.to_checksum_address(candidate)
        code = web3.eth.get_code(checksum)
    except Exception:
        return None
    return checksum if code else None


def detect_admin_controls(web3: Web3, address: str, is_known_asset: bool = False) -> dict[str, Any]:
    addresses = [Web3.to_checksum_address(address)]
    implementation = implementation_address(web3, address)
    if implementation and implementation.lower() != addresses[0].lower():
        addresses.append(implementation)

    bytecodes: list[str] = []
    for target in addresses:
        try:
            bytecodes.append(web3.eth.get_code(target).hex().lower().removeprefix("0x"))
        except Exception:
            continue
    if not bytecodes:
        return {"v6_admin_control_functions": [], "v6_has_owner_controls": False}

    found = sorted(
        {
            signature
            for selector, signature in ADMIN_FUNCTION_SIGNATURES.items()
            if any(selector in bytecode for bytecode in bytecodes)
        }
    )
    admin_found = [
        signature
        for signature in found
        if signature
        not in {
            "owner()",
            "renounceOwnership()",
        }
    ]
    if is_known_asset:
        admin_found = [
            signature
            for signature in admin_found
            if signature
            in {
                "mint(address,uint256)",
                "blacklist(address)",
                "setBlacklist(address,bool)",
            }
        ]

    has_operator_controls = any(
        key in signature.lower()
        for signature in admin_found
        for key in ("operator", "authorized", "authorization", "minter", "mint", "blacklist")
    )
    return {
        "v6_admin_control_functions": admin_found[:10],
        "v6_has_owner_controls": bool(admin_found),
        "v6_has_operator_controls": has_operator_controls,
        "v6_has_mint": any("mint(" in signature.lower() or "minter" in signature.lower() for signature in admin_found),
        "v6_has_blacklist": any("blacklist" in signature.lower() for signature in admin_found),
        "v6_is_proxy": bool(implementation),
        "v6_implementation_address": implementation,
    }


def cors(response):
    response.headers["Access-Control-Allow-Origin"] = "*"
    response.headers["Access-Control-Allow-Headers"] = "Content-Type"
    response.headers["Access-Control-Allow-Methods"] = "GET,POST,OPTIONS"
    return response


def compact_recent_flag(record: dict[str, Any]) -> str:
    output = str(record.get("output") or "").strip()
    if not output:
        return "analysis complete"
    if "Flags:" in output:
        output = output.split("Flags:", 1)[1].strip()
    if "CIA/V6 flags:" in output:
        output = output.split("CIA/V6 flags:", 1)[1].strip()
    output = output.replace("No major red flags.", "clean").replace("Low risk AVAX token.", "low risk")
    output = " ".join(output.split())
    return output[:96]


def recent_scan_item(record: dict[str, Any], created_at: Any) -> dict[str, Any]:
    if isinstance(record, str):
        try:
            record = json.loads(record)
        except json.JSONDecodeError:
            record = {}
    chain = str(record.get("chain") or "AVAX").lower()
    explorer_base = "https://snowtrace.io/address"
    address = record.get("contract_address") or ""
    return {
        "token_name": record.get("token_name") or "Unknown",
        "token_symbol": record.get("token_symbol") or "",
        "address": address,
        "chain": chain,
        "verdict": record.get("label") or "UNKNOWN",
        "risk_percent": record.get("risk_percent") or record.get("rugbuster_avax_score"),
        "flag": compact_recent_flag(record),
        "created_at": created_at.isoformat() if hasattr(created_at, "isoformat") else str(created_at or ""),
        "explorer_url": record.get("explorer_url") or f"{explorer_base}/{address}",
    }


def merge_recent_scans(*groups: list[dict[str, Any]], limit: int) -> list[dict[str, Any]]:
    merged: list[dict[str, Any]] = []
    seen: set[str] = set()
    for group in groups:
        for item in group:
            address = str(item.get("address") or "").lower()
            key = f"{address}:{item.get('created_at', '')}"
            if not address or key in seen:
                continue
            seen.add(key)
            merged.append(item)
    merged.sort(key=lambda item: item.get("created_at", ""), reverse=True)
    return merged[:limit]


@app.after_request
def add_cors_headers(response):
    return cors(response)


@app.route("/health", methods=["GET"])
def health():
    network = resolve_network()
    try:
        known_tokens = validate_known_token_metadata(get_web3())
    except Exception as exc:
        known_tokens = {"checked": False, "ok": False, "errors": [type(exc).__name__]}
    routescan = collector_routescan_api_health()
    return jsonify(
        {
            "ok": True,
            "degraded": not (known_tokens.get("ok") and routescan.get("ok")),
            "network": network,
            "label": NETWORKS[network]["label"],
            "dependencies": {
                "routescan": routescan,
                "known_tokens": known_tokens,
            },
        }
    )


@app.route("/", methods=["GET"])
def root():
    network = resolve_network()
    return jsonify(
        {
            "ok": True,
            "name": "RugBuster Apex",
            "version": "rugbuster-avalanche-api-v1",
            "network": network,
            "label": NETWORKS[network]["label"],
            "classifier_version": "weighted_v2",
            "score_endpoint": "/score?address=0x...",
            "scan_endpoint": "/api/scan",
        }
    )


def public_label_from_report(report: dict[str, Any]) -> str:
    explicit = str(report.get("label") or report.get("verdict") or "").upper()
    if explicit in {"GOOD", "WARN", "DANGER", "INSUFFICIENT_DATA", "UNKNOWN", "NOT_A_TOKEN"}:
        return explicit
    rug_status = str(report.get("rug_status") or "").upper()
    speculation_status = str(report.get("speculation_status") or "").upper()
    if rug_status == "HIGH" or speculation_status == "HIGH":
        return "DANGER"
    if rug_status in {"ELEVATED", "WARN"} or speculation_status in {"ELEVATED", "WARN"}:
        return "WARN"
    if rug_status == "LOW" and speculation_status == "LOW":
        return "GOOD"
    return "UNKNOWN"


def syndicate_verdict_from_report(report: dict[str, Any]) -> str:
    rug_status = str(report.get("rug_status") or "UNKNOWN").upper()
    speculation_status = str(report.get("speculation_status") or "UNKNOWN").upper()
    rug_score = report.get("rug_score")
    speculation_score = report.get("speculation_score")
    reasons = list(report.get("risk_flags") or report.get("rug_reasons") or report.get("speculation_reasons") or [])
    driver = str(reasons[0]) if reasons else "No dominant hard-risk driver surfaced in the available modules"
    return (
        f"RugBuster verdict: rug risk {rug_status}"
        f"{f' ({rug_score})' if rug_score is not None else ''}, "
        f"market liquidity risk {speculation_status}"
        f"{f' ({speculation_score})' if speculation_score is not None else ''}. "
        f"Main driver: {driver}."
    )[:240]


def compact_score_response(report: dict[str, Any], source: str) -> dict[str, Any]:
    address = report.get("address") or report.get("contract_address") or ""
    if report.get("risk_flags"):
        risk_flags = list(report.get("risk_flags") or [])
        priority_flags = [
            flag
            for flag in risk_flags
            if any(
                marker in str(flag).lower()
                for marker in ("admin control", "operator/authorization", "backdoor", "blacklist", "mint", "concentration")
            )
        ]
        risk_flags = priority_flags + [flag for flag in risk_flags if flag not in priority_flags]
    else:
        rug_reasons = list(report.get("rug_reasons") or [])
        priority_rug_reasons = [
            reason
            for reason in rug_reasons
            if "admin control" in reason.lower() or "operator/authorization" in reason.lower()
        ]
        other_rug_reasons = [reason for reason in rug_reasons if reason not in priority_rug_reasons]
        risk_flags = (priority_rug_reasons + other_rug_reasons)[:4] + list(report.get("speculation_reasons") or [])[:4]
    risk_percent = report.get("risk_percent") or report.get("rugbuster_avax_score") or report.get("rug_score")
    return {
        "ok": True,
        "address": Web3.to_checksum_address(address) if Web3.is_address(address) else address,
        "chain": "avalanche",
        "label": public_label_from_report(report),
        "rug_score": report.get("rug_score"),
        "rug_status": report.get("rug_status"),
        "speculation_score": report.get("speculation_score"),
        "speculation_status": report.get("speculation_status"),
        "risk_engine": report.get("risk_engine") or "rugbuster_avax_v1",
        "risk_percent": risk_percent,
        "rugbuster_avax_score": risk_percent,
        "rugbuster_avax_reasons": report.get("rugbuster_avax_reasons") or report.get("rug_reasons") or [],
        "token_name": report.get("token_name"),
        "token_symbol": report.get("symbol") or report.get("token_symbol"),
        "symbol": report.get("symbol") or report.get("token_symbol"),
        "risk_flags": risk_flags[:6],
        "classifier": "weighted_v2",
        "source": source,
        "confidence": report.get("confidence") or report.get("data_confidence"),
        "rug_risk": report.get("rug_risk"),
        "market_liquidity_risk": report.get("market_liquidity_risk"),
        "identity_risk": report.get("identity_risk"),
        "data_confidence": report.get("data_confidence"),
        "rug_reasons": report.get("rug_reasons") or [],
        "speculation_reasons": report.get("speculation_reasons") or [],
        "ai_verdict": report.get("ai_verdict") or report.get("syndicate_ai_verdict") or syndicate_verdict_from_report(report),
        "syndicate_ai_verdict": report.get("syndicate_ai_verdict") or syndicate_verdict_from_report(report),
        "has_liquidity_evidence": report.get("has_liquidity_evidence"),
        "liquidity_usd": report.get("liquidity_usd"),
        "fdv": report.get("fdv"),
        "volume24h": report.get("volume24h"),
        "price_change24h": report.get("price_change24h") or report.get("price_change_24h"),
        "price_change_24h": report.get("price_change_24h") or report.get("price_change24h"),
        "buys24h": report.get("buys24h"),
        "sells24h": report.get("sells24h"),
        "pair_address": report.get("pair_address"),
        "pair_url": report.get("pair_url"),
        "dex_id": report.get("dex_id"),
        "image_url": report.get("image_url"),
        "is_known_chain_asset": report.get("is_known_chain_asset", False),
        "known_asset_category": report.get("known_asset_category"),
        "admin_control_functions": report.get("admin_control_functions") or [],
        "deployer": report.get("deployer"),
        "deployer_balance_avax": report.get("deployer_balance_avax"),
        "token_age_days": report.get("token_age_days"),
        "holders_count": report.get("holders_count"),
        "v6_top5_concentration_pct": report.get("v6_top5_concentration_pct"),
        "v6_top1_concentration_pct": report.get("v6_top1_concentration_pct"),
        "v6_concentration_risk": report.get("v6_concentration_risk"),
        "cia": report.get("cia") or {},
        "v5": report.get("v5") or {},
        "v6": report.get("v6") or {},
        "creator_stats": report.get("creator_stats") or {},
    }


def lookup_cached_score(address: str) -> dict[str, Any] | None:
    cached = get_cached_report(address)
    if cached:
        return compact_score_response(cached, "memory_cache")
    if not DATABASE_URL or psycopg2 is None:
        return None
    try:
        with psycopg2.connect(DATABASE_URL) as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT full_record
                    FROM avax_scans
                    WHERE lower(contract_address) = lower(%s)
                    ORDER BY created_at DESC
                    LIMIT 1
                    """,
                    (address,),
                )
                row = cur.fetchone()
        if not row:
            return None
        record = row[0]
        if isinstance(record, str):
            record = json.loads(record)
        return compact_score_response(record, "postgres_cache")
    except Exception:
        return None


def should_refresh_cached_score(score: dict[str, Any]) -> bool:
    source = str(score.get("source") or "")
    if source != "postgres_cache":
        return False
    label = str(score.get("label") or score.get("verdict") or "").upper()
    risk_percent = score.get("risk_percent")
    if risk_percent is None:
        return True
    return label in {"UNKNOWN", "ALLOW"}


def remote_engine_configured() -> bool:
    return bool(USE_REMOTE_SCORING_ENGINE and SCORING_ENGINE_URL and SCORING_ENGINE_HMAC_SECRET)


def hmac_post_scoring_engine(payload: dict[str, Any]) -> dict[str, Any]:
    body = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
    timestamp = str(int(time.time()))
    signature = hmac.new(
        SCORING_ENGINE_HMAC_SECRET.encode("utf-8"),
        timestamp.encode("utf-8") + b"." + body,
        hashlib.sha256,
    ).hexdigest()
    response = requests.post(
        f"{SCORING_ENGINE_URL}/v1/score",
        data=body,
        headers={
            "Content-Type": "application/json",
            "X-RugBuster-Timestamp": timestamp,
            "X-RugBuster-Signature": signature,
        },
        timeout=SCORING_ENGINE_TIMEOUT_SECONDS,
    )
    response.raise_for_status()
    return response.json()


def market_fields_from_pair(pair_data: dict[str, Any] | None) -> dict[str, Any]:
    pair_data = pair_data or {}
    liquidity_raw = pair_data.get("liquidity", {}).get("usd")
    fdv_raw = pair_data.get("fdv") or pair_data.get("marketCap")
    volume_raw = pair_data.get("volume", {}).get("h24")
    price_change_raw = pair_data.get("priceChange", {}).get("h24")
    txns24h = pair_data.get("txns", {}).get("h24") or {}
    buys_raw = txns24h.get("buys")
    sells_raw = txns24h.get("sells")
    return {
        "has_liquidity_evidence": bool(pair_data.get("pairAddress")),
        "liquidity_usd": float(liquidity_raw) if liquidity_raw is not None else None,
        "fdv": float(fdv_raw) if fdv_raw is not None else None,
        "volume24h": float(volume_raw) if volume_raw is not None else None,
        "price_change_24h": float(price_change_raw) if price_change_raw is not None else None,
        "buys24h": int(buys_raw) if buys_raw is not None else None,
        "sells24h": int(sells_raw) if sells_raw is not None else None,
        "pair_address": pair_data.get("pairAddress"),
        "pair_url": pair_data.get("url"),
        "dex_id": str(pair_data.get("dexId") or "unknown").upper(),
        "image_url": pair_data.get("info", {}).get("imageUrl"),
    }


class NotTokenAddress(ValueError):
    pass


HOLDER_INTEL_API = os.getenv("HOLDER_INTEL_API", "https://api.gopluslabs.io/api/v1/token_security/43114").strip()
HOLDER_INTEL_TIMEOUT_SECONDS = float(os.getenv("HOLDER_INTEL_TIMEOUT_SECONDS", "8"))
HOLDER_INTEL_TTL_SECONDS = int(os.getenv("HOLDER_INTEL_TTL_SECONDS", "900"))
HOLDER_INTEL_CACHE: dict[str, dict[str, Any]] = {}


def fetch_holder_intel(address: str) -> dict[str, Any]:
    """Holder count and concentration for a token.

    Returns {} when the upstream has no data. That empty result is meaningful:
    callers must treat missing holder evidence as "unknown", never as "fine".

    Query one address at a time. Batched queries against this API silently drop
    members of the batch under throttling instead of erroring, which during QA
    made 94% of a sample look unlisted when the data existed.
    """
    key = address.lower()
    cached = HOLDER_INTEL_CACHE.get(key)
    if cached and time.time() - cached["ts"] < HOLDER_INTEL_TTL_SECONDS:
        return cached["intel"]

    try:
        response = requests.get(
            HOLDER_INTEL_API,
            params={"contract_addresses": key},
            timeout=HOLDER_INTEL_TIMEOUT_SECONDS,
        )
        response.raise_for_status()
        info = (response.json().get("result") or {}).get(key) or {}
    except Exception:
        return {}

    if not info:
        return {}

    try:
        holder_count = int(info.get("holder_count") or 0)
    except (TypeError, ValueError):
        holder_count = 0

    percents = []
    for holder in info.get("holders") or []:
        try:
            percents.append(float(holder.get("percent") or 0.0))
        except (TypeError, ValueError):
            continue
    percents.sort(reverse=True)

    intel: dict[str, Any] = {"holder_intel_source": "goplus"}
    if holder_count > 0:
        intel["holders_count"] = holder_count
    for source_key, target_key in (
        ("is_open_source", "goplus_is_open_source"),
        ("is_mintable", "goplus_is_mintable"),
        ("is_proxy", "goplus_is_proxy"),
        ("hidden_owner", "goplus_hidden_owner"),
        ("owner_change_balance", "goplus_owner_change_balance"),
    ):
        if source_key in info:
            intel[target_key] = str(info.get(source_key))
    if percents:
        # `percent` is already a fraction of total supply, so this is real
        # concentration rather than a share of whichever holders were sampled.
        top5_pct = round(sum(percents[:5]) * 100, 1)
        top1_pct = round(percents[0] * 100, 1)
        if 0 <= top5_pct <= 100 and 0 <= top1_pct <= 100:
            intel["v6_top5_concentration_pct"] = top5_pct
            intel["v6_top1_concentration_pct"] = top1_pct

    top5 = intel.get("v6_top5_concentration_pct")
    if top5 is not None:
        # Deliberately conservative: legitimate tokens routinely hold large
        # balances in treasury, staking or LP contracts. JOE sits near 69% and
        # JPYC near 79% across their top five, so anything below the extremes
        # is left unlabelled rather than scored as a risk.
        if top5 >= 95:
            intel["v6_concentration_risk"] = "CRITICAL"
        elif top5 >= 88:
            intel["v6_concentration_risk"] = "HIGH"
        else:
            intel["v6_concentration_risk"] = "LOW"

    HOLDER_INTEL_CACHE[key] = {"ts": time.time(), "intel": intel}
    return intel


def token_age_days_from_pair(pair_data: dict[str, Any] | None) -> float | None:
    """Days since the token's oldest observed market appeared, or None."""
    created_ms = (pair_data or {}).get("pairCreatedAt")
    if not created_ms:
        return None
    try:
        return max(0.0, (time.time() - float(created_ms) / 1000.0) / 86400.0)
    except (TypeError, ValueError):
        return None


def flatten_intel_for_scoring(cia: dict[str, Any], v6: dict[str, Any], creator_stats: dict[str, Any]) -> dict[str, Any]:
    """Flatten collector intel onto the token dict under the keys the scorer reads.

    The CIA/V5/V6 analyses were already being computed and shipped as separate
    payload keys, but the scorer reads flat `v6_*` / `cia_*` / `creator_*` fields
    off the token object, so none of these signals reached scoring. Without them
    the engine scores almost entirely on bytecode capabilities and liquidity
    depth, which cannot separate an established asset from a fresh rug -- both
    have mint() and both can look thin. That gap is what the hand-maintained
    canonical whitelist has been compensating for.

    Two fields are deliberately NOT mapped:

    * holder count. `cia.cluster.total_checked` is the number of wallets
      sampled, capped at 10 -- not the holder count. Mapping it would make every
      token, including WAVAX, trip the "very few holders" penalty.
    * holder concentration. The collector now computes top holders over total
      supply, but this still depends on Routescan holder-list access. Keep it
      unmapped until the dependency health check and regression set prove the
      source is consistently available.

    Both need fixing at the source (and a Routescan key with holder-list access)
    before they can safely drive a verdict.
    """
    backdoor = (v6 or {}).get("backdoor") or {}
    velocity = (v6 or {}).get("velocity") or {}
    funding = (cia or {}).get("funding") or {}
    entropy = (cia or {}).get("entropy") or {}
    wash = (cia or {}).get("wash") or {}
    cluster = (cia or {}).get("cluster") or {}

    return {
        "v6_has_backdoor": bool(backdoor.get("has_backdoor")),
        "v6_backdoor_risk_score": int(backdoor.get("backdoor_risk_score") or 0),
        "v6_rug_velocity_score": float(velocity.get("velocity_score") or 0.0),
        "v6_is_fast_rug": bool(velocity.get("is_fast_rug")),
        "cia_all_fresh_wallets": bool(funding.get("all_fresh")),
        "cia_bot_pattern": bool(entropy.get("is_bot_pattern")),
        "cia_wash_detected": bool(wash.get("wash_detected")),
        "cia_bot_farm": bool(cluster.get("is_bot_farm")),
        "creator_rug_rate": float((creator_stats or {}).get("rug_rate") or 0.0),
    }


def build_remote_scoring_payload(address: str) -> tuple[dict[str, Any], dict[str, Any]]:
    checksum = Web3.to_checksum_address(address)
    web3 = get_web3()
    validate_known_token_metadata(web3)
    onchain = get_onchain_metadata(web3, checksum)
    if not onchain.get("is_probable_erc20"):
        raise NotTokenAddress("Address does not expose a readable ERC-20 token interface")

    try:
        token_info = collector_get_token_info_avax(checksum)
    except Exception:
        token_info = {}
    if not isinstance(token_info, dict):
        token_info = {}
    token_info = {
        **token_info,
        "name": token_info.get("name") or onchain.get("name") or "Unknown",
        "symbol": token_info.get("symbol") or onchain.get("symbol") or "Unknown",
        "decimals": token_info.get("decimals") if token_info.get("decimals") is not None else onchain.get("decimals"),
        "total_supply": token_info.get("total_supply") if token_info.get("total_supply") is not None else onchain.get("total_supply"),
        "is_known_chain_asset": onchain.get("is_known_chain_asset", False),
        "known_asset_category": onchain.get("known_asset_category"),
        "v6_admin_control_functions": onchain.get("v6_admin_control_functions", []),
        "v6_has_owner_controls": onchain.get("v6_has_owner_controls", False),
        "v6_has_operator_controls": onchain.get("v6_has_operator_controls", False),
        "v6_has_mint": onchain.get("v6_has_mint", False),
        "v6_has_blacklist": onchain.get("v6_has_blacklist", False),
        "v6_is_proxy": onchain.get("v6_is_proxy", False),
        "is_contract": onchain.get("is_contract", False),
        "is_probable_erc20": onchain.get("is_probable_erc20", False),
        "contract_tx_count": onchain.get("contract_tx_count", 0),
    }

    try:
        pair_data = get_market_data(checksum)
        pair_source = "dexscreener"
    except Exception:
        pair_data = None
        pair_source = "none"
    token_info.update(market_fields_from_pair(pair_data))
    token_info.update(fetch_holder_intel(checksum))
    age_days = token_age_days_from_pair(pair_data)
    if age_days is not None:
        token_info["token_age_days"] = round(age_days, 1)

    txs = collector_get_contract_transactions(checksum, limit=5)
    deployer = ""
    deploy_timestamp = int(time.time())
    if txs:
        first_tx = txs[0]
        deployer = first_tx.get("from", "")
        deploy_timestamp = int(first_tx.get("timeStamp", deploy_timestamp))
        if "token_age_days" not in token_info and deploy_timestamp:
            token_info["token_age_days"] = round(max(0.0, (time.time() - deploy_timestamp) / 86400.0), 1)

    deployer_balance = collector_get_avax_balance(deployer) if deployer else 0.0
    creator_stats = collector_get_creator_stats(deployer)
    cia = collector_run_cia_analysis_avax(checksum, deployer, deploy_timestamp)
    tx_amounts_raw = cia.get("entropy", {}).get("dominant_amount", 0)
    tx_amounts = [tx_amounts_raw] if tx_amounts_raw else []
    try:
        holder_count = int(token_info.get("holders_count") or 0)
    except (TypeError, ValueError):
        holder_count = 0
    v5 = collector_run_v5_analysis_avax(
        checksum,
        deployer,
        deploy_timestamp,
        token_info.get("name", "Unknown"),
        token_info.get("symbol", ""),
        tx_amounts,
        holder_count,
        cia,
        creator_stats.get("rug_rate", 0.0),
    )
    v6 = collector_run_v6_analysis_avax(checksum, deployer, deploy_timestamp)
    token_info["deployer"] = deployer
    token_info["deployer_balance_avax"] = deployer_balance
    token_info.update(flatten_intel_for_scoring(cia, v6, creator_stats))

    payload = {
        "schema_version": "1",
        "chain": "AVAX",
        "token": token_info,
        "cia": cia,
        "v5": v5,
        "v6": v6,
        "creator_stats": creator_stats,
        "deployer_balance": deployer_balance,
    }
    context = {
        "pair_data": pair_data or {},
        "pair_source": pair_source,
        "deployer": deployer,
        "deployer_balance": deployer_balance,
        "token": token_info,
        "cia": cia,
        "v5": v5,
        "v6": v6,
        "creator_stats": creator_stats,
    }
    return payload, context


def report_from_remote_engine(address: str, result: dict[str, Any], context: dict[str, Any]) -> dict[str, Any]:
    token_info = context.get("token") or {}
    risk_flags = [str(item.get("detail")) for item in result.get("risk_factors", []) if item.get("detail")]
    rug_risk = result.get("rug_risk") or {}
    market_risk = result.get("market_liquidity_risk") or {}
    score = result.get("risk_score")
    return {
        "address": Web3.to_checksum_address(address),
        "token_name": token_info.get("name") or "Unknown",
        "symbol": token_info.get("symbol") or "Unknown",
        "label": str(result.get("verdict") or "INSUFFICIENT_DATA").upper(),
        "risk_engine": "rugbuster_private_scoring_engine",
        "engine_version": result.get("engine_version"),
        "risk_percent": score,
        "rugbuster_avax_score": score,
        "rugbuster_avax_reasons": risk_flags,
        "risk_flags": risk_flags,
        "rug_score": rug_risk.get("score"),
        "rug_status": rug_risk.get("status"),
        "rug_reasons": rug_risk.get("reasons") or [],
        "speculation_score": market_risk.get("score"),
        "speculation_status": market_risk.get("status"),
        "speculation_reasons": market_risk.get("reasons") or [],
        "rug_risk": rug_risk,
        "market_liquidity_risk": market_risk,
        "identity_risk": result.get("identity_risk"),
        "data_confidence": result.get("data_confidence") or result.get("confidence"),
        "confidence": result.get("confidence"),
        "has_liquidity_evidence": token_info.get("has_liquidity_evidence"),
        "liquidity_usd": token_info.get("liquidity_usd"),
        "fdv": token_info.get("fdv"),
        "volume24h": token_info.get("volume24h"),
        "buys24h": token_info.get("buys24h"),
        "sells24h": token_info.get("sells24h"),
        "pair_address": token_info.get("pair_address"),
        "pair_url": token_info.get("pair_url"),
        "dex_id": token_info.get("dex_id"),
        "image_url": token_info.get("image_url"),
        "is_known_chain_asset": token_info.get("is_known_chain_asset", False),
        "known_asset_category": token_info.get("known_asset_category"),
        "admin_control_functions": token_info.get("v6_admin_control_functions", []),
        "deployer": context.get("deployer"),
        "deployer_balance_avax": context.get("deployer_balance"),
        "token_age_days": token_info.get("token_age_days"),
        "holders_count": token_info.get("holders_count"),
        "v6_top5_concentration_pct": token_info.get("v6_top5_concentration_pct"),
        "v6_top1_concentration_pct": token_info.get("v6_top1_concentration_pct"),
        "v6_concentration_risk": token_info.get("v6_concentration_risk"),
        "cia": context.get("cia") or {},
        "v5": context.get("v5") or {},
        "v6": context.get("v6") or {},
        "creator_stats": context.get("creator_stats") or {},
        "syndicate_ai_verdict": syndicate_verdict_from_report(
            {
                "rug_status": rug_risk.get("status"),
                "rug_score": rug_risk.get("score"),
                "speculation_status": market_risk.get("status"),
                "speculation_score": market_risk.get("score"),
                "risk_flags": risk_flags,
                "rug_reasons": rug_risk.get("reasons") or [],
                "speculation_reasons": market_risk.get("reasons") or [],
            }
        ),
        "network": NETWORKS[resolve_network()]["label"],
        "source": "private_scoring_engine",
    }


def insufficient_data_report(address: str, reason: str) -> dict[str, Any]:
    checksum = Web3.to_checksum_address(address)
    return {
        "address": checksum,
        "token_name": "Unknown",
        "symbol": "Unknown",
        "label": "INSUFFICIENT_DATA",
        "risk_engine": "rugbuster_private_scoring_engine",
        "risk_percent": None,
        "rug_score": None,
        "rug_status": "INSUFFICIENT_DATA",
        "rug_reasons": [reason],
        "speculation_score": None,
        "speculation_status": "UNKNOWN",
        "speculation_reasons": ["Remote scoring engine unavailable; local fallback is not authoritative"],
        "risk_flags": [reason, "Remote scoring unavailable; no GOOD verdict emitted from fallback"],
        "data_confidence": {"level": "INSUFFICIENT_DATA", "missing_modules": ["private_scoring_engine"]},
        "network": NETWORKS[resolve_network()]["label"],
        "source": "remote_unavailable_insufficient_data",
    }


def not_a_token_report(address: str, reason: str) -> dict[str, Any]:
    checksum = Web3.to_checksum_address(address)
    return {
        "address": checksum,
        "token_name": "Not an ERC-20 token",
        "symbol": "NOT_TOKEN",
        "label": "NOT_A_TOKEN",
        "risk_engine": "rugbuster_private_scoring_engine",
        "risk_percent": None,
        "rug_score": None,
        "rug_status": "INSUFFICIENT_DATA",
        "rug_reasons": [reason],
        "speculation_score": None,
        "speculation_status": "UNKNOWN",
        "speculation_reasons": ["Address is not a readable ERC-20 token contract"],
        "risk_flags": [reason],
        "data_confidence": {
            "level": "INSUFFICIENT_DATA",
            "missing_modules": ["erc20_metadata"],
        },
        "network": NETWORKS[resolve_network()]["label"],
        "source": "not_a_token_guard",
    }


def score_with_private_engine(address: str) -> dict[str, Any]:
    if not remote_engine_configured():
        return insufficient_data_report(address, "Private scoring engine is not configured")
    payload, context = build_remote_scoring_payload(address)
    result = hmac_post_scoring_engine(payload)
    return report_from_remote_engine(address, result, context)


@app.route("/score", methods=["GET"])
def public_score():
    address = str(request.args.get("address") or "").strip()
    if not Web3.is_address(address):
        return jsonify({"ok": False, "error": "Invalid Avalanche token address"}), 400

    # Cache-first, as the Builder API advertises. A full score is ~6s of live
    # dexscreener, holder-intel and on-chain reads; without this every repeat
    # lookup paid that cost again and occasionally timed out when an upstream
    # was slow. A wallet or launchpad rendering token pages hits the same
    # popular tokens over and over, so most reads land inside the TTL window.
    # `fresh=1` forces a recompute for callers that need it.
    force_fresh = str(request.args.get("fresh") or "").strip().lower() in {"1", "true", "yes"}
    if not force_fresh:
        cached = get_cached_report(address)
        if cached is not None:
            return jsonify(compact_score_response(cached, cached.get("source") or "private_scoring_engine"))

    try:
        report = score_with_private_engine(address)
    except NotTokenAddress as exc:
        report = not_a_token_report(address, str(exc))
    except Exception as exc:
        report = insufficient_data_report(address, f"Private scoring engine failed: {type(exc).__name__}")
    try:
        put_cached_report(address, report)
    except Exception as exc:
        app.logger.warning("Score cache write failed for %s: %s", address, type(exc).__name__)
    try:
        payload = compact_score_response(report, report.get("source") or "private_scoring_engine")
    except Exception as exc:
        app.logger.exception("Score response formatting failed for %s", address)
        fallback = insufficient_data_report(address, f"Score response formatting failed: {type(exc).__name__}")
        payload = {
            "ok": True,
            "address": Web3.to_checksum_address(address),
            "chain": "avalanche",
            "label": "INSUFFICIENT_DATA",
            "rug_status": "INSUFFICIENT_DATA",
            "risk_engine": fallback.get("risk_engine"),
            "risk_percent": None,
            "rugbuster_avax_score": None,
            "risk_flags": fallback.get("risk_flags", []),
            "source": fallback.get("source"),
            "data_confidence": fallback.get("data_confidence"),
        }
    return jsonify(payload)


@app.route("/api/recent-scans", methods=["GET", "POST", "OPTIONS"])
def api_recent_scans():
    if request.method == "OPTIONS":
        return jsonify({"ok": True})

    limit = max(1, min(int(request.args.get("limit", RECENT_SCAN_LIMIT)), 25))
    if request.method == "POST":
        if RECENT_SCAN_INGEST_TOKEN:
            token = request.headers.get("X-RugBuster-Feed-Token", "")
            if token != RECENT_SCAN_INGEST_TOKEN:
                return jsonify({"ok": False, "error": "Unauthorized"}), 401
        payload = request.get_json(silent=True) or {}
        record = payload.get("record") if isinstance(payload.get("record"), dict) else payload
        item = recent_scan_item(record, payload.get("created_at") or time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()))
        RECENT_SCANS.insert(0, item)
        del RECENT_SCANS[100:]
        return jsonify({"ok": True, "item": item})

    db_items: list[dict[str, Any]] = []
    if not DATABASE_URL or psycopg2 is None:
        return jsonify({"ok": True, "items": merge_recent_scans(RECENT_SCANS, limit=limit)})

    try:
        with psycopg2.connect(DATABASE_URL) as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT full_record, created_at
                    FROM avax_scans
                    ORDER BY created_at DESC
                    LIMIT %s
                    """,
                    (limit,),
                )
                db_items = [recent_scan_item(record, created_at) for record, created_at in cur.fetchall()]
        return jsonify({"ok": True, "items": merge_recent_scans(RECENT_SCANS, db_items, limit=limit)})
    except Exception as exc:
        return jsonify({"ok": True, "warning": str(exc), "items": merge_recent_scans(RECENT_SCANS, limit=limit)})


@app.route("/api/scan", methods=["POST", "OPTIONS"])
def api_scan():
    if request.method == "OPTIONS":
        return cors(app.response_class(status=204))

    payload = request.get_json(silent=True) or {}
    address = str(payload.get("address") or "").strip()
    publish = bool(payload.get("publish")) or env_enabled("PUBLISH_TO_REGISTRY")
    publish_modules = bool(payload.get("publish_modules")) or env_enabled("PUBLISH_MODULES_TO_REGISTRY")
    notify = bool(payload.get("notify")) or env_enabled("TELEGRAM_ALERTS")
    use_cached = bool(payload.get("use_cached"))

    if not Web3.is_address(address):
        return jsonify({"ok": False, "error": "Invalid Avalanche token address"}), 400

    report = get_cached_report(address) if use_cached else None
    if report is None:
        try:
            report = score_with_private_engine(address)
        except Exception as exc:
            return jsonify({"ok": False, "error": str(exc)}), 400
        if not report.get("ai_verdict"):
            try:
                report["ai_verdict"] = fetch_deepseek_verdict(report)
                report["ai_model"] = DEEPSEEK_MODEL if report.get("ai_verdict") else None
            except Exception as exc:
                report["ai_verdict"] = None
                report["ai_error"] = str(exc)
        put_cached_report(address, report)

    publish_result = None
    if publish:
        try:
            publish_result = publish_report(report)
        except Exception as exc:
            return jsonify({"ok": False, "error": f"Registry publish failed: {exc}", "report": report}), 400

    module_publish_result = None
    if publish_modules:
        try:
            module_publish_result = publish_report_modules(report)
        except Exception as exc:
            return jsonify({"ok": False, "error": f"Module registry publish failed: {exc}", "report": report}), 400

    telegram_result = None
    if notify:
        try:
            telegram_result = notify_report(report, publish_result, module_publish_result)
        except Exception as exc:
            return jsonify({"ok": False, "error": f"Telegram alert failed: {exc}", "report": report}), 400

    return jsonify(
        {
            "ok": True,
            "report": report,
            "published": publish_result,
            "module_published": module_publish_result,
            "telegram": telegram_result,
        }
    )


@app.route("/api/portfolio", methods=["POST", "OPTIONS"])
def api_portfolio():
    if request.method == "OPTIONS":
        return cors(app.response_class(status=204))

    payload = request.get_json(silent=True) or {}
    address = str(payload.get("address") or "").strip()

    if not Web3.is_address(address):
        return jsonify({"ok": False, "error": "Invalid Avalanche wallet address"}), 400

    try:
        tokens = fetch_portfolio_tokens(address)
    except Exception as exc:
        return jsonify({"ok": False, "error": str(exc)}), 400

    entries = build_portfolio_reports(address, tokens)
    suspicious = any(
        entry["report"]["rug_status"] in {"HIGH", "ELEVATED"}
        or entry["report"]["speculation_status"] == "HIGH"
        for entry in entries
    )
    return jsonify({"ok": True, "wallet": Web3.to_checksum_address(address), "entries": entries, "suspicious": suspicious})


@app.route("/health/telegram", methods=["GET"])
def telegram_health():
    ready = bool(os.getenv("TELEGRAM_BOT_TOKEN")) and bool(os.getenv("TELEGRAM_CHAT_ID"))
    return jsonify({"ok": True, "telegram_ready": ready})


def require_env(name: str) -> str:
    value = os.getenv(name)
    if not value:
        raise RuntimeError(f"Missing required environment variable: {name}")
    return value


def get_optional_env(*names: str) -> str | None:
    for name in names:
        value = os.getenv(name)
        if value:
            return value
    return None


def deepseek_enabled() -> bool:
    return bool(DEEPSEEK_API_KEY)


def build_ai_scan_context(report: dict[str, Any]) -> dict[str, Any]:
    return {
        "token": report.get("address"),
        "name": report.get("token_name"),
        "symbol": report.get("symbol"),
        "rug_score": report.get("rug_score"),
        "rug_status": report.get("rug_status"),
        "rug_reasons": report.get("rug_reasons", [])[:5],
        "speculation_score": report.get("speculation_score"),
        "speculation_status": report.get("speculation_status"),
        "speculation_reasons": report.get("speculation_reasons", [])[:5],
        "liquidity_usd": report.get("liquidity_usd"),
        "fdv": report.get("fdv"),
        "volume24h": report.get("volume24h"),
        "price_change24h": report.get("price_change24h"),
        "buys24h": report.get("buys24h"),
        "sells24h": report.get("sells24h"),
        "dex_id": report.get("dex_id"),
        "source": report.get("source"),
    }


def fetch_deepseek_verdict(report: dict[str, Any]) -> str | None:
    if not deepseek_enabled():
        return None
    context = build_ai_scan_context(report)
    prompt = (
        "Analyze this Avalanche token security scan. "
        "Return one concise RugBuster verdict in max 28 words. "
        "Mention the main risk driver if any. Do not give financial advice.\n\n"
        f"{json.dumps(context, ensure_ascii=False, sort_keys=True)}"
    )
    response = requests.post(
        DEEPSEEK_API_URL,
        headers={
            "Authorization": f"Bearer {DEEPSEEK_API_KEY}",
            "Content-Type": "application/json",
        },
        json={
            "model": DEEPSEEK_MODEL,
            "messages": [
                {"role": "system", "content": "You are RugBuster's concise Avalanche token risk analyst."},
                {"role": "user", "content": prompt},
            ],
            "temperature": 0.2,
            "max_tokens": 90,
        },
        timeout=DEEPSEEK_TIMEOUT_SECONDS,
    )
    response.raise_for_status()
    data = response.json()
    verdict = (
        data.get("choices", [{}])[0]
        .get("message", {})
        .get("content", "")
        .strip()
    )
    return " ".join(verdict.split())[:240] if verdict else None


def env_enabled(name: str) -> bool:
    return (os.getenv(name) or "").strip().lower() in {"1", "true", "yes", "on"}


def call_optional(contract, fn_name: str) -> Any | None:
    try:
        return getattr(contract.functions, fn_name)().call()
    except Exception:
        return None


def get_web3() -> Web3:
    network = resolve_network()
    rpc_url = resolve_rpc(network)
    web3 = Web3(Web3.HTTPProvider(rpc_url, request_kwargs={"timeout": 30}))
    if not web3.is_connected():
        raise RuntimeError(f"Could not connect to {NETWORKS[network]['label']} RPC")
    return web3


def fetch_portfolio_tokens(address: str) -> list[dict[str, Any]]:
    api_key = get_optional_env("GLACIER_API_KEY", "AVACLOUD_API_KEY")
    if not api_key:
        raise RuntimeError("Portfolio scan requires GLACIER_API_KEY (or AVACLOUD_API_KEY) on the backend")

    items: list[dict[str, Any]] = []
    page_token: str | None = None
    checksum = Web3.to_checksum_address(address)
    while True:
        params = {"pageSize": 100, "filterSpamTokens": "true"}
        if page_token:
            params["pageToken"] = page_token
        response = requests.get(
            f"{GLACIER_API}/v1/chains/43114/addresses/{checksum}/balances:listErc20",
            headers={"x-glacier-api-key": api_key},
            params=params,
            timeout=30,
        )
        response.raise_for_status()
        data = response.json()
        page_items = (
            data.get("erc20TokenBalances")
            or data.get("balances")
            or data.get("items")
            or []
        )
        items.extend(page_items)
        page_token = data.get("nextPageToken") or data.get("next_page_token")
        if not page_token:
            break
    return items


def get_onchain_metadata(web3: Web3, address: str) -> dict[str, Any]:
    checksum = Web3.to_checksum_address(address)
    known = KNOWN_TOKEN_METADATA.get(checksum.lower(), {})
    try:
        code = web3.eth.get_code(checksum)
    except Exception:
        code = b""
    token = web3.eth.contract(address=Web3.to_checksum_address(address), abi=ERC20_ABI)
    name = call_optional(token, "name")
    symbol = call_optional(token, "symbol")
    decimals = call_optional(token, "decimals")
    total_supply = call_optional(token, "totalSupply")
    admin_controls = detect_admin_controls(web3, checksum, is_known_asset=bool(known))
    has_readable_metadata = any(
        value is not None and str(value).strip() != ""
        for value in (name, symbol, decimals, total_supply)
    )
    is_probable_erc20 = bool(known) or (bool(code) and decimals is not None and total_supply is not None and has_readable_metadata)
    return {
        "name": name or known.get("name") or "Unknown",
        "symbol": symbol or known.get("symbol") or "Unknown",
        "decimals": decimals if decimals is not None else known.get("decimals"),
        "total_supply": total_supply,
        "is_contract": bool(code),
        "is_probable_erc20": is_probable_erc20,
        "is_known_chain_asset": bool(known),
        "known_asset_category": known.get("category"),
        "metadata_source": "erc20_call" if name or symbol or decimals is not None or total_supply is not None else "known_token_fallback" if known else "unavailable",
        **admin_controls,
    }


def build_report_from_metadata(address: str, metadata: dict[str, Any], pair_data: dict[str, Any] | None, source: str) -> dict[str, Any]:
    pair_data = pair_data or {}
    token_checksum = Web3.to_checksum_address(address)
    market_token = token_side_from_pair(pair_data, token_checksum)
    if market_token:
        if metadata.get("name") in (None, "", "Unknown") and market_token.get("name"):
            metadata["name"] = market_token.get("name")
        if metadata.get("symbol") in (None, "", "Unknown") and market_token.get("symbol"):
            metadata["symbol"] = market_token.get("symbol")
    liquidity_raw = pair_data.get("liquidity", {}).get("usd")
    fdv_raw = pair_data.get("fdv") or pair_data.get("marketCap")
    volume_raw = pair_data.get("volume", {}).get("h24")
    price_change_raw = pair_data.get("priceChange", {}).get("h24")
    liquidity_usd = float(liquidity_raw) if liquidity_raw is not None else None
    fdv = float(fdv_raw) if fdv_raw is not None else None
    volume24h = float(volume_raw) if volume_raw is not None else None
    price_change24h = float(price_change_raw) if price_change_raw is not None else None
    txns24h = pair_data.get("txns", {}).get("h24") or {}
    buys_raw = txns24h.get("buys")
    sells_raw = txns24h.get("sells")
    buys24h = int(buys_raw) if buys_raw is not None else None
    sells24h = int(sells_raw) if sells_raw is not None else None
    socials = pair_data.get("info", {}).get("socials") or []
    websites = pair_data.get("info", {}).get("websites") or []

    scoring_input = {
        "token": Web3.to_checksum_address(address),
        "name": metadata["name"],
        "symbol": metadata["symbol"],
        "decimals": metadata["decimals"],
        "total_supply": metadata["total_supply"],
        "deployer": None,
        "has_liquidity_evidence": bool(pair_data.get("pairAddress")),
        "liquidity_usd": liquidity_usd,
        "fdv": fdv,
        "volume24h": volume24h,
        "price_change_24h": price_change24h,
        "buys24h": buys24h,
        "sells24h": sells24h,
        "pair_address": pair_data.get("pairAddress"),
        "pair_url": pair_data.get("url"),
        "dex_id": str(pair_data.get("dexId") or "unknown").upper(),
        "social_count": len(socials),
        "website_count": len(websites),
        "image_url": pair_data.get("info", {}).get("imageUrl"),
        "contract_tx_count": metadata.get("contract_tx_count", 0),
        "is_known_chain_asset": metadata.get("is_known_chain_asset", False),
        "known_asset_category": metadata.get("known_asset_category"),
        "v6_admin_control_functions": metadata.get("v6_admin_control_functions", []),
        "v6_has_owner_controls": metadata.get("v6_has_owner_controls", False),
        "v6_has_operator_controls": metadata.get("v6_has_operator_controls", False),
        "v6_has_mint": metadata.get("v6_has_mint", False),
        "v6_has_blacklist": metadata.get("v6_has_blacklist", False),
    }

    scores = score_token(scoring_input)
    return {
        "address": scoring_input["token"],
        "token_name": scoring_input["name"],
        "symbol": scoring_input["symbol"],
        "risk_engine": "rugbuster_avax_v1",
        "risk_percent": scores.rug.score,
        "rugbuster_avax_score": scores.rug.score,
        "rugbuster_avax_reasons": list(scores.rug.reasons),
        "rug_score": scores.rug.score,
        "rug_status": scores.rug.status,
        "rug_reasons": list(scores.rug.reasons),
        "speculation_score": scores.speculation.score,
        "speculation_status": scores.speculation.status,
        "speculation_reasons": list(scores.speculation.reasons),
        "has_liquidity_evidence": scoring_input["has_liquidity_evidence"],
        "liquidity_usd": liquidity_usd,
        "fdv": fdv,
        "volume24h": volume24h,
        "price_change24h": price_change24h,
        "buys24h": buys24h,
        "sells24h": sells24h,
        "pair_address": scoring_input["pair_address"],
        "pair_url": scoring_input["pair_url"],
        "dex_id": scoring_input["dex_id"],
        "image_url": scoring_input["image_url"],
        "metadata_source": metadata.get("metadata_source"),
        "is_known_chain_asset": metadata.get("is_known_chain_asset", False),
        "known_asset_category": metadata.get("known_asset_category"),
        "admin_control_functions": metadata.get("v6_admin_control_functions", []),
        "network": NETWORKS[resolve_network()]["label"],
        "source": source,
    }


def fetch_dexscreener_pairs(address: str) -> list[dict[str, Any]]:
    response = requests.get(f"{DEXSCREENER_API}/{address}", timeout=20)
    response.raise_for_status()
    data = response.json()
    return [pair for pair in (data.get("pairs") or []) if (pair.get("chainId") or "").lower() == "avalanche"]

 
def token_side_from_pair(pair: dict[str, Any], address: str) -> dict[str, Any] | None:
    if not pair:
        return None
    target = Web3.to_checksum_address(address).lower()
    for side in ("baseToken", "quoteToken"):
        token = pair.get(side) or {}
        token_address = token.get("address")
        if token_address and Web3.to_checksum_address(token_address).lower() == target:
            return token
    return None


def pair_contains_token(pair: dict[str, Any], address: str) -> bool:
    return token_side_from_pair(pair, address) is not None


def pair_base_is_token(pair: dict[str, Any], address: str) -> bool:
    token = (pair.get("baseToken") or {}).get("address")
    return bool(token and Web3.to_checksum_address(token).lower() == Web3.to_checksum_address(address).lower())


def get_market_data(address: str) -> dict[str, Any]:
    avalanche_pairs = fetch_dexscreener_pairs(address)
    avalanche_pairs = [pair for pair in avalanche_pairs if pair_contains_token(pair, address)]
    if not avalanche_pairs:
        raise RuntimeError("Token not found on Avalanche liquidity venues")

    base_token_pairs = [pair for pair in avalanche_pairs if pair_base_is_token(pair, address)]
    candidate_pairs = base_token_pairs or avalanche_pairs

    return sorted(
        candidate_pairs,
        key=lambda pair: float(pair.get("liquidity", {}).get("usd") or 0),
        reverse=True,
    )[0]


def quote_price_usd(quote_address: str) -> float | None:
    checksum = Web3.to_checksum_address(quote_address)
    if checksum in STABLE_QUOTES:
        return STABLE_QUOTES[checksum]

    try:
        pairs = fetch_dexscreener_pairs(checksum)
    except Exception:
        return None

    if not pairs:
        return None

    best_pair = sorted(
        pairs,
        key=lambda pair: float(pair.get("liquidity", {}).get("usd") or 0),
        reverse=True,
    )[0]
    price = best_pair.get("priceUsd")
    return float(price) if price is not None else None


def load_factory_map() -> dict[str, str]:
    network = resolve_network()
    defaults = FUJI_FACTORIES if network == "fuji" else MAINNET_FACTORIES
    return {name: Web3.to_checksum_address(address) for name, address in defaults.items()}


def get_token_decimals(web3: Web3, address: str) -> int:
    token = web3.eth.contract(address=Web3.to_checksum_address(address), abi=ERC20_ABI)
    decimals = call_optional(token, "decimals")
    return int(decimals) if decimals is not None else 18


def get_pair_from_factories(web3: Web3, token_address: str, total_supply: int | None) -> dict[str, Any] | None:
    token_checksum = Web3.to_checksum_address(token_address)
    factories = load_factory_map()

    best_result: dict[str, Any] | None = None

    for dex_name, factory_address in factories.items():
        factory = web3.eth.contract(address=factory_address, abi=FACTORY_ABI)
        for quote in COMMON_QUOTES:
            if token_checksum == Web3.to_checksum_address(quote):
                continue

            try:
                pair_address = factory.functions.getPair(token_checksum, Web3.to_checksum_address(quote)).call()
            except Exception:
                continue

            if not pair_address or int(pair_address, 16) == 0:
                continue

            pair = web3.eth.contract(address=Web3.to_checksum_address(pair_address), abi=PAIR_ABI)
            try:
                token0 = Web3.to_checksum_address(pair.functions.token0().call())
                token1 = Web3.to_checksum_address(pair.functions.token1().call())
                reserve0, reserve1, _ = pair.functions.getReserves().call()
            except Exception:
                continue

            quote_checksum = Web3.to_checksum_address(quote)
            quote_decimals = get_token_decimals(web3, quote_checksum)
            token_decimals = get_token_decimals(web3, token_checksum)

            if token0 == quote_checksum:
                quote_reserve_raw = reserve0
                token_reserve_raw = reserve1
            elif token1 == quote_checksum:
                quote_reserve_raw = reserve1
                token_reserve_raw = reserve0
            else:
                continue

            if quote_reserve_raw <= 0 or token_reserve_raw <= 0:
                continue

            quote_reserve = float(quote_reserve_raw) / (10 ** quote_decimals)
            token_reserve = float(token_reserve_raw) / (10 ** token_decimals)
            if token_reserve <= 0:
                continue

            quote_usd = quote_price_usd(quote_checksum)
            liquidity_usd = None if quote_usd is None else quote_reserve * quote_usd * 2
            token_price_usd = None if quote_usd is None else (quote_reserve / token_reserve) * quote_usd
            fdv = None
            if token_price_usd is not None and total_supply:
                fdv = (float(total_supply) / (10 ** token_decimals)) * token_price_usd

            candidate = {
                "dexId": dex_name,
                "pairAddress": Web3.to_checksum_address(pair_address),
                "liquidity": {"usd": liquidity_usd},
                "fdv": fdv,
                "marketCap": fdv,
                "volume": {"h24": None},
                "priceChange": {"h24": None},
                "txns": {"h24": {"buys": None, "sells": None}},
                "baseToken": {"address": token_checksum},
                "quoteToken": {"address": quote_checksum},
                "url": None,
                "info": {"socials": None, "websites": None, "imageUrl": None},
                "pairCreatedAt": None,
                "_source": "onchain_pair_lookup",
            }

            if best_result is None or (candidate["liquidity"]["usd"] or 0) > (best_result["liquidity"]["usd"] or 0):
                best_result = candidate

    return best_result


def scan_token(address: str) -> dict[str, Any]:
    web3 = get_web3()
    onchain = get_onchain_metadata(web3, address)
    pair_source = "none"
    try:
        best_pair = get_market_data(address)
        pair_source = "dexscreener"
    except Exception:
        best_pair = get_pair_from_factories(web3, address, onchain.get("total_supply"))
        if best_pair:
            pair_source = "onchain_pair_lookup"
    onchain["contract_tx_count"] = web3.eth.get_transaction_count(Web3.to_checksum_address(address))
    return build_report_from_metadata(address, onchain, best_pair, pair_source)


def parse_glacier_balance(item: dict[str, Any]) -> dict[str, Any] | None:
    token_address = (
        item.get("address")
        or item.get("tokenAddress")
        or (item.get("token") or {}).get("address")
    )
    if not token_address or not Web3.is_address(token_address):
        return None
    decimals = item.get("decimals") or (item.get("token") or {}).get("decimals") or 18
    symbol = item.get("symbol") or (item.get("token") or {}).get("symbol") or "UNKNOWN"
    name = item.get("name") or (item.get("token") or {}).get("name") or symbol
    logo = item.get("logoUri") or item.get("logo") or (item.get("token") or {}).get("logoUri")
    raw_value = item.get("value") or item.get("balanceValue") or item.get("valueUsd")
    if isinstance(raw_value, dict):
        value_usd = raw_value.get("value")
    else:
        value_usd = raw_value
    balance_raw = item.get("balance") or item.get("amount") or item.get("balanceRaw")
    try:
        balance_raw_int = int(str(balance_raw))
    except Exception:
        balance_raw_int = 0
    balance_display = balance_raw_int / (10 ** int(decimals))
    return {
        "address": Web3.to_checksum_address(token_address),
        "symbol": symbol,
        "name": name,
        "decimals": int(decimals),
        "balance_raw": balance_raw_int,
        "balance": balance_display,
        "value_usd": float(value_usd) if value_usd not in (None, "") else None,
        "image_url": logo,
    }


def build_portfolio_reports(wallet_address: str, raw_tokens: list[dict[str, Any]]) -> list[dict[str, Any]]:
    parsed = [entry for entry in (parse_glacier_balance(item) for item in raw_tokens) if entry and entry["balance_raw"] > 0]
    parsed.sort(key=lambda item: item["value_usd"] or 0, reverse=True)
    web3 = get_web3()

    def score_entry(entry: dict[str, Any]) -> dict[str, Any]:
        cached = get_cached_report(entry["address"])
        if cached:
            report = dict(cached)
        else:
            try:
                report = scan_token(entry["address"])
            except Exception:
                onchain = get_onchain_metadata(web3, entry["address"])
                onchain["name"] = entry["name"] or onchain["name"]
                onchain["symbol"] = entry["symbol"] or onchain["symbol"]
                onchain["contract_tx_count"] = web3.eth.get_transaction_count(entry["address"])
                report = build_report_from_metadata(entry["address"], onchain, None, "portfolio_onchain_only")
            put_cached_report(entry["address"], report)
        if entry.get("image_url") and not report.get("image_url"):
            report["image_url"] = entry["image_url"]
        if entry.get("name"):
            report["token_name"] = entry["name"]
        if entry.get("symbol"):
            report["symbol"] = entry["symbol"]
        return {"token": entry, "report": report}

    results: list[dict[str, Any]] = []
    with ThreadPoolExecutor(max_workers=PORTFOLIO_SCAN_WORKERS) as executor:
        futures = {executor.submit(score_entry, entry): entry for entry in parsed}
        for future in as_completed(futures):
            results.append(future.result())

    results.sort(key=lambda item: item["token"]["value_usd"] or 0, reverse=True)
    return results


def publish_report(report: dict[str, Any]) -> dict[str, Any]:
    web3 = get_web3()
    private_key = require_env("PRIVATE_KEY")
    registry_address = require_env("REGISTRY_ADDRESS")
    payload = {"report": report}
    rug_score = report.get("rug_score")
    if rug_score is None:
        raise RuntimeError("Cannot publish a registry score without a rug score")
    return publish_score(
        web3=web3,
        private_key=private_key,
        registry_address=registry_address,
        token=report["address"],
        score=rug_score,
        payload=payload,
    )


def build_report_modules(report: dict[str, Any]) -> list[dict[str, Any]]:
    token = report["address"]
    timestamp = int(time.time())
    base = {
        "token": token,
        "symbol": report.get("symbol"),
        "ts": timestamp,
        "source": report.get("source"),
        "network": report.get("network"),
    }
    modules = [
        {
            "module": "token_metadata",
            "score": 100 if report.get("token_name") not in (None, "Unknown") else 40,
            "payload": {
                **base,
                "name": report.get("token_name"),
                "decimals_known": report.get("symbol") not in (None, "Unknown"),
            },
        },
        {
            "module": "liquidity",
            "score": liquidity_module_score(report.get("liquidity_usd")),
            "payload": {
                **base,
                "liquidity_usd": report.get("liquidity_usd"),
                "has_liquidity_evidence": report.get("has_liquidity_evidence"),
                "pair_address": report.get("pair_address"),
                "dex_id": report.get("dex_id"),
            },
        },
        {
            "module": "market_activity",
            "score": market_activity_module_score(report),
            "payload": {
                **base,
                "volume24h": report.get("volume24h"),
                "buys24h": report.get("buys24h"),
                "sells24h": report.get("sells24h"),
                "price_change24h": report.get("price_change24h"),
            },
        },
        {
            "module": "rug_risk",
            "score": int(report.get("rug_score") or 0),
            "payload": {
                **base,
                "status": report.get("rug_status"),
                "reasons": list(report.get("rug_reasons") or [])[:6],
            },
        },
        {
            "module": "speculation_risk",
            "score": int(report.get("speculation_score") or 0),
            "payload": {
                **base,
                "status": report.get("speculation_status"),
                "reasons": list(report.get("speculation_reasons") or [])[:6],
                "fdv": report.get("fdv"),
            },
        },
        {
            "module": "final_verdict",
            "score": int(report.get("rug_score") or 0),
            "payload": {
                **base,
                "rug_score": report.get("rug_score"),
                "rug_status": report.get("rug_status"),
                "speculation_score": report.get("speculation_score"),
                "speculation_status": report.get("speculation_status"),
                "verdict": verdict_text(report),
            },
        },
    ]
    return modules


def liquidity_module_score(liquidity_usd: float | None) -> int:
    if liquidity_usd is None:
        return 35
    if liquidity_usd < 5_000:
        return 20
    if liquidity_usd < 25_000:
        return 45
    if liquidity_usd < 100_000:
        return 65
    if liquidity_usd < 500_000:
        return 80
    return 95


def market_activity_module_score(report: dict[str, Any]) -> int:
    buys = report.get("buys24h")
    sells = report.get("sells24h")
    volume = report.get("volume24h")
    if buys is None and sells is None and volume is None:
        return 40
    tx_count = int(buys or 0) + int(sells or 0)
    if tx_count == 0 and not volume:
        return 25
    if tx_count < 10:
        return 45
    if tx_count < 50:
        return 65
    return 80


def publish_report_modules(report: dict[str, Any]) -> dict[str, Any]:
    web3 = get_web3()
    private_key = require_env("PRIVATE_KEY")
    registry_address = require_env("REGISTRY_ADDRESS")
    module_receipts = publish_score_modules(
        web3=web3,
        private_key=private_key,
        registry_address=registry_address,
        token=report["address"],
        modules=build_report_modules(report),
    )
    return {
        "count": len(module_receipts),
        "transactions": module_receipts,
        "total_gas_used": sum(int(receipt.get("gas_used") or 0) for receipt in module_receipts),
    }


def notify_report(
    report: dict[str, Any],
    publish_result: dict[str, Any] | None,
    module_publish_result: dict[str, Any] | None = None,
) -> dict[str, Any]:
    bot_token = require_env("TELEGRAM_BOT_TOKEN")
    chat_id = require_env("TELEGRAM_CHAT_ID")
    lines = [
        "🛡️ <b>RugBuster Apex Alert</b>",
        f"💎 <b>Token:</b> {escape_html(report['token_name'])} ({escape_html(report['symbol'])})",
        f"📉 <b>Rug Risk:</b> {format_score(report['rug_score'])} ({escape_html(report['rug_status'])})",
        f"📊 <b>Speculation:</b> {format_score(report['speculation_score'])} ({escape_html(report['speculation_status'])})",
        f"💰 <b>Liq:</b> {escape_html(format_liquidity(report['liquidity_usd']))}",
        f"✅ <b>Verdict:</b> {escape_html(verdict_text(report))}",
    ]
    if publish_result:
        lines.append(f"⛓️ <b>Registry TX:</b> <code>{publish_result['tx_hash']}</code>")
    if module_publish_result:
        lines.append(f"⛓️ <b>Module TXs:</b> <code>{module_publish_result['count']}</code>")
    if report.get("pair_url"):
        lines.append(f"🔗 <a href=\"{report['pair_url']}\">Pair URL</a>")

    high_signal_reasons = list(report.get("rug_reasons") or [])[:3] + list(report.get("speculation_reasons") or [])[:3]
    clean_reasons = [reason for reason in high_signal_reasons if reason]
    if clean_reasons:
        lines.append("")
        lines.append("<b>Signals:</b>")
        lines.extend([f"• {escape_html(reason)}" for reason in clean_reasons[:6]])

    result = send_telegram_alert(
        bot_token=bot_token,
        chat_id=chat_id,
        message="\n".join(lines),
        parse_mode="HTML",
    )
    return {"ok": True, "response": result.get("ok", False)}


def format_liquidity(value: float | None) -> str:
    if value is None:
        return "UNKNOWN"
    return f"${value:,.0f}"


def format_score(value: int | None) -> str:
    if value is None:
        return "UNKNOWN"
    return str(value)


def verdict_text(report: dict[str, Any]) -> str:
    rug_status = report.get("rug_status") or "UNKNOWN"
    speculation_status = report.get("speculation_status") or "UNKNOWN"

    if rug_status == "HIGH":
        return "High rug risk. Hard on-chain facts look bad."
    if speculation_status == "HIGH":
        return "High speculation. Market depth looks dangerous and exit liquidity may be too thin."
    if speculation_status == "UNKNOWN":
        return "Rug score available, but no live liquidity evidence yet."
    if rug_status == "LOW" and speculation_status == "LOW":
        return "No hard rug signals detected and market depth currently looks healthy."
    if rug_status == "LOW" and speculation_status == "ELEVATED":
        return "Low rug risk, but shallow liquidity makes this a speculative position."
    return "Mixed signals. Manual review recommended."


def escape_html(value: Any) -> str:
    text = str(value)
    return (
        text.replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
    )


if __name__ == "__main__":
    host = os.getenv("RUGBUSTER_API_HOST", "0.0.0.0")
    port = int(os.getenv("PORT") or os.getenv("RUGBUSTER_API_PORT", "8787"))
    app.run(host=host, port=port, debug=False)
