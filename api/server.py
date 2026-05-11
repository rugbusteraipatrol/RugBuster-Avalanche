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


def get_market_data(address: str) -> dict[str, Any]:
    response = requests.get(f"{DEXSCREENER_API}/{address}", timeout=20)
    response.raise_for_status()
    data = response.json()
    avalanche_pairs = [pair for pair in (data.get("pairs") or []) if (pair.get("chainId") or "").lower() == "avalanche"]
    if not avalanche_pairs:
        raise RuntimeError("Token not found on Avalanche liquidity venues")

    best_pair = sorted(
        avalanche_pairs,
        key=lambda pair: float(pair.get("liquidity", {}).get("usd") or 0),
        reverse=True,
    )[0]
    return best_pair


def scan_token(address: str) -> dict[str, Any]:
    web3 = get_web3()
    onchain = get_onchain_metadata(web3, address)
    best_pair = get_market_data(address)

    liquidity_usd = float(best_pair.get("liquidity", {}).get("usd") or 0)
    fdv = float(best_pair.get("fdv") or best_pair.get("marketCap") or 0)
    volume24h = float(best_pair.get("volume", {}).get("h24") or 0)
    price_change24h = float(best_pair.get("priceChange", {}).get("h24") or 0)
    txns24h = best_pair.get("txns", {}).get("h24") or {}
    buys24h = int(txns24h.get("buys") or 0)
    sells24h = int(txns24h.get("sells") or 0)
    socials = best_pair.get("info", {}).get("socials") or []
    websites = best_pair.get("info", {}).get("websites") or []

    metadata = {
        "token": Web3.to_checksum_address(address),
        "name": onchain["name"],
        "symbol": onchain["symbol"],
        "decimals": onchain["decimals"],
        "total_supply": onchain["total_supply"],
        "deployer": None,
        "liquidity_usd": liquidity_usd,
        "fdv": fdv,
        "volume24h": volume24h,
        "price_change_24h": price_change24h,
        "buys24h": buys24h,
        "sells24h": sells24h,
        "pair_address": best_pair.get("pairAddress"),
        "pair_url": best_pair.get("url"),
        "dex_id": str(best_pair.get("dexId") or "unknown").upper(),
        "social_count": len(socials),
        "website_count": len(websites),
        "image_url": best_pair.get("info", {}).get("imageUrl"),
        "contract_tx_count": web3.eth.get_transaction_count(Web3.to_checksum_address(address)),
    }

    risk = score_token(metadata)
    reasons = list(risk.reasons)

    if price_change24h <= -60:
        reasons.append(f"Price collapsed {price_change24h:.1f}% in 24h")
    elif price_change24h <= -25:
        reasons.append(f"Price is down {price_change24h:.1f}% in 24h")

    if sells24h > buys24h * 3 and sells24h > 20:
        reasons.append(f"Heavy sell pressure: {sells24h} sells vs {buys24h} buys")

    if volume24h < 10_000:
        reasons.append(f"Low 24h volume at ${volume24h:,.0f}")

    if not socials and not websites:
        reasons.append("No visible project socials or website in market metadata")

    return {
        "address": metadata["token"],
        "token_name": metadata["name"],
        "symbol": metadata["symbol"],
        "score": risk.score,
        "label": risk.label,
        "reasons": reasons,
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
    }


def publish_report(report: dict[str, Any]) -> dict[str, Any]:
    web3 = get_web3()
    private_key = require_env("PRIVATE_KEY")
    registry_address = require_env("REGISTRY_ADDRESS")
    payload = {"report": report}
    return publish_score(
        web3=web3,
        private_key=private_key,
        registry_address=registry_address,
        token=report["address"],
        score=report["score"],
        payload=payload,
    )


def notify_report(report: dict[str, Any], publish_result: dict[str, Any] | None) -> dict[str, Any]:
    bot_token = require_env("TELEGRAM_BOT_TOKEN")
    chat_id = require_env("TELEGRAM_CHAT_ID")
    lines = [
        "*RugBuster Apex Scan*",
        f"Token: `{report['token_name']} ({report['symbol']})`",
        f"Address: `{report['address']}`",
        f"Score: *{report['score']}* `{report['label']}`",
        f"DEX: `{report['dex_id']}`",
        f"Liquidity: `${report['liquidity_usd']:,.0f}`",
    ]
    if publish_result:
        lines.append(f"Registry tx: `{publish_result['tx_hash']}`")
    if report.get("pair_url"):
        lines.append(f"[DexScreener Pair]({report['pair_url']})")
    if report.get("reasons"):
        lines.append("")
        lines.extend([f"- {reason}" for reason in report["reasons"][:6]])

    result = send_telegram_alert(
        bot_token=bot_token,
        chat_id=chat_id,
        message="\n".join(lines),
    )
    return {"ok": True, "response": result.get("ok", False)}


if __name__ == "__main__":
    host = os.getenv("RUGBUSTER_API_HOST", "0.0.0.0")
    port = int(os.getenv("PORT") or os.getenv("RUGBUSTER_API_PORT", "8787"))
    app.run(host=host, port=port, debug=False)
