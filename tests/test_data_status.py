"""Fetch failure must never read as a clean scan.

Every module here used to return the same "empty, therefore fine" default
whether the upstream API answered "there is nothing" or never answered at
all. The first is a finding; the second is a hole. These tests pin the
difference, because it is invisible in the happy path and only shows up
during an outage -- exactly when a false GOOD is most expensive.
"""

from __future__ import annotations

from unittest import mock

from chains.avalanche import avax_collector_v6 as collector
from chains.avalanche.avax_collector_v6 import (
    STATUS_FETCH_FAILED,
    STATUS_NOT_FOUND,
    STATUS_NOT_QUERIED,
    STATUS_OK,
)


# --- the chokepoint: snowtrace_get's None/[] distinction must survive ---

def test_wrapper_marks_failed_fetch_as_fetch_failed():
    with mock.patch.object(collector, "snowtrace_get", return_value=None):
        result = collector.get_token_holders("0xabc")
    assert result == []
    assert result.fetch_status == STATUS_FETCH_FAILED


def test_wrapper_marks_confirmed_empty_as_not_found():
    with mock.patch.object(collector, "snowtrace_get", return_value=[]):
        result = collector.get_token_transfers("0xabc")
    assert result == []
    assert result.fetch_status == STATUS_NOT_FOUND


def test_wrapper_marks_real_data_as_ok():
    with mock.patch.object(collector, "snowtrace_get", return_value=[{"from": "0x1"}]):
        result = collector.get_account_transactions("0xabc")
    assert len(result) == 1
    assert result.fetch_status == STATUS_OK


def test_fetched_list_stays_a_plain_list_for_existing_callers():
    """Existing call sites use `if not txs`, len(), indexing and iteration."""
    empty = collector.FetchedList([], STATUS_FETCH_FAILED)
    full = collector.FetchedList([{"a": 1}, {"a": 2}], STATUS_OK)
    assert not empty
    assert len(full) == 2
    assert full[0] == {"a": 1}
    assert [item["a"] for item in full] == [1, 2]


def test_bare_list_without_status_is_treated_as_unknown_not_clean():
    """A plain [] cannot prove the source was reachable, so it must not be optimistic."""
    assert collector.fetch_status_of([]) == STATUS_FETCH_FAILED
    assert collector.fetch_status_of([{"x": 1}]) == STATUS_OK


# --- holder concentration: the most dangerous default in the file ---

def test_concentration_failure_is_not_reported_as_zero_percent_clean():
    with mock.patch.object(
        collector, "get_token_holders", return_value=collector.FetchedList([], STATUS_FETCH_FAILED)
    ):
        result = collector.analyze_holder_concentration_avax("0xabc")
    assert result["status"] == STATUS_FETCH_FAILED
    assert result["status_reason"]
    # The placeholder numbers are still there for schema stability, but the
    # status is what a consumer must read before trusting them.
    assert result["top5_pct"] == 0.0


def test_concentration_genuinely_empty_is_not_found():
    with mock.patch.object(
        collector, "get_token_holders", return_value=collector.FetchedList([], STATUS_NOT_FOUND)
    ):
        result = collector.analyze_holder_concentration_avax("0xabc")
    assert result["status"] == STATUS_NOT_FOUND


# --- contract backdoor: "did not read it" vs "read it, it is clean" ---

def test_backdoor_rpc_failure_is_not_reported_as_no_backdoor():
    with mock.patch.object(collector.requests, "post", side_effect=OSError("rpc down")):
        result = collector.detect_contract_backdoor_avax("0xabc")
    assert result["status"] == STATUS_FETCH_FAILED
    assert result["has_backdoor"] is False  # unchanged shape...
    assert result["status_reason"]          # ...but no longer a clean claim


def test_backdoor_absent_bytecode_is_not_found():
    response = mock.Mock()
    response.json.return_value = {"result": "0x"}
    with mock.patch.object(collector.requests, "post", return_value=response):
        result = collector.detect_contract_backdoor_avax("0xabc")
    assert result["status"] == STATUS_NOT_FOUND


# --- entropy: too few transactions is not a confident reading ---

def test_entropy_below_minimum_sample_is_not_found():
    transfers = collector.FetchedList(
        [{"tokenDecimal": "18", "value": str(i * 10**18)} for i in range(1, 4)], STATUS_OK
    )
    with mock.patch.object(collector, "get_token_transfers", return_value=transfers):
        result = collector.analyze_transaction_entropy_avax("0xabc")
    assert result["status"] == STATUS_NOT_FOUND
    assert str(collector.MIN_TX_FOR_ENTROPY) in result["status_reason"]


def test_entropy_with_enough_samples_is_ok():
    transfers = collector.FetchedList(
        [{"tokenDecimal": "18", "value": str(i * 10**18)} for i in range(1, 9)], STATUS_OK
    )
    with mock.patch.object(collector, "get_token_transfers", return_value=transfers):
        result = collector.analyze_transaction_entropy_avax("0xabc")
    assert result["status"] == STATUS_OK
    assert result["total_txs"] == 8


# --- wash pattern: an outage used to manufacture a risk signal ---

def test_wash_deployer_history_failure_does_not_flip_linker_wallets_true():
    transfers = collector.FetchedList([{"from": "0xdead", "timeStamp": "0"}], STATUS_OK)
    with mock.patch.object(collector, "get_token_transfers", return_value=transfers), \
         mock.patch.object(
             collector, "get_account_transactions",
             return_value=collector.FetchedList([], STATUS_FETCH_FAILED),
         ):
        result = collector.detect_wash_pattern_avax("0xabc", "0xdead", 0)
    assert result["status"] == STATUS_FETCH_FAILED
    assert result["linker_wallets_connected"] is False
    assert result["wash_detected"] is False


def test_wash_without_deployer_is_not_queried():
    result = collector.detect_wash_pattern_avax("0xabc", "", 0)
    assert result["status"] == STATUS_NOT_QUERIED


# --- funding origin: a broken hop is not "fresh wallet" ---

def test_funding_broken_hop_is_not_reported_as_fresh_wallet():
    with mock.patch.object(
        collector, "get_account_transactions",
        return_value=collector.FetchedList([], STATUS_FETCH_FAILED),
    ):
        result = collector.trace_funding_origin_avax("0xdead", depth=2)
    assert result["status"] == STATUS_FETCH_FAILED
    assert result["is_fresh_wallet"] is False


def test_funding_genuinely_empty_history_is_fresh_wallet():
    with mock.patch.object(
        collector, "get_account_transactions",
        return_value=collector.FetchedList([], STATUS_NOT_FOUND),
    ):
        result = collector.trace_funding_origin_avax("0xdead", depth=2)
    assert result["status"] == STATUS_OK
    assert result["is_fresh_wallet"] is True


# --- deployment latency ---

def test_latency_fetch_failure_is_distinct_from_no_transfers():
    with mock.patch.object(
        collector, "get_token_transfers",
        return_value=collector.FetchedList([], STATUS_FETCH_FAILED),
    ):
        failed = collector.get_deployment_latency_avax("0xabc", 100)
    with mock.patch.object(
        collector, "get_token_transfers",
        return_value=collector.FetchedList([], STATUS_NOT_FOUND),
    ):
        empty = collector.get_deployment_latency_avax("0xabc", 100)
    assert failed["status"] == STATUS_FETCH_FAILED
    assert empty["status"] == STATUS_NOT_FOUND
    assert failed["latency_ms"] == empty["latency_ms"] == -1  # same number, different meaning


# --- the API-facing summary ---

def test_completeness_counts_only_modules_that_produced_data():
    cia = {
        "funding": {"status": STATUS_OK},
        "latency": {"status": STATUS_NOT_FOUND, "status_reason": "no transfers yet"},
        "entropy": {"status": STATUS_OK},
        "wash": {"status": STATUS_OK},
        "cluster": {"status": STATUS_OK},
    }
    v6 = {
        "backdoor": {"status": STATUS_OK},
        "concentration": {"status": STATUS_FETCH_FAILED, "status_reason": "holder list unavailable"},
        "velocity": {"status": STATUS_OK},
    }
    summary = collector.summarize_data_completeness(cia, v6)
    assert summary["modules_total"] == 8
    assert summary["modules_ok"] == 6
    assert summary["completeness_pct"] == 75
    assert summary["has_fetch_failures"] is True
    reported = {item["module"]: item["status"] for item in summary["missing_inputs"]}
    assert reported == {
        "deployment_latency": STATUS_NOT_FOUND,
        "holder_concentration": STATUS_FETCH_FAILED,
    }


def test_not_found_alone_keeps_a_verdict_conclusive():
    """A token with genuinely no pool is a finding, not a hole in the scan."""
    cia = {k: {"status": STATUS_OK} for k in ("funding", "latency", "entropy", "wash", "cluster")}
    v6 = {
        "backdoor": {"status": STATUS_OK},
        "concentration": {"status": STATUS_NOT_FOUND, "status_reason": "token has no holder records"},
        "velocity": {"status": STATUS_OK},
    }
    summary = collector.summarize_data_completeness(cia, v6)
    assert summary["has_fetch_failures"] is False
    assert summary["completeness_pct"] == 88


def test_module_that_never_ran_is_not_queried():
    summary = collector.summarize_data_completeness({}, {})
    assert summary["modules_ok"] == 0
    assert summary["completeness_pct"] == 0
    assert all(item["status"] == STATUS_NOT_QUERIED for item in summary["missing_inputs"])
