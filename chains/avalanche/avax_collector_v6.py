"""
avax_collector_v6.py — Syndicate Avax C-Chain Collector V6

Upgradovan na paritet sa dataset_collector_v6.py:
- V5: Cross-Chain Wallet Matching, Lifecycle Prediction, Name Stylometry, DEX Sweep
- V6: Contract Backdoor Detection (bytecode), Holder Concentration, Rug Velocity
- Throttling/backoff na SnowTrace API pozivima
- V6 intel polja u training record

Instalacija: pip install requests
Pokretanje:  python avax_collector_v6.py [<contract_address>]
"""

import json
import logging
import os
import random
import time
import statistics
import hashlib
from eth_utils import keccak
from pathlib import Path
from collections import defaultdict, Counter
from datetime import datetime, timezone
from typing import Optional

import psycopg2
from psycopg2.extras import Json
import requests

try:
    from web3 import Web3
except ImportError:
    Web3 = None


def load_env(path: Path = Path(".env")) -> None:
    if not path.exists():
        return
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        os.environ.setdefault(key, value)


load_env()


def clean_env_value(name: str, default: str = "") -> str:
    return os.getenv(name, default).strip().strip('"').strip("'")

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------
SNOWTRACE_API_KEY = clean_env_value("SNOWTRACE_API_KEY", "rs_c563928dc3b9c46d70d8698c")
SNOWTRACE_API     = clean_env_value("SNOWTRACE_API", "https://api.routescan.io/v2/network/mainnet/evm/43114/etherscan/api")
AVAX_RPC          = clean_env_value("AVAX_RPC", "https://api.avax.network/ext/bc/C/rpc")

OUTPUT_FILE       = "syndicate_train_avax_v6.jsonl"
DATABASE_URL      = os.getenv("DATABASE_URL")
DB_TABLE          = "avax_scans"
POLL_INTERVAL     = 60
RPC_TIMEOUT       = 15
API_TIMEOUT       = 20
RATE_LIMIT_DELAY  = 1.0
MIN_SCAN_DELAY_MINUTES = int(os.getenv("MIN_SCAN_DELAY_MINUTES", "2"))
MAX_SCAN_DELAY_MINUTES = int(os.getenv("MAX_SCAN_DELAY_MINUTES", "3"))
MAX_TOKENS_PER_DAY = int(os.getenv("MAX_TOKENS_PER_DAY", "120"))
MAX_AVAX_TOTAL = float(os.getenv("MAX_AVAX_TOTAL", "2"))
MAX_EUR_TOTAL = float(os.getenv("MAX_EUR_TOTAL", "20"))
RUN_UNTIL_DATE = os.getenv("RUN_UNTIL_DATE", "2026-07-14")
AVAX_EUR_PRICE_FALLBACK = float(os.getenv("AVAX_EUR_PRICE_FALLBACK", "30"))
TARGET_AVAX_PER_SCAN = float(os.getenv("TARGET_AVAX_PER_SCAN", "0.001"))
EVIDENCE_BYTES_TARGET = int(os.getenv("EVIDENCE_BYTES_TARGET", "2048"))
REQUIRE_ERC20_METADATA = os.getenv("REQUIRE_ERC20_METADATA", "true").strip().lower() in {"1", "true", "yes", "on"}
AVAX_SCAN_LOG = Path(clean_env_value("AVAX_SCAN_LOG", "avax_scan_log.md"))
AVAX_STATE_FILE = Path(clean_env_value("AVAX_STATE_FILE", "avax_collector_state.json"))
AVAX_TELEGRAM_CHAT_ID = clean_env_value("AVAX_TELEGRAM_CHAT_ID") or clean_env_value("TELEGRAM_CHAT_ID") or "@RugBusterAvax"
TELEGRAM_BOT_TOKEN = clean_env_value("AVAX_TELEGRAM_BOT_TOKEN") or clean_env_value("TELEGRAM_BOT_TOKEN")
RECENT_SCAN_FEED_URL = clean_env_value("RECENT_SCAN_FEED_URL", "https://web-production-376bf.up.railway.app/api/recent-scans")
RECENT_SCAN_INGEST_TOKEN = clean_env_value("RECENT_SCAN_INGEST_TOKEN")
ONCHAIN_LOG_ENABLED = os.getenv("ONCHAIN_LOG_ENABLED", "true").strip().lower() in {"1", "true", "yes", "on"}
ONCHAIN_LOG_TO_ADDRESS = clean_env_value("ONCHAIN_LOG_TO_ADDRESS")
ACTIVITY_LOGGER_ADDRESS = clean_env_value("ACTIVITY_LOGGER_ADDRESS")
REGISTRY_ADDRESS = clean_env_value("REGISTRY_ADDRESS", "0x5F30276B3A5079E088Ec3072884286de5a868355")
BOT_PUBLISH_TO_REGISTRY = os.getenv("BOT_PUBLISH_TO_REGISTRY", "true").strip().lower() in {"1", "true", "yes", "on"}
GECKOTERMINAL_ENABLED = os.getenv("GECKOTERMINAL_ENABLED", "true").strip().lower() in {"1", "true", "yes", "on"}
GECKOTERMINAL_NEW_POOLS_URL = clean_env_value(
    "GECKOTERMINAL_NEW_POOLS_URL",
    "https://api.geckoterminal.com/api/v2/networks/avax/new_pools?include=base_token,quote_token",
)
GECKOTERMINAL_TOP_POOLS_ENABLED = os.getenv("GECKOTERMINAL_TOP_POOLS_ENABLED", "true").strip().lower() in {"1", "true", "yes", "on"}
GECKOTERMINAL_POOL_PAGES = int(os.getenv("GECKOTERMINAL_POOL_PAGES", "3"))
GECKOTERMINAL_QUEUE_LOW_WATERMARK = int(os.getenv("GECKOTERMINAL_QUEUE_LOW_WATERMARK", "10"))
GECKOTERMINAL_TOP_POOLS_COOLDOWN_SECONDS = int(os.getenv("GECKOTERMINAL_TOP_POOLS_COOLDOWN_SECONDS", "900"))
RESCAN_COOLDOWN_SECONDS = int(os.getenv("RESCAN_COOLDOWN_SECONDS", "2700"))
V1_PAIR_CREATED_TOPIC = "0x0d3648bd0f6ba80134a33ba9275ac585d9d315f0ad8355cddefde31afa28d0e9"
LB_PAIR_CREATED_TOPIC = "0x" + keccak(text="LBPairCreated(address,address,uint256,address,uint256)").hex()
DEFAULT_V1_DEX_FACTORIES = [
    "0x9Ad6C38BE94206cA50bb0d90783181662f0Cfa10",  # Trader Joe V1
    "0xE54Ca86531e17Ef3616d22Ca28b0D458b6C89106",  # Pangolin
]
DEFAULT_LB_DEX_FACTORIES = [
    "0xb43120c4745967fa9b93E79C149E66B0f2D6Fe0c",  # LFJ V2.2
    "0x8e42f2F4101563bF679975178e880FD87d3eFd4e",  # LFJ V2.1
    "0x6E77932A92582f504FF6c4BdbCef7Da6c198aEEf",  # LFJ V2.0
]
BASE_TOKEN_ADDRESSES = {
    "0xb31f66aa3c1e785363f0875a1b74e27b85fd66c7",  # WAVAX
    "0xa7d7079b0fead91f3e65f86e8915cb59c1a4c664",  # USDC.e
    "0xb97ef9ef8734c71904d8002f8b6bc66dd9c48a6e",  # USDC
    "0xc7198437980c041c805a1edcba50c1ce5db95118",  # USDT.e
    "0xd586e7f844cea2f87f50152665bcbc2c279d8d70",  # DAI.e
}

ACTIVITY_LOGGER_ABI = [
    {
        "inputs": [
            {"internalType": "address", "name": "token", "type": "address"},
            {"internalType": "string", "name": "module", "type": "string"},
            {"internalType": "string", "name": "verdict", "type": "string"},
            {"internalType": "uint8", "name": "score", "type": "uint8"},
            {"internalType": "bytes32", "name": "payloadHash", "type": "bytes32"},
        ],
        "name": "logModule",
        "outputs": [],
        "stateMutability": "nonpayable",
        "type": "function",
    },
    {
        "inputs": [
            {"internalType": "address", "name": "token", "type": "address"},
            {"internalType": "string", "name": "module", "type": "string"},
            {"internalType": "string", "name": "verdict", "type": "string"},
            {"internalType": "uint8", "name": "score", "type": "uint8"},
            {"internalType": "bytes32", "name": "payloadHash", "type": "bytes32"},
            {"internalType": "bytes", "name": "evidence", "type": "bytes"},
        ],
        "name": "logModuleWithEvidence",
        "outputs": [],
        "stateMutability": "nonpayable",
        "type": "function",
    }
]
REGISTRY_ABI = [
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
ERC20_META_ABI = [
    {"constant": True, "inputs": [], "name": "name", "outputs": [{"name": "", "type": "string"}], "type": "function"},
    {"constant": True, "inputs": [], "name": "symbol", "outputs": [{"name": "", "type": "string"}], "type": "function"},
    {"constant": True, "inputs": [], "name": "decimals", "outputs": [{"name": "", "type": "uint8"}], "type": "function"},
    {"constant": True, "inputs": [], "name": "totalSupply", "outputs": [{"name": "", "type": "uint256"}], "type": "function"},
]
ERC20_BYTES32_META_ABI = [
    {"constant": True, "inputs": [], "name": "name", "outputs": [{"name": "", "type": "bytes32"}], "type": "function"},
    {"constant": True, "inputs": [], "name": "symbol", "outputs": [{"name": "", "type": "bytes32"}], "type": "function"},
    {"constant": True, "inputs": [], "name": "decimals", "outputs": [{"name": "", "type": "uint8"}], "type": "function"},
    {"constant": True, "inputs": [], "name": "totalSupply", "outputs": [{"name": "", "type": "uint256"}], "type": "function"},
]

# Throttle: SnowTrace free tier ~5 req/s
_last_api_call = [0.0]
API_MIN_INTERVAL = 0.25  # 250ms = ~4 RPS

# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [AVAX-V6] [%(levelname)s] %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# PostgreSQL storage
# ---------------------------------------------------------------------------

def _db_connect():
    if not DATABASE_URL:
        return None
    return psycopg2.connect(DATABASE_URL)


def init_database() -> None:
    conn = _db_connect()
    if conn is None:
        log.info("DATABASE_URL nije postavljen; PostgreSQL upis je isključen.")
        return

    try:
        with conn:
            with conn.cursor() as cur:
                cur.execute(
                    f"""
                    CREATE TABLE IF NOT EXISTS {DB_TABLE} (
                        id BIGSERIAL PRIMARY KEY,
                        contract_address TEXT NOT NULL UNIQUE,
                        chain TEXT,
                        label TEXT,
                        full_record JSONB NOT NULL,
                        created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
                    )
                    """
                )
        log.info("PostgreSQL tabela spremna: %s", DB_TABLE)
    except Exception as e:
        log.error("PostgreSQL init greška: %s", e)
    finally:
        conn.close()


def save_to_postgres(record: dict) -> None:
    contract_address = record.get("contract_address")
    if not contract_address:
        log.warning("PostgreSQL preskok: record nema contract_address.")
        return

    contract_address = contract_address.lower()
    conn = _db_connect()
    if conn is None:
        return

    try:
        with conn:
            with conn.cursor() as cur:
                cur.execute(
                    f"""
                    INSERT INTO {DB_TABLE} (contract_address, chain, label, full_record)
                    VALUES (%s, %s, %s, %s)
                    ON CONFLICT (contract_address) DO UPDATE SET
                        chain = EXCLUDED.chain,
                        label = EXCLUDED.label,
                        full_record = EXCLUDED.full_record,
                        created_at = NOW()
                    """,
                    (
                        contract_address,
                        record.get("chain"),
                        record.get("label"),
                        Json(record),
                    ),
                )
                log.info("  -> PostgreSQL upsert [%s]: %s", DB_TABLE, contract_address)
    except Exception as e:
        log.error("PostgreSQL upis greška: %s", e)
    finally:
        conn.close()

# ---------------------------------------------------------------------------
# Creator History Tracker
# ---------------------------------------------------------------------------
creator_history = defaultdict(lambda: {"total": 0, "danger": 0, "warn": 0, "good": 0})
seen_contracts: dict[str, float] = {}

def update_creator_history(creator: str, label: str):
    if not creator:
        return
    creator_history[creator]["total"] += 1
    if label in ("DANGER", "WARN", "GOOD"):
        creator_history[creator][label.lower()] += 1

def get_creator_stats(creator: str) -> dict:
    if not creator or creator not in creator_history:
        return {"total": 0, "danger": 0, "rug_rate": 0.0}
    stats = creator_history[creator]
    total = stats["total"]
    danger = stats["danger"]
    rug_rate = (danger / total * 100) if total > 0 else 0.0
    return {"total": total, "danger": danger, "rug_rate": round(rug_rate, 1)}

# ---------------------------------------------------------------------------
# V5 MODULE 1: Cross-Chain Wallet Matching (shared state)
# ---------------------------------------------------------------------------
cross_chain_patterns = {}
seen_token_names = []

def compute_wallet_pattern_hash(deploy_ts: int, tx_amounts: list, holder_count: int) -> str:
    hour_bucket = (deploy_ts // 3600 // 4) % 6
    amount_sig = round(sum(tx_amounts[:5]) / max(len(tx_amounts[:5]), 1), 1) if tx_amounts else 0
    holder_bucket = "low" if holder_count < 10 else "mid" if holder_count < 100 else "high"
    pattern_str = f"{hour_bucket}_{amount_sig}_{holder_bucket}"
    return hashlib.md5(pattern_str.encode()).hexdigest()[:12]


def detect_cross_chain_match(deploy_ts: int, tx_amounts: list, holder_count: int, chain: str = "AVAX") -> dict:
    result = {
        "pattern_hash": "",
        "cross_chain_match": False,
        "match_chains": [],
        "match_count": 0,
    }
    pattern = compute_wallet_pattern_hash(deploy_ts, tx_amounts, holder_count)
    result["pattern_hash"] = pattern

    if pattern in cross_chain_patterns:
        entry = cross_chain_patterns[pattern]
        result["match_chains"] = entry["chains"]
        result["match_count"] = entry["count"]
        result["cross_chain_match"] = chain not in entry["chains"] or len(entry["chains"]) > 1
        entry["count"] += 1
        if chain not in entry["chains"]:
            entry["chains"].append(chain)
    else:
        cross_chain_patterns[pattern] = {"chains": [chain], "count": 1, "first_seen": deploy_ts}

    return result

# ---------------------------------------------------------------------------
# V5 MODULE 2: Lifecycle Prediction
# ---------------------------------------------------------------------------

def predict_lifecycle(intel: dict, creator_rug_rate: float) -> dict:
    result = {
        "estimated_rug_minutes": -1,
        "confidence": 0.0,
        "prediction_text": "Insufficient data",
    }
    sniped    = intel.get("latency", {}).get("is_sniped", False)
    bot_pat   = intel.get("entropy", {}).get("is_bot_pattern", False)
    wash      = intel.get("wash", {}).get("wash_detected", False)
    bot_farm  = intel.get("cluster", {}).get("is_bot_farm", False)
    fresh_f   = intel.get("funding", {}).get("all_fresh", False)

    signals = sum([sniped, bot_pat, wash, bot_farm, fresh_f])

    if signals >= 4:
        result.update({"estimated_rug_minutes": 15, "confidence": 0.87,
                        "prediction_text": "Rug expected within 15 minutes (87% confidence)"})
    elif signals >= 3 and creator_rug_rate > 50:
        result.update({"estimated_rug_minutes": 45, "confidence": 0.72,
                        "prediction_text": "Rug expected within 45 minutes (72% confidence)"})
    elif signals >= 3:
        result.update({"estimated_rug_minutes": 120, "confidence": 0.61,
                        "prediction_text": "Rug expected within 2 hours (61% confidence)"})
    elif signals >= 2:
        result.update({"estimated_rug_minutes": 360, "confidence": 0.45,
                        "prediction_text": "Possible rug within 6 hours (45% confidence)"})
    elif signals == 0:
        result.update({"estimated_rug_minutes": -1, "confidence": 0.0,
                        "prediction_text": "No imminent rug signals"})
    else:
        result.update({"estimated_rug_minutes": 1440, "confidence": 0.20,
                        "prediction_text": "Monitor — weak signals detected"})
    return result

# ---------------------------------------------------------------------------
# V5 MODULE 4: Token Name Stylometry
# ---------------------------------------------------------------------------
SCAM_NAME_PATTERNS = [
    "killer", "2.0", "reborn", "moon", "elon", "musk", "trump",
    "inu", "doge", "pepe", "shiba", "wojak", "chad", "based",
    "ai", "gpt", "agent", "swarm",
    "100x", "1000x", "x100", "x1000",
    "official", "real", "v2", "v3",
]

def analyze_name_stylometry(token_name: str, ticker: str) -> dict:
    result = {
        "name_scam_score": 0,
        "matched_patterns": [],
        "similar_to_previous": False,
        "most_similar_name": "",
        "similarity_score": 0.0,
    }
    if not token_name:
        return result

    name_lower = token_name.lower()
    ticker_lower = ticker.lower() if ticker else ""

    matched = [p for p in SCAM_NAME_PATTERNS if p in name_lower or p in ticker_lower]
    result["matched_patterns"] = matched
    result["name_scam_score"] = min(len(matched) * 25, 100)

    if seen_token_names:
        max_sim, most_sim = 0.0, ""
        for prev in seen_token_names[-200:]:
            s1, s2 = set(name_lower), set(prev.lower())
            if s1 and s2:
                sim = len(s1 & s2) / len(s1 | s2)
                if sim > max_sim:
                    max_sim, most_sim = sim, prev
        if max_sim > 0.8:
            result["similar_to_previous"] = True
            result["most_similar_name"] = most_sim
            result["similarity_score"] = round(max_sim, 2)

    seen_token_names.append(token_name)
    if len(seen_token_names) > 1000:
        seen_token_names.pop(0)

    return result

# ---------------------------------------------------------------------------
# V5 MODULE 5: Dev -> CEX Sweep (Avax known CEX wallets)
# ---------------------------------------------------------------------------
KNOWN_CEX_WALLETS_AVAX = {
    "0x9f8c163cba728e99993abe7495f06c0a3c8ac8b9": "Binance",
    "0x5a52e96bacdabb82fd05763e25335261b270efcb": "Binance",
    "0xeb2d2f1b8c558a40207669291fda468e50c8a0bb": "Kraken",
    "0x0d0707963952f2fba59dd06f2b425ace40b492fe": "Gate.io",
    "0x1c4b70a3968436b9a0a9cf5205c787eb81bb558c": "KuCoin",
}

def detect_cex_sweep_avax(deployer: str, deploy_timestamp: int) -> dict:
    result = {
        "sweep_to_cex": False,
        "cex_destination": "",
        "sweep_count": 0,
        "exit_pattern_confidence": 0.0,
    }
    if not deployer:
        return result

    txs = get_account_transactions(deployer, limit=50)
    if not txs:
        return result

    sweep_count = 0
    destinations = []

    for tx in txs:
        ts = int(tx.get("timeStamp", 0))
        if ts < deploy_timestamp:
            continue
        to_addr = tx.get("to", "").lower()
        if to_addr in KNOWN_CEX_WALLETS_AVAX:
            sweep_count += 1
            destinations.append(KNOWN_CEX_WALLETS_AVAX[to_addr])

    result["sweep_count"] = sweep_count
    if sweep_count > 0:
        result["sweep_to_cex"] = True
        result["cex_destination"] = ", ".join(set(destinations))
        result["exit_pattern_confidence"] = min(sweep_count * 0.3, 1.0)

    return result

# ---------------------------------------------------------------------------
# V6 MODULE 1: Contract Backdoor Detection (EVM bytecode)
# ---------------------------------------------------------------------------
BACKDOOR_SIGNATURES = {
    "8da5cb5b": "owner()",
    "f2fde38b": "transferOwnership(address)",
    "715018a6": "renounceOwnership()",
    "42966c68": "burn(uint256)",
    "40c10f19": "mint(address,uint256)",
    "3ccfd60b": "withdraw()",
    "2e1a7d4d": "withdraw(uint256)",
    "51cff8d9": "withdrawToken(address)",
    "3659cfe6": "upgradeTo(address)",
    "4f1ef286": "upgradeToAndCall(address,bytes)",
    "5c60da1b": "implementation()",
    "8456cb59": "pause()",
    "3f4ba83a": "unpause()",
    "5c975abb": "paused()",
    "044df020": "blacklist(address)",
    "537df3b6": "unBlacklist(address)",
    "fe575a87": "isBlacklisted(address)",
}

def detect_contract_backdoor_avax(contract_address: str) -> dict:
    result = {
        "has_backdoor": False,
        "backdoor_functions": [],
        "has_upgrade_authority": False,
        "has_pause_function": False,
        "has_mint_function": False,
        "has_drain_function": False,
        "has_blacklist": False,
        "is_proxy": False,
        "backdoor_risk_score": 0,
    }
    try:
        payload = {
            "jsonrpc": "2.0", "id": 1,
            "method": "eth_getCode",
            "params": [contract_address, "latest"]
        }
        resp = requests.post(AVAX_RPC, json=payload, timeout=RPC_TIMEOUT)
        bytecode = resp.json().get("result", "0x")

        if bytecode and len(bytecode) > 10:
            bytecode_clean = bytecode.lower().replace("0x", "")
            for sig, func_name in BACKDOOR_SIGNATURES.items():
                if sig in bytecode_clean:
                    result["backdoor_functions"].append(func_name)
                    if "upgradeto" in func_name.lower() or "implementation" in func_name.lower():
                        result["is_proxy"] = True
                        result["has_upgrade_authority"] = True
                    if "pause" in func_name.lower():
                        result["has_pause_function"] = True
                    if "mint" in func_name.lower():
                        result["has_mint_function"] = True
                    if "withdraw" in func_name.lower() or "drain" in func_name.lower():
                        result["has_drain_function"] = True
                    if "blacklist" in func_name.lower():
                        result["has_blacklist"] = True

            result["has_backdoor"] = len(result["backdoor_functions"]) > 0

    except Exception as e:
        log.debug("  [V6] Bytecode analiza greška: %s", e)

    danger_count = sum([
        result["has_upgrade_authority"],
        result["has_mint_function"],
        result["has_drain_function"],
        result["has_pause_function"],
        result["has_blacklist"],
        result["is_proxy"],
    ])
    result["backdoor_risk_score"] = min(danger_count * 20, 100)
    return result

# ---------------------------------------------------------------------------
# V6 MODULE 2: Holder Concentration Risk
# ---------------------------------------------------------------------------

def analyze_holder_concentration_avax(contract_address: str) -> dict:
    result = {
        "top5_pct": 0.0,
        "top1_pct": 0.0,
        "is_concentrated": False,
        "concentration_risk": "LOW",
    }
    holders = get_token_holders(contract_address)
    if not holders or len(holders) < 2:
        return result

    try:
        # Routescan vraća TokenHolderQuantity kao string
        amounts = []
        for h in holders[:10]:
            qty = h.get("TokenHolderQuantity", "0") or "0"
            amounts.append(float(str(qty).replace(",", "")))

        total = sum(amounts)
        if total == 0:
            return result

        top5 = sum(amounts[:5])
        top1 = amounts[0]

        result["top5_pct"] = round(top5 / total * 100, 1)
        result["top1_pct"] = round(top1 / total * 100, 1)
        result["is_concentrated"] = result["top5_pct"] > 80

        if result["top5_pct"] > 90:
            result["concentration_risk"] = "CRITICAL"
        elif result["top5_pct"] > 80:
            result["concentration_risk"] = "HIGH"
        elif result["top5_pct"] > 60:
            result["concentration_risk"] = "MEDIUM"
        else:
            result["concentration_risk"] = "LOW"
    except Exception as e:
        log.debug("  [V6] Holder concentration greška: %s", e)

    return result

# ---------------------------------------------------------------------------
# V6 MODULE 3: Rug Velocity Score
# ---------------------------------------------------------------------------

def calculate_rug_velocity_avax(contract_address: str, deploy_timestamp: int) -> dict:
    result = {
        "velocity_score": 0.0,
        "is_fast_rug": False,
        "unique_sellers_pct": 0.0,
        "volume_decay": False,
    }
    try:
        transfers = get_token_transfers(contract_address, limit=50)
        if not transfers or len(transfers) < 5:
            return result

        # Grupiši po vremenskim prozorima od 5 minuta
        window = 300  # 5 minuta
        windows = defaultdict(list)
        for tx in transfers:
            ts = int(tx.get("timeStamp", 0))
            if ts < deploy_timestamp:
                continue
            w = (ts - deploy_timestamp) // window
            windows[w].append(tx)

        if not windows:
            return result

        # Provjeri da li se volumen naglo smanjuje
        sorted_windows = sorted(windows.keys())
        if len(sorted_windows) >= 3:
            first_vol = len(windows[sorted_windows[0]])
            last_vol = len(windows[sorted_windows[-1]])
            result["volume_decay"] = last_vol < (first_vol * 0.2)

        # Unique sellers u prvih 10 minuta
        early_transfers = [tx for tx in transfers if
                           int(tx.get("timeStamp", 0)) - deploy_timestamp < 600]
        senders = [tx.get("from", "").lower() for tx in early_transfers]
        unique_senders = len(set(senders))
        total_senders = len(senders)
        if total_senders > 0:
            result["unique_sellers_pct"] = round(unique_senders / total_senders * 100, 1)

        # Velocity score: kombinacija sigurnih indikatora
        score = 0.0
        if result["volume_decay"]:
            score += 0.4
        if result["unique_sellers_pct"] > 70:
            score += 0.3
        if len(transfers) > 20 and len(set(t.get("from", "") for t in transfers[:10])) <= 2:
            score += 0.3  # prvih 10 tx od samo 2 adrese = bot

        result["velocity_score"] = round(min(score, 1.0), 2)
        result["is_fast_rug"] = score >= 0.6

    except Exception as e:
        log.debug("  [V6] Rug velocity greška: %s", e)

    return result

# ---------------------------------------------------------------------------
# SnowTrace API helpers (sa throttlingom)
# ---------------------------------------------------------------------------

def _throttle_api():
    now = time.time()
    elapsed = now - _last_api_call[0]
    if elapsed < API_MIN_INTERVAL:
        time.sleep(API_MIN_INTERVAL - elapsed)
    _last_api_call[0] = time.time()


def snowtrace_get(params: dict) -> Optional[dict]:
    _throttle_api()
    params["apikey"] = SNOWTRACE_API_KEY
    headers = {"User-Agent": "SyndicateCollector/6.0", "Accept": "application/json"}
    delays = [2, 4, 8]
    for attempt, delay in enumerate(delays):
        try:
            resp = requests.get(SNOWTRACE_API, params=params, headers=headers, timeout=API_TIMEOUT)
            if resp.status_code == 429:
                log.warning("  [API] Rate limit (429) — čekam %ds (pokušaj %d/3)", delay, attempt + 1)
                time.sleep(delay)
                continue
            resp.raise_for_status()
            data = resp.json()
            if isinstance(data, dict):
                status = data.get("status")
                result = data.get("result")
                if status == "1":
                    return result
                msg = data.get("message", "")
                if "No transactions" in msg or "No records" in msg:
                    return []
                if result is not None:
                    return result
            return []
        except requests.RequestException as e:
            log.warning("  [API] Greška (pokušaj %d/3): %s", attempt + 1, e)
            time.sleep(delay)
    return None


def get_latest_block() -> int:
    try:
        payload = {"jsonrpc": "2.0", "id": 1, "method": "eth_blockNumber", "params": []}
        resp = requests.post(AVAX_RPC, json=payload, timeout=RPC_TIMEOUT)
        return int(resp.json().get("result", "0x0"), 16)
    except Exception as e:
        log.error("Ne mogu dohvatiti blok: %s", e)
        return 0


def get_contract_transactions(address: str, limit: int = 50) -> list:
    params = {
        "module": "account", "action": "txlist",
        "address": address, "startblock": 0, "endblock": 99999999,
        "page": 1, "offset": limit, "sort": "asc",
    }
    result = snowtrace_get(params)
    return result if result else []


def get_token_transfers(address: str, limit: int = 50) -> list:
    params = {
        "module": "account", "action": "tokentx",
        "contractaddress": address, "startblock": 0, "endblock": 99999999,
        "page": 1, "offset": limit, "sort": "asc",
    }
    result = snowtrace_get(params)
    return result if result else []


def get_account_transactions(address: str, limit: int = 100) -> list:
    params = {
        "module": "account", "action": "txlist",
        "address": address, "startblock": 0, "endblock": 99999999,
        "page": 1, "offset": limit, "sort": "asc",
    }
    result = snowtrace_get(params)
    return result if result else []


def get_avax_balance(address: str) -> float:
    params = {"module": "account", "action": "balance", "address": address, "tag": "latest"}
    result = snowtrace_get(params)
    if result:
        try:
            return int(result) / 1e18
        except (ValueError, TypeError):
            pass
    return 0.0


def get_token_holders(address: str) -> list:
    params = {
        "module": "token", "action": "tokenholderlist",
        "contractaddress": address, "page": 1, "offset": 10,
    }
    result = snowtrace_get(params)
    return result if result else []


def get_token_info_avax(contract_address: str) -> dict:
    params = {"module": "token", "action": "tokeninfo", "contractaddress": contract_address}
    result = snowtrace_get(params)
    if result and isinstance(result, list) and len(result) > 0:
        info = result[0]
        return {
            "name": info.get("tokenName", "Unknown"),
            "symbol": info.get("symbol", ""),
            "total_supply": info.get("totalSupply", 0),
            "decimals": info.get("divisor", 18),
            "holders_count": int(info.get("holdersCount", 0)),
        }

    rpc_info = get_erc20_metadata_rpc(contract_address)
    if rpc_info:
        return rpc_info
    return {"name": "Unknown", "symbol": "", "total_supply": 0, "decimals": 18, "holders_count": 0}


def decode_token_text(value) -> str:
    if isinstance(value, bytes):
        return value.rstrip(b"\x00").decode("utf-8", errors="ignore").strip()
    return str(value or "").strip()


def read_erc20_metadata_with_abi(web3, contract_address: str, abi: list[dict]) -> dict | None:
    token = web3.eth.contract(address=Web3.to_checksum_address(contract_address), abi=abi)
    name = decode_token_text(token.functions.name().call())
    symbol = decode_token_text(token.functions.symbol().call())
    decimals = token.functions.decimals().call()
    total_supply = token.functions.totalSupply().call()
    if not name and not symbol:
        return None
    if not name:
        name = symbol
    if not symbol:
        symbol = name[:12]
    return {
        "name": name,
        "symbol": symbol,
        "total_supply": total_supply,
        "decimals": int(decimals),
        "holders_count": 0,
        "is_erc20": True,
    }


def get_erc20_metadata_rpc(contract_address: str) -> dict | None:
    if Web3 is None:
        return None
    web3 = Web3(Web3.HTTPProvider(AVAX_RPC, request_kwargs={"timeout": RPC_TIMEOUT}))
    if not web3.is_connected() or not Web3.is_address(contract_address):
        return None
    for abi in (ERC20_META_ABI, ERC20_BYTES32_META_ABI):
        try:
            metadata = read_erc20_metadata_with_abi(web3, contract_address, abi)
            if metadata:
                return metadata
        except Exception:
            continue
    return None


def has_usable_token_metadata(token_info: dict) -> bool:
    name = str(token_info.get("name") or "").strip()
    symbol = str(token_info.get("symbol") or "").strip()
    return bool((name and name.lower() != "unknown") or symbol)

# ---------------------------------------------------------------------------
# CIA Analitika — iste funkcije iz stare verzije
# ---------------------------------------------------------------------------

def get_account_age_days_avax(address: str) -> float:
    txs = get_account_transactions(address, limit=1000)
    if not txs:
        return 0.0
    oldest_ts = int(txs[0].get("timeStamp", 0))
    if not oldest_ts:
        return 0.0
    return round((time.time() - oldest_ts) / 86400, 1)


def trace_funding_origin_avax(deployer: str, depth: int = 3) -> dict:
    result = {
        "master_wallet": "",
        "hop_count": 0,
        "is_fresh_wallet": False,
        "funding_chain": [deployer],
        "all_fresh": False,
        "wallet_ages_days": [],
    }
    current = deployer
    chain_trace = [deployer]

    for hop in range(depth):
        txs = get_account_transactions(current, limit=10)
        if not txs:
            result["is_fresh_wallet"] = True
            break
        first_tx = txs[0]
        sender = first_tx.get("from", "").lower()
        if sender and sender != current.lower() and sender != "0x0000000000000000000000000000000000000000":
            chain_trace.append(sender)
            current = sender
            result["hop_count"] = hop + 1
        else:
            break
        time.sleep(0.3)

    result["funding_chain"] = chain_trace
    result["master_wallet"] = chain_trace[-1] if len(chain_trace) > 1 else ""

    ages = []
    for addr in chain_trace[1:]:
        age = get_account_age_days_avax(addr)
        ages.append(age)
        time.sleep(0.3)

    result["wallet_ages_days"] = ages
    result["all_fresh"] = all(a < 7 for a in ages) if ages else False
    return result


def get_deployment_latency_avax(contract_address: str, deploy_timestamp: int) -> dict:
    result = {"deploy_time": deploy_timestamp, "first_buy_time": 0, "latency_ms": -1, "is_sniped": False}
    transfers = get_token_transfers(contract_address, limit=10)
    if not transfers or len(transfers) < 2:
        return result
    for tx in transfers:
        ts = int(tx.get("timeStamp", 0))
        if ts > deploy_timestamp:
            result["first_buy_time"] = ts
            latency_ms = (ts - deploy_timestamp) * 1000
            result["latency_ms"] = int(latency_ms)
            result["is_sniped"] = latency_ms < 3000
            break
    return result


def analyze_transaction_entropy_avax(contract_address: str) -> dict:
    result = {
        "total_txs": 0, "unique_amounts": 0,
        "entropy_score": 1.0, "is_bot_pattern": False,
        "dominant_amount": 0, "dominant_amount_pct": 0.0,
    }
    transfers = get_token_transfers(contract_address, limit=30)
    if not transfers:
        return result

    amounts = []
    for tx in transfers:
        try:
            decimals = int(tx.get("tokenDecimal", 18))
            amount = int(tx.get("value", "0")) / (10 ** decimals)
            if amount > 0:
                amounts.append(round(amount, 2))
        except (ValueError, TypeError):
            continue

    if not amounts:
        return result

    result["total_txs"] = len(amounts)
    counter = Counter(amounts)
    result["unique_amounts"] = len(counter)
    most_common_amount, most_common_count = counter.most_common(1)[0]
    dominant_pct = most_common_count / len(amounts)
    result["dominant_amount"] = most_common_amount
    result["dominant_amount_pct"] = round(dominant_pct * 100, 1)
    result["entropy_score"] = round(1.0 - dominant_pct, 2)
    result["is_bot_pattern"] = dominant_pct > 0.6
    return result


def detect_wash_pattern_avax(contract_address: str, deployer: str, deploy_timestamp: int) -> dict:
    result = {
        "wash_detected": False, "dev_sold_fast": False,
        "dev_sell_latency_s": -1, "linker_wallets_connected": False,
    }
    if not deployer:
        return result
    transfers = get_token_transfers(contract_address, limit=20)
    if not transfers:
        return result
    for tx in transfers:
        if tx.get("from", "").lower() == deployer.lower():
            latency = int(tx.get("timeStamp", 0)) - deploy_timestamp
            if 0 < latency < 60:
                result["dev_sold_fast"] = True
                result["dev_sell_latency_s"] = latency
                break
    deployer_txs = get_account_transactions(deployer, limit=10)
    result["linker_wallets_connected"] = len(deployer_txs) < 5
    result["wash_detected"] = result["dev_sold_fast"] and result["linker_wallets_connected"]
    return result


def analyze_holder_cluster_avax(contract_address: str) -> dict:
    result = {"avg_age_days": 0.0, "new_wallets_count": 0, "total_checked": 0, "is_bot_farm": False}
    holders = get_token_holders(contract_address)
    if not holders:
        transfers = get_token_transfers(contract_address, limit=20)
        holder_addrs = list({tx.get("to", "") for tx in transfers if tx.get("to")})[:10]
    else:
        holder_addrs = [h.get("TokenHolderAddress", "") for h in holders[:10]]

    if not holder_addrs:
        return result

    ages = []
    new_count = 0
    for addr in holder_addrs[:10]:
        if not addr:
            continue
        age = get_account_age_days_avax(addr)
        ages.append(age)
        if age < 7:
            new_count += 1
        time.sleep(0.3)

    if not ages:
        return result

    result["total_checked"] = len(ages)
    result["avg_age_days"] = round(statistics.mean(ages), 1)
    result["new_wallets_count"] = new_count
    result["is_bot_farm"] = (new_count / len(ages) > 0.7)
    return result

# ---------------------------------------------------------------------------
# CIA + V5 + V6 runner
# ---------------------------------------------------------------------------

def run_cia_analysis_avax(contract_address: str, deployer: str, deploy_timestamp: int) -> dict:
    log.info("  [CIA] Pokrenuta analiza za %s", contract_address[:12])
    intel = {}

    log.info("  [CIA] Tracing funding origin...")
    intel["funding"] = trace_funding_origin_avax(deployer, depth=3) if deployer else {}
    time.sleep(0.5)

    log.info("  [CIA] Mjerim deployment latency...")
    intel["latency"] = get_deployment_latency_avax(contract_address, deploy_timestamp)
    time.sleep(0.5)

    log.info("  [CIA] Analiziram transaction entropy...")
    intel["entropy"] = analyze_transaction_entropy_avax(contract_address)
    time.sleep(0.5)

    log.info("  [CIA] Detektujem wash pattern...")
    intel["wash"] = detect_wash_pattern_avax(contract_address, deployer, deploy_timestamp)
    time.sleep(0.5)

    log.info("  [CIA] Analiziram holder cluster...")
    intel["cluster"] = analyze_holder_cluster_avax(contract_address)

    return intel


def run_v5_analysis_avax(contract_address: str, deployer: str, deploy_timestamp: int,
                          name: str, ticker: str, tx_amounts: list, holder_count: int,
                          cia_intel: dict, creator_rug_rate: float) -> dict:
    log.info("  [V5] Pokrenuta analiza...")
    v5 = {}

    v5["cross_chain"] = detect_cross_chain_match(deploy_timestamp, tx_amounts, holder_count, "AVAX")
    if v5["cross_chain"]["cross_chain_match"]:
        log.warning("  [V5] Cross-chain match: pattern viđen na %s", v5["cross_chain"]["match_chains"])

    v5["name_style"] = analyze_name_stylometry(name, ticker)
    if v5["name_style"]["name_scam_score"] > 50:
        log.warning("  [V5] Name scam score: %d (%s)", v5["name_style"]["name_scam_score"],
                    v5["name_style"]["matched_patterns"])

    log.info("  [V5] CEX sweep detekcija...")
    v5["cex_sweep"] = detect_cex_sweep_avax(deployer, deploy_timestamp)
    if v5["cex_sweep"]["sweep_to_cex"]:
        log.warning("  [V5] CEX sweep detected -> %s", v5["cex_sweep"]["cex_destination"])

    v5["lifecycle"] = predict_lifecycle(cia_intel, creator_rug_rate)
    log.info("  [V5] Lifecycle: %s", v5["lifecycle"]["prediction_text"])

    return v5


def run_v6_analysis_avax(contract_address: str, deployer: str, deploy_timestamp: int) -> dict:
    log.info("  [V6] Pokrenuta analiza...")
    v6 = {}

    log.info("  [V6] Contract backdoor scan...")
    v6["backdoor"] = detect_contract_backdoor_avax(contract_address)
    if v6["backdoor"]["has_backdoor"]:
        log.warning("  [V6] Backdoor funkcije: %s", v6["backdoor"]["backdoor_functions"])

    log.info("  [V6] Holder concentration...")
    v6["concentration"] = analyze_holder_concentration_avax(contract_address)
    if v6["concentration"]["concentration_risk"] in ("HIGH", "CRITICAL"):
        log.warning("  [V6] Koncentracija: %s (top5=%s%%)",
                    v6["concentration"]["concentration_risk"],
                    v6["concentration"]["top5_pct"])

    log.info("  [V6] Rug velocity...")
    v6["velocity"] = calculate_rug_velocity_avax(contract_address, deploy_timestamp)
    if v6["velocity"]["is_fast_rug"]:
        log.warning("  [V6] Fast rug detected (score=%.2f)", v6["velocity"]["velocity_score"])

    return v6


def v6_success_rate(v5: dict, v6: dict) -> str:
    """Log koliko modula je vratio stvarne podatke."""
    checks = [
        v5.get("cex_sweep", {}).get("sweep_to_cex", False) is not False,
        v5.get("cross_chain", {}).get("pattern_hash", "") != "",
        v6.get("backdoor", {}).get("backdoor_risk_score", -1) >= 0,
        v6.get("concentration", {}).get("top5_pct", -1) >= 0,
        v6.get("velocity", {}).get("velocity_score", -1) >= 0,
    ]
    ok = sum(checks)
    return f"{ok}/5 V5+V6 modula OK"

# ---------------------------------------------------------------------------
# Label logika
# ---------------------------------------------------------------------------

def classify_avax_token_v6(token_info: dict, cia_intel: dict, v5: dict, v6: dict,
                             deployer_balance: float) -> tuple:
    flags = []

    funding  = cia_intel.get("funding", {})
    latency  = cia_intel.get("latency", {})
    entropy  = cia_intel.get("entropy", {})
    wash     = cia_intel.get("wash", {})
    cluster  = cia_intel.get("cluster", {})
    backdoor = v6.get("backdoor", {})
    conc     = v6.get("concentration", {})
    vel      = v6.get("velocity", {})
    sweep    = v5.get("cex_sweep", {})
    style    = v5.get("name_style", {})
    xchain   = v5.get("cross_chain", {})

    # CIA flags
    if funding.get("all_fresh"):       flags.append("Fresh funding chain")
    if funding.get("is_fresh_wallet"): flags.append("Deployer is fresh wallet")
    if latency.get("is_sniped"):       flags.append(f"Sniped in {latency.get('latency_ms')}ms")
    if entropy.get("is_bot_pattern"):  flags.append(f"Bot txs ({entropy.get('dominant_amount_pct')}% same)")
    if wash.get("wash_detected"):      flags.append("Wash trading detected")
    if wash.get("dev_sold_fast"):      flags.append(f"Dev sold in {wash.get('dev_sell_latency_s')}s")
    if cluster.get("is_bot_farm"):     flags.append(f"Bot farm ({cluster.get('new_wallets_count')}/{cluster.get('total_checked')} new)")
    if deployer_balance < 0.1:        flags.append("Near-zero deployer balance")
    if token_info.get("holders_count", 0) < 10: flags.append("Less than 10 holders")
    # V5 flags
    if sweep.get("sweep_to_cex"):      flags.append(f"CEX sweep -> {sweep.get('cex_destination')}")
    if style.get("name_scam_score", 0) > 50: flags.append(f"Scam name pattern ({style.get('matched_patterns')})")
    if xchain.get("cross_chain_match"): flags.append(f"Cross-chain scam match ({xchain.get('match_chains')})")
    # V6 flags
    if backdoor.get("has_mint_function"):  flags.append("Mint function in bytecode")
    if backdoor.get("has_drain_function"): flags.append("Withdraw/drain function")
    if backdoor.get("is_proxy"):           flags.append("Upgradeable proxy contract")
    if backdoor.get("has_blacklist"):      flags.append("Blacklist function")
    if conc.get("concentration_risk") in ("HIGH", "CRITICAL"):
        flags.append(f"High concentration (top5={conc.get('top5_pct')}%)")
    if vel.get("is_fast_rug"):             flags.append(f"Fast rug velocity ({vel.get('velocity_score')})")

    # Scoring
    danger_count = sum([
        wash.get("wash_detected", False),
        cluster.get("is_bot_farm", False),
        funding.get("all_fresh", False),
        entropy.get("is_bot_pattern", False),
        wash.get("linker_wallets_connected", False),
        backdoor.get("backdoor_risk_score", 0) >= 60,
        conc.get("concentration_risk") == "CRITICAL",
        vel.get("is_fast_rug", False),
        sweep.get("sweep_to_cex", False),
        xchain.get("cross_chain_match", False),
    ])

    if danger_count >= 5:
        return "DANGER", flags
    elif danger_count >= 3 or len(flags) >= 4:
        return "WARN", flags
    else:
        return "GOOD", flags


def risk_status_from_percent(score: int) -> str:
    if score >= 75:
        return "DANGER"
    if score >= 45:
        return "WARN"
    return "GOOD"


def calculate_rugbuster_avax_risk(
    token_info: dict,
    cia_intel: dict,
    v5: dict,
    v6: dict,
    creator_stats: dict,
    deployer_balance: float,
) -> tuple[int, list[str]]:
    funding  = cia_intel.get("funding", {})
    entropy  = cia_intel.get("entropy", {})
    wash     = cia_intel.get("wash", {})
    cluster  = cia_intel.get("cluster", {})
    backdoor = v6.get("backdoor", {})
    conc     = v6.get("concentration", {})
    vel      = v6.get("velocity", {})
    sweep    = v5.get("cex_sweep", {})
    style    = v5.get("name_style", {})
    xchain   = v5.get("cross_chain", {})

    score = 8
    reasons: list[str] = []

    def add(points: int, reason: str) -> None:
        nonlocal score
        score += points
        reasons.append(reason)

    backdoor_score = int(backdoor.get("backdoor_risk_score", 0) or 0)
    if backdoor.get("has_backdoor") or backdoor_score >= 40:
        add(min(35, max(12, backdoor_score // 2)), f"Bytecode backdoor risk {backdoor_score}/100")
    if backdoor.get("is_proxy"):
        add(18, "Upgradeable proxy contract")
    if backdoor.get("has_mint_function"):
        add(18, "Mint function in bytecode")
    if backdoor.get("has_blacklist"):
        add(12, "Blacklist function")

    top5 = float(conc.get("top5_pct", 0) or 0)
    concentration = str(conc.get("concentration_risk", "LOW")).upper()
    if concentration == "CRITICAL" or top5 >= 90:
        add(30, f"Critical holder concentration top5={top5:.1f}%")
    elif concentration == "HIGH" or top5 >= 75:
        add(22, f"High holder concentration top5={top5:.1f}%")
    elif concentration == "MEDIUM" or top5 >= 55:
        add(10, f"Moderate holder concentration top5={top5:.1f}%")

    if funding.get("all_fresh"):
        add(12, "Fresh funding chain")
    if entropy.get("is_bot_pattern"):
        add(10, "Bot-like transaction entropy")
    if wash.get("wash_detected"):
        add(18, "Wash trading pattern detected")
    if cluster.get("is_bot_farm"):
        add(15, "Bot farm holder cluster")
    velocity = float(vel.get("velocity_score", 0) or 0)
    if vel.get("is_fast_rug") or velocity >= 0.65:
        add(20, f"High rug velocity score {velocity}")

    if sweep.get("sweep_to_cex"):
        add(12, f"CEX sweep -> {sweep.get('cex_destination')}")
    if style.get("name_scam_score", 0) > 50:
        add(10, f"Scam name pattern {style.get('matched_patterns')}")
    if xchain.get("cross_chain_match"):
        add(16, f"Cross-chain scam match {xchain.get('match_chains')}")

    creator_rug_rate = float(creator_stats.get("rug_rate", 0) or 0)
    if creator_rug_rate >= 80:
        score = max(score, 88)
        reasons.append(f"Deployer history: {creator_rug_rate:.1f}% rug rate")
    elif creator_rug_rate >= 40:
        score = max(score, 72)
        reasons.append(f"Deployer history: {creator_rug_rate:.1f}% rug rate")

    holders = int(token_info.get("holders_count", 0) or 0)
    if holders and holders < 10:
        add(8, f"Very few holders ({holders})")
    if deployer_balance and deployer_balance < 0.1:
        add(6, f"Near-zero deployer balance ({deployer_balance:.4f} AVAX)")

    if not reasons:
        reasons.append("No hard Avalanche rug signals detected")

    return max(0, min(98, round(score))), reasons[:8]

# ---------------------------------------------------------------------------
# Dataset writer
# ---------------------------------------------------------------------------

def count_lines(path: Path) -> int:
    if not path.exists():
        return 0
    try:
        with path.open("rb") as f:
            return sum(1 for _ in f)
    except OSError:
        return 0


def build_training_record_v6(
    contract_address: str,
    token_info: dict,
    deployer: str,
    deploy_timestamp: int,
    creator_stats: dict,
    cia_intel: dict,
    v5: dict,
    v6: dict,
    label: str,
    risk_flags: list,
    risk_percent: int | None = None,
    avax_risk_reasons: list[str] | None = None,
) -> dict:
    funding  = cia_intel.get("funding", {})
    latency  = cia_intel.get("latency", {})
    entropy  = cia_intel.get("entropy", {})
    wash     = cia_intel.get("wash", {})
    cluster  = cia_intel.get("cluster", {})
    backdoor = v6.get("backdoor", {})
    conc     = v6.get("concentration", {})
    vel      = v6.get("velocity", {})
    sweep    = v5.get("cex_sweep", {})
    style    = v5.get("name_style", {})
    xchain   = v5.get("cross_chain", {})
    lifecycle = v5.get("lifecycle", {})
    avax_risk_reasons = avax_risk_reasons or []
    risk_percent = int(risk_percent if risk_percent is not None else 0)

    if creator_stats["total"] == 0:
        creator_risk = "NEW - no previous tokens"
    elif creator_stats["rug_rate"] >= 80:
        creator_risk = f"HIGH RISK - {creator_stats['rug_rate']}% rug rate ({creator_stats['danger']}/{creator_stats['total']})"
    elif creator_stats["rug_rate"] >= 40:
        creator_risk = f"MODERATE RISK - {creator_stats['rug_rate']}% rug rate"
    else:
        creator_risk = f"LOW RISK - {creator_stats['rug_rate']}% rug rate"

    input_text = f"""Token: {token_info.get('name', 'Unknown')} ({token_info.get('symbol', '')})
Chain: AVAX (C-Chain)
Contract: {contract_address}
Explorer: https://snowtrace.io/address/{contract_address}
Deployer: {deployer[:20] + '...' if deployer else 'Unknown'}
Total Supply: {token_info.get('total_supply', 'N/A')}
Holders: {token_info.get('holders_count', 0)}
RugBuster AVAX Risk: {risk_percent}%
Risk Flags: {', '.join(risk_flags) or 'None detected'}
Native Risk Reasons: {', '.join(avax_risk_reasons) or 'No hard Avalanche rug signals detected'}
Deployer History: {creator_risk}
--- CIA INTEL ---
Funding Origin: Master wallet traced {funding.get('hop_count', 0)} hops | All fresh: {funding.get('all_fresh', False)}
Deployment Latency: {latency.get('latency_ms', -1)}ms | Sniped: {latency.get('is_sniped', False)}
Transaction Entropy: {entropy.get('entropy_score', 1.0)} | Bot pattern: {entropy.get('is_bot_pattern', False)} | Dominant: {entropy.get('dominant_amount', 0)} tokens ({entropy.get('dominant_amount_pct', 0)}%)
Wash Pattern: {wash.get('wash_detected', False)} | Dev sold in {wash.get('dev_sell_latency_s', -1)}s | Single-use deployer: {wash.get('linker_wallets_connected', False)}
Holder Cluster: avg age {cluster.get('avg_age_days', 0)} days | New wallets: {cluster.get('new_wallets_count', 0)}/{cluster.get('total_checked', 0)} | Bot farm: {cluster.get('is_bot_farm', False)}
--- V5 INTEL ---
Cross-Chain Match: {xchain.get('cross_chain_match', False)} | Pattern: {xchain.get('pattern_hash', 'N/A')} | Chains: {xchain.get('match_chains', [])}
Name Scam Score: {style.get('name_scam_score', 0)}/100 | Patterns: {style.get('matched_patterns', [])}
CEX Sweep: {sweep.get('sweep_to_cex', False)} | Destination: {sweep.get('cex_destination', 'N/A')} | Confidence: {sweep.get('exit_pattern_confidence', 0.0)}
Lifecycle Prediction: {lifecycle.get('prediction_text', 'N/A')} | Est. rug: {lifecycle.get('estimated_rug_minutes', -1)} min
--- V6 INTEL ---
Contract Backdoor: {backdoor.get('has_backdoor', False)} | Functions: {backdoor.get('backdoor_functions', [])} | Risk score: {backdoor.get('backdoor_risk_score', 0)}/100
Proxy/Upgradeable: {backdoor.get('is_proxy', False)} | Mint function: {backdoor.get('has_mint_function', False)} | Blacklist: {backdoor.get('has_blacklist', False)}
Holder Concentration: top5={conc.get('top5_pct', 0)}% | top1={conc.get('top1_pct', 0)}% | Risk: {conc.get('concentration_risk', 'LOW')}
Rug Velocity: score={vel.get('velocity_score', 0)} | Fast rug: {vel.get('is_fast_rug', False)} | Volume decay: {vel.get('volume_decay', False)}"""

    cia_flags = f" CIA/V6 flags: {', '.join(risk_flags[:4])}." if risk_flags else ""
    native_flags = f" RugBuster AVAX Risk: {risk_percent}%. Reasons: {', '.join(avax_risk_reasons[:3])}."
    if label == "DANGER":
        output = f"DANGER - High risk AVAX token.{native_flags}{cia_flags} Deployer rug rate: {creator_stats['rug_rate']}%."
    elif label == "WARN":
        output = f"WARN - Moderate risk AVAX token.{native_flags}{cia_flags}"
    else:
        output = f"GOOD - Low risk AVAX token. RugBuster AVAX Risk: {risk_percent}%. No major red flags. Deployer history: {creator_risk}."

    return {
        "instruction": "Analyze this Avalanche (AVAX) token and classify its risk level as DANGER, WARN, or GOOD.",
        "contract_address": contract_address.lower(),
        "token_name": token_info.get("name", "Unknown"),
        "token_symbol": token_info.get("symbol", ""),
        "explorer_url": f"https://snowtrace.io/address/{contract_address}",
        "input": input_text,
        "output": output,
        "label": label,
        "chain": "AVAX",
        "risk_engine": "rugbuster_avax_v1",
        "risk_percent": risk_percent,
        "rugbuster_avax_score": risk_percent,
        "rugbuster_avax_reasons": avax_risk_reasons,
        "creator": deployer,
        "creator_rug_rate": creator_stats["rug_rate"],
        # CIA
        "cia_funding_hops": funding.get("hop_count", 0),
        "cia_all_fresh_wallets": funding.get("all_fresh", False),
        "cia_deployment_latency_ms": latency.get("latency_ms", -1),
        "cia_sniped": latency.get("is_sniped", False),
        "cia_entropy_score": entropy.get("entropy_score", 1.0),
        "cia_bot_pattern": entropy.get("is_bot_pattern", False),
        "cia_wash_detected": wash.get("wash_detected", False),
        "cia_bot_farm": cluster.get("is_bot_farm", False),
        "cia_avg_holder_age_days": cluster.get("avg_age_days", 0.0),
        # V5
        "v5_cross_chain_match": xchain.get("cross_chain_match", False),
        "v5_cross_chain_pattern": xchain.get("pattern_hash", ""),
        "v5_name_scam_score": style.get("name_scam_score", 0),
        "v5_name_patterns": style.get("matched_patterns", []),
        "v5_cex_sweep": sweep.get("sweep_to_cex", False),
        "v5_cex_destination": sweep.get("cex_destination", ""),
        "v5_lifecycle_minutes": lifecycle.get("estimated_rug_minutes", -1),
        "v5_lifecycle_confidence": lifecycle.get("confidence", 0.0),
        # V6
        "v6_has_backdoor": backdoor.get("has_backdoor", False),
        "v6_backdoor_functions": backdoor.get("backdoor_functions", []),
        "v6_backdoor_risk_score": backdoor.get("backdoor_risk_score", 0),
        "v6_is_proxy": backdoor.get("is_proxy", False),
        "v6_has_mint": backdoor.get("has_mint_function", False),
        "v6_has_blacklist": backdoor.get("has_blacklist", False),
        "v6_top5_concentration_pct": conc.get("top5_pct", 0.0),
        "v6_concentration_risk": conc.get("concentration_risk", "LOW"),
        "v6_rug_velocity_score": vel.get("velocity_score", 0.0),
        "v6_is_fast_rug": vel.get("is_fast_rug", False),
    }


def append_to_dataset(record: dict, output_path: Path) -> None:
    try:
        with output_path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(record, ensure_ascii=False) + "\n")
        log.info("  -> Snimljeno [%s][AVAX-V6] Rug rate: %s%%  Ukupno: %d",
                 record["label"], record.get("creator_rug_rate", "N/A"), count_lines(output_path))
        save_to_postgres(record)
        publish_recent_scan_feed(record)
    except OSError as e:
        log.error("Nije moguće zapisati: %s", e)


def publish_recent_scan_feed(record: dict) -> None:
    if not RECENT_SCAN_FEED_URL:
        return
    headers = {"Content-Type": "application/json"}
    if RECENT_SCAN_INGEST_TOKEN:
        headers["X-RugBuster-Feed-Token"] = RECENT_SCAN_INGEST_TOKEN
    try:
        response = requests.post(
            RECENT_SCAN_FEED_URL,
            json={"record": record},
            headers=headers,
            timeout=10,
        )
        if response.ok:
            log.info("  -> Recent scan feed updated.")
        else:
            log.warning("Recent scan feed nije prihvatio zapis: HTTP %s", response.status_code)
    except Exception as e:
        log.warning("Recent scan feed greška: %s", e)


# ---------------------------------------------------------------------------
# 24/7 automation: daily limits, EVM data writes, Telegram, markdown log
# ---------------------------------------------------------------------------

def utc_day() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d")


def avax_to_wei(amount: float) -> int:
    return int(amount * 10**18)


def wei_to_avax(amount: int) -> float:
    return amount / 10**18


def load_daily_state() -> dict:
    default = {"date": utc_day(), "tokens": 0, "avax_spent_wei": 0, "eur_spent": 0.0}
    if not AVAX_STATE_FILE.exists():
        return default
    try:
        state = json.loads(AVAX_STATE_FILE.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return default
    if state.get("date") != utc_day():
        state["date"] = utc_day()
        state["tokens"] = 0
    state.setdefault("tokens", 0)
    state.setdefault("avax_spent_wei", 0)
    state.setdefault("eur_spent", 0.0)
    return state


def save_daily_state(state: dict) -> None:
    AVAX_STATE_FILE.write_text(json.dumps(state, indent=2, sort_keys=True), encoding="utf-8")


def daily_limits_open(state: dict) -> bool:
    if int(state.get("tokens", 0)) >= MAX_TOKENS_PER_DAY:
        return False
    if int(state.get("avax_spent_wei", 0)) >= avax_to_wei(MAX_AVAX_TOTAL):
        return False
    if float(state.get("eur_spent", 0.0)) >= MAX_EUR_TOTAL:
        return False
    if datetime.now(timezone.utc).date().isoformat() > RUN_UNTIL_DATE:
        return False
    return True


def seconds_until_next_utc_day() -> int:
    now = datetime.now(timezone.utc)
    tomorrow = datetime(now.year, now.month, now.day, tzinfo=timezone.utc).timestamp() + 86400
    return max(60, int(tomorrow - now.timestamp()))


def run_until_reached() -> bool:
    return datetime.now(timezone.utc).date().isoformat() > RUN_UNTIL_DATE


def fetch_avax_eur_price() -> float:
    try:
        response = requests.get(
            "https://api.coingecko.com/api/v3/simple/price",
            params={"ids": "avalanche-2", "vs_currencies": "eur"},
            timeout=10,
        )
        response.raise_for_status()
        price = float(response.json()["avalanche-2"]["eur"])
        return price if price > 0 else AVAX_EUR_PRICE_FALLBACK
    except Exception as exc:
        log.warning("Ne mogu dohvatiti AVAX/EUR cenu, koristim fallback %.2f EUR: %s", AVAX_EUR_PRICE_FALLBACK, exc)
        return AVAX_EUR_PRICE_FALLBACK


def verdict_score(verdict: str) -> int:
    return {"GOOD": 85, "WARN": 55, "DANGER": 15}.get(str(verdict).upper(), 40)


def module_score(module: str, record: dict) -> int:
    if module == "funding_origin":
        if record.get("cia_all_fresh_wallets"):
            return 20
        if record.get("cia_funding_hops", 0) > 1:
            return 55
        return 80
    if module == "holder_concentration":
        risk = record.get("v6_concentration_risk", "LOW")
        return {"LOW": 85, "MEDIUM": 60, "HIGH": 35, "CRITICAL": 15}.get(str(risk).upper(), 45)
    if module == "backdoor_check":
        return max(0, 100 - int(record.get("v6_backdoor_risk_score", 0) or 0))
    if module == "liquidity_status":
        holders = int(record.get("v6_top5_concentration_pct", 0) or 0)
        return 35 if holders >= 80 else 65 if holders >= 50 else 75
    if module == "rug_velocity":
        velocity = float(record.get("v6_rug_velocity_score", 0) or 0)
        return max(0, min(100, int(100 - velocity)))
    return verdict_score(record.get("label", "UNKNOWN"))


def module_payloads(record: dict) -> list[dict]:
    token = record.get("contract_address", "")
    verdict = record.get("label", "UNKNOWN")
    ts = int(time.time())
    modules = [
        ("funding_origin", {
            "hops": record.get("cia_funding_hops", 0),
            "all_fresh": record.get("cia_all_fresh_wallets", False),
        }),
        ("holder_concentration", {
            "top5_pct": record.get("v6_top5_concentration_pct", 0),
            "risk": record.get("v6_concentration_risk", "UNKNOWN"),
        }),
        ("backdoor_check", {
            "has_backdoor": record.get("v6_has_backdoor", False),
            "functions": record.get("v6_backdoor_functions", []),
            "risk_score": record.get("v6_backdoor_risk_score", 0),
        }),
        ("liquidity_status", {
            "holder_count": "unknown",
            "top5_pct": record.get("v6_top5_concentration_pct", 0),
        }),
        ("rug_velocity", {
            "velocity_score": record.get("v6_rug_velocity_score", 0),
            "fast_rug": record.get("v6_is_fast_rug", False),
        }),
        ("final_verdict", {
            "flags": record.get("output", "")[:240],
            "risk_engine": record.get("risk_engine", "rugbuster_avax_v1"),
            "risk_percent": record.get("risk_percent", record.get("rugbuster_avax_score", 0)),
            "reasons": list(record.get("rugbuster_avax_reasons") or [])[:3],
        }),
    ]
    return [
        {
            "module": module,
            "token": token,
            "verdict": verdict,
            "score": module_score(module, record),
            "ts": ts,
            "evidence": evidence,
        }
        for module, evidence in modules
    ]


def stable_payload_json(payload: dict) -> str:
    return json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def apply_eip1559_fee_strategy(web3, tx: dict) -> dict:
    latest_block = web3.eth.get_block("latest")
    base_fee = int(latest_block.get("baseFeePerGas", 0) or 0)
    network_gas_price = int(web3.eth.gas_price)
    priority_fee = min(web3.to_wei(2, "gwei"), max(network_gas_price // 2, 1))
    max_fee = max(network_gas_price * 2, base_fee * 2 + priority_fee, priority_fee + 1)
    tx["maxPriorityFeePerGas"] = priority_fee
    tx["maxFeePerGas"] = max_fee
    return tx


def raw_transaction(signed_tx) -> bytes:
    return getattr(signed_tx, "raw_transaction", None) or getattr(signed_tx, "rawTransaction")


def payload_hash(payload: dict) -> bytes:
    return keccak(text=stable_payload_json(payload))


def registry_metadata_hash(payload: dict) -> bytes:
    return hashlib.sha256(stable_payload_json(payload).encode("utf-8")).digest()


def evidence_bytes(payload: dict) -> bytes:
    encoded = stable_payload_json(payload).encode("utf-8")
    if len(encoded) >= EVIDENCE_BYTES_TARGET:
        return encoded[:EVIDENCE_BYTES_TARGET]
    digest = hashlib.sha256(encoded).hexdigest().encode("ascii")
    padding = b"|rb-evidence|" + digest
    while len(encoded) < EVIDENCE_BYTES_TARGET:
        encoded += padding
    return encoded[:EVIDENCE_BYTES_TARGET]


def onchain_logging_ready() -> bool:
    if not ONCHAIN_LOG_ENABLED:
        return False
    if Web3 is None:
        log.error("web3 nije instaliran. Dodaj `web3` iz requirements.txt i pokreni pip install -r requirements.txt.")
        return False
    if not (clean_env_value("AVAX_LOG_PRIVATE_KEY") or clean_env_value("PRIVATE_KEY")):
        log.error("Nema AVAX_LOG_PRIVATE_KEY/PRIVATE_KEY u .env; on-chain upis preskočen.")
        return False
    return True


def require_private_key() -> str:
    private_key = clean_env_value("AVAX_LOG_PRIVATE_KEY") or clean_env_value("PRIVATE_KEY")
    candidate = private_key[2:] if private_key.startswith("0x") else private_key
    if len(candidate) != 64 or any(ch not in "0123456789abcdefABCDEF" for ch in candidate):
        raise RuntimeError("AVAX_LOG_PRIVATE_KEY nije validan hex private key: koristi 64 hex karaktera, bez navodnika/razmaka")
    return private_key


def checksum_env_address(value: str, name: str) -> str:
    cleaned = value.strip().strip('"').strip("'")
    if not Web3.is_address(cleaned):
        raise RuntimeError(f"{name} nije validna EVM adresa: {cleaned}")
    return Web3.to_checksum_address(cleaned)


def build_activity_logger_tx(web3, logger_contract, account_address: str, payload: dict, nonce: int) -> dict:
    token = Web3.to_checksum_address(payload["token"])
    score = max(0, min(100, int(payload.get("score") or 0)))
    fn_name = "logModuleWithEvidence" if payload.get("_with_evidence", True) else "logModule"
    if fn_name == "logModuleWithEvidence":
        return logger_contract.functions.logModuleWithEvidence(
            token,
            str(payload.get("module") or "unknown"),
            str(payload.get("verdict") or "UNKNOWN"),
            score,
            payload_hash(payload),
            evidence_bytes(payload),
        ).build_transaction(
            {
                "from": account_address,
                "nonce": nonce,
                "chainId": web3.eth.chain_id,
            }
        )
    return logger_contract.functions.logModule(
        token,
        str(payload.get("module") or "unknown"),
        str(payload.get("verdict") or "UNKNOWN"),
        score,
        payload_hash(payload),
    ).build_transaction(
        {
            "from": account_address,
            "nonce": nonce,
            "chainId": web3.eth.chain_id,
        }
    )


def build_registry_update_score_tx(web3, registry_contract, account_address: str, payload: dict, nonce: int) -> dict:
    token = Web3.to_checksum_address(payload["token"])
    score = max(0, min(100, int(payload.get("score") or 0)))
    registry_payload = {
        "module": str(payload.get("module") or "unknown"),
        "token": token,
        "verdict": str(payload.get("verdict") or "UNKNOWN"),
        "score": score,
        "ts": int(payload.get("ts") or time.time()),
        "evidence": payload.get("evidence", {}),
    }
    return registry_contract.functions.updateScore(
        token,
        score,
        registry_metadata_hash(registry_payload),
    ).build_transaction(
        {
            "from": account_address,
            "nonce": nonce,
            "chainId": web3.eth.chain_id,
        }
    )


def build_raw_data_tx(web3, account_address: str, target: str, payload: dict, nonce: int) -> dict:
    encoded = stable_payload_json(payload).encode("utf-8")
    return {
        "from": account_address,
        "to": target,
        "value": 0,
        "data": "0x" + encoded.hex(),
        "nonce": nonce,
        "chainId": web3.eth.chain_id,
        "type": 2,
    }


def apply_target_scan_fee(web3, tx: dict, modules_count: int) -> dict:
    if TARGET_AVAX_PER_SCAN <= 0 or modules_count <= 0:
        return tx
    latest_block = web3.eth.get_block("latest")
    base_fee = int(latest_block.get("baseFeePerGas", 0) or 0)
    target_wei = avax_to_wei(TARGET_AVAX_PER_SCAN) // modules_count
    target_fee_per_gas = max(int(target_wei // max(int(tx["gas"]), 1)), base_fee + 1)
    current_max_fee = int(tx.get("maxFeePerGas", 0) or 0)
    if target_fee_per_gas <= current_max_fee:
        return tx
    tx["maxFeePerGas"] = target_fee_per_gas
    tx["maxPriorityFeePerGas"] = max(target_fee_per_gas - base_fee, 1)
    return tx


def publish_module_payloads_onchain(payloads: list[dict], state: dict) -> list[dict]:
    if not onchain_logging_ready():
        return []

    web3 = Web3(Web3.HTTPProvider(AVAX_RPC, request_kwargs={"timeout": 30}))
    if not web3.is_connected():
        raise RuntimeError("Ne mogu da se povežem na AVAX RPC za on-chain logging")

    private_key = require_private_key()
    account = web3.eth.account.from_key(private_key)
    registry_contract = None
    if BOT_PUBLISH_TO_REGISTRY and REGISTRY_ADDRESS:
        registry_contract = web3.eth.contract(
            address=checksum_env_address(REGISTRY_ADDRESS, "REGISTRY_ADDRESS"),
            abi=REGISTRY_ABI,
        )
        target = checksum_env_address(REGISTRY_ADDRESS, "REGISTRY_ADDRESS")
        log.info("  [ONCHAIN] Registry mode: %s", target)
    else:
        target = ""
    logger_contract = None
    if registry_contract is None and ACTIVITY_LOGGER_ADDRESS:
        logger_contract = web3.eth.contract(
            address=checksum_env_address(ACTIVITY_LOGGER_ADDRESS, "ACTIVITY_LOGGER_ADDRESS"),
            abi=ACTIVITY_LOGGER_ABI,
        )
        target = checksum_env_address(ACTIVITY_LOGGER_ADDRESS, "ACTIVITY_LOGGER_ADDRESS")
    elif registry_contract is None:
        target = checksum_env_address(ONCHAIN_LOG_TO_ADDRESS or account.address, "ONCHAIN_LOG_TO_ADDRESS")
        log.warning("ACTIVITY_LOGGER_ADDRESS nije postavljen; koristim raw tx.data fallback na %s", target)
    next_nonce = web3.eth.get_transaction_count(account.address, "pending")
    avax_eur_price = fetch_avax_eur_price()
    total_budget_wei = avax_to_wei(MAX_AVAX_TOTAL)
    sent: list[dict] = []

    for payload in payloads:
        if registry_contract is not None:
            tx = build_registry_update_score_tx(web3, registry_contract, account.address, payload, next_nonce)
        elif logger_contract is not None:
            try:
                tx = build_activity_logger_tx(web3, logger_contract, account.address, payload, next_nonce)
            except Exception as exc:
                fallback_payload = dict(payload)
                fallback_payload["_with_evidence"] = False
                log.warning("  [ONCHAIN] evidence call nije dostupan (%s); koristim logModule fallback.", exc)
                tx = build_activity_logger_tx(web3, logger_contract, account.address, fallback_payload, next_nonce)
        else:
            tx = build_raw_data_tx(web3, account.address, target, payload, next_nonce)
        try:
            estimated_gas = int(web3.eth.estimate_gas(tx))
        except Exception as exc:
            if logger_contract is not None and payload.get("_with_evidence", True):
                fallback_payload = dict(payload)
                fallback_payload["_with_evidence"] = False
                log.warning("  [ONCHAIN] estimate za evidence call pukao (%s); koristim logModule fallback.", exc)
                tx = build_activity_logger_tx(web3, logger_contract, account.address, fallback_payload, next_nonce)
                estimated_gas = int(web3.eth.estimate_gas(tx))
            else:
                raise
        tx["gas"] = max(int(estimated_gas * 1.25), estimated_gas + 10_000)
        tx = apply_eip1559_fee_strategy(web3, tx)
        tx = apply_target_scan_fee(web3, tx, len(payloads))

        projected_cost = int(tx["gas"]) * int(tx["maxFeePerGas"])
        projected_avax = wei_to_avax(projected_cost)
        projected_eur = projected_avax * avax_eur_price
        if int(state.get("avax_spent_wei", 0)) + projected_cost > total_budget_wei:
            log.warning(
                "Ukupni AVAX limit bi bio pređen (%0.6f/%0.6f). Stajem sa tx upisom.",
                wei_to_avax(int(state.get("avax_spent_wei", 0))),
                MAX_AVAX_TOTAL,
            )
            break
        if float(state.get("eur_spent", 0.0)) + projected_eur > MAX_EUR_TOTAL:
            log.warning(
                "Ukupni EUR limit bi bio pređen (%0.2f/%0.2f EUR). Stajem sa tx upisom.",
                float(state.get("eur_spent", 0.0)),
                MAX_EUR_TOTAL,
            )
            break

        signed = account.sign_transaction(tx)
        tx_hash = web3.eth.send_raw_transaction(raw_transaction(signed))
        receipt = web3.eth.wait_for_transaction_receipt(tx_hash, timeout=180)
        actual_cost = int(receipt.gasUsed) * int(getattr(receipt, "effectiveGasPrice", 0) or tx["maxFeePerGas"])
        actual_avax = wei_to_avax(actual_cost)
        actual_eur = actual_avax * avax_eur_price
        state["avax_spent_wei"] = int(state.get("avax_spent_wei", 0)) + actual_cost
        state["eur_spent"] = round(float(state.get("eur_spent", 0.0)) + actual_eur, 6)
        save_daily_state(state)

        item = {
            "module": payload["module"],
            "tx_hash": tx_hash.hex(),
            "status": int(receipt.status),
            "gas_used": int(receipt.gasUsed),
            "avax_spent": actual_avax,
            "eur_spent": actual_eur,
            "to": target,
        }
        sent.append(item)
        log.info("  [ONCHAIN] %s -> %s gas=%s", payload["module"], item["tx_hash"], item["gas_used"])
        next_nonce += 1

    return sent


def append_markdown_scan_log(record: dict, txs: list[dict]) -> None:
    lines = [
        f"## {datetime.now(timezone.utc).isoformat()} - {record.get('contract_address')}",
        "",
        f"- Verdict: `{record.get('label')}`",
        f"- Token: `{record.get('contract_address')}`",
        f"- Output: {record.get('output', '')}",
        "- Transactions:",
    ]
    if txs:
        for item in txs:
            lines.append(
                f"  - `{item['module']}`: `{item['tx_hash']}` "
                f"(status={item['status']}, gas={item['gas_used']}, "
                f"avax={item['avax_spent']:.8f}, eur={item.get('eur_spent', 0):.4f})"
            )
    else:
        lines.append("  - no on-chain tx sent")
    lines.append("")
    with AVAX_SCAN_LOG.open("a", encoding="utf-8") as f:
        f.write("\n".join(lines))


def send_telegram_alert_avax(record: dict, txs: list[dict]) -> None:
    if not txs:
        log.warning("  [TELEGRAM] Preskočen alert: nema potvrđenih on-chain module tx hash-eva.")
        return
    if not TELEGRAM_BOT_TOKEN:
        log.info("Telegram preskočen: TELEGRAM_BOT_TOKEN/AVAX_TELEGRAM_BOT_TOKEN nije postavljen.")
        return
    token = record.get("contract_address", "")
    name = record.get("token_name") or "Unknown"
    symbol = record.get("token_symbol") or ""
    token_label = f"{name} ({symbol})" if symbol else name
    tx_lines = "\n".join(
        [f"• {item['module']}: https://snowtrace.io/tx/{item['tx_hash']}" for item in txs[:6]]
    )
    message = (
        "🛡️ RugBuster AVAX Alert\n"
        f"Token: {token_label}\n"
        f"Address: {token}\n"
        f"Verdict: {record.get('label')}\n"
        f"RugBuster AVAX Risk: {record.get('risk_percent', record.get('rugbuster_avax_score', 'UNKNOWN'))}%\n"
        f"Flags: {record.get('output', '')[:400]}\n"
        f"Explorer: https://snowtrace.io/address/{token}\n\n"
        f"On-chain module writes:\n{tx_lines}"
    )
    response = requests.post(
        f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage",
        json={"chat_id": AVAX_TELEGRAM_CHAT_ID, "text": message, "disable_web_page_preview": True},
        timeout=20,
    )
    try:
        response.raise_for_status()
        log.info("  [TELEGRAM] Alert poslat na %s", AVAX_TELEGRAM_CHAT_ID)
    except requests.RequestException as e:
        log.warning("  [TELEGRAM] Alert nije poslat: %s", e)


# ---------------------------------------------------------------------------
# Token processing
# ---------------------------------------------------------------------------

def process_token_avax(token_data: dict, output_path: Path) -> dict | None:
    contract = token_data.get("address", "").lower()
    if not contract:
        return None
    last_scan_at = float(seen_contracts.get(contract, 0.0))
    if last_scan_at and time.time() - last_scan_at < RESCAN_COOLDOWN_SECONDS:
        return None
    seen_contracts[contract] = time.time()

    deployer = token_data.get("deployer", "")
    deploy_timestamp = token_data.get("timestamp", int(time.time()))
    name = token_data.get("name", "Unknown")
    symbol = token_data.get("symbol", "")

    log.info("Novi AVAX token: %s (%s) | %s", name, symbol, contract[:12])
    time.sleep(RATE_LIMIT_DELAY)

    token_info = get_token_info_avax(contract)
    if token_info.get("name") == "Unknown" and name != "Unknown":
        token_info["name"] = name
        token_info["symbol"] = symbol
    if REQUIRE_ERC20_METADATA and not has_usable_token_metadata(token_info):
        log.info("  Preskačem contract bez ERC20 name/symbol metadata: %s", contract)
        return None

    deployer_balance = get_avax_balance(deployer) if deployer else 0.0
    log.info("  Deployer balance: %.4f AVAX", deployer_balance)

    creator_stats = get_creator_stats(deployer)
    cia_intel = run_cia_analysis_avax(contract, deployer, deploy_timestamp)

    # Podaci za V5
    tx_amounts_raw = cia_intel.get("entropy", {}).get("dominant_amount", 0)
    tx_amounts = [tx_amounts_raw] if tx_amounts_raw else []
    holder_count = cia_intel.get("cluster", {}).get("total_checked", 0)

    v5_intel = run_v5_analysis_avax(
        contract, deployer, deploy_timestamp,
        token_info.get("name", "Unknown"), token_info.get("symbol", ""),
        tx_amounts, holder_count, cia_intel, creator_stats["rug_rate"]
    )
    v6_intel = run_v6_analysis_avax(contract, deployer, deploy_timestamp)

    log.info("  [V6] %s", v6_success_rate(v5_intel, v6_intel))

    _, risk_flags = classify_avax_token_v6(token_info, cia_intel, v5_intel, v6_intel, deployer_balance)
    risk_percent, avax_risk_reasons = calculate_rugbuster_avax_risk(
        token_info,
        cia_intel,
        v5_intel,
        v6_intel,
        creator_stats,
        deployer_balance,
    )
    label = risk_status_from_percent(risk_percent)
    merged_flags = list(dict.fromkeys([*avax_risk_reasons, *risk_flags]))

    record = build_training_record_v6(
        contract, token_info, deployer, deploy_timestamp,
        creator_stats, cia_intel, v5_intel, v6_intel, label, merged_flags,
        risk_percent, avax_risk_reasons
    )
    append_to_dataset(record, output_path)
    update_creator_history(deployer, label)
    return record

# ---------------------------------------------------------------------------
# Block scanner (isti kao stara verzija)
# ---------------------------------------------------------------------------

def configured_dex_factories() -> list[str]:
    raw = clean_env_value("DEX_FACTORIES_JSON")
    if raw:
        try:
            values = json.loads(raw)
            if isinstance(values, list):
                return [str(value) for value in values if Web3 is None or Web3.is_address(str(value))]
        except json.JSONDecodeError:
            log.warning("DEX_FACTORIES_JSON nije validan JSON; koristim default factory adrese.")
    return DEFAULT_V1_DEX_FACTORIES


def configured_lb_factories() -> list[str]:
    raw = clean_env_value("LB_DEX_FACTORIES_JSON")
    if raw:
        try:
            values = json.loads(raw)
            if isinstance(values, list):
                return [str(value) for value in values if Web3 is None or Web3.is_address(str(value))]
        except json.JSONDecodeError:
            log.warning("LB_DEX_FACTORIES_JSON nije validan JSON; koristim default LFJ LB factory adrese.")
    return DEFAULT_LB_DEX_FACTORIES


def topic_to_address(topic: str) -> str:
    topic = str(topic or "")
    if topic.startswith("0x") and len(topic) >= 42:
        return "0x" + topic[-40:].lower()
    return ""


def choose_pair_token(token0: str, token1: str) -> str:
    token0 = token0.lower()
    token1 = token1.lower()
    if token0 in BASE_TOKEN_ADDRESSES and token1 not in BASE_TOKEN_ADDRESSES:
        return token1
    if token1 in BASE_TOKEN_ADDRESSES and token0 not in BASE_TOKEN_ADDRESSES:
        return token0
    return token0


def token_id_to_address(token_id: str) -> str:
    token_id = str(token_id or "")
    if "_" in token_id:
        token_id = token_id.split("_", 1)[1]
    token_id = token_id.lower()
    if token_id.startswith("0x") and len(token_id) == 42:
        return token_id
    return ""


def parse_geckoterminal_pool_tokens(data: dict, source: str) -> list[dict]:
    tokens = {}
    pools = data.get("data", [])
    included = {
        item.get("id"): item
        for item in data.get("included", [])
        if isinstance(item, dict) and item.get("type") == "token"
    }
    if not isinstance(pools, list):
        log.warning("GeckoTerminal %s nije vratio listu: %s", source, pools)
        return []

    for pool in pools:
        relationships = pool.get("relationships", {})
        base_id = relationships.get("base_token", {}).get("data", {}).get("id", "")
        quote_id = relationships.get("quote_token", {}).get("data", {}).get("id", "")
        base_addr = token_id_to_address(base_id)
        quote_addr = token_id_to_address(quote_id)
        token_addr = choose_pair_token(base_addr, quote_addr)
        if not token_addr or token_addr in BASE_TOKEN_ADDRESSES or token_addr in tokens:
            continue

        token_item = included.get(f"avax_{token_addr}") or included.get(base_id) or included.get(quote_id) or {}
        attrs = token_item.get("attributes", {})
        pool_attrs = pool.get("attributes", {})
        tokens[token_addr] = {
            "address": token_addr,
            "name": attrs.get("name") or pool_attrs.get("name", "Unknown").split("/", 1)[0].strip(),
            "symbol": attrs.get("symbol", ""),
            "deployer": "",
            "block": 0,
            "timestamp": int(time.time()),
            "pair": pool_attrs.get("address", ""),
            "source": source,
        }
        log.info(
            "  GeckoTerminal %s token: %s (%s) | token=%s pair=%s",
            source,
            tokens[token_addr]["name"],
            tokens[token_addr]["symbol"],
            token_addr[:12],
            str(tokens[token_addr]["pair"])[:12],
        )
    return list(tokens.values())


def get_geckoterminal_new_pool_tokens() -> list[dict]:
    if not GECKOTERMINAL_ENABLED:
        return []
    try:
        resp = requests.get(GECKOTERMINAL_NEW_POOLS_URL, timeout=API_TIMEOUT)
        if resp.status_code == 429:
            log.warning("GeckoTerminal new_pools rate limit; preskačem ovaj krug.")
            return []
        resp.raise_for_status()
        return parse_geckoterminal_pool_tokens(resp.json(), "new_pools")
    except Exception as e:
        log.warning("GeckoTerminal new_pools greška: %s", e)
    return []


def get_geckoterminal_top_pool_tokens() -> list[dict]:
    if not (GECKOTERMINAL_ENABLED and GECKOTERMINAL_TOP_POOLS_ENABLED):
        return []
    tokens: dict[str, dict] = {}
    page_count = max(1, min(GECKOTERMINAL_POOL_PAGES, 5))
    for page in range(1, page_count + 1):
        try:
            url = f"https://api.geckoterminal.com/api/v2/networks/avax/pools?include=base_token,quote_token&page={page}"
            resp = requests.get(url, timeout=API_TIMEOUT)
            if resp.status_code == 429:
                log.warning("GeckoTerminal pools rate limit na page=%d; stajem sa refill-om.", page)
                break
            resp.raise_for_status()
            for token in parse_geckoterminal_pool_tokens(resp.json(), f"top_pools_p{page}"):
                tokens.setdefault(token["address"], token)
            time.sleep(0.8)
        except Exception as e:
            log.warning("GeckoTerminal pools page=%d greška: %s", page, e)
            break
    return list(tokens.values())


def fetch_pair_created_logs(from_block: int, to_block: int, factories: list[str], topic: str) -> list[dict]:
    if not factories:
        return []
    payload = {
        "jsonrpc": "2.0",
        "id": 1,
        "method": "eth_getLogs",
        "params": [{
            "fromBlock": hex(from_block),
            "toBlock": hex(to_block),
            "address": factories,
            "topics": [topic],
        }],
    }
    resp = requests.post(AVAX_RPC, json=payload, timeout=RPC_TIMEOUT)
    logs = resp.json().get("result", [])
    if not isinstance(logs, list):
        log.warning("DEX pair scan nije vratio listu: %s", logs)
        return []
    return logs


def get_new_dex_pair_tokens(from_block: int, to_block: int) -> list[dict]:
    tokens = {}
    try:
        pair_logs = []
        pair_logs.extend(fetch_pair_created_logs(from_block, to_block, configured_dex_factories(), V1_PAIR_CREATED_TOPIC))
        pair_logs.extend(fetch_pair_created_logs(from_block, to_block, configured_lb_factories(), LB_PAIR_CREATED_TOPIC))
        for item in pair_logs:
            topics = item.get("topics", [])
            if len(topics) < 3:
                continue
            token0 = topic_to_address(topics[1])
            token1 = topic_to_address(topics[2])
            token_addr = choose_pair_token(token0, token1)
            if not token_addr or token_addr in tokens:
                continue
            pair_addr = ""
            data = str(item.get("data") or "")
            if data.startswith("0x") and len(data) >= 66:
                pair_addr = "0x" + data[26:66].lower()
            block_num = int(str(item.get("blockNumber", "0x0")), 16)
            token_info = get_erc20_metadata_rpc(token_addr) or {}
            tokens[token_addr] = {
                "address": token_addr,
                "name": token_info.get("name", "Unknown"),
                "symbol": token_info.get("symbol", ""),
                "deployer": str(item.get("address", "")).lower(),
                "block": block_num,
                "timestamp": int(time.time()),
                "pair": pair_addr,
                "source": "dex_pair",
            }
            log.info(
                "  Novi DEX pair token: %s (%s) | token=%s pair=%s",
                tokens[token_addr]["name"],
                tokens[token_addr]["symbol"],
                token_addr[:12],
                pair_addr[:12] if pair_addr else "unknown",
            )
    except Exception as e:
        log.warning("DEX pair scan greška: %s", e)
    return list(tokens.values())


def get_new_token_deployments(from_block: int, to_block: int = 0) -> list:
    contracts = {}
    try:
        batch = []
        for i, block_num in enumerate(range(from_block, to_block + 1)):
            batch.append({
                "jsonrpc": "2.0", "id": i,
                "method": "eth_getBlockByNumber",
                "params": [hex(block_num), True]
            })

        all_blocks = []
        for chunk_start in range(0, len(batch), 20):
            chunk = batch[chunk_start:chunk_start + 20]
            resp = requests.post(AVAX_RPC, json=chunk, timeout=30)
            results = resp.json()
            if isinstance(results, list):
                all_blocks.extend(results)
            time.sleep(0.2)

        for item in all_blocks:
            block_data = item.get("result")
            if not block_data:
                continue
            txs = block_data.get("transactions", [])
            ts = int(block_data.get("timestamp", "0x0"), 16)
            block_num = int(block_data.get("number", "0x0"), 16)

            for tx in txs:
                if tx.get("to") is None or tx.get("to") == "":
                    deployer = tx.get("from", "").lower()
                    tx_hash = tx.get("hash", "")
                    receipt_payload = {
                        "jsonrpc": "2.0", "id": 1,
                        "method": "eth_getTransactionReceipt",
                        "params": [tx_hash]
                    }
                    rec_resp = requests.post(AVAX_RPC, json=receipt_payload, timeout=RPC_TIMEOUT)
                    receipt = rec_resp.json().get("result")
                    if receipt:
                        contract_addr = receipt.get("contractAddress", "")
                        if contract_addr and contract_addr.lower() not in contracts:
                            contracts[contract_addr.lower()] = {
                                "address": contract_addr.lower(),
                                "name": "Unknown", "symbol": "",
                                "deployer": deployer,
                                "block": block_num,
                                "timestamp": ts,
                            }
                            log.info("  Nova contract: %s od %s", contract_addr[:12], deployer[:12])
                    time.sleep(0.1)

    except Exception as e:
        log.warning("RPC block scan greška: %s", e)

    return list(contracts.values())

# ---------------------------------------------------------------------------
# Polling loop
# ---------------------------------------------------------------------------

def poll_loop(output_path: Path) -> None:
    log.info("=" * 60)
    log.info("  Syndicate Avax Collector V6 — CIA + V5 + V6 Intel")
    log.info("  V5: Cross-Chain, Lifecycle, Name Stylometry, CEX Sweep")
    log.info("  V6: Bytecode Backdoor, Holder Concentration, Rug Velocity")
    log.info("  Interval     : %ds", POLL_INTERVAL)
    log.info("  Scan delay   : %d-%d min random", MIN_SCAN_DELAY_MINUTES, MAX_SCAN_DELAY_MINUTES)
    log.info(
        "  Campaign     : until %s, %d tokens/day, %.2f EUR total, %.4f AVAX total",
        RUN_UNTIL_DATE,
        MAX_TOKENS_PER_DAY,
        MAX_EUR_TOTAL,
        MAX_AVAX_TOTAL,
    )
    log.info("  Izlazni fajl : %s", output_path.absolute())
    log.info("  Tx log       : %s", AVAX_SCAN_LOG.absolute())
    log.info("  Prethodni zapisi: %d", count_lines(output_path))
    log.info("=" * 60)

    current_block = get_latest_block()
    if not current_block:
        log.error("Ne mogu dohvatiti trenutni blok.")
        return

    log.info("Start blok: %d", current_block)
    pending_tokens: list[dict] = []
    queued_contracts: set[str] = set()
    last_top_pool_refill_at = 0.0
    next_scan_at = 0.0

    def enqueue_token(token_data: dict) -> None:
        address = token_data.get("address", "").lower()
        last_scan_at = float(seen_contracts.get(address, 0.0)) if address else 0.0
        recently_scanned = last_scan_at and time.time() - last_scan_at < RESCAN_COOLDOWN_SECONDS
        if not address or recently_scanned or address in queued_contracts:
            return
        pending_tokens.append(token_data)
        queued_contracts.add(address)

    while True:
        try:
            state = load_daily_state()
            if not daily_limits_open(state):
                if run_until_reached():
                    log.warning("Campaign end reached (%s). Collector stopped.", RUN_UNTIL_DATE)
                    return
                sleep_for = seconds_until_next_utc_day()
                log.warning(
                    "Limit dostignut: tokens_today=%s/%s, avax_total=%.6f/%.6f, eur_total=%.2f/%.2f. Nastavljam za %ds.",
                    state.get("tokens", 0),
                    MAX_TOKENS_PER_DAY,
                    wei_to_avax(int(state.get("avax_spent_wei", 0))),
                    MAX_AVAX_TOTAL,
                    float(state.get("eur_spent", 0.0)),
                    MAX_EUR_TOTAL,
                    sleep_for,
                )
                time.sleep(sleep_for)
                continue

            log.info("Polling od bloka %d...", current_block)
            new_block = get_latest_block()
            log.info("Trenutni blok: %d (diff: %d)", new_block, new_block - current_block)

            if not new_block or new_block <= current_block:
                log.info("Nema novih blokova. Čekam %ds...", POLL_INTERVAL)
                time.sleep(POLL_INTERVAL)
                continue

            gecko_tokens = get_geckoterminal_new_pool_tokens()
            log.info("Nađeno %d GeckoTerminal AVAX new-pool tokena", len(gecko_tokens))
            for token_data in gecko_tokens:
                enqueue_token(token_data)

            if (
                len(pending_tokens) < GECKOTERMINAL_QUEUE_LOW_WATERMARK
                and time.time() - last_top_pool_refill_at >= GECKOTERMINAL_TOP_POOLS_COOLDOWN_SECONDS
            ):
                top_pool_tokens = get_geckoterminal_top_pool_tokens()
                last_top_pool_refill_at = time.time()
                log.info("Nađeno %d GeckoTerminal AVAX top-pool refill tokena", len(top_pool_tokens))
                for token_data in top_pool_tokens:
                    enqueue_token(token_data)

            dex_tokens = get_new_dex_pair_tokens(current_block, new_block)
            log.info("Nađeno %d novih DEX pair tokena u blokovima %d-%d",
                     len(dex_tokens), current_block, new_block)
            for token_data in dex_tokens:
                enqueue_token(token_data)

            deployments = get_new_token_deployments(current_block, new_block)
            log.info("Nađeno %d novih fallback contract deploy-eva u blokovima %d-%d",
                     len(deployments), current_block, new_block)
            for token_data in deployments:
                enqueue_token(token_data)

            now = time.time()
            if pending_tokens and now >= next_scan_at:
                token_data = pending_tokens.pop(0)
                queued_contracts.discard(token_data.get("address", "").lower())
                try:
                    state = load_daily_state()
                    if not daily_limits_open(state):
                        pending_tokens.insert(0, token_data)
                        queued_contracts.add(token_data.get("address", "").lower())
                        continue

                    record = process_token_avax(token_data, output_path)
                    if not record:
                        next_scan_at = time.time()
                        log.info("Preskočen contract bez validnog token metadata. Queue=%d.", len(pending_tokens))
                    else:
                        txs = []
                        try:
                            txs = publish_module_payloads_onchain(module_payloads(record), state)
                        except Exception as exc:
                            log.error("  [ONCHAIN] Module writes failed, alert neće biti poslat bez tx hash-eva: %s", exc)
                        append_markdown_scan_log(record, txs)
                        send_telegram_alert_avax(record, txs)
                        state = load_daily_state()
                        state["tokens"] = int(state.get("tokens", 0)) + 1
                        save_daily_state(state)

                        delay_minutes = random.randint(MIN_SCAN_DELAY_MINUTES, MAX_SCAN_DELAY_MINUTES)
                        next_scan_at = time.time() + delay_minutes * 60
                        log.info(
                            "Sledeći scan za %d min. Queue=%d, daily=%s/%s tokens, total %.6f/%.6f AVAX, %.2f/%.2f EUR.",
                            delay_minutes,
                            len(pending_tokens),
                            state.get("tokens", 0),
                            MAX_TOKENS_PER_DAY,
                            wei_to_avax(int(state.get("avax_spent_wei", 0))),
                            MAX_AVAX_TOTAL,
                            float(state.get("eur_spent", 0.0)),
                            MAX_EUR_TOTAL,
                        )
                except Exception as e:
                    log.error("Greška pri procesiranju: %s", e)
            elif pending_tokens:
                wait_left = max(0, int(next_scan_at - now))
                log.info("Queue=%d. Sledeći scan za još %ds.", len(pending_tokens), wait_left)

            current_block = new_block
            log.info("Čekam %ds...", POLL_INTERVAL)
            time.sleep(POLL_INTERVAL)

        except KeyboardInterrupt:
            raise
        except Exception as e:
            log.error("Poll loop greška: %s. Nastavljam za 30s...", e)
            time.sleep(30)

# ---------------------------------------------------------------------------
# CLI scan mode
# ---------------------------------------------------------------------------

def scan_single_avax(address: str) -> None:
    log.info("Skeniram AVAX token: %s", address)

    token_info = get_token_info_avax(address)
    deployer = ""
    deploy_timestamp = int(time.time())

    txs = get_contract_transactions(address, limit=5)
    if txs:
        first_tx = txs[0]
        deployer = first_tx.get("from", "")
        deploy_timestamp = int(first_tx.get("timeStamp", time.time()))

    deployer_balance = get_avax_balance(deployer) if deployer else 0.0
    creator_stats = get_creator_stats(deployer)
    cia_intel = run_cia_analysis_avax(address, deployer, deploy_timestamp)

    tx_amounts = [cia_intel.get("entropy", {}).get("dominant_amount", 0)]
    holder_count = cia_intel.get("cluster", {}).get("total_checked", 0)

    v5_intel = run_v5_analysis_avax(
        address, deployer, deploy_timestamp,
        token_info.get("name", "Unknown"), token_info.get("symbol", ""),
        tx_amounts, holder_count, cia_intel, creator_stats["rug_rate"]
    )
    v6_intel = run_v6_analysis_avax(address, deployer, deploy_timestamp)

    _, risk_flags = classify_avax_token_v6(token_info, cia_intel, v5_intel, v6_intel, deployer_balance)
    risk_percent, avax_risk_reasons = calculate_rugbuster_avax_risk(
        token_info,
        cia_intel,
        v5_intel,
        v6_intel,
        creator_stats,
        deployer_balance,
    )
    label = risk_status_from_percent(risk_percent)
    risk_flags = list(dict.fromkeys([*avax_risk_reasons, *risk_flags]))

    backdoor = v6_intel.get("backdoor", {})
    conc     = v6_intel.get("concentration", {})
    vel      = v6_intel.get("velocity", {})
    xchain   = v5_intel.get("cross_chain", {})
    lifecycle = v5_intel.get("lifecycle", {})

    print(f"\n{'='*60}")
    print(f"  SYNDICATE AVAX V6 SCAN")
    print(f"  Contract : {address}")
    print(f"  Token    : {token_info.get('name')} ({token_info.get('symbol')})")
    print(f"  Deployer : {deployer[:20] + '...' if deployer else 'NOT FOUND'}")
    print(f"  Balance  : {deployer_balance:.4f} AVAX")
    print(f"  Holders  : {token_info.get('holders_count', 0)}")
    print(f"  Label    : {label}")
    print(f"  RB Risk  : {risk_percent}%")
    print(f"  Flags    : {', '.join(risk_flags) or 'None'}")
    print(f"--- CIA INTEL ---")
    print(f"  Latency  : {cia_intel.get('latency', {}).get('latency_ms', -1)}ms | Sniped: {cia_intel.get('latency', {}).get('is_sniped', False)}")
    print(f"  Bot pat  : {cia_intel.get('entropy', {}).get('is_bot_pattern', False)}")
    print(f"  Wash     : {cia_intel.get('wash', {}).get('wash_detected', False)}")
    print(f"  Bot farm : {cia_intel.get('cluster', {}).get('is_bot_farm', False)}")
    print(f"  Funding  : {cia_intel.get('funding', {}).get('hop_count', 0)} hops | All fresh: {cia_intel.get('funding', {}).get('all_fresh', False)}")
    print(f"--- V5 INTEL ---")
    print(f"  Cross-chain: {xchain.get('cross_chain_match', False)} ({xchain.get('match_chains', [])})")
    print(f"  CEX sweep  : {v5_intel.get('cex_sweep', {}).get('sweep_to_cex', False)}")
    print(f"  Name score : {v5_intel.get('name_style', {}).get('name_scam_score', 0)}/100")
    print(f"  Lifecycle  : {lifecycle.get('prediction_text', 'N/A')}")
    print(f"--- V6 INTEL ---")
    print(f"  Backdoor   : {backdoor.get('has_backdoor', False)} | Functions: {backdoor.get('backdoor_functions', [])}")
    print(f"  Proxy      : {backdoor.get('is_proxy', False)} | Risk score: {backdoor.get('backdoor_risk_score', 0)}/100")
    print(f"  Top5 hold  : {conc.get('top5_pct', 0)}% ({conc.get('concentration_risk', 'LOW')})")
    print(f"  Rug vel    : {vel.get('velocity_score', 0)} | Fast rug: {vel.get('is_fast_rug', False)}")
    print(f"{'='*60}\n")

# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def main() -> None:
    import sys

    init_database()

    if len(sys.argv) > 1:
        scan_single_avax(sys.argv[1])
        return

    output_path = Path(OUTPUT_FILE)
    try:
        poll_loop(output_path)
    except KeyboardInterrupt:
        log.info("Zaustavljeno. Ukupno: %d zapisa", count_lines(output_path))
        log.info("Deployer tracking: %d unikatnih deployera", len(creator_history))


if __name__ == "__main__":
    main()
