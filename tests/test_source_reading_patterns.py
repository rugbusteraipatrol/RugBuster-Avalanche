"""Reading a source is not the same as finding its text.

Review asked for one function, burn(address,uint256), in four shapes, and
required that they not all come out as a power to destroy another holder's
balance. The previous reader passed all four as long as the modifier's and
callee's definitions were somewhere in the file, and matched modifier names as
substrings of the header -- so a burn that only lets you burn your own tokens
read as an unrestricted power over everyone.

Each test states one pattern the reader recognises, or one it must refuse to.
"""

from __future__ import annotations

import json

from test_function_matcher import _read, _read_with_source, collector

BURN = "burn(address,uint256)"

CONTEXT = ("function _msgSender() internal view virtual returns (address) "
           "{ return msg.sender; } ")
# OpenZeppelin 5 Ownable, the shape TIME actually publishes.
OWNABLE = (
    "address private _owner; "
    "modifier onlyOwner() { _checkOwner(); _; } "
    "function owner() public view virtual returns (address) { return _owner; } "
    "function _checkOwner() internal view virtual { "
    "  if (owner() != _msgSender()) { revert OwnableUnauthorizedAccount(_msgSender()); } } "
)
ERC20_BURN = (
    "function _burn(address account, uint256 value) internal { "
    "  if (account == address(0)) { revert ERC20InvalidSender(address(0)); } "
    "  _update(account, address(0), value); } "
)
SPEND_ALLOWANCE = (
    "function _spendAllowance(address owner, address spender, uint256 value) internal virtual { "
    "  uint256 currentAllowance = allowance(owner, spender); } "
)


def _burn_reading(declaration: str, extra: str = "") -> dict:
    return _read_with_source(CONTEXT + OWNABLE + ERC20_BURN + extra + declaration, BURN)


# --- the four shapes review named -------------------------------------------

def test_owner_only_direct_burn_is_the_power():
    """TIME's declaration. The owner destroys any holder's balance; nothing in
    the body asks the holder."""
    reading = _burn_reading(
        "function burn(address account, uint256 value) external onlyOwner { _burn(account, value); }")
    assert reading["source_read_powers"] == ["burn_others"]
    assert reading["control"] == "role_gated"
    assert reading["declarations"][BURN]["outcome"] == collector.OUTCOME_ESTABLISHED
    assert reading["capability_check"] == collector.CAPABILITY_COMPLETE


def test_a_mandatory_allowance_rules_the_power_out():
    """ERC20Burnable.burnFrom under another name: the holder approved it."""
    reading = _burn_reading(
        "function burn(address account, uint256 value) public virtual "
        "{ _spendAllowance(account, _msgSender(), value); _burn(account, value); }",
        extra=SPEND_ALLOWANCE)
    assert reading["source_read_powers"] == []
    assert BURN in reading["ruled_out_functions"]
    assert "allowance" in reading["ruled_out_functions"][BURN]
    assert reading["capability_check"] == collector.CAPABILITY_COMPLETE


def test_requiring_the_account_to_be_the_caller_rules_the_power_out():
    reading = _burn_reading(
        "function burn(address account, uint256 value) external "
        "{ require(account == msg.sender, 'own balance only'); _burn(account, value); }")
    assert reading["source_read_powers"] == []
    assert "caller" in reading["ruled_out_functions"][BURN]


def test_the_same_self_check_written_as_a_revert_is_recognised():
    reading = _burn_reading(
        "function burn(address account, uint256 value) external "
        "{ if (account != _msgSender()) { revert NotYours(); } _burn(account, value); }")
    assert BURN in reading["ruled_out_functions"]


def test_an_unresolved_modifier_is_neither_a_power_nor_a_clearance():
    source = (CONTEXT + ERC20_BURN +
              "function burn(address account, uint256 value) external onlyMinter { _burn(account, value); }")
    reading = _read_with_source(source, BURN)
    assert reading["source_read_powers"] == []
    assert BURN not in reading["ruled_out_functions"]
    assert "onlyMinter" in reading["unread_restrictions"][BURN]
    assert reading["unconfirmed_powers"] == ["burn_others"]
    assert reading["capability_check"] == collector.CAPABILITY_INCOMPLETE


def test_an_unresolved_internal_call_is_neither_a_power_nor_a_clearance():
    reading = _burn_reading(
        "function burn(address account, uint256 value) external onlyOwner "
        "{ _checkedBurn(account, value); }")
    assert reading["source_read_powers"] == []
    assert BURN not in reading["ruled_out_functions"]
    assert "_checkedBurn" in reading["unread_restrictions"][BURN]
    assert reading["capability_check"] == collector.CAPABILITY_INCOMPLETE


def test_the_four_shapes_do_not_all_read_as_the_power():
    shapes = {
        "owner": "function burn(address account, uint256 value) external onlyOwner { _burn(account, value); }",
        "allowance": ("function burn(address account, uint256 value) public "
                      "{ _spendAllowance(account, _msgSender(), value); _burn(account, value); }"),
        "self": ("function burn(address account, uint256 value) external "
                 "{ require(account == msg.sender); _burn(account, value); }"),
        "unresolved": ("function burn(address account, uint256 value) external onlyMinter "
                       "{ _burn(account, value); }"),
    }
    outcomes = {
        label: _burn_reading(body, extra=SPEND_ALLOWANCE)["declarations"][BURN]["outcome"]
        for label, body in shapes.items()
    }
    assert outcomes == {
        "owner": collector.OUTCOME_ESTABLISHED,
        "allowance": collector.OUTCOME_RULED_OUT,
        "self": collector.OUTCOME_RULED_OUT,
        "unresolved": collector.OUTCOME_UNRESOLVED,
    }


# --- a definition found is not a definition understood ----------------------

def test_a_defined_modifier_that_does_something_unrecognised_is_unresolved():
    source = (CONTEXT + ERC20_BURN +
              "modifier onlyOwner() { require(trusted[msg.sender]); _; } "
              "function burn(address account, uint256 value) external onlyOwner { _burn(account, value); }")
    reading = _read_with_source(source, BURN)
    assert reading["declarations"][BURN]["outcome"] == collector.OUTCOME_UNRESOLVED


def test_the_opposite_polarity_is_not_a_self_check():
    """`account != msg.sender` in a require forbids burning your own tokens.
    Reading it as a self check would clear a function that burns everyone else's."""
    reading = _burn_reading(
        "function burn(address account, uint256 value) external "
        "{ require(account != msg.sender); _burn(account, value); }")
    assert BURN not in reading["ruled_out_functions"]
    assert reading["source_read_powers"] == []


def test_a_composite_condition_is_not_recognised():
    reading = _burn_reading(
        "function burn(address account, uint256 value) external "
        "{ require(msg.sender == owner() || minters[msg.sender]); _burn(account, value); }")
    assert reading["declarations"][BURN]["outcome"] == collector.OUTCOME_UNRESOLVED


def test_a_modifier_name_is_matched_whole_not_as_a_substring():
    """The old reader found `onlyowner` inside `onlyOwnerOrMinter`."""
    reading = _burn_reading(
        "function burn(address account, uint256 value) external onlyOwnerOrMinter "
        "{ _burn(account, value); }")
    assert "onlyOwnerOrMinter" in reading["unread_restrictions"][BURN]


def test_an_empty_modifier_restricts_nothing():
    source = ("modifier onlyOwner() { _; } "
              "function _mint(address to, uint256 amount) internal { } "
              "function mint(address to, uint256 amount) external onlyOwner { _mint(to, amount); }")
    reading = _read_with_source(source, "mint(address,uint256)")
    assert reading["source_read_powers"] == ["mint"]
    assert reading["control"] == "unrestricted"


def test_the_overload_with_the_right_arguments_is_the_one_read():
    """burn(uint256) and burn(address,uint256) share a name. The old reader took
    whichever came first in the file."""
    reading = _burn_reading(
        "function burn(uint256 value) public { _burn(_msgSender(), value); } "
        "function burn(address account, uint256 value) external onlyOwner { _burn(account, value); }")
    assert reading["source_read_powers"] == ["burn_others"]


def test_a_declaration_inside_a_comment_is_not_code():
    source = ("// function burn(address account, uint256 value) external { _burn(account, value); }\n"
              "/* function burn(address account, uint256 value) external { _burn(account, value); } */")
    reading = _read_with_source(source, BURN)
    assert "declaration not found" in reading["unread_restrictions"][BURN]


def test_standard_json_input_is_read():
    bundle = {"language": "Solidity", "sources": {
        "Token.sol": {"content": CONTEXT + OWNABLE + ERC20_BURN +
                      "function burn(address account, uint256 value) external onlyOwner "
                      "{ _burn(account, value); }"}}}
    reading = _read_with_source("{" + json.dumps(bundle) + "}", BURN)
    assert reading["source_read_powers"] == ["burn_others"]


# --- when the check is finished ---------------------------------------------

def test_a_proxy_is_never_a_finished_check():
    """Its functions live in an implementation contract this does not read."""
    source = ("function _getAdmin() internal view returns (address) { } "
              "function _upgradeToAndCall(address i, bytes memory d, bool f) internal { } "
              "modifier ifAdmin() { if (msg.sender == _getAdmin()) { _; } else { _fallback(); } } "
              "function upgradeTo(address newImplementation) external ifAdmin "
              "{ _upgradeToAndCall(newImplementation, bytes(''), false); }")
    reading = _read_with_source(source, "upgradeTo(address)", "implementation()")
    assert reading["source_read_powers"] == ["upgrade"]
    assert reading["control"] == "role_gated"
    assert reading["capability_check"] == collector.CAPABILITY_INCOMPLETE
    assert "proxy implementation" in reading["unread_restrictions"]


def test_a_selector_alone_leaves_the_check_unfinished():
    reading = _read("mint(address,uint256)")
    assert reading["capability_check"] == collector.CAPABILITY_INCOMPLETE
    assert reading["unconfirmed_powers"] == ["mint"]


def test_nothing_possible_is_a_finished_check_and_asks_no_explorer():
    """Wrapped AVAX matches only the unwrap. There is nothing to establish, so
    no explorer call is made and the check is complete."""
    def _explorer_must_not_be_called(*_args, **_kwargs):
        raise AssertionError("explorer called for a contract with no possible power")

    original = collector.requests.get
    collector.requests.get = _explorer_must_not_be_called
    try:
        reading = _read_with_source("", "withdraw(uint256)", "owner()")
    finally:
        collector.requests.get = original
    assert reading["capability_check"] == collector.CAPABILITY_COMPLETE
    assert reading["source_status"] == collector.STATUS_NOT_QUERIED


def test_an_unreadable_contract_is_not_run_rather_than_complete():
    def _raise(*_args, **_kwargs):
        raise TimeoutError("rpc")

    original = collector.requests.post
    collector.requests.post = _raise
    try:
        reading = collector.detect_contract_backdoor_avax("0xtest")
    finally:
        collector.requests.post = original
    assert reading["capability_check"] == collector.CAPABILITY_NOT_RUN


def test_an_explorer_error_string_is_a_failed_fetch_not_a_crash():
    class _Get:
        @staticmethod
        def json():
            return {"status": "0", "result": "Max rate limit reached"}

    original = collector.requests.get
    collector.requests.get = lambda *a, **k: _Get()
    try:
        fetched = collector.fetch_verified_source("0xtest")
    finally:
        collector.requests.get = original
    assert fetched["status"] == collector.STATUS_FETCH_FAILED
