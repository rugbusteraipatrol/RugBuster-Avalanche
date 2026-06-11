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
DATABASE_URL = os.getenv("DATABASE_URL")
RECENT_SCAN_LIMIT = int(os.getenv("RECENT_SCAN_LIMIT", "10"))
RECENT_SCAN_INGEST_TOKEN = os.getenv("RECENT_SCAN_INGEST_TOKEN", "").strip()
RECENT_SCANS: list[dict[str, Any]] = []
DEEPSEEK_API_KEY = os.getenv("DEEPSEEK_API_KEY", "").strip()
DEEPSEEK_MODEL = os.getenv("DEEPSEEK_MODEL", "deepseek-chat").strip()
DEEPSEEK_API_URL = os.getenv("DEEPSEEK_API_URL", "https://api.deepseek.com/chat/completions").strip()
DEEPSEEK_TIMEOUT_SECONDS = int(os.getenv("DEEPSEEK_TIMEOUT_SECONDS", "20"))


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
    explorer_base = "https://bscscan.com/address" if chain in {"bnb", "bsc"} else "https://snowtrace.io/address"
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
    return jsonify({"ok": True, "network": network, "label": NETWORKS[network]["label"]})


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
    rug_status = str(report.get("rug_status") or "").upper()
    speculation_status = str(report.get("speculation_status") or "").upper()
    if rug_status == "HIGH" or speculation_status == "HIGH":
        return "DANGER"
    if rug_status in {"ELEVATED", "WARN"} or speculation_status in {"ELEVATED", "WARN"}:
        return "WARN"
    if rug_status == "LOW" and speculation_status == "LOW":
        return "GOOD"
    return "UNKNOWN"


def compact_score_response(report: dict[str, Any], source: str) -> dict[str, Any]:
    address = report.get("address") or report.get("contract_address") or ""
    risk_flags = list(report.get("rug_reasons") or [])[:4] + list(report.get("speculation_reasons") or [])[:4]
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
        "risk_flags": risk_flags[:6],
        "classifier": "weighted_v2",
        "source": source,
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


@app.route("/score", methods=["GET"])
def public_score():
    address = str(request.args.get("address") or "").strip()
    if not Web3.is_address(address):
        return jsonify({"ok": False, "error": "Invalid Avalanche token address"}), 400

    score = lookup_cached_score(address)
    if score:
        return jsonify(score)

    try:
        report = scan_token(address)
    except Exception as exc:
        return jsonify({"ok": False, "error": str(exc), "address": address}), 404
    put_cached_report(address, report)
    return jsonify(compact_score_response(report, "live_score"))


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
            report = scan_token(address)
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
