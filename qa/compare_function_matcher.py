#!/usr/bin/env python3
"""Old matcher against new, on every address in the golden set.

The old rule matched substrings of a function's human-readable name. The names
lie in both directions, so this replays both rules over the same bytecode and
reports every token whose reading changed, with the functions that caused it.

Reads bytecode from the public RPC and nothing else. It does not call the
scoring engine and does not touch a RugBuster service, so it measures the
matcher and not a verdict.
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "chains" / "avalanche"))

import avax_collector_v6 as collector  # noqa: E402


def old_reading(functions: list[str]) -> dict:
    """The previous rule, kept here so the comparison is against what shipped."""
    reading = {
        "has_backdoor": bool(functions),
        "has_upgrade_authority": False,
        "has_pause_function": False,
        "has_mint_function": False,
        "has_drain_function": False,
        "has_blacklist": False,
        "is_proxy": False,
    }
    for name in functions:
        lowered = name.lower()
        if "upgradeto" in lowered or "implementation" in lowered:
            reading["is_proxy"] = True
            reading["has_upgrade_authority"] = True
        if "pause" in lowered:
            reading["has_pause_function"] = True
        if "mint" in lowered:
            reading["has_mint_function"] = True
        if "withdraw" in lowered or "drain" in lowered:
            reading["has_drain_function"] = True
        if "blacklist" in lowered:
            reading["has_blacklist"] = True
    danger = sum([
        reading["has_upgrade_authority"], reading["has_mint_function"],
        reading["has_drain_function"], reading["has_pause_function"],
        reading["has_blacklist"], reading["is_proxy"],
    ])
    reading["backdoor_risk_score"] = min(danger * 20, 100)
    return reading


FLAGS = ("has_backdoor", "has_upgrade_authority", "has_pause_function",
         "has_mint_function", "has_drain_function", "has_blacklist")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--golden", type=Path, default=ROOT / "qa" / "golden_set.yaml")
    parser.add_argument("--output", type=Path, default=ROOT / "qa" / "function_matcher_comparison.json")
    parser.add_argument("--pause", type=float, default=0.35)
    args = parser.parse_args()

    entries = yaml.safe_load(args.golden.read_text(encoding="utf-8"))["entries"]
    rows, changed, unread = [], [], []

    for index, entry in enumerate(entries, start=1):
        new = collector.detect_contract_backdoor_avax(entry["address"])
        if str(new.get("status")) != collector.STATUS_OK:
            unread.append(entry.get("symbol"))
        old = old_reading(list(new.get("backdoor_functions") or []))

        deltas = {flag: [old[flag], new[flag]] for flag in FLAGS if old[flag] != new[flag]}
        if old["backdoor_risk_score"] != new["backdoor_risk_score"]:
            deltas["backdoor_risk_score"] = [old["backdoor_risk_score"], new["backdoor_risk_score"]]

        row = {
            "symbol": entry.get("symbol"),
            "category": entry.get("category"),
            "address": entry["address"],
            "functions": list(new.get("backdoor_functions") or []),
            "powers": list(new.get("powers") or []),
            "has_owner": new.get("has_owner"),
            "status": new.get("status"),
            "changed": deltas,
        }
        rows.append(row)
        if deltas:
            changed.append(row)
        print(f"[{index:03d}/{len(entries)}] {str(entry.get('symbol'))[:12]:12s} "
              f"{'CHANGED' if deltas else 'same':8s} powers={new.get('powers')}", flush=True)
        time.sleep(args.pause)

    # A power that only ever disappears means the new rule cannot make anything
    # look worse than it did; a power that appears would need explaining.
    gained = [r["symbol"] for r in changed
              if any(before is False and after is True for before, after in r["changed"].values()
                     if isinstance(before, bool))]

    args.output.write_text(json.dumps({
        "entries": len(entries),
        "changed": len(changed),
        "bytecode_not_read": unread,
        "tokens_that_gained_a_power": gained,
        "results": rows,
    }, indent=2) + "\n", encoding="utf-8")

    print(f"\n{len(changed)} of {len(entries)} readings changed")
    print(f"tokens that gained a power under the new rule: {gained or 'none'}")
    print(f"bytecode could not be read for: {unread or 'none'}")
    print(f"saved={args.output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
