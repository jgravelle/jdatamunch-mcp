"""find_unused_columns — runtime-driven column dead-weight detection (v1.6.0).

Surfaces columns in a dataset's schema that have **zero or stale**
runtime reads over a configurable window. Reads from the
``runtime_query_calls`` table populated by :func:`ingest_sql_log_file`.

Why this tool needs runtime data: without query-traffic evidence the
question "is this column unused?" is unanswerable from the static
schema alone — every column *might* be read by something we can't see.
Static signals (PK / FK / type) only help us *exclude* candidates
(don't flag the primary key, don't flag `created_at`); they can't
provide positive evidence of use.

Refusal: when the dataset has zero rows in ``runtime_query_calls``,
the tool returns an explicit ``refused_no_runtime_data`` error rather
than silently flagging every column as unused. Operators get a clear
remediation hint pointing at ``ingest_sql_log``.

Verdict reasons per surfaced column:

* ``zero_hits``    — column never appeared in any query, in or out of
  window
* ``stale``        — column appeared at some point, but never within
  the requested window
* ``below_min_calls`` — column has hits in window but fewer than
  ``min_calls``

Returns plain rows — caller batches into whatever downstream report
they want.
"""

from __future__ import annotations

import re
import sqlite3
import time
from datetime import datetime, timedelta, timezone
from typing import Optional

from ..storage.data_store import DataStore
from ..runtime.tables import ensure_runtime_tables


# Default audit-field heuristics. Matches the names that almost always
# arrive via ORM/migration scaffolding (and almost never via human use).
_AUDIT_PATTERNS = (
    re.compile(r"^created_at$", re.IGNORECASE),
    re.compile(r"^updated_at$", re.IGNORECASE),
    re.compile(r"^deleted_at$", re.IGNORECASE),
    re.compile(r"^modified_at$", re.IGNORECASE),
    re.compile(r"^inserted_at$", re.IGNORECASE),
    re.compile(r"^_(?:created|updated|deleted|modified)_(?:at|by)$", re.IGNORECASE),
    re.compile(r"^etl_.*$", re.IGNORECASE),
    re.compile(r"^_dbt_.*$", re.IGNORECASE),
    re.compile(r"^dbt_(?:loaded|updated|inserted)_at$", re.IGNORECASE),
)


def _is_audit_field(name: str) -> bool:
    return any(rx.match(name) for rx in _AUDIT_PATTERNS)


def _fk_status_for(col: dict) -> str:
    """Cheap PK/FK classifier from the column profile.

    jData doesn't track foreign keys explicitly; ``is_primary_key_candidate``
    is the only structural signal available today. Treat that as ``pk``
    and everything else as ``none`` for now. When a dedicated FK detector
    lands, the second branch grows ``fk_source`` / ``fk_target`` cases.
    """
    if col.get("is_primary_key_candidate"):
        return "pk"
    return "none"


def _row_count(conn: sqlite3.Connection, table: str) -> int:
    try:
        cur = conn.execute(f"SELECT COUNT(*) FROM {table}")
        row = cur.fetchone()
        return int(row[0]) if row else 0
    except sqlite3.OperationalError:
        return 0


def find_unused_columns(
    dataset_id: str,
    window_days: int = 30,
    min_calls: int = 0,
    exclude_pk: bool = True,
    exclude_audit: bool = True,
    storage_path: Optional[str] = None,
) -> dict:
    """Surface columns with no (or stale) runtime traffic.

    Args:
        dataset_id: The indexed dataset to audit.
        window_days: Look-back window. Default 30.
        min_calls: Floor for "considered used" within the window.
            Default 0 — any single observed call counts.
        exclude_pk: Skip primary-key-candidate columns. Default True —
            PKs are almost always read by JOINs but may not always
            appear in extracted column tokens.
        exclude_audit: Skip created_at / updated_at / dbt_* / etl_*
            scaffolding columns. Default True.
        storage_path: Custom data-index root.

    Returns:
        ``{result: {unused: [...], dataset, total_columns, evaluated,
        window_days, ...}, _meta}`` on success.
        ``{error, reason, hint, ...}`` on refusal.
    """
    t0 = time.perf_counter()
    store = DataStore(base_path=storage_path)
    idx = store.load(dataset_id)
    if idx is None:
        return {
            "error": f"Dataset not indexed: {dataset_id}",
            "reason": "dataset_not_found",
            "hint": "Run index_local first, then ingest_sql_log to populate runtime data.",
        }

    db_path = store.sqlite_path(dataset_id)
    if not db_path.exists():
        return {
            "error": f"Dataset SQLite missing: {dataset_id}",
            "reason": "dataset_not_found",
            "hint": "Run index_local first, then ingest_sql_log to populate runtime data.",
        }

    conn = sqlite3.connect(str(db_path))
    try:
        # Ensure tables exist — legacy v2 datasets may not have them yet.
        ensure_runtime_tables(conn)

        # Refusal guard: zero runtime rows ⇒ refuse rather than mass-flag.
        if _row_count(conn, "runtime_query_calls") == 0:
            return {
                "error": "No runtime data ingested for this dataset.",
                "reason": "refused_no_runtime_data",
                "hint": (
                    "find_unused_columns needs at least one query log ingested via "
                    "ingest_sql_log before it can distinguish unused columns from "
                    "unobserved ones. Without runtime data, every column would be "
                    "trivially flagged as unused — silent garbage. "
                    "Run ingest_sql_log against a representative SQL log first."
                ),
                "_meta": {
                    "latency_ms": int((time.perf_counter() - t0) * 1000),
                    "dataset": dataset_id,
                },
            }

        # Build (column → {calls_in_window, last_seen_overall, last_seen_in_window})
        # in one pass. We need both windowed and lifetime stats so we can
        # distinguish "never seen" from "seen but stale."
        cutoff = (datetime.now(tz=timezone.utc) - timedelta(days=window_days)).isoformat()

        cur = conn.execute(
            """
            SELECT
                column_ref,
                SUM(CASE WHEN last_seen >= ? THEN calls ELSE 0 END) AS calls_in_window,
                MAX(last_seen) AS last_seen_overall,
                MAX(CASE WHEN last_seen >= ? THEN last_seen ELSE NULL END) AS last_seen_in_window
            FROM runtime_query_calls
            WHERE source = 'sql_log' AND column_ref != ''
            GROUP BY column_ref
            """,
            (cutoff, cutoff),
        )
        per_col: dict[str, dict] = {}
        for row in cur.fetchall():
            col, calls_in_window, last_overall, last_in_window = row
            per_col[col] = {
                "calls_in_window": int(calls_in_window or 0),
                "last_seen_overall": last_overall,
                "last_seen_in_window": last_in_window,
            }
    finally:
        conn.close()

    # Walk the dataset's static schema; surface columns that fail the
    # window / min_calls test, after applying exclusions.
    unused: list[dict] = []
    evaluated = 0
    skipped_pk = 0
    skipped_audit = 0

    for col in idx.columns:
        col_name = (col.get("name") or "").lower()
        if not col_name:
            continue
        fk_status = _fk_status_for(col)
        if exclude_pk and fk_status == "pk":
            skipped_pk += 1
            continue
        if exclude_audit and _is_audit_field(col_name):
            skipped_audit += 1
            continue
        evaluated += 1

        # Find the per-column runtime row (column tokens are stored lowercase).
        stats = per_col.get(col_name)

        if stats is None:
            reason = "zero_hits"
            calls_in_window = 0
            last_seen = None
        elif stats["calls_in_window"] == 0:
            # Has been seen at some point, but not in the window.
            reason = "stale"
            calls_in_window = 0
            last_seen = stats["last_seen_overall"]
        elif stats["calls_in_window"] < min_calls:
            reason = "below_min_calls"
            calls_in_window = stats["calls_in_window"]
            last_seen = stats["last_seen_in_window"]
        else:
            # Has enough calls in window — not unused, skip.
            continue

        # Find the source table for this column. In jData each dataset
        # maps to one logical table — its name is the dataset_id (or
        # its lower-case form, depending on how indexed). For now we
        # surface the dataset_id as the table_ref.
        unused.append({
            "table": dataset_id,
            "column": col.get("name") or col_name,
            "calls_in_window": calls_in_window,
            "last_seen": last_seen,
            "fk_status": fk_status,
            "type": col.get("type", "unknown"),
            "reason": reason,
        })

    return {
        "result": {
            "dataset": dataset_id,
            "total_columns": len(idx.columns),
            "evaluated": evaluated,
            "skipped_pk": skipped_pk,
            "skipped_audit": skipped_audit,
            "window_days": window_days,
            "min_calls": min_calls,
            "unused_count": len(unused),
            "unused": unused,
        },
        "_meta": {
            "latency_ms": int((time.perf_counter() - t0) * 1000),
            "exclude_pk": exclude_pk,
            "exclude_audit": exclude_audit,
        },
    }
