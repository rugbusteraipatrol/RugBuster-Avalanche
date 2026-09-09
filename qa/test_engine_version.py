"""Changing the local scoring rules must change the local engine version.

`cache_key` is version-scoped, so a cached verdict is only reused while the
versions in that key hold. Until now the key carried `DATA_CONTRACT_VERSION`
alone, which describes the *shape* of a response, not the rules that produce
the numbers in it. Editing `risk_engine.py` therefore changed the scores while
the key stayed identical, and the cache kept serving the previous rules'
verdicts for the rest of their window.

`LOCAL_ENGINE_VERSION` now sits in the key alongside it, and this file pins that
version against the contents of the file it describes.

The whole module is hashed rather than a list of functions. The Solana
equivalent started with a curated list and an independent review broke it in one
line: `rugcheck_to_risk` delegated to `_linear`, `_linear` was not on the list,
and changing it moved the verdict while the fingerprint stood still. A list only
covers what someone remembered; the risk is the change nobody thought about.

Cosmetic edits trip this too. That is the intended trade -- being asked "did
this change a score?" about a comment is cheap, and not being asked about a real
change is what this prevents.
"""

from __future__ import annotations

import hashlib
import json
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "chains" / "avalanche"))

import risk_engine  # noqa: E402

ENGINE_FILE = REPO_ROOT / "chains" / "avalanche" / "risk_engine.py"

# Bump together with LOCAL_ENGINE_VERSION. Take the new value from the failure
# message after reviewing the diff, never from a passing run of an unreviewed
# change.
EXPECTED_VERSION = "2026.09.3"
EXPECTED_FINGERPRINT = "25e1b91e57ed1bde"


def fingerprint_of(source: str) -> str:
    """Hash of the scoring rules, normalised for line endings only."""
    return hashlib.sha256(source.replace("\r\n", "\n").encode("utf-8")).hexdigest()[:16]


def engine_fingerprint() -> str:
    return fingerprint_of(ENGINE_FILE.read_text(encoding="utf-8"))


def test_local_engine_version_matches_the_scoring_rules():
    actual = engine_fingerprint()

    if EXPECTED_FINGERPRINT == "PLACEHOLDER":
        raise AssertionError(
            "Engine fingerprint is not pinned yet.\n"
            f"  Set EXPECTED_FINGERPRINT = {actual!r}\n"
            f"  alongside LOCAL_ENGINE_VERSION {risk_engine.LOCAL_ENGINE_VERSION!r}."
        )

    assert actual == EXPECTED_FINGERPRINT, (
        "risk_engine.py changed but LOCAL_ENGINE_VERSION did not.\n"
        f"  LOCAL_ENGINE_VERSION is still {risk_engine.LOCAL_ENGINE_VERSION!r}.\n"
        f"  Expected fingerprint {EXPECTED_FINGERPRINT!r}, got {actual!r}.\n"
        "\n"
        "The score cache is keyed on this version. Leaving it alone keeps\n"
        "verdicts produced by the previous rules valid for the rest of their\n"
        "window.\n"
        "\n"
        "If the edit cannot change a score, update the fingerprint alone.\n"
        "If it can, bump LOCAL_ENGINE_VERSION and update both, in one commit."
    )


def test_the_pinned_version_is_the_running_one():
    assert risk_engine.LOCAL_ENGINE_VERSION == EXPECTED_VERSION


def test_a_change_to_a_transitive_helper_is_detected(tmp_path):
    """Mutates a copy on disk, since the fingerprint reads file content.

    A runtime monkeypatch is deliberately not detected: the pin describes what
    shipped, not what a test patched into memory.
    """
    source = ENGINE_FILE.read_text(encoding="utf-8")
    assert "def clamp(" in source, "helper renamed; update this test"
    mutated = source.replace("def clamp(", "def clamp_renamed_by_mutation_test(", 1)
    (tmp_path / "risk_engine.py").write_text(mutated, encoding="utf-8")
    assert fingerprint_of(mutated) != engine_fingerprint(), (
        "Changing a transitive helper left the fingerprint unchanged."
    )


def test_a_change_to_a_scoring_threshold_is_detected():
    source = ENGINE_FILE.read_text(encoding="utf-8")
    assert "creator_prior_danger_rate >= 80" in source, "threshold changed; update this test"
    mutated = source.replace("creator_prior_danger_rate >= 80", "creator_prior_danger_rate >= 60", 1)
    assert fingerprint_of(mutated) != engine_fingerprint()


def test_the_fingerprint_is_stable_across_line_endings():
    source = ENGINE_FILE.read_text(encoding="utf-8")
    assert fingerprint_of(source.replace("\n", "\r\n")) == fingerprint_of(source)


def test_the_cache_key_carries_both_versions():
    """The defect this file exists for: a version-scoped key that could not see
    a scoring change."""
    sys.path.insert(0, str(REPO_ROOT / "api"))
    import server  # noqa: E402

    key = server.cache_key("0xb31f66aa3c1e785363f0875a1b74e27b85fd66c7")
    assert server.DATA_CONTRACT_VERSION in key
    assert risk_engine.LOCAL_ENGINE_VERSION in key


# --- the matcher is scoring input and was outside every gate ----------------
#
# `detect_contract_backdoor_avax` feeds v6_backdoor_risk_score to the engine,
# and it lives in the collector, which no fingerprint covered. A change to what
# a function is taken to mean could land without any version moving, which is
# exactly how the substring rule survived long enough to report Wrapped AVAX as
# holding a drain function.

FUNCTION_TABLE_FINGERPRINT = "8f1862e73df381c2"


def function_table_fingerprint() -> str:
    sys.path.insert(0, str(REPO_ROOT / "chains" / "avalanche"))
    import avax_collector_v6 as collector  # noqa: E402

    canonical = json.dumps(
        {
            "signatures": {sig: [name, power]
                           for sig, (name, power) in sorted(collector.FUNCTION_SIGNATURES.items())},
            "power_fields": dict(sorted(collector.POWER_FIELDS.items())),
            "proxy_markers": sorted(collector.PROXY_MARKERS),
            "ownership_markers": sorted(collector.OWNERSHIP_MARKERS),
        },
        sort_keys=True,
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()[:16]


def test_what_a_function_is_taken_to_mean_cannot_change_silently():
    assert function_table_fingerprint() == FUNCTION_TABLE_FINGERPRINT, (
        "The function table changed. Review the diff, then update "
        "FUNCTION_TABLE_FINGERPRINT and bump LOCAL_ENGINE_VERSION together -- "
        "this table is scoring input."
    )


def test_no_selector_is_listed_twice_under_different_names():
    sys.path.insert(0, str(REPO_ROOT / "chains" / "avalanche"))
    import avax_collector_v6 as collector  # noqa: E402

    names = [name for name, _power in collector.FUNCTION_SIGNATURES.values()]
    assert len(names) == len(set(names))
