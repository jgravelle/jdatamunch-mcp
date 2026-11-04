"""sample_rows tool: Return a sample of rows from a dataset."""

import json
import time
from typing import Optional

from ..config import get_index_path, MAX_COLUMNS_ROWS
from ..redact import redact_rows, redaction_meta
from ..storage.data_store import DataStore
from ..storage.sqlite_store import query_sample
from ..storage.token_tracker import estimate_savings, record_savings, per_call_costs


def sample_rows(
    dataset: str,
    n: int = 5,
    method: str = "head",
    columns: Optional[list] = None,
    seed: Optional[int] = None,
    redact: bool = True,
    redact_patterns: Optional[list] = None,
    redact_skip_columns: Optional[list] = None,
    storage_path: Optional[str] = None,
) -> dict:
    """Return a sample of rows from the dataset.

    Useful for quickly understanding data shape without prior knowledge.
    method: "head" (first rows), "tail" (last rows), or "random"
    seed: when method='random', makes selection deterministic (A9).

    Sampled cells are scrubbed for PII / credentials by default. Pass
    ``redact=False`` to see raw values when working with data you own.
    """
    t0 = time.time()

    if method not in ("head", "tail", "random"):
        return {"error": f"INVALID_FILTER: method must be 'head', 'tail', or 'random', got {method!r}"}

    n = min(max(1, n), 100)

    store = DataStore(base_path=storage_path or str(get_index_path()))
    idx = store.load(dataset)
    if idx is None:
        return {"error": f"NOT_INDEXED: dataset {dataset!r} is not indexed."}

    sqlite_path = store.sqlite_path(dataset)
    if not sqlite_path.exists():
        return {"error": f"NOT_INDEXED: SQLite database missing for {dataset!r}. Re-index."}

    schema_cols = idx.columns

    # Auto-limit columns on wide tables when no explicit projection
    column_truncation = None
    if not columns and len(schema_cols) > MAX_COLUMNS_ROWS:
        omitted = [c["name"] for c in schema_cols[MAX_COLUMNS_ROWS:]]
        column_truncation = {
            "total_columns": len(schema_cols),
            "returned": MAX_COLUMNS_ROWS,
            "omitted_columns": omitted,
            "hint": "Use columns=['col1','col2'] to select specific columns",
        }
        columns = [c["name"] for c in schema_cols[:MAX_COLUMNS_ROWS]]

    # Validate column projection
    if columns:
        schema_names = {c["name"] for c in schema_cols}
        bad = [c for c in columns if c not in schema_names]
        if bad:
            return {"error": f"INVALID_COLUMN: {bad}"}

    rows = query_sample(
        sqlite_path=sqlite_path,
        schema_columns=schema_cols,
        n=n,
        method=method,
        columns=columns,
        seed=seed,
    )

    redaction_summary: Optional[dict] = None
    if redact:
        rows, redaction_summary = redact_rows(
            rows,
            custom_patterns=redact_patterns,
            skip_columns=redact_skip_columns,
        )

    response_bytes = len(json.dumps(rows).encode("utf-8"))
    tokens_saved = estimate_savings(idx.source_size_bytes, response_bytes)
    total_saved = record_savings(tokens_saved, str(store.base_path), tool="sample_rows")

    result_body: dict = {
        "rows": rows,
        "returned": len(rows),
        "method": method,
        "dataset_rows": idx.row_count,
    }
    meta: dict = {
        "timing_ms": round((time.time() - t0) * 1000, 1),
        "tokens_saved": tokens_saved,
        **per_call_costs(tokens_saved, total_saved),
    }
    if column_truncation:
        meta["column_truncation"] = column_truncation
    meta["redaction"] = redaction_meta(
        applied=redact,
        summary=redaction_summary,
        custom_patterns=redact_patterns,
    )

    return {"result": result_body, "_meta": meta}
