"""Retrieval verdict — suite-parity honesty contract (jData side).

Mirrors the agent-facing ``_meta.verdict`` that jCodeMunch and jDocMunch emit on
their search tools: an empty column search is positive, token-saving evidence —
the index can attest "no column matches this" where a nearest-neighbour search
always returns its closest something. Clean-room jData implementation (no
cross-suite import); only the wire shape is shared.

jData search scores are rank-normalized (the top hit is always 1.0), so there is
no calibrated confidence signal to threshold. This tool therefore emits
``ok`` / ``absent`` / ``degraded`` only — no ``low_confidence`` (an honest
divergence from the jDoc/jcm search tools, which do carry a confidence metric).
``degraded`` fires when semantic search was requested but the embedding channel
fell back to keyword-only.
"""

from __future__ import annotations

from typing import Optional, Sequence

STATE_OK = "ok"
STATE_ABSENT = "absent"
STATE_DEGRADED = "degraded"

# Version pin for the ranking/verdict scoring logic. Bump when the semantics
# of the emitted scores or the verdict state machine change, so an agent (or a
# replay fixture) can tell "the data changed" apart from "the scorer changed".
SCORER_VERSION = 1

_NOTES = {
    STATE_OK: "Confident matches returned.",
    STATE_ABSENT: (
        "No column matched after scanning the dataset. Treat this as strong "
        "evidence no such column/value is present; do not reformulate the same "
        "query expecting a hit."
    ),
    STATE_DEGRADED: (
        "Semantic search was requested but the embedding channel was "
        "unavailable, so results are keyword-only and absence is NOT proven. "
        "Configure an embedding provider (or run embed_dataset) for semantic "
        "recall."
    ),
}

#: Why a `degraded` verdict is degraded. `_NOTES[STATE_DEGRADED]` remains the
#: semantic-channel wording and is reached through `semantic_channel`, so the
#: text callers already parse for that case is unchanged.
_DEGRADED_NOTES = {
    "index_rewritten": (
        "The dataset was rewritten while this scan ran, so \"we looked and it "
        "is not there\" describes rows that were moving as we read them; "
        "absence is NOT proven. The embedding channel is unaffected — re-run "
        "once the write settles rather than reconfiguring a provider."
    ),
}


def _note_for(state: str, degraded_because: str = "") -> str:
    """The note for a verdict, keyed on its CAUSE and not only its state.

    Two conditions produce ``degraded`` and they call for opposite actions:
    configure a provider, or wait for a write to finish. Keyed on state alone,
    an index-rewritten degrade was served the semantic-unavailable text, which
    named a channel that was working and prescribed a fix for a problem the
    caller did not have. An unrecognised cause falls back to the state note, so
    a future third cause is merely unspecific rather than wrong — and adding
    one without its note is a gap, never a false statement.
    """
    if state == STATE_DEGRADED and degraded_because in _DEGRADED_NOTES:
        return _DEGRADED_NOTES[degraded_because]
    return _NOTES[state]


def suggest_columns(
    query: str,
    columns: Optional[Sequence[dict]],
    cap: int = 5,
) -> list:
    """Column names containing a query term (near-misses).

    Returned on an absent verdict so the agent can redirect instead of retrying
    the same empty query.
    """
    terms = [t for t in (query or "").lower().split() if len(t) >= 3]
    if not terms or not columns:
        return []
    out: list = []
    seen: set = set()
    for col in columns:
        name = str(col.get("name", ""))
        name_lower = name.lower()
        if name and name not in seen and any(t in name_lower for t in terms):
            seen.add(name)
            out.append(name)
            if len(out) >= cap:
                break
    return out


def build_coverage_disclosure(
    coverage: Optional[dict],
    *,
    indexed_at: Optional[str] = None,
    index_version: Optional[int] = None,
) -> Optional[dict]:
    """Query-time coverage block for a non-``ok`` verdict.

    ``coverage`` is the block persisted in ``index.json`` at ingest time. When
    it is missing (index predates the contract) this returns None — an empty
    block means "coverage unknown" and is never fabricated. Empty fields are
    omitted from the returned dict.
    """
    if not coverage:
        return None
    out: dict = {}
    generation: dict = {}
    if indexed_at:
        generation["indexed_at"] = indexed_at
    if index_version is not None:
        generation["index_version"] = index_version
    if generation:
        out["generation"] = generation
    if coverage.get("walk"):
        out["walk"] = coverage["walk"]
    if coverage.get("rows_indexed") is not None:
        out["rows_indexed"] = coverage["rows_indexed"]
    excluded = {k: v for k, v in (coverage.get("skip_counts") or {}).items() if v}
    if excluded:
        out["excluded"] = excluded
    return out or None


def build_verdict(
    *,
    result_count: int,
    semantic_requested: bool = False,
    semantic_available: bool = True,
    lexical_used: bool = True,
    index_changed: bool = False,
    did_you_mean: Optional[Sequence[str]] = None,
    coverage: Optional[dict] = None,
) -> dict:
    """Compute the ``_meta.verdict`` dict for a column search.

    ``degraded`` takes precedence over ``absent``: a downgraded channel means a
    partial scan, which cannot prove absence.

    ``coverage`` (from :func:`build_coverage_disclosure`) is attached only to
    ``absent`` / ``degraded`` verdicts: an absence claim must disclose what was
    excluded at index time, while ``ok`` verdicts stay lean.
    """
    # ⚠⚠ TWO different conditions produce `degraded`, and they need different
    # notes. Keying the note on the STATE alone gave an index-rewritten
    # degrade the semantic-unavailable text — "the embedding channel was
    # unavailable ... Configure an embedding provider" — in calls where a
    # provider was configured, available and used. The note did not merely
    # omit the cause, it asserted a different one and prescribed a fix for a
    # problem the caller did not have. `degraded_because` is carried so the
    # note describes what actually happened.
    degraded_because = ""
    if semantic_requested and not semantic_available:
        state = STATE_DEGRADED
        degraded_because = "semantic_channel"
    elif result_count == 0 and index_changed:
        # The dataset was rewritten underneath this scan, so "we looked and it
        # is not there" describes rows that were moving while we read them.
        # degraded cannot prove absence, so the refusal falls out of the
        # existing "only `absent` proves absence" check.
        state = STATE_DEGRADED
        degraded_because = "index_rewritten"
    elif result_count == 0:
        state = STATE_ABSENT
    else:
        state = STATE_OK

    if semantic_requested and not semantic_available:
        semantic_channel = "unavailable"
    elif semantic_requested:
        semantic_channel = "ok"
    else:
        semantic_channel = "off"

    verdict = {
        "state": state,
        "scorer": SCORER_VERSION,
        "channels": {
            "lexical": "ok" if lexical_used else "off",
            "semantic": semantic_channel,
        },
        "note": _note_for(state, degraded_because),
    }
    if index_changed:
        # Present ONLY as a positive detection. jData models no index
        # freshness, so a permanent `index: "fresh"` would be a claim this
        # product cannot back — the same honesty that made the stale gate a
        # disclosure here rather than a rule. We can prove it IS being
        # rewritten; we cannot prove it is current.
        verdict["channels"]["index"] = "rebuilding"
    if did_you_mean:
        verdict["did_you_mean"] = list(did_you_mean)[:5]
    if coverage and state in (STATE_ABSENT, STATE_DEGRADED):
        verdict["coverage"] = coverage
    return verdict
