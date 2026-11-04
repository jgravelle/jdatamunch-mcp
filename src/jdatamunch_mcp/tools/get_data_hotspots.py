"""get_data_hotspots tool: Identify high-risk / high-attention columns in a dataset.

v2 (v1.10.0) adds an optional 4th signal — **runtime traffic** — fused
from ``runtime_query_calls`` (populated by ``ingest_sql_log``). When
traffic data is present the score becomes ``null + card + outlier +
traffic`` with rebalanced weights; the traffic axis amplifies risk on
heavily-queried columns. When traffic data is absent, the tool falls
back to v1's three-signal scoring but surfaces an honest-hint caveat in
``_meta.runtime_caveat`` so callers know production attention wasn't
consulted (pattern lifted from ``check_column_drop_safe`` v1.8.0 and
jcm's ``check_delete_safe`` v1.108.6).
"""

import sqlite3
import time
from datetime import datetime, timedelta, timezone
from typing import Optional

from ..config import get_index_path
from ..storage.data_store import DataStore
from ..storage import result_cache
from ..storage.token_tracker import estimate_savings, record_savings, per_call_costs
from ..runtime.tables import ensure_runtime_tables


# v1 weights (used when traffic data is absent or include_runtime=False)
_W_NULL_V1 = 0.4
_W_CARD_V1 = 0.3
_W_OUTLIER_V1 = 0.3

# v2 weights (used when traffic data is present)
_W_NULL_V2 = 0.30
_W_CARD_V2 = 0.20
_W_OUTLIER_V2 = 0.20
_W_TRAFFIC_V2 = 0.30

_HIGH_THRESHOLD = 0.6
_MEDIUM_THRESHOLD = 0.3


def _cardinality_score(col: dict, row_count: int) -> float:
    card = col.get("cardinality") or 0
    if row_count == 0:
        return 0.0
    ratio = card / row_count
    if col["type"] in ("string", "integer") and card <= 1:
        return 0.8
    if col["type"] == "string" and ratio > 0.95:
        return 0.5
    return min(ratio, 1.0) * 0.2


def _outlier_score(col: dict) -> float:
    if col["type"] not in ("integer", "float"):
        return 0.0
    mn = col.get("min")
    mx = col.get("max")
    mean = col.get("mean")
    if mn is None or mx is None or mean is None or mean == 0:
        return 0.0
    try:
        spread = abs(float(mx) - float(mn))
        cv = spread / abs(float(mean))
        return min(cv / 5.0, 1.0)
    except (TypeError, ZeroDivisionError, ValueError):
        return 0.0


def _load_traffic(
    db_path,
    column_names: list[str],
    window_days: int,
) -> tuple[dict[str, int], bool]:
    """Read per-column call counts within window from runtime_query_calls.

    Returns ``(per_column_calls, runtime_data_present)``. ``runtime_data_present``
    is True iff at least one row exists in runtime_query_calls regardless of
    window — distinguishes "no traces ingested at all" from "ingested but
    quiet in the window."
    """
    if not db_path.exists():
        return {}, False
    cutoff = (datetime.now(tz=timezone.utc) - timedelta(days=window_days)).isoformat()
    conn = sqlite3.connect(str(db_path))
    try:
        ensure_runtime_tables(conn)
        row = conn.execute("SELECT COUNT(*) FROM runtime_query_calls").fetchone()
        has_any = bool(row and row[0])
        if not has_any:
            return {}, False
        cur = conn.execute(
            """
            SELECT column_ref, SUM(calls) AS total_calls
            FROM runtime_query_calls
            WHERE column_ref != '' AND last_seen >= ?
            GROUP BY column_ref
            """,
            (cutoff,),
        )
        per_col: dict[str, int] = {}
        wanted = {c.lower() for c in column_names}
        for col, calls in cur.fetchall():
            if col and col.lower() in wanted:
                per_col[col.lower()] = int(calls or 0)
        return per_col, True
    finally:
        conn.close()


def get_data_hotspots(
    dataset: str,
    top_n: int = 10,
    include_runtime: bool = True,
    window_days: int = 30,
    storage_path: Optional[str] = None,
) -> dict:
    """Return the highest-risk columns in a dataset.

    Risk combines: null rate, cardinality anomalies, numeric spread, and
    (when ``include_runtime=True`` and traces exist) runtime traffic.

    Args:
        dataset: Indexed dataset to audit.
        top_n: Cap on returned hotspots. Default 10, max 50.
        include_runtime: Fuse the traffic signal from runtime_query_calls
            when available. Default True. When True but no traces are
            ingested, the response carries an honest-hint caveat rather
            than silently falling back to v1 scoring.
        window_days: Lookback window for the traffic signal. Default 30.
        storage_path: Custom data-index root.
    """
    t0 = time.perf_counter()
    top_n = min(max(1, top_n), 50)
    store = DataStore(base_path=storage_path or str(get_index_path()))

    idx = store.load(dataset)
    if idx is None:
        return {"error": f"NOT_INDEXED: dataset {dataset!r} is not indexed. Call index_local first."}

    column_names = [c["name"] for c in idx.columns]
    per_col_calls: dict[str, int] = {}
    runtime_data_present = False
    if include_runtime:
        try:
            per_col_calls, runtime_data_present = _load_traffic(
                store.sqlite_path(dataset), column_names, window_days
            )
        except sqlite3.OperationalError:
            per_col_calls, runtime_data_present = {}, False

    use_traffic = include_runtime and runtime_data_present and bool(per_col_calls)
    max_calls = max(per_col_calls.values()) if per_col_calls else 0

    cache_key = result_cache.make_key(
        "get_data_hotspots",
        idx.source_hash,
        {
            "top_n": top_n,
            "include_runtime": include_runtime,
            "window_days": window_days,
            "traffic_signature": max_calls,
        },
    )
    cached = result_cache.get(store.dataset_dir(dataset), cache_key, tool="get_data_hotspots")
    if cached is not None:
        cached.setdefault("_meta", {})
        cached["_meta"]["cache_hit"] = True
        cached["_meta"]["timing_ms"] = round((time.perf_counter() - t0) * 1000, 1)
        return cached

    row_count = idx.row_count or 1
    scored = []

    for col in idx.columns:
        null_pct = (col.get("null_pct") or 0.0) / 100.0
        card_score = _cardinality_score(col, row_count)
        outlier_score = _outlier_score(col)
        calls = per_col_calls.get(col["name"].lower(), 0)
        traffic_score = (calls / max_calls) if (use_traffic and max_calls > 0) else 0.0

        if use_traffic:
            hotspot_score = round(
                _W_NULL_V2 * null_pct
                + _W_CARD_V2 * card_score
                + _W_OUTLIER_V2 * outlier_score
                + _W_TRAFFIC_V2 * traffic_score,
                4,
            )
        else:
            hotspot_score = round(
                _W_NULL_V1 * null_pct + _W_CARD_V1 * card_score + _W_OUTLIER_V1 * outlier_score,
                4,
            )

        if hotspot_score >= _HIGH_THRESHOLD:
            assessment = "high"
        elif hotspot_score >= _MEDIUM_THRESHOLD:
            assessment = "medium"
        else:
            assessment = "low"

        entry: dict = {
            "column": col["name"],
            "type": col["type"],
            "hotspot_score": hotspot_score,
            "assessment": assessment,
            "null_pct": col.get("null_pct") or 0.0,
            "cardinality": col.get("cardinality") or 0,
        }
        if col["type"] in ("integer", "float"):
            entry["min"] = col.get("min")
            entry["max"] = col.get("max")
            entry["mean"] = col.get("mean")
        if use_traffic:
            entry["traffic_calls"] = calls
            entry["traffic_score"] = round(traffic_score, 4)
        scored.append(entry)

    scored.sort(key=lambda x: x["hotspot_score"], reverse=True)
    top = scored[:top_n]

    high_count = sum(1 for s in scored if s["assessment"] == "high")
    medium_count = sum(1 for s in scored if s["assessment"] == "medium")

    if high_count > 0:
        overall = "high"
    elif medium_count > 0:
        overall = "medium"
    else:
        overall = "low"

    import json
    response_bytes = len(json.dumps(top).encode("utf-8"))
    tokens_saved = estimate_savings(idx.source_size_bytes, response_bytes)
    total_saved = record_savings(tokens_saved, str(store.base_path), tool="get_data_hotspots")

    meta: dict = {
        "timing_ms": round((time.perf_counter() - t0) * 1000, 1),
        "tokens_saved": tokens_saved,
        "cache_hit": False,
        "signals_used": (
            ["null_pct", "cardinality", "outlier", "traffic"]
            if use_traffic
            else ["null_pct", "cardinality", "outlier"]
        ),
        "runtime_data_present": runtime_data_present,
        **per_call_costs(tokens_saved, total_saved),
    }
    if include_runtime and not runtime_data_present:
        meta["runtime_caveat"] = (
            "Score reflects static signals only — no runtime traces ingested "
            "for this dataset, so production attention was not consulted. "
            "A 100%-null column nobody queries is less urgent than a 30%-null "
            "column queried 10k times/day. Run `ingest_sql_log` against a "
            "representative SQL log to fuse traffic into the score."
        )

    response = {
        "result": {
            "dataset": dataset,
            "total_columns": len(scored),
            "high_risk_columns": high_count,
            "medium_risk_columns": medium_count,
            "overall_assessment": overall,
            "hotspots": top,
            "runtime_data_present": runtime_data_present,
        },
        "_meta": meta,
    }
    result_cache.put(store.dataset_dir(dataset), cache_key, response)
    return response
