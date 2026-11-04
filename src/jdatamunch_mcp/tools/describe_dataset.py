"""describe_dataset tool: Full schema profile for a dataset."""

import json
import time
from typing import Optional

from ..config import get_index_path, MAX_COLUMNS_DESCRIBE
from ..storage.data_store import DataStore
from ..storage.token_tracker import estimate_savings, record_savings, cost_avoided
from ..redact import redact_scalar_list

# Compact orientation (the default since PROFILE_VERSION 2). One row per
# column under a "fields" header. Design note: the cut-offs are fixed round
# numbers, keyed only on column type and distinct count.
COMPACT_FIELDS = ["name", "type", "null_pct", "distinct", "range_or_top_values"]
COMPACT_ALL_VALUES_UP_TO = 10   # list every value when distinct <= this
COMPACT_TOP_VALUES = 3          # else list this many of the most frequent
COMPACT_VALUE_CHARS = 40        # cut each listed value to this many characters
_COMPACT_MORE = (
    "Compact view. detail='full' adds samples, top values with counts, mean, "
    "median and summaries; describe_column gives one column in depth."
)
_DETAILS = ("compact", "full")


def _num(v):
    if isinstance(v, float):
        return float(f"{v:.6g}")
    return v


def _cut(v):
    if isinstance(v, str) and len(v) > COMPACT_VALUE_CHARS:
        return v[: COMPACT_VALUE_CHARS - 3] + "..."
    return v


def _frequent_values(c: dict) -> list:
    """Most frequent values first; every value when the column is small."""
    vi = c.get("value_index")
    if vi:
        ordered = [k for k, _ in sorted(vi.items(), key=lambda kv: -kv[1])]
    elif c.get("top_values"):
        ordered = [tv["value"] for tv in c["top_values"]]
    else:
        ordered = list(c.get("sample_values") or [])
    exact = c.get("cardinality_is_exact", True)
    if exact and len(ordered) == c.get("cardinality") and len(ordered) <= COMPACT_ALL_VALUES_UP_TO:
        return ordered
    return ordered[:COMPACT_TOP_VALUES]


def _compact_row(c: dict) -> list:
    t = c["type"]
    distinct = c["cardinality"] if c.get("cardinality_is_exact", True) else f"~{c['cardinality']}"
    if t in ("integer", "float"):
        extra = [_num(c.get("min")), _num(c.get("max"))] if c.get("min") is not None else []
    elif t == "datetime":
        extra = [c.get("datetime_min"), c.get("datetime_max")] if c.get("datetime_min") else []
    else:
        extra = [_cut(v) for v in _frequent_values(c)]
    return [c["name"], t, c["null_pct"], distinct, extra]


def describe_dataset(
    dataset: str,
    columns: Optional[list] = None,
    columns_offset: int = 0,
    storage_path: Optional[str] = None,
    detail: str = "compact",
) -> dict:
    """Return schema profile for a dataset — all columns with type, cardinality, samples.

    This is the primary orientation tool. A single call replaces reading the entire source file.
    detail="compact" (default) returns one row per column; detail="full" returns
    the full per-column profile.
    """
    t0 = time.time()
    if detail not in _DETAILS:
        return {"error": f"INVALID_DETAIL: detail must be one of {list(_DETAILS)}, got {detail!r}"}
    store = DataStore(base_path=storage_path or str(get_index_path()))

    idx = store.load(dataset)
    if idx is None:
        return {"error": f"NOT_INDEXED: dataset {dataset!r} is not indexed. Call index_local first."}

    # Filter to requested columns
    all_cols = idx.columns
    explicit_columns = bool(columns)
    if columns:
        col_set = set(columns)
        filtered = [c for c in all_cols if c["name"] in col_set]
        missing = col_set - {c["name"] for c in filtered}
        if missing:
            return {"error": f"INVALID_COLUMN: {sorted(missing)}"}
    else:
        filtered = all_cols

    # Wide-table pagination: auto-paginate when no explicit column filter
    column_pagination = None
    if not explicit_columns and len(filtered) > MAX_COLUMNS_DESCRIBE:
        total_cols = len(filtered)
        start = min(columns_offset, total_cols)
        end = min(start + MAX_COLUMNS_DESCRIBE, total_cols)
        remaining = [c["name"] for c in filtered[end:]]
        column_pagination = {
            "total_columns": total_cols,
            "offset": start,
            "returned": end - start,
            "truncated": end < total_cols,
            "remaining_column_names": remaining,
            "hint": "Use columns=['col1','col2'] to profile specific columns, or columns_offset to page",
        }
        filtered = filtered[start:end]

    if detail == "compact":
        return _compact_response(dataset, idx, filtered, column_pagination, store, t0)

    # Build column summaries for the response (exclude heavy value_index for bandwidth)
    col_summaries = []
    for c in filtered:
        s: dict = {
            "id": f"{dataset}::{c['name']}#column",
            "name": c["name"],
            "type": c["type"],
            "cardinality": c["cardinality"],
            "cardinality_is_exact": c["cardinality_is_exact"],
            "null_pct": c["null_pct"],
            "is_unique": c["is_unique"],
            "sample_values": c.get("sample_values", []),
        }
        if c["type"] in ("integer", "float"):
            s["min"] = c.get("min")
            s["max"] = c.get("max")
            s["mean"] = c.get("mean")
            s["median"] = c.get("median")
        if c["type"] == "datetime":
            s["datetime_min"] = c.get("datetime_min")
            s["datetime_max"] = c.get("datetime_max")
            s["datetime_format"] = c.get("datetime_format")
        if c.get("top_values"):
            s["top_values"] = c["top_values"][:5]  # preview only; use describe_column for full
        if c.get("ai_summary"):
            s["ai_summary"] = c["ai_summary"]
        col_summaries.append(s)

    # Token savings: raw file vs this response
    response_bytes = len(json.dumps(col_summaries).encode("utf-8"))
    tokens_saved = estimate_savings(idx.source_size_bytes, response_bytes)
    total_saved = record_savings(tokens_saved, str(store.base_path), tool="describe_dataset")

    result_body: dict = {
        "dataset": idx.dataset,
        "file": idx.source_path.split("/")[-1].split("\\")[-1],
        "rows": idx.row_count,
        "column_count": idx.column_count,
        "source_size_bytes": idx.source_size_bytes,
        "indexed_at": idx.indexed_at,
        "columns": col_summaries,
        "dataset_summary": idx.dataset_summary,
    }
    if column_pagination:
        result_body["column_pagination"] = column_pagination

    return {
        "result": result_body,
        "_meta": {
            "timing_ms": round((time.time() - t0) * 1000, 1),
            "tokens_saved": tokens_saved,
            "total_tokens_saved": total_saved,
            **cost_avoided(tokens_saved, total_saved),
        },
    }


def _compact_response(dataset, idx, filtered, column_pagination, store, t0) -> dict:
    rows = [_compact_row(c) for c in filtered]

    # Listed text values go through the default redaction, like describe_column.
    redaction = {"cells_redacted": 0}
    for row in rows:
        if row[1] == "string" and row[4]:
            row[4], summ = redact_scalar_list(row[4])
            redaction["cells_redacted"] += summ.get("cells_redacted", 0)

    unique = [c["name"] for c in filtered if c.get("is_unique")]
    fmts = {c["name"]: c["datetime_format"] for c in filtered
            if c["type"] == "datetime" and c.get("datetime_format")}

    body: dict = {
        "dataset": idx.dataset,
        "file": idx.source_path.split("/")[-1].split("\\")[-1],
        "rows": idx.row_count,
        "column_count": idx.column_count,
        "detail": "compact",
        "fields": COMPACT_FIELDS,
        "columns": rows,
    }
    if unique:
        body["unique"] = unique
    if fmts:
        body["datetime_formats"] = fmts
    if column_pagination:
        body["column_pagination"] = column_pagination
    body["more"] = _COMPACT_MORE

    response_bytes = len(json.dumps(body, separators=(",", ":")).encode("utf-8"))
    tokens_saved = estimate_savings(idx.source_size_bytes, response_bytes)
    total_saved = record_savings(tokens_saved, str(store.base_path), tool="describe_dataset")
    return {
        "result": body,
        "_meta": {
            "timing_ms": round((time.time() - t0) * 1000, 1),
            "tokens_saved": tokens_saved,
            "total_tokens_saved": total_saved,
            **cost_avoided(tokens_saved, total_saved),
            "redaction": {"applied": True, **redaction},
        },
    }
