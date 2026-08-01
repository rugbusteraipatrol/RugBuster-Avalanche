#!/usr/bin/env python3
"""RugBuster Avalanche shield-demo regression harness.

Replays golden_set.yaml against the live /score endpoint and enforces the
gate invariants agreed with QA:

  I1. Every blue-chip / canonical asset must resolve to GOOD.
  I2. When the private scoring engine is unreachable or unconfigured, the
      API must never return GOOD (fallback must degrade to INSUFFICIENT_DATA).
  I3. At least `scam_min_pass_fraction` of known-scam addresses must resolve
      to WARN or DANGER, and none may resolve to GOOD.
  I4. Every response must report source == required_source (no silent
      fallback to a stale/local scorer without saying so).

Usage:
    python run_regression.py                      # live golden-set check
    python run_regression.py --golden other.yaml   # use a different file
    python run_regression.py --test-local-fallback # also spin up a local
                                                     # server with a broken
                                                     # SCORING_ENGINE_URL and
                                                     # assert I2 holds (requires
                                                     # repo checkout + deps)

Exit code 0 = all gates green. Exit code 1 = at least one gate failed.
Advisory-only findings (e.g. a brand-new dust-liquidity token scoring GOOD)
are printed as WARN but do not fail the run.
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
import time
from pathlib import Path

import requests
import yaml

DEFAULT_GOLDEN = Path(__file__).resolve().parent / "golden_set.yaml"


def load_golden(path: Path) -> dict:
    with open(path, "r", encoding="utf-8") as f:
        return yaml.safe_load(f)


def call_score(endpoint: str, address: str, timeout: float = 30) -> dict:
    try:
        r = requests.get(endpoint, params={"address": address}, timeout=timeout)
        try:
            body = r.json()
        except Exception:
            return {"ok": False, "http_status": r.status_code, "error": f"non-JSON response: {r.text[:200]}"}
        body["http_status"] = r.status_code
        return body
    except Exception as exc:
        return {"ok": False, "http_status": None, "error": str(exc)}


def evaluate_entry(entry: dict, response: dict, required_source: str) -> dict:
    label = response.get("label")
    source = response.get("source")
    expected = entry.get("expected_labels")
    forbidden = entry.get("forbidden_labels") or []
    is_gate = bool(entry.get("gate"))
    is_advisory = bool(entry.get("advisory"))

    problems = []
    if not response.get("ok"):
        problems.append(f"request failed: {response.get('error')}")
    if expected and label not in expected:
        problems.append(f"label={label} not in expected {expected}")
    if label in forbidden:
        problems.append(f"label={label} is forbidden ({forbidden})")
    if response.get("ok") and source != required_source:
        problems.append(f"source={source!r} != required {required_source!r}")

    if not problems:
        status = "PASS"
    elif is_advisory and not is_gate:
        status = "WARN"
    else:
        status = "FAIL" if is_gate else "WARN"

    return {
        "symbol": entry["symbol"],
        "address": entry["address"],
        "category": entry["category"],
        "label": label,
        "score": response.get("rug_score"),
        "source": source,
        "status": status,
        "problems": problems,
        "gate": is_gate,
    }


def run_golden_set(golden_path: Path) -> int:
    doc = load_golden(golden_path)
    endpoint = doc["endpoint"]
    required_source = doc["required_source"]
    gates_cfg = doc.get("gates", {})
    entries = doc["entries"]

    print(f"Endpoint: {endpoint}")
    print(f"Entries:  {len(entries)}")
    print(f"Required source: {required_source}\n")

    results = []
    for i, entry in enumerate(entries, 1):
        resp = call_score(endpoint, entry["address"])
        result = evaluate_entry(entry, resp, required_source)
        results.append(result)
        marker = {"PASS": "  ", "WARN": "! ", "FAIL": "X "}[result["status"]]
        print(f"{marker}[{i:02d}/{len(entries)}] {result['category']:15s} {result['symbol']:14s} "
              f"label={result['label']!s:18s} score={result['score']!s:6s} source={result['source']}"
              + (f"  <-- {'; '.join(result['problems'])}" if result["problems"] else ""))
        time.sleep(0.4)

    print("\n" + "=" * 70)
    print("GATE SUMMARY")
    print("=" * 70)

    overall_pass = True

    bluechip = [r for r in results if r["category"] == "bluechip"]
    bc_fail = [r for r in bluechip if r["status"] == "FAIL"]
    ok = not bc_fail
    overall_pass &= ok
    print(f"[{'PASS' if ok else 'FAIL'}] I1 blue-chip all GOOD: {len(bluechip) - len(bc_fail)}/{len(bluechip)}")
    for r in bc_fail:
        print(f"        - {r['symbol']} ({r['address']}): {'; '.join(r['problems'])}")

    ref = [r for r in results if r["category"] == "reference_case"]
    ref_fail = [r for r in ref if r["status"] == "FAIL"]
    ok = not ref_fail
    overall_pass &= ok
    print(f"[{'PASS' if ok else 'FAIL'}] I2b reference cases (e.g. BITS) never GOOD: {len(ref) - len(ref_fail)}/{len(ref)}")
    for r in ref_fail:
        print(f"        - {r['symbol']} ({r['address']}): {'; '.join(r['problems'])}")

    scam = [r for r in results if r["category"] == "scam"]
    scam_ok = [r for r in scam if r["label"] in ("WARN", "DANGER")]
    scam_good = [r for r in scam if r["label"] == "GOOD"]
    min_fraction = gates_cfg.get("scam_min_pass_fraction", 0.8)
    frac = len(scam_ok) / len(scam) if scam else 1.0
    ok = frac >= min_fraction and not scam_good
    overall_pass &= ok
    print(f"[{'PASS' if ok else 'FAIL'}] I3 scam detection: {len(scam_ok)}/{len(scam)} WARN|DANGER "
          f"(min {min_fraction:.0%}), {len(scam_good)} false-GOOD")
    for r in scam_good:
        print(f"        - FALSE GOOD: {r['symbol']} ({r['address']})")

    src_fail = [r for r in results if r["status"] != "FAIL" and r["source"] != required_source]
    # only count entries whose request actually succeeded
    src_fail = [r for r in src_fail if r["label"] is not None]
    ok = not src_fail
    overall_pass &= ok
    print(f"[{'PASS' if ok else 'FAIL'}] I4 source == {required_source!r} for all: "
          f"{len(results) - len(src_fail)}/{len(results)}")
    for r in src_fail:
        print(f"        - {r['symbol']}: source={r['source']}")

    new_lowliq_warn = [r for r in results if r["category"] == "new_lowliq" and r["status"] == "WARN"]
    if new_lowliq_warn:
        print(f"[ADVISORY] {len(new_lowliq_warn)} new/low-liquidity tokens scored GOOD "
              f"(not a hard gate, but worth a human look):")
        for r in new_lowliq_warn:
            print(f"        - {r['symbol']} ({r['address']})")

    print("\n" + "=" * 70)
    if overall_pass:
        print("RESULT: GREEN — all gates pass")
    else:
        print("RESULT: RED — one or more gates failed")
    print("=" * 70)

    return 0 if overall_pass else 1


def run_local_fallback_test(repo_root: Path) -> int:
    """Spin up the Flask app locally with an unreachable scoring engine URL
    and assert that a canonical asset (WAVAX) never resolves to GOOD."""

    import os
    import socket

    port = 8788
    env = os.environ.copy()
    env.update({
        "RUGBUSTER_NETWORK": "mainnet",
        "USE_REMOTE_SCORING_ENGINE": "true",
        "SCORING_ENGINE_URL": "http://127.0.0.1:9",
        "SCORING_ENGINE_HMAC_SECRET": "local-fallback-test-secret",
        "SCORING_ENGINE_TIMEOUT_SECONDS": "3",
        "DATABASE_URL": "",
        "PORT": str(port),
        "RUGBUSTER_API_HOST": "127.0.0.1",
    })

    print(f"Starting local server (repo: {repo_root}) with unreachable SCORING_ENGINE_URL ...")
    proc = subprocess.Popen(
        [sys.executable, "api/server.py"],
        cwd=str(repo_root),
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
    )
    try:
        deadline = time.time() + 15
        up = False
        while time.time() < deadline:
            try:
                with socket.create_connection(("127.0.0.1", port), timeout=1):
                    up = True
                    break
            except OSError:
                time.sleep(0.5)
        if not up:
            print("FAIL: local server did not come up in time")
            return 1

        wavax = "0xb31f66aa3c1e785363f0875a1b74e27b85fd66c7"
        resp = call_score(f"http://127.0.0.1:{port}/score", wavax)
        label = resp.get("label")
        source = resp.get("source")
        print(f"WAVAX with engine unreachable -> label={label} source={source}")

        if label == "GOOD":
            print("FAIL: I2 VIOLATED — fallback returned GOOD with engine unreachable")
            return 1
        if label != "INSUFFICIENT_DATA":
            print(f"WARN: expected INSUFFICIENT_DATA, got {label} (not GOOD, so I2 not violated, but check logic)")
            return 0
        print("PASS: I2 holds — engine-unreachable fallback correctly returns INSUFFICIENT_DATA, never GOOD")
        return 0
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            proc.kill()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--golden", type=Path, default=DEFAULT_GOLDEN, help="Path to golden_set.yaml")
    parser.add_argument("--test-local-fallback", action="store_true",
                         help="Also spin up a local server with a broken engine URL to verify I2")
    parser.add_argument("--repo-root", type=Path, default=None,
                         help="RugBuster-Avalanche repo root (required for --test-local-fallback)")
    args = parser.parse_args()

    rc = run_golden_set(args.golden)

    if args.test_local_fallback:
        if not args.repo_root:
            print("\n--repo-root is required for --test-local-fallback", file=sys.stderr)
            return 1
        print("\n" + "=" * 70)
        print("LOCAL FALLBACK TEST (invariant I2)")
        print("=" * 70)
        rc2 = run_local_fallback_test(args.repo_root)
        rc = rc or rc2

    return rc


if __name__ == "__main__":
    sys.exit(main())
