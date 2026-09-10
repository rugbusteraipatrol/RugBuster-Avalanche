"""A gap that does not withhold the verdict is still shown, and never called blocking.

WAVAX came back GOOD with `holder_concentration` listed under
blocking_data_gaps. The engine now splits the two; this pins that the API
carries both halves to the caller and keeps them apart.
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
for sub in ("api", "scripts", "chains/avalanche"):
    sys.path.insert(0, str(ROOT / sub))

import server  # noqa: E402

WAVAX = "0xB31f66AA3C1e785363F0875A1B74E27b85FD66c7"
NOT_AN_INPUT = {"gap": "holder_concentration", "reason": "not an input to this verdict"}


def test_the_engine_split_reaches_the_report():
    report = server.report_from_remote_engine(
        WAVAX,
        {"verdict": "GOOD", "blocking_data_gaps": [], "non_blocking_data_gaps": [NOT_AN_INPUT]},
        {"token": {"name": "Wrapped AVAX", "symbol": "WAVAX"}},
    )
    assert report["blocking_data_gaps"] == []
    assert report["non_blocking_data_gaps"] == [NOT_AN_INPUT]


def test_the_projection_carries_both_halves():
    body = server.compact_score_response(
        {"address": WAVAX, "label": "GOOD", "blocking_data_gaps": [],
         "non_blocking_data_gaps": [NOT_AN_INPUT]}, "memory_cache")
    assert body["blocking_data_gaps"] == []
    assert body["non_blocking_data_gaps"] == [NOT_AN_INPUT]


def test_a_report_cached_before_the_split_still_projects():
    body = server.compact_score_response({"address": WAVAX, "label": "GOOD"}, "memory_cache")
    assert body["non_blocking_data_gaps"] == []


def test_a_withheld_verdict_has_nothing_non_blocking_to_add():
    report = server.insufficient_data_report(WAVAX, "engine unreachable")
    assert report["non_blocking_data_gaps"] == []
