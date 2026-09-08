"""What the scanner actually saw, split into claims that can each be checked.

A single score answers "how worried should I be" and hides which question it
answered. Four different things were being folded into one number:

* what the contract's owner **can** do, right now;
* what the **market** looks like -- depth, volume, distribution;
* who the **issuer** is, if that is established at all;
* what this **deployer has done before**;

and a fifth that is not evidence but governs all of it: how much of the above
we actually managed to read.

Those move independently and mean different things. A bridge legitimately keeps
mint authority; a regulated stablecoin legitimately keeps freeze authority. Both
facts are real risk to a holder and both are ordinary operation. Collapsing them
into "risky" or "safe" throws away the only part a reader can act on.

Three rules this module exists to enforce, each written after getting one of
them wrong:

**Missing evidence is UNKNOWN.** Not clean, not guilty. Every dimension carries
its own status, and a dimension we could not read says so instead of defaulting
to a reassuring shape.

**A known issuer does not make its privileges harmless.** Identity contextualises
an authority; it does not cancel it. `technical_controls` reports the authority
whether or not `issuer_identity` recognises the mint.

**Our own past warnings are not confirmed incidents.** `creator_history` reports
scanner labels under a name that says so, with `confirmed_incidents` kept as a
separate, currently uncollected field. Feeding our earlier guesses back as
evidence would let one mistake harden into a record.

This step is deliberately **additive**: it introduces no new judgement and
changes no verdict. `label`, `rug_score`, `rug_status` and the speculation pair
come out of `build_evidence` untouched -- pinned by a test -- so the dimensions
can be reviewed on their own before anything is tuned against them.
"""

from __future__ import annotations

from typing import Any

OK = "OK"
UNKNOWN = "UNKNOWN"
NOT_COLLECTED = "NOT_COLLECTED"


def _status_of(block: Any) -> str:
    """A module's own status, or UNKNOWN when it did not report one."""
    if not isinstance(block, dict) or not block:
        return UNKNOWN
    status = str(block.get("status") or "").upper()
    return status or OK


def technical_controls(report: dict[str, Any]) -> dict[str, Any]:
    """What the contract's controller can do, independent of who they are.

    Reported whether or not the issuer is recognised. A curated identity may
    explain why an authority exists; it does not remove the holder's exposure
    to it, and presenting it as removed is how a wrapped-asset bridge and an
    anonymous mint end up looking the same.
    """
    v6 = report.get("v6") if isinstance(report.get("v6"), dict) else {}
    backdoor = v6.get("backdoor") if isinstance(v6.get("backdoor"), dict) else {}
    admin_functions = list(
        report.get("admin_control_functions")
        or backdoor.get("admin_functions")
        or []
    )
    backdoor_functions = list(backdoor.get("backdoor_functions") or [])

    status = _status_of(backdoor)
    if not backdoor and not admin_functions:
        status = UNKNOWN

    return {
        "status": status,
        "admin_functions": admin_functions,
        "backdoor_functions": backdoor_functions,
        "has_backdoor": backdoor.get("has_backdoor"),
        "reason": backdoor.get("status_reason") or "",
        "note": (
            "Powers the controller holds now. Legitimate for some asset classes "
            "and still exposure for a holder; read alongside issuer_identity, "
            "which never cancels this."
        ),
    }


def market(report: dict[str, Any]) -> dict[str, Any]:
    """Depth, volume and distribution -- the market's state, not the deployer's."""
    block = report.get("market_liquidity_risk")
    block = block if isinstance(block, dict) else {}
    liquidity = report.get("liquidity_usd")
    return {
        "status": _status_of(block) if block else UNKNOWN,
        "risk_status": block.get("status"),
        "risk_score": block.get("score"),
        "reasons": list(block.get("reasons") or []),
        "liquidity_usd": liquidity,
        "fdv": report.get("fdv"),
        "has_liquidity_evidence": report.get("has_liquidity_evidence"),
        "note": (
            "Changes with the market from day to day and is not a claim about "
            "the deployer's intent."
        ),
    }


def issuer_identity(report: dict[str, Any]) -> dict[str, Any]:
    """Whether the mint is a curated, recognised asset.

    Not recognised means **not established**, which is the common case and not
    an accusation. Holder counts and liquidity are deliberately not consulted:
    size is not identity, and treating it as identity is what let an unverified
    mint with concentrated ownership be waved through elsewhere.
    """
    known = report.get("is_known_chain_asset")
    if known is None:
        known = report.get("is_known_avax_asset")

    if known is True:
        return {
            "status": OK,
            "recognised": True,
            "category": report.get("known_asset_category"),
            "basis": "curated_list",
            "note": "On our curated list of canonical assets for this chain.",
        }
    return {
        "status": UNKNOWN,
        "recognised": False,
        "category": None,
        "basis": "curated_list",
        "note": (
            "Not on the curated list. That means the issuer is unestablished "
            "here, not that it is illegitimate."
        ),
    }


def creator_history(report: dict[str, Any]) -> dict[str, Any]:
    """What this deployer's previous tokens were *labelled by us*.

    The field names carry the distinction because the numbers cannot: these are
    counts of our own earlier scanner verdicts, not independently confirmed rug
    events. Presenting them as confirmed would let a single earlier mistake
    harden into evidence and then justify the next one.

    `confirmed_incidents` is where sourced, dated, deduplicated events belong.
    It reports NOT_COLLECTED until such a store exists, rather than reporting
    zero -- "we have not looked" and "there were none" are different answers.
    """
    stats = report.get("creator_stats") if isinstance(report.get("creator_stats"), dict) else {}
    total = stats.get("total")
    status = str(stats.get("status") or "").upper()

    if not stats or total is None:
        status = UNKNOWN
    elif not status:
        status = OK if total else UNKNOWN

    return {
        "status": status,
        "deployer": report.get("deployer"),
        "prior_tokens_scanned_by_us": total,
        "prior_tokens_we_labelled_danger": stats.get("danger"),
        # Both names: this branch is cut from main, while the creator-history
        # branch renames the field to say whose labels it counts. Reading only
        # one of them would leave this silently empty depending on merge order.
        "prior_danger_rate_pct": stats.get("prior_danger_rate_pct", stats.get("rug_rate")),
        "reason": stats.get("status_reason") or "",
        "confirmed_incidents": {
            "count": None,
            "status": NOT_COLLECTED,
            "note": (
                "Sourced, dated incidents belong here. No such store exists yet, "
                "so this is not zero -- it is uncollected."
            ),
        },
        "note": (
            "Counts of our own earlier verdicts on this deployer's tokens. Not "
            "independently confirmed rug events."
        ),
    }


def coverage(report: dict[str, Any]) -> dict[str, Any]:
    """How much of the above we managed to read, and how current it is."""
    return {
        "status": OK if report.get("completeness_pct") is not None else UNKNOWN,
        "completeness_pct": report.get("completeness_pct"),
        "missing_inputs": list(report.get("missing_inputs") or []),
        "verdict_is_conclusive": report.get("verdict_is_conclusive"),
        "data_freshness": report.get("data_freshness"),
        # Three moments under three names. `observed_at` is null because no
        # upstream here returns a verified observation time, and a scalar
        # timestamp must not imply that Routescan, DexScreener, Glacier and the
        # chain all looked at the token at the same instant.
        "computed_at": report.get("computed_at"),
        "served_at": report.get("served_at"),
        "observed_at": report.get("observed_at"),
        "observation_coverage": report.get("observation_coverage"),
        "verdict_age_seconds": report.get("verdict_age_seconds"),
        "note": (
            "Governs how far the other dimensions can be trusted. A dimension "
            "listed in missing_inputs was not read, not read as clean. "
            "computed_at is when we produced the verdict; observed_at stays "
            "null because no upstream gives us a verified observation time."
        ),
    }


DIMENSIONS = {
    "technical_controls": technical_controls,
    "market": market,
    "issuer_identity": issuer_identity,
    "creator_history": creator_history,
    "coverage": coverage,
}


def build_evidence(report: dict[str, Any]) -> dict[str, Any]:
    """The five dimensions, each derived only from its own inputs.

    No dimension reads another's conclusion. That independence is the point:
    once one can soften another, the response is a single score again wearing
    five labels.
    """
    return {name: builder(report) for name, builder in DIMENSIONS.items()}
