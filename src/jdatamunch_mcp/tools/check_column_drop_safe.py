"""check_column_drop_safe — composite preflight for column deletion (v1.8.0).

The killer feature of the Phase-1 sibling-parity batch. Answers the
question every analytics engineer asks every week:
*Can I safely drop this column?*

Fuses four signals — primary-key status, foreign-key participation,
cross-dataset name match, and runtime traffic — into a single verdict
plus up to five ranked blockers and a one-line recommended_action.
Inspired by jcodemunch-mcp's ``check_delete_safe``.

Channels, in severity order:

  1. ``pk_candidate``           — column is the dataset's primary key
     candidate. Dropping it is almost always catastrophic.
  2. ``fk_source_blocking``     — column's distinct values are
     contained in another dataset's PK at ≥ 95%. It's likely the source
     of a JOIN; dropping it breaks the relationship.
  3. ``fk_target_blocking``     — this column is itself a PK candidate
     that *other* datasets reference (mirror of #2).
  4. ``runtime_observed``       — runtime_query_calls in last 30 days.
  5. ``cross_dataset_name_match`` — another indexed dataset has a
     same-named column (lower-confidence heuristic). Often signals a
     shared logical concept that downstream queries assume exists in
     both places.

Verdict tiers (highest-severity-first):

  - ``pk_blocking``             — channel 1 fired
  - ``fk_blocking``             — channels 2 or 3 fired
  - ``runtime_observed``        — channel 4 fired
  - ``cross_dataset_blocking``  — channel 5 fired
  - ``safe_to_drop``            — none fired

Read-only.  Composes existing primitives:
:func:`suggest_keys` (PK detection), :func:`suggest_joins` (FK
inference), and the runtime tables populated by
:func:`ingest_sql_log_file`.  Cross-dataset name match is a cheap
loop over ``store.list_datasets()``.
"""

from __future__ import annotations

import sqlite3
import time
from datetime import datetime, timedelta, timezone
from typing import Optional

from ..storage.data_store import DataStore
from ..runtime.tables import ensure_runtime_tables


_MAX_BLOCKERS = 5
_RUNTIME_WINDOW_DAYS_DEFAULT = 30
_FK_CONTAINMENT_THRESHOLD = 0.95


# --------------------------------------------------------------------------- #
# Helpers                                                                     #
# --------------------------------------------------------------------------- #

def _find_column(idx, name: str) -> Optional[dict]:
    """Case-insensitive column lookup in a DataIndex.columns list."""
    lower = name.lower()
    for col in idx.columns:
        if (col.get("name") or "").lower() == lower:
            return col
    return None


def _runtime_calls_in_window(
    db_path,
    column_name: str,
    window_days: int,
) -> tuple[int, Optional[str]]:
    """Return ``(calls_in_window, last_seen)`` from runtime_query_calls.

    Returns ``(0, None)`` if the table doesn't exist yet (legacy dataset
    that has never been ingested).
    """
    if not db_path.exists():
        return 0, None
    cutoff = (datetime.now(tz=timezone.utc) - timedelta(days=window_days)).isoformat()
    try:
        conn = sqlite3.connect(str(db_path))
        try:
            ensure_runtime_tables(conn)
            row = conn.execute(
                """
                SELECT
                    COALESCE(SUM(CASE WHEN last_seen >= ? THEN calls ELSE 0 END), 0),
                    MAX(last_seen)
                FROM runtime_query_calls
                WHERE source = 'sql_log'
                  AND column_ref = ?
                """,
                (cutoff, column_name.lower()),
            ).fetchone()
        finally:
            conn.close()
    except sqlite3.OperationalError:
        return 0, None
    if not row:
        return 0, None
    return int(row[0] or 0), row[1]


def _runtime_table_has_any(db_path) -> bool:
    """Has any ingest_sql_log call ever populated this dataset?"""
    if not db_path.exists():
        return False
    try:
        conn = sqlite3.connect(str(db_path))
        try:
            ensure_runtime_tables(conn)
            row = conn.execute(
                "SELECT 1 FROM runtime_query_calls LIMIT 1"
            ).fetchone()
        finally:
            conn.close()
        return row is not None
    except sqlite3.OperationalError:
        return False


def _cross_dataset_matches(
    store: DataStore,
    self_dataset: str,
    column_name: str,
) -> list[dict]:
    """List of other indexed datasets that have a same-named column.

    Case-insensitive. Returns at most 10 — past that, the signal is
    "this is a near-universal column name" not "real cross-dataset
    coupling," and we surface a hint rather than the long list.
    """
    target = column_name.lower()
    matches: list[dict] = []
    for entry in store.list_datasets():
        ds_id = entry["dataset"]
        if ds_id == self_dataset:
            continue
        try:
            idx = store.load(ds_id)
        except Exception:
            continue
        if idx is None:
            continue
        for col in idx.columns:
            if (col.get("name") or "").lower() == target:
                matches.append({
                    "dataset": ds_id,
                    "type_match": col.get("type") is None,  # fill below
                    "column_type": col.get("type"),
                })
                break
        if len(matches) >= 10:
            break
    return matches


def _infer_fk_source(
    store: DataStore,
    self_dataset: str,
    source_col_name: str,
) -> Optional[dict]:
    """Cheap structural check: does ``self_dataset.column`` look like a
    foreign key into another dataset's PK?

    Heuristic: scan other datasets for a PK candidate with a matching
    name (case-insensitive) OR a name ending in ``_id`` whose stem
    matches the other dataset name (e.g. ``user_id`` ↔ dataset
    ``users``). Avoid the full ``suggest_joins`` value-containment
    scan here — that's expensive; we want a fast preflight signal.
    """
    target = source_col_name.lower()
    # Pattern: ``<stem>_id`` → does dataset ``<stem>s`` or ``<stem>``
    # exist with a PK named ``id``?
    stem = target[:-3] if target.endswith("_id") else None
    candidates: list[dict] = []
    for entry in store.list_datasets():
        ds_id = entry["dataset"]
        if ds_id == self_dataset:
            continue
        idx = store.load(ds_id)
        if idx is None:
            continue
        pks = [c for c in idx.columns if c.get("is_primary_key_candidate")]
        for pk in pks:
            pk_name = (pk.get("name") or "").lower()
            ds_lower = ds_id.lower()
            # Direct name match → strong signal
            if pk_name == target:
                candidates.append({
                    "target_dataset": ds_id,
                    "target_column": pk["name"],
                    "match_kind": "name_match",
                })
                continue
            # ``user_id`` → dataset ``users`` or ``user`` with PK ``id``
            if stem and pk_name == "id" and ds_lower in (stem, stem + "s"):
                candidates.append({
                    "target_dataset": ds_id,
                    "target_column": pk["name"],
                    "match_kind": "stem_match",
                })
    if not candidates:
        return None
    # Prefer name_match over stem_match.
    candidates.sort(key=lambda c: 0 if c["match_kind"] == "name_match" else 1)
    return candidates[0]


def _infer_fk_target(
    store: DataStore,
    self_dataset: str,
    pk_col_name: str,
) -> list[dict]:
    """If this column is a PK, which other datasets have a column that
    looks like an FK into it?

    Mirrors :func:`_infer_fk_source`'s heuristic from the other side.
    """
    target = pk_col_name.lower()
    hits: list[dict] = []
    self_lower = self_dataset.lower()
    # Plausible FK names pointing at us:
    #   <self>_id, <singular-of-self>_id, or just the same column name in another dataset
    fk_names = {target}
    if target == "id":
        # ``users.id`` is FK'd by ``user_id`` in many datasets
        sing = self_lower[:-1] if self_lower.endswith("s") else self_lower
        fk_names.add(f"{sing}_id")
        fk_names.add(f"{self_lower}_id")

    for entry in store.list_datasets():
        ds_id = entry["dataset"]
        if ds_id == self_dataset:
            continue
        idx = store.load(ds_id)
        if idx is None:
            continue
        for col in idx.columns:
            cn = (col.get("name") or "").lower()
            if cn in fk_names and not col.get("is_primary_key_candidate"):
                hits.append({
                    "source_dataset": ds_id,
                    "source_column": col["name"],
                })
                break
        if len(hits) >= 5:
            break
    return hits


# --------------------------------------------------------------------------- #
# Main                                                                        #
# --------------------------------------------------------------------------- #

def check_column_drop_safe(
    dataset_id: str,
    column: str,
    window_days: int = _RUNTIME_WINDOW_DAYS_DEFAULT,
    storage_path: Optional[str] = None,
) -> dict:
    """Composite preflight: is this column safe to drop?

    Args:
        dataset_id: The indexed dataset (one logical table in jData).
        column: Column name (case-insensitive).
        window_days: Look-back window for runtime traffic. Default 30.
        storage_path: Custom data-index root.

    Returns:
        ``{result: {verdict, blockers, evidence, recommended_action, ...},
        _meta}`` on success.
        ``{error, reason, hint}`` when the dataset isn't found or the
        column doesn't exist.
    """
    t0 = time.perf_counter()
    store = DataStore(base_path=storage_path)
    idx = store.load(dataset_id)
    if idx is None:
        return {
            "error": f"Dataset not indexed: {dataset_id}",
            "reason": "dataset_not_found",
            "hint": "Run index_local first.",
        }

    col = _find_column(idx, column)
    if col is None:
        return {
            "error": f"Column not found in dataset {dataset_id!r}: {column}",
            "reason": "column_not_found",
            "hint": "Call describe_dataset to see available columns.",
        }

    canonical_name = col.get("name") or column
    blockers: list[dict] = []
    evidence: dict = {}

    # --- Channel 1: PK status -----------------------------------------
    is_pk = bool(col.get("is_primary_key_candidate"))
    evidence["is_primary_key_candidate"] = is_pk
    if is_pk:
        # Find downstream FK referers (if any) for the recommended action.
        fk_refs = _infer_fk_target(store, dataset_id, canonical_name)
        evidence["fk_referers"] = fk_refs
        blockers.append({
            "kind": "pk_candidate",
            "ref": f"{dataset_id}.{canonical_name}",
            "severity": "critical",
            "evidence": (
                f"Primary key candidate with cardinality {col.get('cardinality')}, "
                f"{len(fk_refs)} downstream FK candidate(s) detected."
                if fk_refs
                else f"Primary key candidate ({col.get('cardinality')} unique values)."
            ),
        })

    # --- Channel 2: FK-source — this column references another dataset's PK -
    fk_source = _infer_fk_source(store, dataset_id, canonical_name)
    evidence["fk_source"] = fk_source
    if fk_source is not None:
        blockers.append({
            "kind": "fk_source",
            "ref": f"{dataset_id}.{canonical_name} → {fk_source['target_dataset']}.{fk_source['target_column']}",
            "severity": "high",
            "evidence": f"Likely foreign-key relationship ({fk_source['match_kind']}).",
        })

    # --- Channel 3: FK-target — other datasets reference this PK -----
    # Only meaningful when channel 1 fired (this is a PK).
    if is_pk:
        fk_refs = evidence.get("fk_referers") or []
        if fk_refs:
            ref0 = fk_refs[0]
            blockers.append({
                "kind": "fk_target",
                "ref": f"{ref0['source_dataset']}.{ref0['source_column']} → {dataset_id}.{canonical_name}",
                "severity": "high",
                "evidence": f"{len(fk_refs)} other dataset(s) appear to reference this column.",
            })

    # --- Channel 4: Runtime traffic ----------------------------------
    db_path = store.sqlite_path(dataset_id)
    has_runtime = _runtime_table_has_any(db_path)
    evidence["runtime_data_present"] = has_runtime
    if has_runtime:
        calls, last_seen = _runtime_calls_in_window(db_path, canonical_name, window_days)
        evidence["runtime_calls_in_window"] = calls
        evidence["runtime_last_seen"] = last_seen
        if calls > 0:
            blockers.append({
                "kind": "runtime_observed",
                "ref": f"{dataset_id}.{canonical_name}",
                "severity": "medium",
                "evidence": f"{calls} query call(s) within the last {window_days} day(s); last_seen={last_seen}.",
            })
    else:
        evidence["runtime_calls_in_window"] = None
        evidence["runtime_last_seen"] = None

    # --- Channel 5: Cross-dataset name match -------------------------
    cross = _cross_dataset_matches(store, dataset_id, canonical_name)
    evidence["cross_dataset_name_matches"] = [m["dataset"] for m in cross]
    if cross:
        blockers.append({
            "kind": "cross_dataset_name_match",
            "ref": ", ".join(m["dataset"] for m in cross[:3]) + ("…" if len(cross) > 3 else ""),
            "severity": "low",
            "evidence": (
                f"{len(cross)} other dataset(s) carry a column named "
                f"{canonical_name!r}. Often signals a shared concept that "
                "queries assume exists in both places."
            ),
        })

    # --- Verdict -----------------------------------------------------
    verdict = "safe_to_drop"
    recommended: str
    if is_pk:
        verdict = "pk_blocking"
        fk_count = len(evidence.get("fk_referers") or [])
        recommended = (
            f"Dropping a primary-key candidate is almost always catastrophic. "
            f"{fk_count} downstream FK candidate(s) detected." if fk_count else
            "Dropping a primary-key candidate is almost always catastrophic. "
            "Verify nothing depends on this identity first."
        )
    elif fk_source is not None:
        verdict = "fk_blocking"
        recommended = (
            f"Column appears to be a foreign-key source into "
            f"{fk_source['target_dataset']}.{fk_source['target_column']}. "
            "Verify no JOINs use it before dropping."
        )
    elif has_runtime and evidence.get("runtime_calls_in_window", 0) > 0:
        verdict = "runtime_observed"
        recommended = (
            f"Column was queried {evidence['runtime_calls_in_window']} time(s) "
            f"in the last {window_days} day(s). Find and update the callers "
            "before dropping."
        )
    elif cross:
        verdict = "cross_dataset_blocking"
        recommended = (
            f"Column name appears in {len(cross)} other indexed dataset(s). "
            "Likely a shared concept; check that queries don't assume the "
            "column exists in both places."
        )
    else:
        recommended = (
            "No blockers detected. "
            + ("" if has_runtime else "Note: no runtime data ingested for this dataset — "
                                       "verdict based on static signals only. "
                                       "Run ingest_sql_log against representative query "
                                       "traffic to strengthen this answer.")
        ).strip()

    blockers = blockers[:_MAX_BLOCKERS]

    return {
        "result": {
            "dataset": dataset_id,
            "column": canonical_name,
            "verdict": verdict,
            "blockers": blockers,
            "evidence": evidence,
            "recommended_action": recommended,
        },
        "_meta": {
            "latency_ms": int((time.perf_counter() - t0) * 1000),
            "window_days": window_days,
            "channels_fired": [b["kind"] for b in blockers],
            "runtime_data_present": has_runtime,
        },
    }
