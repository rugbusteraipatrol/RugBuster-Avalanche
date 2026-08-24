from chains.avalanche import avax_collector_v6 as collector


class FakeResponse:
    def __init__(self, bytecode):
        self.bytecode = bytecode

    def json(self):
        return {"result": self.bytecode}


def detect_from_selectors(monkeypatch, selectors):
    bytecode = "0x6000" + "".join(selectors) + "00"

    def fake_post(*_args, **_kwargs):
        return FakeResponse(bytecode)

    monkeypatch.setattr(collector.requests, "post", fake_post)
    return collector.detect_contract_backdoor_avax("0x0000000000000000000000000000000000000001")


def test_burn_uint256_is_not_a_backdoor_by_itself(monkeypatch):
    result = detect_from_selectors(monkeypatch, ["42966c68"])

    assert result["has_backdoor"] is False
    assert result["has_drain_function"] is False
    assert result["backdoor_risk_score"] == 0
    assert "burn(uint256)" in result["detected_capabilities"]
    assert result["backdoor_confidence"] == "LOW"


def test_wrapped_native_withdraw_uint256_is_not_a_drain_by_selector_alone(monkeypatch):
    result = detect_from_selectors(monkeypatch, ["2e1a7d4d"])

    assert result["has_backdoor"] is False
    assert result["has_drain_function"] is False
    assert result["backdoor_risk_score"] == 0
    assert "withdraw(uint256)" in result["detected_capabilities"]
    assert result["dangerous_combinations"] == []


def test_proxy_upgradeability_is_governance_risk_not_confirmed_backdoor(monkeypatch):
    result = detect_from_selectors(monkeypatch, ["3659cfe6", "4f1ef286", "5c60da1b"])

    assert result["is_proxy"] is True
    assert result["has_upgrade_authority"] is True
    assert result["has_backdoor"] is False
    assert result["backdoor_confidence"] == "LOW"
    assert result["backdoor_risk_score"] < 40
    assert set(result["privileged_functions"]) == {
        "upgradeTo(address)",
        "upgradeToAndCall(address,bytes)",
        "implementation()",
    }


def test_benchmark_safe_rows_do_not_mark_standard_capabilities_as_backdoors(monkeypatch):
    fixtures = {
        "WAVAX": ["2e1a7d4d"],
        "USDC": ["3659cfe6", "4f1ef286", "5c60da1b"],
        "JOE": ["8da5cb5b", "f2fde38b", "715018a6", "40c10f19"],
        "sAVAX": ["3659cfe6", "4f1ef286", "5c60da1b"],
        "GMX": ["40c10f19"],
        "COQ": ["8da5cb5b", "f2fde38b", "715018a6"],
        "BTCBR": ["42966c68"],
    }

    for symbol, selectors in fixtures.items():
        result = detect_from_selectors(monkeypatch, selectors)
        assert result["has_backdoor"] is False, symbol
        assert result["backdoor_confidence"] in {"LOW", "MEDIUM"}, symbol
        assert "detected_capabilities" in result, symbol
        assert "privileged_functions" in result, symbol
        assert "dangerous_combinations" in result, symbol
