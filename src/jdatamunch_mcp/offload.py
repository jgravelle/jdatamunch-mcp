"""Offloadable-work classification: is this payload grunt-work for a cheaper model?

The question this answers is deliberately narrow. Not "which model should run
this" (that is a claim about someone else's model that we cannot test), but
"is the work this payload enables **extractive, self-contained and free of
evidence gaps**" (a property of our own output that we can substantiate).

We label. We never route, execute, or hold anyone's credentials.

Three rules shape everything below:

1. **Tri-state, never boolean.** ``not_evaluated`` is not ``not_offloadable``.
   A classifier that was never given enough to run must not render as a
   negative verdict, for the same reason an unmeasured signal must not score
   as zero.

2. **Fail closed.** Every unknown that bears on the answer disqualifies. A
   false ``offloadable`` sends real work to a model that will confabulate over
   the gap; a false ``not_offloadable`` costs nothing but a missed saving.

3. **Domain-neutral vocabulary.** ``EvidenceShape`` speaks in *units* and
   *containers*, not symbols and files, so jDocMunch (sections/documents) and
   jDataMunch (columns/datasets) map onto the identical contract. Suite parity
   is a contract obligation, so the vocabulary has to be neutral from the first
   commit rather than retrofitted.

This module is pure. It performs no I/O, imports nothing from the server, and
is NOT registered as an MCP tool (it adds no new top-level action). Callers build an
:class:`EvidenceShape` from a response they already hold.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional

# Bumped whenever a rule below changes the verdict for an unchanged input.
# Any published label rate is only comparable against the same version.
CRITERION_VERSION = 2

# --- verdict states -------------------------------------------------------

OFFLOADABLE = "offloadable"
NOT_OFFLOADABLE = "not_offloadable"
NOT_EVALUATED = "not_evaluated"

# --- qualifier codes ------------------------------------------------------

Q_EXTRACTIVE = "EXTRACTIVE"
Q_SELF_CONTAINED = "SELF_CONTAINED"
Q_SINGLE_CONTAINER = "SINGLE_CONTAINER"
Q_WIDE_RANK_MARGIN = "WIDE_RANK_MARGIN"
Q_IDENTITY_LOOKUP = "IDENTITY_LOOKUP"
Q_NO_ABSENCE_FLAGS = "NO_ABSENCE_FLAGS"

# --- disqualifier codes ---------------------------------------------------

D_STALE_INDEX = "STALE_INDEX"
D_TRI_STATE_UNKNOWN = "TRI_STATE_UNKNOWN"
D_INCOMPLETE_COVERAGE = "INCOMPLETE_COVERAGE"
D_ABSENCE_CLAIM = "ABSENCE_CLAIM"
D_DEGRADED_VERDICT = "DEGRADED_VERDICT"
D_NO_BODIES = "NO_BODIES"
D_CROSS_CONTAINER_SYNTHESIS = "CROSS_CONTAINER_SYNTHESIS"
D_GRAPH_TRAVERSAL = "GRAPH_TRAVERSAL"
D_NARROW_RANK_MARGIN = "NARROW_RANK_MARGIN"
D_TRUNCATED = "TRUNCATED"

# --- thresholds -----------------------------------------------------------

#: Top-1 must beat top-2 by this fraction of top-1 for a ranked retrieval to
#: count as unambiguous. Must come from this server's OWN confidence component rather than a
#: second derivation: two code paths settling one question is a defect class
#: the suite has paid for before.
MIN_RANK_MARGIN = 0.25

#: Above this many distinct containers the answer stops being a lookup and
#: starts being synthesis. Deliberately small.
MAX_CONTAINERS = 2

#: Freshness readings that permit a label at all. Everything else, including
#: ``unknown``, disqualifies.
_FRESH_ENOUGH = frozenset({"fresh", "not_tracked"})

#: Retrieval modes. ``identity`` means the caller named the subject (a direct
#: id lookup), so there is no ranking to be ambiguous about and a missing rank
#: margin is Not Applicable rather than Unknown. Conflating those two is how a
#: perfectly unambiguous lookup gets scored as a narrow margin.
MODE_IDENTITY = "identity"
MODE_RANKED = "ranked"


@dataclass(frozen=True)
class EvidenceShape:
    """Normalized description of a retrieval payload, domain-neutral.

    Every field distinguishes ``None`` (we could not establish this) from a
    measured value. ``None`` is never silently read as a negative: it either
    disqualifies via :data:`D_TRI_STATE_UNKNOWN` or, when it means the
    classifier cannot run at all, produces :data:`NOT_EVALUATED`.
    """

    #: Symbols / sections / columns actually returned in the body.
    unit_count: Optional[int] = None

    #: Distinct files / documents / datasets those units came from.
    container_count: Optional[int] = None

    #: Is the full text of each unit in the payload, as opposed to a reference,
    #: signature or summary the consumer would have to go fetch? ``None`` means
    #: the caller did not establish it.
    bodies_present: Optional[bool] = None

    #: True when any returned body was truncated or bounded. A partial body can
    #: cut mid-token, so the consumer cannot tell absence from truncation.
    truncated: Optional[bool] = None

    #: ``identity`` or ``ranked``. See :data:`MODE_IDENTITY`.
    retrieval_mode: Optional[str] = None

    #: 0-1 top-1 vs top-2 dominance, from this server's own confidence
    #: component. Meaningful only when ``retrieval_mode == "ranked"``.
    rank_margin: Optional[float] = None

    #: Repo/corpus freshness: fresh / stale / unknown / not_tracked.
    freshness: Optional[str] = None

    #: Tri-state corpus coverage. ``None`` means the index predates the field
    #: or could not report, which is Unknown and disqualifies.
    coverage_complete: Optional[bool] = None

    #: Response verdict state (``ok`` / ``degraded`` / ...).
    verdict_state: Optional[str] = None

    #: True when the payload asserts that something is absent. An absence claim
    #: is precisely where a small model invents, so it is never offloadable
    #: regardless of how clean the rest of the shape reads.
    has_absence_claim: bool = False

    #: True when answering requires walking a graph (call hierarchy, blast
    #: radius, dependency cycles) rather than reading what was returned.
    graph_traversal: bool = False

    #: Tool call that would adjudicate a downstream answer over this payload,
    #: e.g. ``{"tool": "<verifier>", "args": {...}}``. Optional, but a
    #: label without one requires the consumer to trust us.
    verify_with: Optional[dict] = None

    #: Free-form provenance for the harness. Never part of the verdict.
    notes: tuple = field(default_factory=tuple)


def _makes_corpus_claim(shape: EvidenceShape) -> bool:
    """Does this payload assert something about the corpus as a whole?

    True for a ranked ordering (implicitly "these are the best matches in the
    corpus") and for any absence claim. False for an identity lookup, which
    asserts only that the named subject has this content.
    """
    return bool(shape.has_absence_claim or shape.retrieval_mode == MODE_RANKED)


def classify(shape: EvidenceShape) -> dict:
    """Return an offloadable verdict for *shape*.

    The verdict is a dict, not a score::

        {
          "offloadable": "offloadable" | "not_offloadable" | "not_evaluated",
          "qualifiers": [...],
          "disqualifiers": [...],
          "shape": {"units": int|None, "containers": int|None},
          "verify_with": {...} | None,
          "criterion_version": int,
        }

    A bare number would be unauditable and would invite optimizing the label
    rate, which is the metric most likely to be gamed the moment anyone reports
    on it. Reason codes make every verdict explainable and falsifiable.
    """
    base = {
        "shape": {"units": shape.unit_count, "containers": shape.container_count},
        "criterion_version": CRITERION_VERSION,
    }

    # --- can the classifier run at all? -----------------------------------
    # Distinct from "it ran and said no". A caller that supplied neither a unit
    # count nor a body reading has not given us a payload to judge.
    if shape.unit_count is None and shape.bodies_present is None:
        return {
            **base,
            "offloadable": NOT_EVALUATED,
            "qualifiers": [],
            "disqualifiers": [],
            "verify_with": None,
            "reason": "no payload shape supplied",
        }

    # An empty result set is not grunt-work; there is nothing to do with it,
    # and if it carries an absence claim it is actively hazardous.
    if shape.unit_count == 0:
        return {
            **base,
            "offloadable": NOT_OFFLOADABLE,
            "qualifiers": [],
            "disqualifiers": [D_ABSENCE_CLAIM] if shape.has_absence_claim else [],
            "verify_with": None,
            "reason": "empty result set",
        }

    disqualifiers: list[str] = []
    qualifiers: list[str] = []

    # --- evidence integrity ------------------------------------------------
    # These fire first and independently. A payload can be structurally perfect
    # and still be unfit because we cannot vouch for what it rests on.
    if shape.has_absence_claim:
        disqualifiers.append(D_ABSENCE_CLAIM)

    if shape.freshness is None:
        disqualifiers.append(D_TRI_STATE_UNKNOWN)
    elif shape.freshness == "stale":
        disqualifiers.append(D_STALE_INDEX)
    elif shape.freshness not in _FRESH_ENOUGH:
        # "unknown" and any future reading we do not recognise land here.
        disqualifiers.append(D_TRI_STATE_UNKNOWN)

    # Corpus coverage bears on a payload that makes a claim ABOUT the corpus:
    # a ranked ordering ("these are the best matches") or an absence ("it is
    # not there"). It does not bear on an identity lookup, where the caller
    # named the subject and we returned it: whether some other file went
    # unindexed cannot make this body wrong.
    #
    # ⚠ Requiring it universally was the criterion's first measured defect.
    # It disqualified 100% of both arms on TRI_STATE_UNKNOWN, because
    # `verdict.coverage` ships only on the absence-capable tools. Treating an
    # inapplicable field as Unknown is the same category error as scoring an
    # identity lookup's absent rank margin as ambiguity.
    if _makes_corpus_claim(shape):
        if shape.coverage_complete is None:
            disqualifiers.append(D_TRI_STATE_UNKNOWN)
        elif shape.coverage_complete is False:
            disqualifiers.append(D_INCOMPLETE_COVERAGE)

    if shape.verdict_state is not None and shape.verdict_state != "ok":
        disqualifiers.append(D_DEGRADED_VERDICT)

    # --- is the answer actually in the payload? ---------------------------
    if shape.bodies_present is None:
        disqualifiers.append(D_TRI_STATE_UNKNOWN)
    elif shape.bodies_present is False:
        disqualifiers.append(D_NO_BODIES)
    else:
        qualifiers.append(Q_EXTRACTIVE)

    if shape.truncated:
        disqualifiers.append(D_TRUNCATED)

    # --- shape of the work -------------------------------------------------
    if shape.graph_traversal:
        disqualifiers.append(D_GRAPH_TRAVERSAL)

    if shape.container_count is not None:
        if shape.container_count > MAX_CONTAINERS:
            disqualifiers.append(D_CROSS_CONTAINER_SYNTHESIS)
        elif shape.container_count == 1:
            qualifiers.append(Q_SINGLE_CONTAINER)

    # --- ranking ambiguity -------------------------------------------------
    # An identity lookup has no ranking to be ambiguous about. Treating its
    # absent margin as Unknown would disqualify the single cleanest case we
    # have, which is why mode is carried explicitly.
    if shape.retrieval_mode == MODE_IDENTITY:
        qualifiers.append(Q_IDENTITY_LOOKUP)
    elif shape.retrieval_mode == MODE_RANKED:
        if shape.rank_margin is None:
            disqualifiers.append(D_TRI_STATE_UNKNOWN)
        elif shape.rank_margin < MIN_RANK_MARGIN:
            disqualifiers.append(D_NARROW_RANK_MARGIN)
        else:
            qualifiers.append(Q_WIDE_RANK_MARGIN)
    else:
        # Mode not stated: we cannot tell N/A from Unknown, so fail closed.
        disqualifiers.append(D_TRI_STATE_UNKNOWN)

    # --- derived qualifiers ------------------------------------------------
    if not shape.has_absence_claim and shape.freshness in _FRESH_ENOUGH:
        qualifiers.append(Q_NO_ABSENCE_FLAGS)

    if Q_EXTRACTIVE in qualifiers and not shape.truncated and not shape.graph_traversal:
        qualifiers.append(Q_SELF_CONTAINED)

    # Dedupe while preserving declaration order, so a repeated TRI_STATE_UNKNOWN
    # from three separate unknowns reads once. The count of unknowns is not the
    # signal; the presence of any is.
    disqualifiers = list(dict.fromkeys(disqualifiers))

    verdict = NOT_OFFLOADABLE if disqualifiers else OFFLOADABLE
    return {
        **base,
        "offloadable": verdict,
        "qualifiers": qualifiers if verdict == OFFLOADABLE else [],
        "disqualifiers": disqualifiers,
        # A label the consumer cannot check is a label that requires trusting
        # us. Carried only on a positive verdict, where it has work to do.
        "verify_with": shape.verify_with if verdict == OFFLOADABLE else None,
    }


#: Env gate. Absent or anything but a truthy token means the annotation is not
#: emitted at all.
#:
#: ⚠ Deliberately an ENV FLAG rather than a tool parameter. A new parameter
#: shows up in `tools/list`, and that schema is a public artifact the moment a
#: release ships — it would announce the work before the work is ready to be
#: announced. An env gate changes no schema, adds no catalog action, and is
#: reversible. Revisit when the annotation is ready to be public; a parameter
#: (or an always-on field) is the better long-term shape.
ENV_GATE = "JDATAMUNCH_OFFLOADABLE"

#: Suite-wide switch, honoured by jCodeMunch, jDocMunch and jDataMunch alike.
#:
#: ⚠ Discovered by doing the port, not designed up front: modelmaxxing is a
#: decision a client makes ONCE, about a whole session. Making an operator set
#: three separate variables to express one intention is a contract defect, so
#: the shared name is the primary switch and the per-product one is an
#: override. The per-product variable wins when both are set, because a
#: narrower scope beating a broader one is the only precedence that lets
#: someone disable a single server.
ENV_GATE_SUITE = "JMUNCH_OFFLOADABLE"

_TRUTHY = frozenset({"1", "true", "yes", "on"})
_FALSY = frozenset({"0", "false", "no", "off"})


def is_enabled(environ=None) -> bool:
    """Is the annotation switched on? Default OFF.

    Per-product gate first (it can turn the suite switch OFF for this server
    alone), then the suite gate.
    """
    import os

    env = os.environ if environ is None else environ
    own = (env.get(ENV_GATE) or "").strip().lower()
    if own in _TRUTHY:
        return True
    if own in _FALSY:
        return False
    return (env.get(ENV_GATE_SUITE) or "").strip().lower() in _TRUTHY


# --- the suite contract ---------------------------------------------------
#
# ⚠⚠ The three servers do NOT share a package: each munch is an independent
# product with its own PyPI release, and nothing imports named symbols across
# them. So this module is COPIED, and copies drift — the exact defect that
# shipped as jcm v1.108.240, where three renderings of one expression fell out
# of step and two of them over-claimed.
#
# The digest is the mitigation available without a shared dependency. It covers
# every part of the contract a consumer can observe. It is pinned as the same
# literal in all three repositories, so a unilateral change to any of them
# fails that repository's own test and forces the change to be made in all
# three deliberately.


def contract_surface() -> dict:
    """Everything a consumer of this annotation can observe."""
    return {
        "criterion_version": CRITERION_VERSION,
        "states": sorted([OFFLOADABLE, NOT_OFFLOADABLE, NOT_EVALUATED]),
        "qualifiers": sorted(
            v for k, v in globals().items() if k.startswith("Q_") and isinstance(v, str)
        ),
        "disqualifiers": sorted(
            v for k, v in globals().items() if k.startswith("D_") and isinstance(v, str)
        ),
        "shape_fields": sorted(EvidenceShape.__dataclass_fields__),
        "thresholds": {
            "MIN_RANK_MARGIN": MIN_RANK_MARGIN,
            "MAX_CONTAINERS": MAX_CONTAINERS,
        },
        "modes": sorted([MODE_IDENTITY, MODE_RANKED]),
        "suite_gate": ENV_GATE_SUITE,
    }


def contract_digest() -> str:
    import hashlib
    import json

    canonical = json.dumps(contract_surface(), sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()[:16]


#: ⚠ Pinned. Identical in jcodemunch-mcp, jdocmunch-mcp and jdatamunch-mcp.
#: Changing the contract means changing this literal in ALL THREE, in one go.
CONTRACT_DIGEST = "3d4cf47e24fac358"


def annotate(response: dict, *, environ=None, **shape_kwargs) -> dict:
    """Attach ``_meta.offloadable`` to *response* in place, if gated on.

    Returns the same dict either way, so a caller can use it as a pass-through.

    Three properties this must have, and each is load-bearing:

    1. **Off by default.** No gate, no field, no cost.
    2. **Never raises.** This is an advisory annotation on somebody else's
       answer. A classifier defect must not be able to fail a retrieval that
       otherwise succeeded, so every error path degrades to "no annotation"
       rather than propagating.
    3. **Never mutates the answer.** It only adds one ``_meta`` key. Nothing
       here may touch ``source``, ``symbols`` or ``verdict``.
    """
    try:
        if not is_enabled(environ):
            return response
        if not isinstance(response, dict):
            return response
        meta = response.get("_meta")
        if not isinstance(meta, dict):
            meta = {}
            response["_meta"] = meta
        meta["offloadable"] = classify(shape_from_response(response, **shape_kwargs))
    except Exception:  # pragma: no cover - defensive, see property 2
        pass
    return response


def not_evaluated(response: dict, *, reason: str, environ=None) -> dict:
    """Attach an explicit ``not_evaluated`` verdict, if gated on.

    ⚠ For payloads carrying no evidence to judge (an error result). When the
    gate is ON, silence would be ambiguous with the gate being OFF, and
    "we did not assess it" is a THIRD state that has to be sayable — the same
    reason the verdict is tri-state in the first place.
    """
    try:
        if not is_enabled(environ) or not isinstance(response, dict):
            return response
        meta = response.get("_meta")
        if not isinstance(meta, dict):
            meta = {}
            response["_meta"] = meta
        meta["offloadable"] = {
            "offloadable": NOT_EVALUATED,
            "qualifiers": [],
            "disqualifiers": [],
            "shape": {"units": None, "containers": None},
            "verify_with": None,
            "criterion_version": CRITERION_VERSION,
            "reason": reason,
        }
    except Exception:  # pragma: no cover
        pass
    return response


def shape_from_response(
    response: dict,
    *,
    retrieval_mode: Optional[str] = None,
    body_field: str = "source",
    unit_field: str = "results",
    container_field: str = "file",
    graph_traversal: bool = False,
    verify_with: Optional[dict] = None,
    rank_margin: Optional[float] = None,
    units: Optional[list] = None,
) -> EvidenceShape:
    """Build an :class:`EvidenceShape` from a jCodeMunch tool response.

    Reads only fields the response already carries. The field names are
    parameters rather than constants precisely so the sibling servers can point
    this at ``sections``/``document`` or ``columns``/``dataset`` without a
    second implementation of the same logic.

    Anything this cannot establish is left ``None`` rather than defaulted.
    A default here would be indistinguishable from a measurement downstream,
    which is the whole failure mode the tri-states exist to prevent.
    """
    meta = response.get("_meta") or {}
    verdict = meta.get("verdict") or {}
    coverage = verdict.get("coverage") or {}

    # An explicit list wins. Some payloads nest the unit inside a wrapper
    # (jDocMunch's batch entries are ``{"section": {...}}``), so the caller
    # that knows the layout hands over the real units rather than this module
    # growing a per-product unwrapping rule it would have to keep in step.
    if units is None:
        units = response.get(unit_field)
    if units is None and "symbols" in response:
        units = response.get("symbols")
    if isinstance(units, dict):
        units = [units]
    unit_list = list(units) if isinstance(units, (list, tuple)) else None

    # A flat single-symbol response (get_symbol_source with symbol_id) is one
    # unit, not zero. Shape-follows-input means the body IS the response.
    if unit_list is None and body_field in response:
        unit_list = [response]

    unit_count = len(unit_list) if unit_list is not None else None

    containers = None
    if unit_list:
        seen = {
            u.get(container_field)
            for u in unit_list
            if isinstance(u, dict) and u.get(container_field)
        }
        containers = len(seen) if seen else None

    bodies_present = None
    truncated = None
    if unit_list:
        readings = [
            bool(u.get(body_field)) for u in unit_list if isinstance(u, dict)
        ]
        if readings:
            bodies_present = all(readings)
            truncated = any(
                bool(u.get("source_truncated")) or bool(u.get("truncated"))
                for u in unit_list
                if isinstance(u, dict)
            )

    # Repo-level freshness, preferring the explicit reading over the boolean.
    freshness = meta.get("repo_freshness")
    if freshness is None:
        fresh_block = meta.get("freshness")
        if isinstance(fresh_block, dict):
            # Per-entry buckets: any stale or unknown entry taints the payload.
            if fresh_block.get("stale_index"):
                freshness = "stale"
            elif fresh_block.get("unknown"):
                freshness = "unknown"
            elif fresh_block.get("edited_uncommitted"):
                freshness = "stale"
            elif fresh_block.get("fresh"):
                freshness = "fresh"
    if freshness is None:
        channel = (verdict.get("channels") or {}).get("index")
        if channel in {"fresh", "stale", "unknown", "not_tracked"}:
            freshness = channel

    # ⚠ Precedence is deliberate: an explicitly supplied margin wins over the
    # response's own. The caller may hold `compute_confidence`'s components for
    # a payload whose producing tool does not yet put them in ``_meta`` — which
    # is presently EVERY ranked tool, measured 2026-08-04. Passing None here
    # leaves the response as the only source, so the default path is unchanged.
    #
    # The margin must still come from `compute_confidence`, never from a second
    # derivation. Two code paths settling one question is a defect class we
    # have already paid for.
    if rank_margin is None:
        components = meta.get("confidence_components")
        if isinstance(components, dict):
            gap = components.get("gap")
            if isinstance(gap, (int, float)):
                rank_margin = float(gap)
    else:
        rank_margin = float(rank_margin)

    absence = bool(
        response.get("absent")
        or meta.get("absence")
        or (verdict.get("state") == "absent")
    )

    return EvidenceShape(
        unit_count=unit_count,
        container_count=containers,
        bodies_present=bodies_present,
        truncated=truncated,
        retrieval_mode=retrieval_mode,
        rank_margin=rank_margin,
        freshness=freshness,
        coverage_complete=coverage.get("complete"),
        verdict_state=verdict.get("state"),
        has_absence_claim=absence,
        graph_traversal=graph_traversal,
        verify_with=verify_with,
    )
