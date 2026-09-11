"""Say, in a sentence, what was found and what could not be established.

Written for Avalanche and deliberately not shared with the Solana service.
Copying field names between services is how `withhold_verdict` came to blank a
label here while leaving every numeric verdict field populated: the code read
correct and the vocabulary underneath it was another chain's. The three fields
are the same; nothing below them is.

Two things this has to say that the Solana one does not.

The first is that `label` here mixes two independent readings. Rug risk is our
claim about the contract and its deployer. Market liquidity risk is a reading of
the pool on the day of the scan. LINK.e is the worked example: rug_status stayed
LOW throughout while its pool drained, the combined label moved to WARN, and a
reader with only the label saw a scanner accusing a Chainlink bridge asset.

The second is the same as everywhere: most of what keeps a token out of GOOD is
something we could not check, and printing that as an unexplained warning
invites the reader to supply a reason we never gave them.
"""

from __future__ import annotations

from typing import Any

FINDING, REFUSAL, GAP = "FINDING", "REFUSAL", "GAP"

# A dimension is "read" unless its status says otherwise. Written as a list of
# the ways a reading can be absent, because the statuses that mean *present*
# are open-ended here: the market dimension reports a risk level ("LOW"), not
# "OK", and treating anything that is not literally OK as unread made a
# perfectly good liquidity reading show up as something we had not checked.
UNREAD_STATUSES = {"", "UNKNOWN", "NOT_COLLECTED", "NOT_QUERIED", "FETCH_FAILED", "NOT_FOUND"}


def _was_read(block: dict[str, Any]) -> bool:
    return str(block.get("status") or "").upper() not in UNREAD_STATUSES


# Powers that let a controller act on someone else's balance. `has_backdoor`
# is not one of them: on this chain it is raised by the presence of a burn
# function, which on canonical assets burns the caller's own tokens. LINK.e
# and wrapped AVAX both come back with has_backdoor true, backdoor_risk_score
# zero, and every specific power false. Leading with that would have this
# service announce a controller power over holders on Chainlink's bridged
# token, in the same response that scores it GOOD.
CONTROLLER_POWERS = (
    "has_drain_function",
    "has_blacklist",
    "has_pause_function",
    "has_upgrade_authority",
    "has_mint_function",
)


def _dimension(evidence: dict[str, Any], name: str) -> dict[str, Any]:
    value = (evidence or {}).get(name)
    return value if isinstance(value, dict) else {}


def not_established(payload: dict[str, Any]) -> list[str]:
    """Plainly: the questions this answer does not settle."""
    evidence = payload.get("evidence") if isinstance(payload.get("evidence"), dict) else {}
    gaps: list[str] = []

    controls = _dimension(evidence, "technical_controls")
    identity = _dimension(evidence, "issuer_identity")
    issuer_known = identity.get("recognised") is True

    if not _was_read(controls):
        gaps.append("whether this contract holds admin or backdoor functions")
    elif controls.get("admin_functions") and not issuer_known:
        gaps.append("who holds the admin functions on this contract")

    if not issuer_known:
        gaps.append("who issued this token")

    # Concentration is measured here, so it is only a gap when unmeasured --
    # and when it is measured, who those holders are is still unestablished.
    if payload.get("v6_top1_concentration_pct") is None:
        gaps.append("how concentrated ownership is")
    elif float(payload.get("v6_top1_concentration_pct") or 0) > 0:
        gaps.append("who the largest holders are")

    creator = _dimension(evidence, "creator_history")
    if not _was_read(creator):
        gaps.append("what this deployer's previous tokens did")
    incidents = creator.get("confirmed_incidents")
    if isinstance(incidents, dict) and not _was_read(incidents):
        gaps.append("whether this token or its deployer has a confirmed incident on record")

    if not _was_read(_dimension(evidence, "market")):
        gaps.append("how deep this token's market is")

    for missing in _dimension(evidence, "coverage").get("missing_inputs") or []:
        gaps.append(f"the reading from {missing}, which could not be collected")

    seen: set[str] = set()
    return [gap for gap in gaps if not (gap in seen or seen.add(gap))]


def _headline(payload: dict[str, Any]) -> tuple[str, str]:
    label = str(payload.get("label") or "").upper()
    rug_status = str(payload.get("rug_status") or "").upper()
    speculation = str(payload.get("speculation_status") or "").upper()
    v6 = payload.get("v6") if isinstance(payload.get("v6"), dict) else {}
    backdoor = v6.get("backdoor") if isinstance(v6.get("backdoor"), dict) else {}

    # DYP, 2026-09-11: every check ran (completeness 100%, no blocking gap) and
    # the verdict was still withheld, because no live pool was found and the
    # market reading has no score. "Too little was readable" described a scan
    # that read everything; say what actually held the verdict back.
    if (label == "INSUFFICIENT_DATA"
            and not (payload.get("blocking_data_gaps") or [])
            and payload.get("has_liquidity_evidence") is False
            and speculation in {"", "UNKNOWN"}):
        return (
            "The contract and holder checks ran, but no live market was found "
            "on supported Avalanche venues, so market risk could not be "
            "measured and no verdict is given. That is our answer, not a clean "
            "bill of health.",
            REFUSAL,
        )
    if label == "INSUFFICIENT_DATA" or payload.get("verdict_is_conclusive") is False:
        return (
            "Too little was readable to judge this token. That is our answer, "
            "not a clean bill of health.",
            REFUSAL,
        )
    powers = [name for name in CONTROLLER_POWERS if backdoor.get(name) is True]
    if powers:
        # Name the functions, not the detector's word for them. `withdraw` is
        # matched by substring and set has_drain_function, so Wrapped AVAX --
        # where withdraw(uint256) burns the caller's own wrapper and returns
        # their own native token -- is reported as holding a drain function.
        # Repeating that word would make this service call the chain's most
        # canonical asset drainable in the same response that scores it GOOD.
        functions = [str(f) for f in (backdoor.get("backdoor_functions") or []) if f]
        named = ", ".join(functions) if functions else "controller functions"
        if payload.get("is_known_chain_asset") is True:
            category = str(payload.get("known_asset_category") or "recognised asset").replace("_", " ")
            return (
                f"A {category} on Avalanche. Its contract exposes {named}, which "
                "is expected for this kind of asset and is reported rather than "
                "read as intent.",
                FINDING,
            )
        return (
            f"This contract exposes {named}, which its controller can call. "
            "What that does here has not been established -- the function was "
            "found by name, not by reading what it does.",
            FINDING,
        )
    if rug_status == "DANGER":
        return "Findings against this contract or its deployer, listed below.", FINDING

    # The label mixes two readings. Where they disagree, say which one moved.
    if rug_status in {"LOW", "MEDIUM"} and speculation in {"HIGH", "DANGER", "MEDIUM"} \
            and label != "GOOD":
        return (
            f"Rug risk reads {rug_status.lower()}; the warning comes from market "
            "liquidity, which is the state of the pool today and not a claim "
            "about the contract or its deployer.",
            FINDING,
        )
    if label == "GOOD":
        return "Nothing found against this token in what was checked.", FINDING
    return "Nothing conclusive was found either way.", GAP


def describe(payload: dict[str, Any]) -> dict[str, Any]:
    """A sentence and a list, both restating fields computed elsewhere."""
    summary, basis = _headline(payload)
    gaps = not_established(payload)

    # Only a GAP earns the reassuring clause. A refusal to judge must never be
    # softened with it -- on a token we could barely read, "not evidence
    # against the token" is the sentence a reader would most like to hear and
    # the one least supported by what we know.
    if basis == GAP and gaps:
        summary += (
            " What is missing is knowledge on our side, not evidence against "
            "the token: see not_established."
        )

    return {"verdict_summary": summary, "verdict_basis": basis, "not_established": gaps}
