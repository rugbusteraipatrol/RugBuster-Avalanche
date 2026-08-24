import requests

from api import server


WAVAX = "0xb31f66aa3c1e785363f0875a1b74e27b85fd66c7"


def test_private_engine_timeout_returns_json_insufficient_data(monkeypatch):
    def timeout_score(_address):
        raise requests.Timeout("controlled timeout")

    monkeypatch.setattr(server, "score_with_private_engine", timeout_score)

    response = server.app.test_client().get(f"/score?address={WAVAX}")
    body = response.get_json()

    assert response.status_code == 200
    assert response.is_json
    assert body["ok"] is True
    assert body["label"] == "INSUFFICIENT_DATA"
    assert body["rug_status"] == "INSUFFICIENT_DATA"
    assert any("Private scoring engine failed" in reason for reason in body["risk_flags"])


def test_public_score_never_falls_back_to_html_500_after_scoring(monkeypatch):
    def unavailable_score(address):
        return server.insufficient_data_report(address, "controlled upstream failure")

    def broken_cache_write(_address, _report):
        raise RuntimeError("cache write failed")

    monkeypatch.setattr(server, "score_with_private_engine", unavailable_score)
    monkeypatch.setattr(server, "put_cached_report", broken_cache_write)

    response = server.app.test_client().get(f"/score?address={WAVAX}")
    body = response.get_json()

    assert response.status_code == 200
    assert response.is_json
    assert body["label"] == "INSUFFICIENT_DATA"
    assert body["source"] == "remote_unavailable_insufficient_data"


def test_remote_identity_risk_is_exposed_in_compact_score_response():
    identity_risk = {
        "status": "HIGH",
        "confidence": "HIGH",
        "confusable_with": "USDC",
        "official_contract_match": False,
        "reasons": [{"code": "identity_protected_symbol_confusable", "detail": "Protected symbol mismatch"}],
    }
    report = server.report_from_remote_engine(
        "0x8e53ad52980478794bb5b459b7cbdd836975e4cb",
        {
            "verdict": "DANGER",
            "risk_score": 90,
            "risk_factors": [{"code": "identity_protected_symbol_confusable", "detail": "Protected symbol mismatch"}],
            "rug_risk": {"score": 12, "status": "LOW", "reasons": []},
            "market_liquidity_risk": {"score": 20, "status": "LOW", "reasons": []},
            "identity_risk": identity_risk,
            "confidence": {"level": "NORMAL"},
        },
        {
            "token": {
                "name": "U\u0301SD\u0421",
                "symbol": "U\u0301SD\u0421",
                "has_liquidity_evidence": True,
            },
            "cia": {},
            "v5": {},
            "v6": {},
            "creator_stats": {},
            "deployer": None,
            "deployer_balance": None,
        },
    )

    payload = server.compact_score_response(report, "private_scoring_engine")

    assert payload["identity_risk"] == identity_risk
    assert payload["risk_flags"] == ["Protected symbol mismatch"]
