"""The public API must not answer one visitor at a time.

Measured 2026-09-11 against production: gunicorn ran with its defaults, one
sync worker and a 30-second timeout. Four /score requests sent together took
6, 11, 17 and 24 seconds -- each waited for the one before. Across a 113-token
comparison the mean was 39.8 s and the slowest 81.9 s, and the logs showed
WORKER TIMEOUT, which also restarts the worker and empties the in-memory
score cache.

A score is mostly waiting on upstream reads, so threads carry the load. One
worker is kept so SCAN_CACHE and the holder/creator caches stay shared.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]


def _web_command() -> str:
    for line in (ROOT / "Procfile").read_text(encoding="utf-8").splitlines():
        if line.startswith("web:"):
            return line
    pytest.fail("Procfile has no web process")


def _option(command: str, name: str) -> str | None:
    match = re.search(rf"--{name}[ =](\S+)", command)
    return match.group(1) if match else None


def test_web_uses_threads():
    command = _web_command()
    assert _option(command, "worker-class") == "gthread"
    assert int(_option(command, "threads") or 1) >= 4


def test_web_keeps_one_worker_so_caches_are_shared():
    assert int(_option(_web_command(), "workers") or 1) == 1


def test_timeout_outlasts_a_slow_score():
    # dexscreener 20 s + holder intel 8 s + engine 8 s + contract reads; the
    # default 30 s killed scores that would have finished.
    assert int(_option(_web_command(), "timeout") or 30) >= 90
