"""A function's name is not its behaviour.

The matcher used to decide what a function does by looking for substrings of
its human-readable name, and the names lie in both directions. Measured against
the 143-entry golden set with `qa/compare_function_matcher.py`:

    123 of 143 readings changed
    5 of those changed the risk score, all of them downwards
    0 tokens gained a power

    WAVAX  20 -> 0   withdraw(uint256) matched "withdraw" and set
                     has_drain_function. On Wrapped AVAX it is the unwrap: it
                     burns the caller's own wrapper and returns their own
                     native token.
    STG    20 -> 0   paused() matched "pause". It is a view getter.
    USDt   40 -> 20  is_proxy and has_upgrade_authority were both counted for
    sAVAX  40 -> 20  the same proxy, and implementation() -- a view getter --
    AVAI   40 -> 20  could raise the score on its own.

    118 more lost only `has_backdoor`, which used to be true whenever any
    known selector was found at all. burn(uint256) alone was enough, which is
    how LINK.e -- a Chainlink bridge asset scored GOOD -- was reported as
    holding a controller power over holders.

No scam or rug-factory token lost a point. The powers that remain are the ones
that mean something: USDt and sAVAX upgrade, JOE and GMX mint, SDOG and TIME
mint, ALOT pause.
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "chains" / "avalanche"))

import avax_collector_v6 as collector  # noqa: E402

SELECTORS = {name: sig for sig, (name, _p) in collector.FUNCTION_SIGNATURES.items()}


def _read(*function_names: str) -> dict:
    """Run the matcher over bytecode containing exactly these selectors."""
    bytecode = "0x" + "".join(SELECTORS[name] for name in function_names) + "00" * 8

    class _Response:
        @staticmethod
        def json():
            return {"result": bytecode}

    original = collector.requests.post
    collector.requests.post = lambda *a, **k: _Response()
    try:
        return collector.detect_contract_backdoor_avax("0xtest")
    finally:
        collector.requests.post = original


# --- the false positives this replaces -------------------------------------

def test_the_unwrap_on_a_wrapped_native_is_not_a_drain():
    """withdraw(uint256) burns the caller's own wrapper and returns their own
    native token. It is the whole purpose of the contract."""
    reading = _read("withdraw(uint256)")
    assert reading["has_drain_function"] is False
    assert reading["backdoor_risk_score"] == 0
    assert reading["backdoor_functions"] == ["withdraw(uint256)"]


def test_a_plain_erc20_burn_is_not_a_backdoor():
    """burn(uint256) burns the caller's own balance."""
    reading = _read("burn(uint256)")
    assert reading["has_backdoor"] is False
    assert reading["powers"] == []


def test_a_view_getter_is_not_a_power():
    for name in ("paused()", "isBlacklisted(address)", "owner()", "implementation()"):
        reading = _read(name)
        assert reading["powers"] == [], f"{name} was read as granting a power"
        assert reading["backdoor_risk_score"] == 0, name


def test_renouncing_ownership_is_not_a_power():
    assert _read("renounceOwnership()")["powers"] == []


def test_a_proxy_is_counted_once():
    """is_proxy and has_upgrade_authority describe the same fact, and
    implementation() is a getter beside them."""
    reading = _read("upgradeTo(address)", "upgradeToAndCall(address,bytes)", "implementation()")
    assert reading["is_proxy"] is True
    assert reading["has_upgrade_authority"] is True
    assert reading["powers"] == ["upgrade"]
    assert reading["backdoor_risk_score"] == 20


# --- what must still bite --------------------------------------------------

def test_minting_is_a_power():
    reading = _read("mint(address,uint256)")
    assert reading["has_mint_function"] is True
    assert reading["has_backdoor"] is True
    assert reading["backdoor_risk_score"] == 20


def test_pausing_transfers_is_a_power_and_the_getter_beside_it_is_not():
    reading = _read("pause()", "unpause()", "paused()")
    assert reading["has_pause_function"] is True
    assert reading["powers"] == ["pause"]
    assert reading["backdoor_risk_score"] == 20


def test_blacklisting_is_a_power():
    reading = _read("blacklist(address)", "isBlacklisted(address)")
    assert reading["has_blacklist"] is True
    assert reading["powers"] == ["blacklist"]


def test_sweeping_arbitrary_tokens_is_still_a_drain():
    """withdrawToken(address) takes tokens the contract holds for others. This
    is the one withdraw-shaped function that keeps the flag."""
    reading = _read("withdrawToken(address)")
    assert reading["has_drain_function"] is True
    assert reading["powers"] == ["sweep"]


def test_several_powers_accumulate():
    reading = _read("mint(address,uint256)", "pause()", "blacklist(address)")
    assert reading["backdoor_risk_score"] == 60
    assert reading["powers"] == ["blacklist", "mint", "pause"]


# --- ownership is reported, not scored -------------------------------------

def test_an_owner_is_reported_without_being_scored():
    """Centralisation is worth knowing. On its own it grants nothing over
    anyone's balance, and counting it put every Ownable contract at 20."""
    reading = _read("owner()", "transferOwnership(address)")
    assert reading["has_owner"] is True
    assert reading["powers"] == []
    assert reading["backdoor_risk_score"] == 0


# --- absent is not clean ---------------------------------------------------

def test_unreadable_bytecode_is_not_a_clean_reading():
    class _Empty:
        @staticmethod
        def json():
            return {"result": "0x"}

    original = collector.requests.post
    collector.requests.post = lambda *a, **k: _Empty()
    try:
        reading = collector.detect_contract_backdoor_avax("0xtest")
    finally:
        collector.requests.post = original
    assert reading["status"] != collector.STATUS_OK
    assert reading["status_reason"]


def test_an_rpc_failure_is_reported_as_a_failure():
    def _raise(*a, **k):
        raise TimeoutError("rpc")

    original = collector.requests.post
    collector.requests.post = _raise
    try:
        reading = collector.detect_contract_backdoor_avax("0xtest")
    finally:
        collector.requests.post = original
    assert reading["status"] == collector.STATUS_FETCH_FAILED


# --- the table cannot name a power it has no field for ---------------------

def test_every_declared_power_has_a_field():
    """The first version of the table named `ownership` with no field behind
    it. The KeyError was raised inside the scan's broad `except` and reported
    as "bytecode could not be read from RPC" -- a misconfiguration wearing an
    outage's clothes, which then reads as an absent signal."""
    declared = {power for _name, power in collector.FUNCTION_SIGNATURES.values() if power}
    assert declared <= set(collector.POWER_FIELDS)


def test_the_old_name_still_resolves_for_anything_importing_it():
    assert collector.BACKDOOR_SIGNATURES
    assert set(collector.BACKDOOR_SIGNATURES) == set(collector.FUNCTION_SIGNATURES)


# --- the table must not be able to lie about itself ------------------------

def test_every_selector_hashes_to_the_name_beside_it():
    """Three of seventeen did not, and all three carried a power.

        51cff8d9  labelled withdrawToken(address)  -> really withdraw(address)
        044df020  labelled blacklist(address)      -> hashes to nothing I could
        537df3b6  labelled unBlacklist(address)       identify

    So `sweep` never fired on the function it named, and `blacklist` fired on
    two byte sequences of unknown meaning. The names had been taken on trust
    and the table was the only place they were written down.

    Recomputing the selector is the whole check. A table that cannot be
    verified against itself is a list of magic numbers.
    """
    from eth_utils import keccak

    wrong = {
        selector: (name, keccak(text=name)[:4].hex())
        for selector, (name, _power) in collector.FUNCTION_SIGNATURES.items()
        if keccak(text=name)[:4].hex() != selector
    }
    assert not wrong, f"selectors that do not hash to their own name: {wrong}"


def test_burning_someone_elses_balance_is_a_power():
    """Found by asking what TIME's concrete risk was rather than what its label
    said. Its admin functions include burn(address,uint256) -- destroying a
    holder's tokens -- and the matcher had no entry for it at all."""
    for name in ("burn(address,uint256)", "burnFrom(address,uint256)"):
        reading = _read(name)
        assert reading["powers"] == ["burn_others"], name
        assert reading["has_burn_others"] is True
        assert reading["backdoor_risk_score"] == 20


def test_burning_your_own_balance_is_still_not_a_power():
    """The one-argument form. Same word, different function."""
    assert _read("burn(uint256)")["powers"] == []


def test_an_ambiguous_withdraw_grants_nothing_until_the_code_is_read():
    """withdraw(address) may send the caller's own balance somewhere or sweep
    the contract's. The name does not say, so neither do we."""
    assert _read("withdraw(address)")["powers"] == []


def test_the_real_withdrawToken_still_sweeps():
    assert _read("withdrawToken(address)")["powers"] == ["sweep"]
