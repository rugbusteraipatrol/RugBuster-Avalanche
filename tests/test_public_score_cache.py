"""The public /score endpoint must serve repeats from cache.

It advertises "cache-first" on the Builder API page, but the handler used to
write the cache and never read it, so every call paid the full ~6s recompute
and occasionally timed out. These tests pin the read path.
"""

from __future__ import annotations

import importlib
from unittest import mock

import pytest


@pytest.fixture()
def server(monkeypatch):
    monkeypatch.setenv("RUGBUSTER_NETWORK", "mainnet")
    import api.server as srv
    importlib.reload(srv)
    srv.SCAN_CACHE.clear()
    srv.app.config.update(TESTING=True)
    return srv


VALID = "0xb31f66aa3c1e785363f0875a1b74e27b85fd66c7"  # WAVAX
REPORT = {
    "source": "private_scoring_engine",
    "label": "GOOD",
    "rug_risk": {"score": 12},
    "market_liquidity_risk": {"score": 6},
}


def test_second_call_is_served_from_cache_without_recomputing(server):
    calls = {"n": 0}

    def fake_score(address):
        calls["n"] += 1
        return dict(REPORT)

    with mock.patch.object(server, "score_with_private_engine", side_effect=fake_score):
        client = server.app.test_client()
        first = client.get(f"/score?address={VALID}")
        second = client.get(f"/score?address={VALID}")

    assert first.status_code == 200 and second.status_code == 200
    assert first.get_json()["label"] == "GOOD"
    assert second.get_json()["label"] == "GOOD"
    assert calls["n"] == 1, "second call recomputed instead of using the cache"


def test_fresh_param_forces_a_recompute(server):
    calls = {"n": 0}

    def fake_score(address):
        calls["n"] += 1
        return dict(REPORT)

    with mock.patch.object(server, "score_with_private_engine", side_effect=fake_score):
        client = server.app.test_client()
        client.get(f"/score?address={VALID}")
        client.get(f"/score?address={VALID}&fresh=1")

    assert calls["n"] == 2, "fresh=1 should bypass the cache"


def test_expired_cache_entry_recomputes(server):
    calls = {"n": 0}

    def fake_score(address):
        calls["n"] += 1
        return dict(REPORT)

    with mock.patch.object(server, "score_with_private_engine", side_effect=fake_score):
        client = server.app.test_client()
        client.get(f"/score?address={VALID}")
        # age the single cache entry past its TTL
        for entry in server.SCAN_CACHE.values():
            entry["ts"] -= server.SCAN_CACHE_TTL_SECONDS + 1
        client.get(f"/score?address={VALID}")

    assert calls["n"] == 2, "an expired entry must not be served"
