"""join_datasets tool: Cross-dataset SQL JOIN via ATTACH DATABASE."""

import json
import sqlite3
import time
from typing import Optional

from ..config import get_index_path
from ..storage.data_store import DataStore
from ..storage.sqlite_store import _qcol, _build_where
from ..storage.token_tracker import estimate_savings, record_savings, cost_avoided

# Hard caps
MAX_JOIN_ROWS = 500
MAX_COLUMNS_PER_SIDE = 30


def join_datasets(
    dataset_a: str,
    dataset_b: str,
    join_column_a: str,
    join_column_b: str,
    join_type: str = "inner",
    columns_a: Optional[list] = None,
    columns_b: Optional[list] = None,
    filters_a: Optional[list] = None,
    filters_b: Optional[list] = None,
    limit: int = 50,
    offset: int = 0,
    order_by: Optional[str] = None,
    order_dir: str = "asc",
    storage_path: Optional[str] = None,
) -> dict:
    """Join two indexed datasets on specified columns.

    Uses SQLite ATTACH DATABASE to bring both datasets into one connection,
    then executes a parameterized JOIN query.
    """
    t0 = time.time()

    # Validate join_type
    join_type = join_type.lower().strip()
    valid_joins = {"inner", "left", "right", "cross"}
    if join_type not in valid_joins:
        return {"error": f"INVALID_JOIN_TYPE: {join_type!r}. Must be one of: {sorted(valid_joins)}"}

    # Clamp limits
    limit = min(max(1, limit), MAX_JOIN_ROWS)
    offset = max(0, offset)

    store = DataStore(base_path=storage_path or str(get_index_path()))

    # Load both indexes
    idx_a = store.load(dataset_a)
    if idx_a is None:
        return {"error": f"NOT_INDEXED: dataset {dataset_a!r} is not indexed."}

    idx_b = store.load(dataset_b)
    if idx_b is None:
        return {"error": f"NOT_INDEXED: dataset {dataset_b!r} is not indexed."}

    # Validate join columns exist
    cols_a_names = {c["name"] for c in idx_a.columns}
    cols_b_names = {c["name"] for c in idx_b.columns}

    if join_column_a not in cols_a_names:
        return {"error": f"INVALID_COLUMN: {join_column_a!r} not found in {dataset_a!r}. Available: {sorted(cols_a_names)}"}
    if join_column_b not in cols_b_names:
        return {"error": f"INVALID_COLUMN: {join_column_b!r} not found in {dataset_b!r}. Available: {sorted(cols_b_names)}"}

    # Resolve column projections
    if columns_a:
        missing = set(columns_a) - cols_a_names
        if missing:
            return {"error": f"INVALID_COLUMN in dataset_a: {sorted(missing)}"}
        select_a = columns_a[:MAX_COLUMNS_PER_SIDE]
    else:
        select_a = [c["name"] for c in idx_a.columns[:MAX_COLUMNS_PER_SIDE]]

    if columns_b:
        missing = set(columns_b) - cols_b_names
        if missing:
            return {"error": f"INVALID_COLUMN in dataset_b: {sorted(missing)}"}
        select_b = columns_b[:MAX_COLUMNS_PER_SIDE]
    else:
        select_b = [c["name"] for c in idx_b.columns[:MAX_COLUMNS_PER_SIDE]]

    # Ensure join columns are in the projection
    if join_column_a not in select_a:
        select_a.insert(0, join_column_a)
    if join_column_b not in select_b:
        select_b.insert(0, join_column_b)

    # Build column SQL with table prefixes to avoid ambiguity
    # Columns from dataset_b that collide with dataset_a get a suffix
    col_sql_parts = []
    output_names = []
    for col in select_a:
        col_sql_parts.append(f"a.{_qcol(col)} AS {_qcol(col)}")
        output_names.append(col)
    for col in select_b:
        alias = col if col not in set(select_a) else f"{col}__b"
        col_sql_parts.append(f"b.{_qcol(col)} AS {_qcol(alias)}")
        output_names.append(alias)

    col_sql = ", ".join(col_sql_parts)

    # Build WHERE clauses for each side
    params: list = []

    # For filters, we need to prefix column references with the table alias
    where_parts = []

    if filters_a:
        try:
            where_sql_a, params_a = _build_where(filters_a, idx_a.columns)
            if where_sql_a:
                # Replace bare column references with a. prefixed ones
                where_parts.append(_prefix_where(where_sql_a, "a"))
                params.extend(params_a)
        except ValueError as e:
            return {"error": f"FILTER_ERROR (dataset_a): {e}"}

    if filters_b:
        try:
            where_sql_b, params_b = _build_where(filters_b, idx_b.columns)
            if where_sql_b:
                where_parts.append(_prefix_where(where_sql_b, "b"))
                params.extend(params_b)
        except ValueError as e:
            return {"error": f"FILTER_ERROR (dataset_b): {e}"}

    where_clause = f"WHERE {' AND '.join(where_parts)}" if where_parts else ""

    # Build JOIN SQL
    join_keyword = {
        "inner": "INNER JOIN",
        "left": "LEFT JOIN",
        "right": "LEFT JOIN",  # SQLite has no RIGHT JOIN; we swap tables
        "cross": "CROSS JOIN",
    }[join_type]

    # For RIGHT JOIN, swap a and b
    if join_type == "right":
        # Swap everything
        select_a, select_b = select_b, select_a
        join_column_a, join_column_b = join_column_b, join_column_a
        dataset_a, dataset_b = dataset_b, dataset_a
        idx_a, idx_b = idx_b, idx_a
        filters_a, filters_b = filters_b, filters_a
        # Rebuild column SQL after swap
        col_sql_parts = []
        output_names = []
        for col in select_a:
            col_sql_parts.append(f"a.{_qcol(col)} AS {_qcol(col)}")
            output_names.append(col)
        for col in select_b:
            alias = col if col not in set(select_a) else f"{col}__b"
            col_sql_parts.append(f"b.{_qcol(col)} AS {_qcol(alias)}")
            output_names.append(alias)
        col_sql = ", ".join(col_sql_parts)
        # Rebuild where clause after swap
        params = []
        where_parts = []
        if filters_a:
            try:
                where_sql_a, params_a = _build_where(filters_a, idx_a.columns)
                if where_sql_a:
                    where_parts.append(_prefix_where(where_sql_a, "a"))
                    params.extend(params_a)
            except ValueError as e:
                return {"error": f"FILTER_ERROR: {e}"}
        if filters_b:
            try:
                where_sql_b, params_b = _build_where(filters_b, idx_b.columns)
                if where_sql_b:
                    where_parts.append(_prefix_where(where_sql_b, "b"))
                    params.extend(params_b)
            except ValueError as e:
                return {"error": f"FILTER_ERROR: {e}"}
        where_clause = f"WHERE {' AND '.join(where_parts)}" if where_parts else ""

    # Build join condition
    if join_type == "cross":
        join_on = ""
    else:
        join_on = f"ON a.{_qcol(join_column_a)} = b.{_qcol(join_column_b)}"

    # Order by clause
    order_clause = ""
    if order_by and order_by in set(output_names):
        direction = "DESC" if order_dir.lower() == "desc" else "ASC"
        order_clause = f"ORDER BY {_qcol(order_by)} {direction}"

    # Main query
    data_sql = (
        f"SELECT {col_sql} FROM db_a.rows a "
        f"{join_keyword} db_b.rows b {join_on} "
        f"{where_clause} {order_clause} "
        f"LIMIT ? OFFSET ?"
    )

    count_sql = (
        f"SELECT COUNT(*) FROM db_a.rows a "
        f"{join_keyword} db_b.rows b {join_on} "
        f"{where_clause}"
    )

    # Execute with ATTACH
    sqlite_a = store.sqlite_path(dataset_a)
    sqlite_b = store.sqlite_path(dataset_b)

    if not sqlite_a.exists():
        return {"error": f"NOT_INDEXED: SQLite database missing for {dataset_a!r}. Re-index."}
    if not sqlite_b.exists():
        return {"error": f"NOT_INDEXED: SQLite database missing for {dataset_b!r}. Re-index."}

    try:
        conn = sqlite3.connect(":memory:")
        conn.execute("ATTACH DATABASE ? AS db_a", (str(sqlite_a),))
        conn.execute("ATTACH DATABASE ? AS db_b", (str(sqlite_b),))
        conn.execute("PRAGMA query_only=1")
        conn.row_factory = sqlite3.Row

        # Get total count
        total = conn.execute(count_sql, params).fetchone()[0]

        # Get rows
        cursor = conn.execute(data_sql, params + [limit, offset])
        rows = []
        for row in cursor:
            d = {}
            for col_name in output_names:
                d[col_name] = row[col_name]
            rows.append(d)

        conn.close()
    except sqlite3.OperationalError as e:
        return {"error": f"JOIN_ERROR: {e}"}

    duration_ms = round((time.time() - t0) * 1000, 1)

    response_bytes = len(json.dumps(rows).encode("utf-8"))
    combined_source = (idx_a.source_size_bytes or 0) + (idx_b.source_size_bytes or 0)
    tokens_saved = estimate_savings(combined_source, response_bytes)
    total_saved = record_savings(tokens_saved, str(store.base_path), tool="join_datasets")

    return {
        "result": {
            "dataset_a": dataset_a,
            "dataset_b": dataset_b,
            "join_type": join_type,
            "join_on": f"{dataset_a}.{join_column_a} = {dataset_b}.{join_column_b}",
            "total_matching": total,
            "returned": len(rows),
            "offset": offset,
            "columns_a": [c for c in output_names if c in select_a],
            "columns_b": [c for c in output_names if c not in select_a],
            "rows": rows,
        },
        "_meta": {
            "timing_ms": duration_ms,
            "tokens_saved": tokens_saved,
            "total_tokens_saved": total_saved,
            **cost_avoided(tokens_saved, total_saved),
        },
    }


def _prefix_where(where_sql: str, alias: str) -> str:
    """Prefix double-quoted column names in a WHERE clause with a table alias.

    _build_where produces clauses like: "col_name" = ? AND "other" > ?
    We need: a."col_name" = ? AND a."other" > ?
    """
    import re
    return re.sub(r'"([^"]*(?:""[^"]*)*)"', f'{alias}."\\1"', where_sql)
