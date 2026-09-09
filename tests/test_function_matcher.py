"""A selector is four bytes. It is not a function, and it is not a power.

Three claims were being made on the evidence of one:

    the bytes 40c10f19 appear in the bytecode
    therefore a mint(address,uint256) function exists
    therefore the controller can mint

Only the first is read from the chain. The matcher now says so: selectors give
`possible_functions` and `possible_powers`, and nothing reaches
`source_read_powers` without the contract's published source being read.

What the source pass establishes, and only this: the function is declared,
whether its declaration carries an access modifier, and for a two-argument burn
whether the body spends an allowance. What it does not: who holds the role, or
anything at all about a contract that publishes no source. Text matching on
Solidity is not compilation -- stronger than a selector, weaker than an audit.

Verified by reading real sources rather than reasoning from names:

    TIME   burn(address,uint256) external onlyOwner -> _burn(account, value),
           no allowance spent. The owner can destroy any holder's balance.
    SDOG   burnFrom subtracts allowance ("burn amount exceeds allowance")
    BLS    burnFrom calls _spendAllowance
    sAVAX  upgradeTo(address) external ifAdmin on a TransparentUpgradeableProxy
           with a live EIP-1967 admin at 0x2295e1cad2ea081a4a2ed85f59006e6fd42b5a66

Three selectors also did not hash to the name beside them, and all three
carried a power: 51cff8d9 was labelled withdrawToken(address) and is really
withdraw(address); 044df020 and 537df3b6 were labelled blacklist and
unBlacklist and hash to nothing identifiable.

And the earlier substring rule, measured over the golden set before it went:
123 of 143 readings changed, no token gained a power, and the five that moved
score moved down -- WAVAX and STG to zero, USDt, sAVAX and AVAI from a
double-counted proxy.
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "chains" / "avalanche"))

import avax_collector_v6 as collector  # noqa: E402

SELECTORS = {name: sig for sig, (name, _p) in collector.FUNCTION_SIGNATURES.items()}


def _read(*function_names: str) -> dict:
    """Selector matching only, with no source lookup."""
    bytecode = "0x" + "".join(SELECTORS[name] for name in function_names) + "00" * 8

    class _Response:
        @staticmethod
        def json():
            return {"result": bytecode}

    original = collector.requests.post
    collector.requests.post = lambda *a, **k: _Response()
    try:
        return collector.detect_contract_backdoor_avax("0xtest", confirm_from_source=False)
    finally:
        collector.requests.post = original


def _read_with_source(source: str, *function_names: str) -> dict:
    """Selector matching plus a source pass over the text supplied."""
    bytecode = "0x" + "".join(SELECTORS[name] for name in function_names) + "00" * 8

    class _Post:
        @staticmethod
        def json():
            return {"result": bytecode}

    class _Get:
        @staticmethod
        def json():
            return {"result": [{"SourceCode": source, "ContractName": "T"}]}

    post, get = collector.requests.post, collector.requests.get
    collector.requests.post = lambda *a, **k: _Post()
    collector.requests.get = lambda *a, **k: _Get()
    try:
        return collector.detect_contract_backdoor_avax("0xtest")
    finally:
        collector.requests.post, collector.requests.get = post, get


# --- a selector confirms nothing -------------------------------------------

def test_a_matched_selector_is_possible_and_not_confirmed():
    reading = _read("mint(address,uint256)")
    assert reading["possible_functions"] == ["mint(address,uint256)"]
    assert reading["possible_powers"] == ["mint"]
    assert reading["source_read_powers"] == []
    assert reading["has_backdoor"] is False
    assert reading["backdoor_risk_score"] == 0
    assert reading["control"] == "unknown"


def test_an_unpublished_source_leaves_the_power_possible():
    """The common case, and it is not a finding about the token."""
    reading = _read_with_source("", "mint(address,uint256)")
    assert reading["possible_powers"] == ["mint"]
    assert reading["source_read_powers"] == []
    assert reading["source_status"] == collector.STATUS_NOT_FOUND
    assert reading["control"] == "unknown"


def test_a_published_source_with_everything_resolved_is_read():
    source = ("modifier onlyOwner() { require(msg.sender == owner); _; } "
              "function _mint(address to, uint256 amount) internal { } "
              "function mint(address to, uint256 amount) external onlyOwner { _mint(to, amount); }")
    reading = _read_with_source(source, "mint(address,uint256)")
    assert reading["source_read_powers"] == ["mint"]
    assert reading["control"] == "role_gated"
    assert reading["backdoor_risk_score"] == 20


def test_a_selector_whose_function_is_absent_from_the_source_stays_unconfirmed():
    """Four bytes can appear in bytecode without being a function at all."""
    source = "function transfer(address to, uint256 amount) public { }"
    reading = _read_with_source(source, "mint(address,uint256)")
    assert reading["source_read_powers"] == []
    assert "mint(address,uint256)" in reading["unread_restrictions"]


# --- what the source pass reads --------------------------------------------

def test_an_owner_burn_of_someone_elses_balance_is_read():
    """TIME's actual declaration, with its modifier and callee resolved."""
    source = ("modifier onlyOwner() { require(msg.sender == owner); _; } "
              "function _burn(address account, uint256 value) internal { } "
              "function burn(address account, uint256 value) external onlyOwner "
              "{ _burn(account, value); }")
    reading = _read_with_source(source, "burn(address,uint256)")
    assert reading["source_read_powers"] == ["burn_others"]
    assert reading["control"] == "role_gated"


def test_a_burn_that_spends_an_allowance_is_not_that_power():
    """BLS's declaration. The holder approved it, so it cannot touch an
    unwilling one, whatever the function is called."""
    source = ("function _spendAllowance(address a, address b, uint256 v) internal { } "
              "function _msgSender() internal returns (address) { } "
              "function _burn(address account, uint256 value) internal { } "
              "function burn(address account, uint256 value) public virtual "
              "{ _spendAllowance(account, _msgSender(), value); _burn(account, value); }")
    assert _read_with_source(source, "burn(address,uint256)")["source_read_powers"] == []


def test_an_unrestricted_declaration_is_recorded_as_such():
    source = ("function _mint(address to, uint256 amount) internal { } "
              "function mint(address to, uint256 amount) public { _mint(to, amount); }")
    reading = _read_with_source(source, "mint(address,uint256)")
    assert reading["source_read_powers"] == ["mint"]
    assert reading["control"] == "unrestricted"


def test_who_holds_the_role_is_never_claimed():
    """`role_gated` says a modifier is present. It does not say whose key."""
    reading = _read_with_source(
        "modifier onlyOwner() { _; } function pause() external onlyOwner { }", "pause()")
    assert reading["control"] == "role_gated"
    assert reading.get("controller_address") is None


# --- the false positives this replaced -------------------------------------

def test_the_unwrap_on_a_wrapped_native_is_not_a_drain():
    """withdraw(uint256) burns the caller's own wrapper and returns their own
    native token. It is the whole purpose of the contract."""
    reading = _read("withdraw(uint256)")
    assert reading["possible_powers"] == []
    assert reading["possible_functions"] == ["withdraw(uint256)"]


def test_a_plain_erc20_burn_is_not_a_power():
    assert _read("burn(uint256)")["possible_powers"] == []


def test_a_view_getter_is_not_a_power():
    for name in ("paused()", "isBlacklisted(address)", "owner()", "implementation()"):
        assert _read(name)["possible_powers"] == [], name


def test_renouncing_ownership_is_not_a_power():
    assert _read("renounceOwnership()")["possible_powers"] == []


def test_an_ambiguous_withdraw_grants_nothing():
    """withdraw(address) may send the caller's own balance or sweep the
    contract's. The name does not say, so neither do we."""
    assert _read("withdraw(address)")["possible_powers"] == []


def test_an_allowance_based_burn_is_not_a_possible_power_either():
    assert _read("burnFrom(address,uint256)")["possible_powers"] == []


def test_a_proxy_is_counted_once():
    reading = _read("upgradeTo(address)", "upgradeToAndCall(address,bytes)", "implementation()")
    assert reading["is_proxy"] is True
    assert reading["possible_powers"] == ["upgrade"]


def test_an_owner_is_reported_without_being_scored():
    reading = _read("owner()", "transferOwnership(address)")
    assert reading["has_owner"] is True
    assert reading["possible_powers"] == []
    assert reading["backdoor_risk_score"] == 0


# --- what must still be seen -----------------------------------------------

def test_the_real_withdrawToken_is_a_possible_sweep():
    assert _read("withdrawToken(address)")["possible_powers"] == ["sweep"]


def test_pausing_and_blacklisting_are_possible_powers():
    assert _read("pause()", "unpause()", "paused()")["possible_powers"] == ["pause"]
    assert _read("blacklist(address)", "isBlacklisted(address)")["possible_powers"] == ["blacklist"]


def test_several_possible_powers_accumulate():
    reading = _read("mint(address,uint256)", "pause()", "blacklist(address)")
    assert reading["possible_powers"] == ["blacklist", "mint", "pause"]


# --- absent is not clean ---------------------------------------------------

def test_unreadable_bytecode_is_not_a_clean_reading():
    class _Empty:
        @staticmethod
        def json():
            return {"result": "0x"}

    original = collector.requests.post
    collector.requests.post = lambda *a, **k: _Empty()
    try:
        reading = collector.detect_contract_backdoor_avax("0xtest", confirm_from_source=False)
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
        reading = collector.detect_contract_backdoor_avax("0xtest", confirm_from_source=False)
    finally:
        collector.requests.post = original
    assert reading["status"] == collector.STATUS_FETCH_FAILED


def test_a_failed_source_fetch_confirms_nothing_and_says_so():
    class _Post:
        @staticmethod
        def json():
            return {"result": "0x" + SELECTORS["mint(address,uint256)"] + "00" * 8}

    def _raise(*a, **k):
        raise TimeoutError("explorer")

    post, get = collector.requests.post, collector.requests.get
    collector.requests.post = lambda *a, **k: _Post()
    collector.requests.get = _raise
    try:
        reading = collector.detect_contract_backdoor_avax("0xtest")
    finally:
        collector.requests.post, collector.requests.get = post, get
    assert reading["source_status"] == collector.STATUS_FETCH_FAILED
    assert reading["source_read_powers"] == []
    assert reading["possible_powers"] == ["mint"]


# --- the table must not be able to lie about itself ------------------------

def test_every_selector_hashes_to_the_name_beside_it():
    """Three of seventeen did not, and all three carried a power. Recomputing
    the selector is the whole check: a table that cannot be verified against
    itself is a list of magic numbers."""
    from eth_utils import keccak

    wrong = {
        selector: (name, keccak(text=name)[:4].hex())
        for selector, (name, _power) in collector.FUNCTION_SIGNATURES.items()
        if keccak(text=name)[:4].hex() != selector
    }
    assert not wrong, f"selectors that do not hash to their own name: {wrong}"


def test_every_declared_power_has_a_field():
    declared = {power for _name, power in collector.FUNCTION_SIGNATURES.values() if power}
    assert declared <= set(collector.POWER_FIELDS)


def test_the_old_name_still_resolves_for_anything_importing_it():
    assert set(collector.BACKDOOR_SIGNATURES) == set(collector.FUNCTION_SIGNATURES)


# --- a restriction we could not read is a gap, not a power ------------------

def test_an_inherited_modifier_leaves_the_power_unread():
    """The case review named. `onlyOwner` comes from a base contract the
    explorer did not flatten into this file, so what it enforces is unknown --
    and a text scan that ignores that is claiming to have read something it
    never saw."""
    source = ("function _mint(address to, uint256 amount) internal { } "
              "function mint(address to, uint256 amount) external onlyOwner "
              "{ _mint(to, amount); }")
    reading = _read_with_source(source, "mint(address,uint256)")
    assert reading["source_read_powers"] == []
    assert "mint(address,uint256)" in reading["unread_restrictions"]
    assert "onlyowner" in reading["unread_restrictions"]["mint(address,uint256)"]


def test_an_unresolved_internal_call_leaves_the_power_unread():
    """A restriction can sit in a function the declaration calls rather than in
    the declaration. If that function is not in this source, we have not read
    the restriction."""
    source = ("modifier onlyOwner() { _; } "
              "function mint(address to, uint256 amount) external onlyOwner "
              "{ _checkedMint(to, amount); }")
    reading = _read_with_source(source, "mint(address,uint256)")
    assert reading["source_read_powers"] == []
    assert "_checkedmint" in reading["unread_restrictions"]["mint(address,uint256)"]


def test_an_unread_restriction_keeps_the_function_visible():
    """It must not vanish because we could not resolve it. Possible stays
    possible and the gap is named."""
    source = ("function mint(address to, uint256 amount) external onlyOwner { _mint(to, amount); }")
    reading = _read_with_source(source, "mint(address,uint256)")
    assert reading["possible_powers"] == ["mint"]
    assert reading["possible_functions"] == ["mint(address,uint256)"]
    assert reading["backdoor_risk_score"] == 0


def test_an_allowance_spent_in_a_called_function_still_counts():
    """SDOG's shape: burnFrom delegates to _burnFrom, and the allowance check
    lives there. Reading only the declaration would have missed it."""
    source = ("function _burnFrom(address a, uint256 v) internal "
              "{ allowance(a, msg.sender); } "
              "function burn(address account, uint256 value) public "
              "{ _burnFrom(account, value); }")
    assert _read_with_source(source, "burn(address,uint256)")["source_read_powers"] == []
