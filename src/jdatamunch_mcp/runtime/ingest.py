"""Orchestrator: parse → redact → resolve → upsert.

The full pipeline for :func:`ingest_sql_log_file`. Walks a SQL log file
through three stages:

1. **Parse** — :func:`sql_log.parse_sql_log_file` yields one
   :class:`SqlQueryRecord` per row.
2. **Redact** — :func:`jdatamunch_mcp.redact.redact_sql_query_text`
   scrubs the query body. Counts roll up into the per-source summary
   and persist via ``runtime_redaction_log``.
3. **Resolve** — for each (table, column) tuple the parser found, look
   up the indexed dataset whose name matches the table (case-
   insensitive, exact). Columns referenced in the query that aren't in
   the dataset's schema are silently dropped — over-emission from the
   parser is by design.
4. **Upsert** — write the rollup row into that dataset's
   ``data.sqlite`` ``runtime_query_calls`` table. ON CONFLICT
   increments the call count, accumulates ``total_time_ms``, and
   refreshes ``last_seen``.

Unmapped queries (tables with no matching dataset) increment the
``unmapped`` counter in the response but aren't persisted anywhere.
"""

from __future__ import annotations

import hashlib
import sqlite3
from datetime import datetime, timezone
from typing import Optional

from ..redact import redact_sql_query_text
from ..storage.data_store import DataStore
from .sql_log import parse_sql_log_file
from .tables import ensure_runtime_tables


def _now_iso() -> str:
    return datetime.now(tz=timezone.utc).isoformat()


def _fingerprint(redacted_query: str) -> str:
    """Stable identity for a (post-redaction) query shape."""
    return "sha256:" + hashlib.sha256(redacted_query.encode("utf-8")).hexdigest()[:32]


def _build_dataset_index(store: DataStore) -> dict[str, dict]:
    """Map ``dataset_name_lower → {dataset_id, columns: set[str]}``.

    Loads each indexed dataset's column list once so the per-row hot
    loop is a dict lookup, not a fresh file read per query.
    """
    out: dict[str, dict] = {}
    for entry in store.list_datasets():
        ds_id = entry["dataset"]
        idx = store.load(ds_id)
        if idx is None:
            continue
        cols = {c.get("name", "").lower() for c in idx.columns if isinstance(c, dict) and c.get("name")}
        out[ds_id.lower()] = {"dataset_id": ds_id, "columns": cols}
    return out


def _upsert_call(
    conn: sqlite3.Connection,
    fingerprint: str,
    table_ref: str,
    column_ref: Optional[str],
    calls: int,
    total_time_ms: Optional[float],
    mean_time_ms: Optional[float],
    source: str,
    now: str,
) -> None:
    """ON CONFLICT upsert into runtime_query_calls.

    column_ref=NULL semantics differ from column_ref="x" — both are
    distinct rows so a query that selects 5 columns produces 5 rows
    plus one column_ref=NULL "table-touched" row.
    """
    # SQLite uses '' for NULL in the primary key; canonicalise.
    col_pk = column_ref if column_ref is not None else ""
    cur = conn.cursor()
    cur.execute(
        """
        INSERT INTO runtime_query_calls
            (query_fingerprint, table_ref, column_ref, calls, total_time_ms,
             mean_time_ms, first_seen, last_seen, source)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(query_fingerprint, table_ref, column_ref, source) DO UPDATE SET
            calls = calls + excluded.calls,
            total_time_ms = total_time_ms + COALESCE(excluded.total_time_ms, 0),
            mean_time_ms = COALESCE(excluded.mean_time_ms, mean_time_ms),
            last_seen = excluded.last_seen
        """,
        (
            fingerprint, table_ref, col_pk, calls,
            total_time_ms or 0.0, mean_time_ms,
            now, now, source,
        ),
    )


def _upsert_redaction(
    conn: sqlite3.Connection,
    pattern: str,
    count: int,
    source: str,
    now: str,
) -> None:
    cur = conn.cursor()
    cur.execute(
        """
        INSERT INTO runtime_redaction_log (pattern, count, source, last_seen)
        VALUES (?, ?, ?, ?)
        ON CONFLICT(pattern, source) DO UPDATE SET
            count = count + excluded.count,
            last_seen = excluded.last_seen
        """,
        (pattern, count, source, now),
    )


def ingest_sql_log_file(
    file_path: str,
    *,
    source: str = "auto",
    redact: bool = True,
    max_rows: int = 100_000,
    storage_path: Optional[str] = None,
) -> dict:
    """Ingest a SQL log file into the per-dataset runtime tables.

    Args:
        file_path: Path to the log file. CSV / JSONL / .gz auto-detected.
        source: ``"pg_stat_statements"``, ``"jsonl"``, or ``"auto"``.
        redact: When True (default), every query body is scrubbed before
            it lands in ``runtime_query_calls``. Set to False ONLY on
            synthetic data — never on prod logs.
        max_rows: Hard cap on rows ingested. Default 100k.
        storage_path: Custom data-index root (defaults to ~/.data-index).

    Returns:
        ``{ingested_rows, resolved_to_datasets, unmapped_queries,
        by_dataset, redaction_summary, source, _meta}``.
    """
    store = DataStore(base_path=storage_path)
    dataset_idx = _build_dataset_index(store)

    if not dataset_idx:
        return {
            "ingested_rows": 0,
            "resolved_to_datasets": 0,
            "unmapped_queries": 0,
            "by_dataset": {},
            "redaction_summary": {"cells_redacted": 0, "patterns_matched": {}},
            "source": source,
            "warnings": ["no indexed datasets — nothing to map against"],
        }

    # Open one connection per dataset, lazily.
    conns: dict[str, sqlite3.Connection] = {}
    per_dataset_stats: dict[str, dict] = {}
    redaction_totals: dict[str, int] = {}
    cells_total = 0
    ingested = 0
    unmapped = 0
    parsed_source = source

    now = _now_iso()

    try:
        for rec in parse_sql_log_file(file_path, source=source, max_rows=max_rows):
            ingested += 1
            query = rec.query
            if redact:
                query, summary = redact_sql_query_text(query)
                cells_total += int(summary.get("cells_redacted", 0))
                for kind, n in (summary.get("patterns_matched") or {}).items():
                    redaction_totals[kind] = redaction_totals.get(kind, 0) + int(n)
            fp = _fingerprint(query)

            if not rec.tables:
                # Cannot map a query without a table reference.
                unmapped += 1
                continue

            mapped_any = False
            for tbl in rec.tables:
                meta = dataset_idx.get(tbl.lower())
                if meta is None:
                    continue
                mapped_any = True
                ds_id = meta["dataset_id"]
                if ds_id not in conns:
                    db_path = store.sqlite_path(ds_id)
                    if not db_path.exists():
                        continue
                    c = sqlite3.connect(str(db_path))
                    ensure_runtime_tables(c)
                    conns[ds_id] = c
                    per_dataset_stats[ds_id] = {
                        "queries_attributed": 0,
                        "columns_hit": set(),
                        "tables_hit": set(),
                    }
                c = conns[ds_id]
                stats = per_dataset_stats[ds_id]
                stats["queries_attributed"] += 1
                stats["tables_hit"].add(tbl.lower())

                # Per-column rows.
                cols_observed = rec.columns_by_table.get(tbl, [])
                col_set = meta["columns"]
                wrote_col_row = False
                for col in cols_observed:
                    if col not in col_set:
                        continue
                    _upsert_call(
                        conn=c, fingerprint=fp, table_ref=tbl.lower(),
                        column_ref=col, calls=rec.calls,
                        total_time_ms=rec.total_time_ms,
                        mean_time_ms=rec.mean_time_ms,
                        source="sql_log", now=now,
                    )
                    stats["columns_hit"].add(f"{tbl.lower()}.{col}")
                    wrote_col_row = True

                # Always also write a column_ref="" "table-touched" row.
                _upsert_call(
                    conn=c, fingerprint=fp, table_ref=tbl.lower(),
                    column_ref=None, calls=rec.calls,
                    total_time_ms=rec.total_time_ms,
                    mean_time_ms=rec.mean_time_ms,
                    source="sql_log", now=now,
                )

            if not mapped_any:
                unmapped += 1

        # Flush redaction-log rows to each dataset we wrote into.
        if redact and redaction_totals:
            for ds_id, c in conns.items():
                for kind, n in redaction_totals.items():
                    _upsert_redaction(c, kind, n, "sql_log", now)

        for c in conns.values():
            c.commit()
    finally:
        for c in conns.values():
            try:
                c.close()
            except Exception:
                pass

    return {
        "ingested_rows": ingested,
        "resolved_to_datasets": len(per_dataset_stats),
        "unmapped_queries": unmapped,
        "by_dataset": {
            ds_id: {
                "queries_attributed": s["queries_attributed"],
                "tables_hit": sorted(s["tables_hit"]),
                "columns_hit": sorted(s["columns_hit"]),
            }
            for ds_id, s in per_dataset_stats.items()
        },
        "redaction_summary": {
            "cells_redacted": cells_total,
            "patterns_matched": redaction_totals,
            "applied": bool(redact),
        },
        "source": parsed_source,
    }
