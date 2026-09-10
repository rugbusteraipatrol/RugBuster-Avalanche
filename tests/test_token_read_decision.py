"""Only an address with no code is ever called NOT_A_TOKEN.

A failed read is not evidence about the contract, however many times it
fails. Review asked for four cases by name; each is here, followed by the one
reading that does justify NOT_A_TOKEN and the ones that must not.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
for sub in ("api", "scripts", "chains/avalanche"):
    sys.path.insert(0, str(ROOT / sub))

import server  # noqa: E402
from web3 import Web3  # noqa: E402

ADDRESS = Web3.to_checksum_address("0x00000000000000000000000000000000000000aa")


class _Field:
    """One ERC-20 call. Each attempt takes the next outcome; the last repeats."""

    def __init__(self, outcomes):
        self.outcomes = list(outcomes)

    def call(self):
        outcome = self.outcomes.pop(0) if len(self.outcomes) > 1 else self.outcomes[0]
        if isinstance(outcome, BaseException):
            raise outcome
        return outcome


def _chain(code=b"\x60\x80", code_error=None, **fields):
    handles = {name: _Field(outcomes) for name, outcomes in fields.items()}

    class _Functions:
        pass

    for name, handle in handles.items():
        setattr(_Functions, name, staticmethod(lambda handle=handle: handle))

    class _Token:
        functions = _Functions

    class _Eth:
        @staticmethod
        def get_code(_address):
            if code_error:
                raise code_error
            return code

        @staticmethod
        def contract(address=None, abi=None):
            return _Token

    class _Web3:
        eth = _Eth

    return _Web3


@pytest.fixture(autouse=True)
def _no_retry_pause(monkeypatch):
    monkeypatch.setattr(server.time, "sleep", lambda _seconds: None)


def _decide(web3):
    metadata = server.get_onchain_metadata(web3, ADDRESS)
    return metadata, server.token_read_decision(metadata)


# --- the four cases review named ---------------------------------------------

def test_a_transport_timeout_on_both_attempts_is_unavailable():
    timeout = TimeoutError("read timed out")
    metadata, (decision, reason) = _decide(_chain(
        name=[timeout], symbol=[timeout], decimals=[timeout], totalSupply=[timeout]))
    assert decision == "unavailable"
    assert "TimeoutError" in reason
    assert metadata["read_failed"] is True


def test_name_and_symbol_read_but_decimals_and_supply_failing_is_unavailable():
    failure = ConnectionError("throttled")
    _metadata, (decision, reason) = _decide(_chain(
        name=["Some Token"], symbol=["SOME"], decimals=[failure], totalSupply=[failure]))
    assert decision == "unavailable"
    assert "decimals=ConnectionError" in reason


def test_a_first_attempt_that_fails_and_a_second_that_succeeds_is_a_token():
    failure = ConnectionError("throttled")
    metadata, (decision, _reason) = _decide(_chain(
        name=[failure, "Some Token"], symbol=[failure, "SOME"],
        decimals=[failure, 18], totalSupply=[failure, 10**24]))
    assert decision == "token"
    assert metadata["read_failed"] is False
    assert metadata["decimals"] == 18


def test_zero_decimals_and_zero_supply_are_readings_not_absences():
    """0 is a value. A falsy check would read it as missing."""
    metadata, (decision, _reason) = _decide(_chain(
        name=["Zero"], symbol=["ZERO"], decimals=[0], totalSupply=[0]))
    assert decision == "token"
    assert metadata["decimals"] == 0
    assert metadata["total_supply"] == 0
    assert metadata["read_failed"] is False


# --- the only evidence that justifies NOT_A_TOKEN ----------------------------

def test_no_code_at_the_address_is_the_evidence():
    _metadata, (decision, evidence) = _decide(_chain(
        code=b"", name=[None], symbol=[None], decimals=[None], totalSupply=[None]))
    assert decision == "not_a_token"
    assert "no contract code" in evidence


def test_code_that_could_not_be_read_is_unavailable_not_absent():
    _metadata, (decision, reason) = _decide(_chain(
        code_error=TimeoutError("rpc"), name=[None], symbol=[None],
        decimals=[None], totalSupply=[None]))
    assert decision == "unavailable"
    assert "code=TimeoutError" in reason


def test_a_contract_whose_fields_return_nothing_is_not_called_a_non_token():
    """Contract code is present and the fields came back empty, without an error,
    both times. That establishes no ERC-20 interface -- and rules none out."""
    _metadata, (decision, reason) = _decide(_chain(
        name=[None], symbol=[None], decimals=[None], totalSupply=[None]))
    assert decision == "unavailable"
    assert "neither established nor ruled out" in reason


# --- and what reaches the caller ---------------------------------------------

def test_an_unavailable_read_reaches_the_caller_as_insufficient_data(monkeypatch):
    failure = ConnectionError("throttled")
    monkeypatch.setattr(server, "get_web3", lambda: _chain(
        name=[failure], symbol=[failure], decimals=[failure], totalSupply=[failure]))
    monkeypatch.setattr(server, "validate_known_token_metadata", lambda _web3: None)
    with pytest.raises(server.TokenReadUnavailable):
        server.build_remote_scoring_payload(ADDRESS)


def test_only_an_empty_address_reaches_the_caller_as_not_a_token(monkeypatch):
    monkeypatch.setattr(server, "get_web3", lambda: _chain(
        code=b"", name=[None], symbol=[None], decimals=[None], totalSupply=[None]))
    monkeypatch.setattr(server, "validate_known_token_metadata", lambda _web3: None)
    with pytest.raises(server.NotTokenAddress):
        server.build_remote_scoring_payload(ADDRESS)
