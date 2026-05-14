from __future__ import annotations

import json
import os
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
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
GLACIER_API = "https://glacier-api.avax.network"
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
SCAN_CACHE_TTL_SECONDS = 180
SCAN_CACHE: dict[str, dict[str, Any]] = {}
PORTFOLIO_SCAN_WORKERS = 3


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
    use_cached = bool(payload.get("use_cached"))

    if not Web3.is_address(address):
        return jsonify({"ok": False, "error": "Invalid Avalanche token address"}), 400

    report = get_cached_report(address) if use_cached else None
    if report is None:
        try:
            report = scan_token(address)
        except Exception as exc:
            return jsonify({"ok": False, "error": str(exc)}), 400
        put_cached_report(address, report)

    publish_result = None
    if publish:
        try:
            publish_result = publish_report(report)
        except Exception as exc:
            return jsonify({"ok": False, "error": f"Registry publish failed: {exc}", "report": report}), 400

    telegram_result = None
    if notify:
        try:
            telegram_result = notify_report(report, publish_result)
        except Exception as exc:
            return jsonify({"ok": False, "error": f"Telegram alert failed: {exc}", "report": report}), 400

    return jsonify(
        {
            "ok": True,
            "report": report,
            "published": publish_result,
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
    token = web3.eth.contract(address=Web3.to_checksum_address(address), abi=ERC20_ABI)
    return {
        "name": call_optional(token, "name") or "Unknown",
        "symbol": call_optional(token, "symbol") or "Unknown",
        "decimals": call_optional(token, "decimals"),
        "total_supply": call_optional(token, "totalSupply"),
    }


def build_report_from_metadata(address: str, metadata: dict[str, Any], pair_data: dict[str, Any] | None, source: str) -> dict[str, Any]:
    pair_data = pair_data or {}
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
    }

    scores = score_token(scoring_input)
    return {
        "address": scoring_input["token"],
        "token_name": scoring_input["name"],
        "symbol": scoring_input["symbol"],
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
        "network": NETWORKS[resolve_network()]["label"],
        "source": source,
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


def notify_report(report: dict[str, Any], publish_result: dict[str, Any] | None) -> dict[str, Any]:
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
