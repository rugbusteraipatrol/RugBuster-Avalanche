from __future__ import annotations

import json
import os
import sys
from pathlib import Path
from typing import Any

import requests
from flask import Flask, jsonify, request
from web3 import Web3

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "chains" / "avalanche"))
sys.path.insert(0, str(ROOT / "scripts"))

from bridge import publish_score, send_telegram_alert  # noqa: E402
from risk_engine import score_token  # noqa: E402
from network_config import NETWORKS, load_env, resolve_network, resolve_rpc  # noqa: E402

load_env()

DEXSCREENER_API = "https://api.dexscreener.com/latest/dex/tokens"
STABLE_QUOTES = {
    "0xB97EF9Ef8734C71904D8002F8b6Bc66Dd9c48a6E": 1.0,  # USDC
    "0x9702230A8Ea53601f5cD2dc00fDBc13d4dF4A8c7": 1.0,  # USDT.e
}
COMMON_QUOTES = [
    "0xB31f66AA3C1e785363F0875A1B74E27b85FD66c7",  # WAVAX
    "0xB97EF9Ef8734C71904D8002F8b6Bc66Dd9c48a6E",  # USDC
    "0x9702230A8Ea53601f5cD2dc00fDBc13d4dF4A8c7",  # USDT.e
    "0x49D5c2BdFfac6CE2BFdB6640F4F80f226bc10bAB",  # WETH.e
]
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


def cors(response):
    response.headers["Access-Control-Allow-Origin"] = "*"
    response.headers["Access-Control-Allow-Headers"] = "Content-Type"
    response.headers["Access-Control-Allow-Methods"] = "GET,POST,OPTIONS"
    return response


@app.after_request
def add_cors_headers(response):
    return cors(response)


@app.route("/health", methods=["GET"])
def health():
    network = resolve_network()
    return jsonify({"ok": True, "network": network, "label": NETWORKS[network]["label"]})


@app.route("/api/scan", methods=["POST", "OPTIONS"])
def api_scan():
    if request.method == "OPTIONS":
        return cors(app.response_class(status=204))

    payload = request.get_json(silent=True) or {}
    address = str(payload.get("address") or "").strip()
    publish = bool(payload.get("publish"))
    notify = bool(payload.get("notify"))

    if not Web3.is_address(address):
        return jsonify({"ok": False, "error": "Invalid Avalanche token address"}), 400

    try:
        report = scan_token(address)
    except Exception as exc:
        return jsonify({"ok": False, "error": str(exc)}), 400

    publish_result = None
    if publish:
        publish_result = publish_report(report)

    telegram_result = None
    if notify:
        telegram_result = notify_report(report, publish_result)

    return jsonify(
        {
            "ok": True,
            "report": report,
            "published": publish_result,
            "telegram": telegram_result,
        }
    )


def require_env(name: str) -> str:
    value = os.getenv(name)
    if not value:
        raise RuntimeError(f"Missing required environment variable: {name}")
    return value


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


def get_onchain_metadata(web3: Web3, address: str) -> dict[str, Any]:
    token = web3.eth.contract(address=Web3.to_checksum_address(address), abi=ERC20_ABI)
    return {
        "name": call_optional(token, "name") or "Unknown",
        "symbol": call_optional(token, "symbol") or "Unknown",
        "decimals": call_optional(token, "decimals"),
        "total_supply": call_optional(token, "totalSupply"),
    }


def fetch_dexscreener_pairs(address: str) -> list[dict[str, Any]]:
    response = requests.get(f"{DEXSCREENER_API}/{address}", timeout=20)
    response.raise_for_status()
    data = response.json()
    return [pair for pair in (data.get("pairs") or []) if (pair.get("chainId") or "").lower() == "avalanche"]

 
def get_market_data(address: str) -> dict[str, Any]:
    avalanche_pairs = fetch_dexscreener_pairs(address)
    if not avalanche_pairs:
        raise RuntimeError("Token not found on Avalanche liquidity venues")

    return sorted(
        avalanche_pairs,
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
    except RuntimeError:
        best_pair = get_pair_from_factories(web3, address, onchain.get("total_supply"))
        if best_pair:
            pair_source = "onchain_pair_lookup"

    pair_data = best_pair or {}
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

    metadata = {
        "token": Web3.to_checksum_address(address),
        "name": onchain["name"],
        "symbol": onchain["symbol"],
        "decimals": onchain["decimals"],
        "total_supply": onchain["total_supply"],
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
        "contract_tx_count": web3.eth.get_transaction_count(Web3.to_checksum_address(address)),
    }

    scores = score_token(metadata)

    return {
        "address": metadata["token"],
        "token_name": metadata["name"],
        "symbol": metadata["symbol"],
        "rug_score": scores.rug.score,
        "rug_status": scores.rug.status,
        "rug_reasons": list(scores.rug.reasons),
        "speculation_score": scores.speculation.score,
        "speculation_status": scores.speculation.status,
        "speculation_reasons": list(scores.speculation.reasons),
        "has_liquidity_evidence": metadata["has_liquidity_evidence"],
        "liquidity_usd": liquidity_usd,
        "fdv": fdv,
        "volume24h": volume24h,
        "price_change24h": price_change24h,
        "buys24h": buys24h,
        "sells24h": sells24h,
        "pair_address": metadata["pair_address"],
        "pair_url": metadata["pair_url"],
        "dex_id": metadata["dex_id"],
        "image_url": metadata["image_url"],
        "network": NETWORKS[resolve_network()]["label"],
        "source": pair_source,
    }


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


def notify_report(report: dict[str, Any], publish_result: dict[str, Any] | None) -> dict[str, Any]:
    bot_token = require_env("TELEGRAM_BOT_TOKEN")
    chat_id = require_env("TELEGRAM_CHAT_ID")
    lines = [
        "*RugBuster Apex Scan*",
        f"Token: `{report['token_name']} ({report['symbol']})`",
        f"Address: `{report['address']}`",
        f"Rug Score: *{report['rug_score']}* `{report['rug_status']}`",
        f"Speculation Score: *{report['speculation_score'] if report['speculation_score'] is not None else 'UNKNOWN'}* `{report['speculation_status']}`",
        f"DEX: `{report['dex_id']}`",
        f"Liquidity: `{format_liquidity(report['liquidity_usd'])}`",
    ]
    if publish_result:
        lines.append(f"Registry tx: `{publish_result['tx_hash']}`")
    if report.get("pair_url"):
        lines.append(f"[DexScreener Pair]({report['pair_url']})")
    all_reasons = list(report.get("rug_reasons") or []) + list(report.get("speculation_reasons") or [])
    if all_reasons:
        lines.append("")
        lines.extend([f"- {reason}" for reason in all_reasons[:6]])

    result = send_telegram_alert(
        bot_token=bot_token,
        chat_id=chat_id,
        message="\n".join(lines),
    )
    return {"ok": True, "response": result.get("ok", False)}


def format_liquidity(value: float | None) -> str:
    if value is None:
        return "UNKNOWN"
    return f"${value:,.0f}"


if __name__ == "__main__":
    host = os.getenv("RUGBUSTER_API_HOST", "0.0.0.0")
    port = int(os.getenv("PORT") or os.getenv("RUGBUSTER_API_PORT", "8787"))
    app.run(host=host, port=port, debug=False)
