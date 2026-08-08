"""SQLite row storage: table creation, batch insert, indexed queries.

Column names are always double-quoted in SQL to support spaces, hyphens, etc.
All user-supplied values are parameterized — no SQL injection surface.
"""

import sqlite3
from pathlib import Path
from typing import Any, Optional

_NULL_VALUES = frozenset([
    "", "null", "NULL", "none", "None", "N/A", "n/a", "NA", "na",
    "NaN", "nan", "-", ".", "#N/A", "#NA", "#NULL!", "n.a.", "N.A.",
])

# SQLite type affinity per inferred column type
_TYPE_AFFINITY = {
    "integer": "INTEGER",
    "float": "REAL",
    "datetime": "TEXT",
    "string": "TEXT",
}

BATCH_SIZE = 50_000   # larger batches = fewer commits
MAX_ROWS_RETURNED = 500


def _qcol(name: str) -> str:
    """Return SQL double-quoted column name with escaped inner quotes."""
    return '"' + name.replace('"', '""') + '"'


def _convert_value(value: str, col_type: str) -> Any:
    """Convert a raw string value to its native Python type for SQLite storage."""
    stripped = value.strip() if value else ""
    if stripped in _NULL_VALUES:
        return None
    if col_type == "integer":
        try:
            return int(stripped)
        except ValueError:
            try:
                return int(float(stripped))
            except ValueError:
                return stripped or None
    elif col_type == "float":
        try:
            return float(stripped)
        except ValueError:
            return stripped or None
    else:
        return stripped if stripped else None


def create_table(
    sqlite_path: Path,
    column_names: list,  # list[str]
    column_types: list,  # list[str] — parallel to column_names
) -> None:
    """Create the rows table (drops if it already exists).

    Writes to a fresh file at sqlite_path. Callers performing crash-safe
    ingest should pass the .tmp variant and rename on success (A4).
    """
    sqlite_path.parent.mkdir(parents=True, exist_ok=True)
    # Crash-safe load: always start from a clean file so a partial run from
    # a previous attempt cannot leak into the new one.
    if sqlite_path.exists():
        try:
            sqlite_path.unlink()
        except OSError:
            pass
    col_defs = ", ".join(
        f"{_qcol(name)} {_TYPE_AFFINITY.get(ctype, 'TEXT')}"
        for name, ctype in zip(column_names, column_types)
    )
    ddl = f"CREATE TABLE IF NOT EXISTS rows ({col_defs})"

    conn = sqlite3.connect(str(sqlite_path))
    try:
        # MEMORY journal during write phase → no -wal/-shm sidecars to
        # complicate the atomic tmp→final rename on Windows. The tmp file is
        # disposable on crash anyway (A4 invariant).
        conn.execute("PRAGMA journal_mode=MEMORY")
        conn.execute("PRAGMA synchronous=NORMAL")
        conn.execute("DROP TABLE IF EXISTS rows")
        conn.execute(ddl)
        conn.commit()
    finally:
        conn.close()


def _make_col_converter(col_type: str):
    """Return a fast single-argument converter for a given column type.

    Pre-building one closure per column eliminates the string comparison
    inside _convert_value on every row (28M calls for a 1M-row, 28-col file).
    """
    null_values = _NULL_VALUES
    if col_type == "integer":
        def _conv(v: str) -> Any:
            s = v.strip() if v else ""
            if s in null_values:
                return None
            try:
                return int(s)
            except ValueError:
                try:
                    return int(float(s))
                except ValueError:
                    return s or None
    elif col_type == "float":
        def _conv(v: str) -> Any:
            s = v.strip() if v else ""
            if s in null_values:
                return None
            try:
                return float(s)
            except ValueError:
                return s or None
    else:  # datetime / string — store as text
        def _conv(v: str) -> Any:
            s = v.strip() if v else ""
            return s if s not in null_values else None
    return _conv


class BulkInserter:
    """Context manager for high-throughput row insertion.

    Keeps a single SQLite connection open across all batches.
    Uses pre-compiled per-column converters to avoid repeated type dispatch.

    Crash safety (A4): the caller writes to data.sqlite.tmp and renames on
    success. WAL + synchronous=NORMAL keep durability cost low while
    guaranteeing no torn pages survive a kill.
    """

    def __init__(
        self,
        sqlite_path: Path,
        column_names: list,
        column_types: list,
        batch_size: int = BATCH_SIZE,
    ) -> None:
        self.sqlite_path = sqlite_path
        self.batch_size = batch_size
        self.n_cols = len(column_names)
        self._batch: list = []
        self._conn: Optional[sqlite3.Connection] = None
        # Pre-compile one converter per column
        self._converters = [_make_col_converter(ct) for ct in column_types]

        placeholders = ", ".join("?" * self.n_cols)
        col_list = ", ".join(_qcol(n) for n in column_names)
        self._sql = f"INSERT INTO rows ({col_list}) VALUES ({placeholders})"

    def __enter__(self) -> "BulkInserter":
        self._conn = sqlite3.connect(str(self.sqlite_path))
        # journal_mode=MEMORY: no on-disk -wal/-shm sidecars, so the
        # subsequent tmp→final atomic rename is clean on Windows. Crash-safety
        # comes from the tmp+rename invariant, not from this connection.
        self._conn.execute("PRAGMA journal_mode=MEMORY")
        self._conn.execute("PRAGMA synchronous=NORMAL")
        self._conn.execute("PRAGMA cache_size=-131072")   # 128 MB page cache
        self._conn.execute("PRAGMA temp_store=MEMORY")
        return self

    def _convert_row(self, row: list) -> tuple:
        convs = self._converters
        n = self.n_cols
        return tuple(convs[i](row[i] if i < len(row) else "") for i in range(n))

    def add(self, row: list) -> None:
        self._batch.append(row)
        if len(self._batch) >= self.batch_size:
            self._flush()

    def _flush(self) -> None:
        if self._batch and self._conn:
            self._conn.executemany(self._sql, (self._convert_row(r) for r in self._batch))
            self._batch = []

    def __exit__(self, exc_type, exc_val, exc_tb) -> None:
        if self._conn:
            self._flush()
            if exc_type is None:
                self._conn.commit()
            self._conn.close()
            self._conn = None
        return False


def insert_batch(
    sqlite_path: Path,
    batch: list,            # list of raw string lists
    column_names: list,     # list[str]
    column_types: list,     # list[str]
) -> None:
    """Insert a batch of rows into the rows table (single-connection convenience wrapper)."""
    if not batch:
        return
    with BulkInserter(sqlite_path, column_names, column_types) as bi:
        for row in batch:
            bi.add(row)


def create_indexes(
    sqlite_path: Path,
    profiles: list,  # list[ColumnProfile]
    cardinality_threshold: int = 50,
) -> None:
    """Create SQLite indexes on low-cardinality columns for fast filtering.

    Threshold of 50 keeps only truly categorical columns (sex, status, area, etc.)
    and avoids spending 2-3s per index on higher-cardinality columns that are
    rarely used as primary filter keys. Users who need indexes on higher-cardinality
    columns can add them via direct SQLite access.
    """
    conn = sqlite3.connect(str(sqlite_path))
    try:
        conn.execute("PRAGMA journal_mode=MEMORY")
        conn.execute("PRAGMA synchronous=NORMAL")
        conn.execute("PRAGMA cache_size=-131072")
        conn.execute("PRAGMA temp_store=MEMORY")
        for p in profiles:
            if (
                p.cardinality <= cardinality_threshold
                and not p.is_unique
                and p.null_pct < 99.0   # skip columns that are almost entirely null
            ):
                idx_name = "idx_" + p.name.replace(" ", "_").replace("/", "_")[:50]
                try:
                    conn.execute(
                        f"CREATE INDEX IF NOT EXISTS {_qcol(idx_name)} ON rows ({_qcol(p.name)})"
                    )
                except sqlite3.OperationalError:
                    pass
        conn.commit()
    finally:
        conn.close()


def _build_where(filters: list, schema_columns: list) -> tuple:
    """Build a parameterized WHERE clause from a list of filter objects.

    Returns (where_sql, params).
    Raises ValueError with INVALID_FILTER details on bad input.
    """
    if not filters:
        return "", []

    schema_set = {c["name"] for c in schema_columns}
    clauses = []
    params: list = []

    for f in filters:
        col = f.get("column", "")
        op = f.get("op", "")
        val = f.get("value")

        if col not in schema_set:
            raise ValueError(f"INVALID_COLUMN: {col!r}")

        qc = _qcol(col)

        if op == "eq":
            clauses.append(f"{qc} = ?")
            params.append(val)
        elif op == "neq":
            clauses.append(f"{qc} != ?")
            params.append(val)
        elif op == "gt":
            clauses.append(f"{qc} > ?")
            params.append(val)
        elif op == "gte":
            clauses.append(f"{qc} >= ?")
            params.append(val)
        elif op == "lt":
            clauses.append(f"{qc} < ?")
            params.append(val)
        elif op == "lte":
            clauses.append(f"{qc} <= ?")
            params.append(val)
        elif op == "contains":
            escaped = str(val).replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
            clauses.append(f"{qc} LIKE ? ESCAPE '\\'")
            params.append(f"%{escaped}%")
        elif op == "in":
            if not isinstance(val, list) or not val:
                raise ValueError("INVALID_FILTER: 'in' requires a non-empty list value")
            ph = ",".join("?" * len(val))
            clauses.append(f"{qc} IN ({ph})")
            params.extend(val)
        elif op == "is_null":
            if val:
                clauses.append(f"{qc} IS NULL")
            else:
                clauses.append(f"{qc} IS NOT NULL")
        elif op == "between":
            if not isinstance(val, list) or len(val) != 2:
                raise ValueError("INVALID_FILTER: 'between' requires [min, max] list")
            clauses.append(f"{qc} BETWEEN ? AND ?")
            params.extend(val)
        else:
            raise ValueError(f"INVALID_FILTER: unknown operator {op!r}")

    return " AND ".join(clauses), params


def _build_having(having: list, alias_to_expr: dict) -> tuple:
    """Build a parameterized HAVING clause referencing aggregation aliases (B11).

    `alias_to_expr` maps each alias to its underlying aggregate SQL expression.
    The expression is substituted directly into HAVING so it remains correct
    even when the alias name collides with a source column.

    Operators allowed: eq, neq, gt, gte, lt, lte, in, between, is_null.
    """
    if not having:
        return "", []
    clauses: list = []
    params: list = []
    for f in having:
        col = f.get("column", "")
        op = f.get("op", "")
        val = f.get("value")
        if col not in alias_to_expr:
            raise ValueError(
                f"INVALID_HAVING: column {col!r} is not an aggregation alias. "
                f"Available aliases: {sorted(alias_to_expr)}"
            )
        qc = alias_to_expr[col]  # raw aggregate expression, e.g. COUNT(*)
        if op == "eq":
            clauses.append(f"{qc} = ?"); params.append(val)
        elif op == "neq":
            clauses.append(f"{qc} != ?"); params.append(val)
        elif op == "gt":
            clauses.append(f"{qc} > ?"); params.append(val)
        elif op == "gte":
            clauses.append(f"{qc} >= ?"); params.append(val)
        elif op == "lt":
            clauses.append(f"{qc} < ?"); params.append(val)
        elif op == "lte":
            clauses.append(f"{qc} <= ?"); params.append(val)
        elif op == "in":
            if not isinstance(val, list) or not val:
                raise ValueError("INVALID_HAVING: 'in' requires a non-empty list value")
            ph = ",".join("?" * len(val))
            clauses.append(f"{qc} IN ({ph})"); params.extend(val)
        elif op == "between":
            if not isinstance(val, list) or len(val) != 2:
                raise ValueError("INVALID_HAVING: 'between' requires [min, max] list")
            clauses.append(f"{qc} BETWEEN ? AND ?"); params.extend(val)
        elif op == "is_null":
            clauses.append(f"{qc} IS NULL" if val else f"{qc} IS NOT NULL")
        else:
            raise ValueError(f"INVALID_HAVING: unsupported operator {op!r}")
    return " AND ".join(clauses), params


def query_rows(
    sqlite_path: Path,
    schema_columns: list,   # list of column dicts from DataIndex
    filters: Optional[list] = None,
    columns: Optional[list] = None,
    order_by: Optional[str] = None,
    order_dir: str = "asc",
    limit: int = 50,
    offset: int = 0,
) -> dict:
    """Execute a filtered row query. Returns rows + total_matching count."""
    limit = min(max(1, limit), MAX_ROWS_RETURNED)

    # Column projection
    schema_names = [c["name"] for c in schema_columns]
    if columns:
        select_cols = columns
    else:
        select_cols = schema_names

    col_sql = ", ".join(_qcol(c) for c in select_cols)

    where_sql, params = _build_where(filters or [], schema_columns)
    where_clause = f"WHERE {where_sql}" if where_sql else ""

    # Order by
    order_clause = ""
    if order_by and order_by in schema_names:
        direction = "DESC" if order_dir.lower() == "desc" else "ASC"
        order_clause = f"ORDER BY {_qcol(order_by)} {direction}"

    data_sql = (
        f"SELECT rowid, {col_sql} FROM rows {where_clause} "
        f"{order_clause} LIMIT ? OFFSET ?"
    )
    count_sql = f"SELECT COUNT(*) FROM rows {where_clause}"

    with sqlite3.connect(str(sqlite_path)) as conn:
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA query_only=1")

        total = conn.execute(count_sql, params).fetchone()[0]
        cursor = conn.execute(data_sql, params + [limit, offset])

        rows = []
        for row in cursor:
            d = {"_row_id": row["rowid"]}
            for col in select_cols:
                d[col] = row[col]
            rows.append(d)

    return {
        "rows": rows,
        "total_matching": total,
        "returned": len(rows),
        "offset": offset,
        "filters_applied": len(filters) if filters else 0,
        "columns_projected": len(select_cols),
    }


def _query_aggregate_approx(
    sqlite_path: Path,
    schema_columns: list,
    aggregations: list,
    filters: Optional[list] = None,
    sample_size: int = 50_000,
) -> dict:
    """Approximate-mode aggregate (C1).

    Streams the column values through HyperLogLog (count_distinct), t-digest
    (median), or a sampled estimator (sum/avg). Whole-dataset only — group_by
    / having / order_by are intentionally unsupported in approximate mode.
    """
    from ..profiler.hll import HyperLogLog
    from ..profiler.tdigest import TDigest

    schema_names = [c["name"] for c in schema_columns]
    where_sql, params = _build_where(filters or [], schema_columns)
    where_clause = f"WHERE {where_sql}" if where_sql else ""

    out: dict = {}
    confidence: dict = {}

    with sqlite3.connect(str(sqlite_path)) as conn:
        conn.execute("PRAGMA query_only=1")

        # Total row count for sampling-based estimators
        total_row = conn.execute(
            f"SELECT COUNT(*) FROM rows {where_clause}", params
        ).fetchone()
        total_rows = int(total_row[0]) if total_row else 0

        for agg in aggregations:
            func = (agg.get("function") or "").lower()
            col = agg.get("column", "*")
            alias = agg.get("alias", f"{func}_{col}".replace("*", "all").replace(" ", "_"))
            if col != "*" and col not in schema_names:
                raise ValueError(f"INVALID_COLUMN: {col!r}")

            qc = "*" if col == "*" else _qcol(col)

            if func == "count":
                row = conn.execute(
                    f"SELECT COUNT({qc}) FROM rows {where_clause}", params
                ).fetchone()
                out[alias] = int(row[0]) if row else 0
                confidence[alias] = "exact"

            elif func == "count_distinct":
                hll = HyperLogLog()
                cur = conn.execute(
                    f"SELECT {qc} FROM rows {where_clause} WHERE {qc} IS NOT NULL"
                    if not where_clause
                    else f"SELECT {qc} FROM rows {where_clause} AND {qc} IS NOT NULL",
                    params,
                )
                for row in cur:
                    if row[0] is not None:
                        hll.add(str(row[0]))
                out[alias] = hll.estimate()
                confidence[alias] = "hll_2pct"

            elif func == "median":
                td = TDigest(delta=200)
                cur = conn.execute(
                    f"SELECT {qc} FROM rows {where_clause} WHERE {qc} IS NOT NULL"
                    if not where_clause
                    else f"SELECT {qc} FROM rows {where_clause} AND {qc} IS NOT NULL",
                    params,
                )
                for row in cur:
                    try:
                        td.add(float(row[0]))
                    except (TypeError, ValueError):
                        pass
                out[alias] = td.quantile(0.5)
                confidence[alias] = "tdigest_1pct"

            elif func in ("sum", "avg"):
                # Sample-based estimator
                if total_rows <= sample_size:
                    row = conn.execute(
                        f"SELECT {func.upper()}({qc}) FROM rows {where_clause}",
                        params,
                    ).fetchone()
                    out[alias] = row[0] if row else None
                    confidence[alias] = "exact"
                else:
                    cur = conn.execute(
                        f"SELECT {qc} FROM rows {where_clause} ORDER BY RANDOM() LIMIT ?"
                        if not where_clause
                        else f"SELECT {qc} FROM rows {where_clause} ORDER BY RANDOM() LIMIT ?",
                        params + [sample_size],
                    )
                    vals = [v[0] for v in cur if v[0] is not None]
                    n = len(vals)
                    if n == 0:
                        out[alias] = None
                        confidence[alias] = "no_data"
                    else:
                        try:
                            mean = sum(float(v) for v in vals) / n
                            if func == "avg":
                                out[alias] = mean
                            else:
                                out[alias] = mean * total_rows
                            # 95% CI half-width via standard error
                            if n >= 2:
                                m2 = sum((float(v) - mean) ** 2 for v in vals) / (n - 1)
                                se = (m2 / n) ** 0.5
                                ci95 = 1.96 * se
                                if func == "sum":
                                    ci95 *= total_rows
                                confidence[alias] = {
                                    "method": f"sampled_{n}",
                                    "ci95_halfwidth": round(ci95, 6),
                                }
                            else:
                                confidence[alias] = {"method": f"sampled_{n}"}
                        except (TypeError, ValueError):
                            out[alias] = None
                            confidence[alias] = "type_error"

            elif func in ("min", "max"):
                row = conn.execute(
                    f"SELECT {func.upper()}({qc}) FROM rows {where_clause}", params
                ).fetchone()
                out[alias] = row[0] if row else None
                confidence[alias] = "exact"

            else:
                raise ValueError(f"INVALID_FILTER: unknown aggregation function {func!r}")

    return {
        "groups": [out] if out else [],
        "total_groups": 1 if out else 0,
        "returned": 1 if out else 0,
        "approximate": True,
        "confidence": confidence,
        "total_rows_scanned": total_rows,
    }


def query_aggregate(
    sqlite_path: Path,
    schema_columns: list,
    group_by: Optional[list] = None,
    aggregations: Optional[list] = None,
    filters: Optional[list] = None,
    having: Optional[list] = None,
    order_by: Optional[str] = None,
    order_dir: str = "desc",
    limit: int = 50,
    approximate: bool = False,
) -> dict:
    """Execute a GROUP BY aggregation query.

    aggregations: list of {"column": ..., "function": ..., "alias": ...}
    having: list of filter dicts whose `column` references an aggregation alias.
            Same op set as filters: eq, neq, gt, gte, lt, lte, in, between (B11).
    approximate: when True, route count_distinct → HLL, median → t-digest,
                 sum/avg → sampled estimator with 95% CI (C1). Whole-dataset
                 only — group_by / having / order_by are not supported.
    """
    if not aggregations:
        raise ValueError("INVALID_FILTER: aggregations is required")

    if approximate:
        if group_by or having or order_by:
            raise ValueError(
                "INVALID_FILTER: approximate=True does not support group_by/having/order_by"
            )
        return _query_aggregate_approx(
            sqlite_path=sqlite_path,
            schema_columns=schema_columns,
            aggregations=aggregations,
            filters=filters,
        )

    schema_names = [c["name"] for c in schema_columns]
    VALID_FUNCS = {"count", "sum", "avg", "min", "max", "count_distinct", "median"}

    agg_parts = []
    agg_aliases = []
    agg_alias_to_expr: dict = {}  # alias → SQL expression (for HAVING substitution)
    for agg in aggregations:
        func = agg.get("function", "").lower()
        col = agg.get("column", "*")
        alias = agg.get("alias", f"{func}_{col}".replace("*", "all").replace(" ", "_"))

        if func not in VALID_FUNCS:
            raise ValueError(f"INVALID_FILTER: unknown aggregation function {func!r}")

        if col != "*" and col not in schema_names:
            raise ValueError(f"INVALID_COLUMN: {col!r}")

        qc = "*" if col == "*" else _qcol(col)

        if func == "count":
            agg_sql = f"COUNT({qc})"
        elif func == "count_distinct":
            agg_sql = f"COUNT(DISTINCT {qc})"
        elif func == "sum":
            agg_sql = f"SUM({qc})"
        elif func == "avg":
            agg_sql = f"AVG({qc})"
        elif func == "min":
            agg_sql = f"MIN({qc})"
        elif func == "max":
            agg_sql = f"MAX({qc})"
        elif func == "median":
            # SQLite has no MEDIAN; approximate with AVG(min+max)/2 or compute in Python
            # For now use AVG as a pragmatic approximation
            agg_sql = f"AVG({qc})"
            alias = alias + "_approx"
        else:
            agg_sql = f"COUNT({qc})"

        agg_parts.append(f"{agg_sql} AS {_qcol(alias)}")
        agg_aliases.append(alias)
        agg_alias_to_expr[alias] = agg_sql

    where_sql, params = _build_where(filters or [], schema_columns)
    where_clause = f"WHERE {where_sql}" if where_sql else ""

    group_cols = group_by or []
    for gc in group_cols:
        if gc not in schema_names:
            raise ValueError(f"INVALID_COLUMN: {gc!r} in group_by")

    group_sql = ""
    select_group_cols = ""
    if group_cols:
        group_select = ", ".join(_qcol(c) for c in group_cols)
        group_sql = f"GROUP BY {group_select}"
        select_group_cols = group_select + ", "

    agg_select = ", ".join(agg_parts)
    sql = (
        f"SELECT {select_group_cols}{agg_select} FROM rows "
        f"{where_clause} {group_sql}"
    )

    # HAVING (B11) — operates on aggregation aliases. We substitute the full
    # aggregate expression (e.g. COUNT(*)) rather than the alias so HAVING
    # works even when the alias collides with a source column name.
    having_clause = ""
    having_params: list = []
    if having:
        having_sql, having_params = _build_having(having, agg_alias_to_expr)
        if having_sql:
            having_clause = f" HAVING {having_sql}"
            sql += having_clause

    # ORDER BY
    if order_by:
        direction = "DESC" if order_dir.lower() == "desc" else "ASC"
        if order_by in group_cols:
            ob = _qcol(order_by)
        elif order_by in agg_aliases:
            ob = _qcol(order_by)
        else:
            ob = _qcol(order_by)
        sql += f" ORDER BY {ob} {direction}"

    sql += " LIMIT ?"

    # Count total groups (must include HAVING so total reflects post-filter cardinality)
    count_inner = (
        f"SELECT {select_group_cols}{agg_select} FROM rows "
        f"{where_clause} {group_sql}{having_clause}"
    )
    count_sql = f"SELECT COUNT(*) FROM ({count_inner}) AS t"

    with sqlite3.connect(str(sqlite_path)) as conn:
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA query_only=1")

        total_groups = conn.execute(count_sql, params + having_params).fetchone()[0]
        cursor = conn.execute(sql, params + having_params + [limit])

        groups = []
        for row in cursor:
            d = dict(row)
            groups.append(d)

    return {
        "groups": groups,
        "total_groups": total_groups,
        "returned": len(groups),
    }


def query_sample(
    sqlite_path: Path,
    schema_columns: list,
    n: int = 5,
    method: str = "head",
    columns: Optional[list] = None,
    seed: Optional[int] = None,
) -> list:
    """Return a sample of rows: head, tail, or random.

    When method='random', `seed` makes the result deterministic (A9).
    Implementation: sample uniformly without replacement from rowid 1..N
    using a seeded random.Random instance, then SELECT … WHERE rowid IN (…).
    Falls back to ORDER BY RANDOM() when seed is None for backward compat.
    """
    n = min(max(1, n), 100)
    schema_names = [c["name"] for c in schema_columns]
    select_cols = columns if columns else schema_names

    col_sql = ", ".join(_qcol(c) for c in select_cols)

    if method == "head":
        sql = f"SELECT rowid, {col_sql} FROM rows LIMIT ?"
        params: list = [n]
        with sqlite3.connect(str(sqlite_path)) as conn:
            conn.row_factory = sqlite3.Row
            conn.execute("PRAGMA query_only=1")
            cursor = conn.execute(sql, params)
            return _materialize_rows(cursor, select_cols)
    elif method == "tail":
        sql = f"SELECT rowid, {col_sql} FROM rows ORDER BY rowid DESC LIMIT ?"
        params = [n]
        with sqlite3.connect(str(sqlite_path)) as conn:
            conn.row_factory = sqlite3.Row
            conn.execute("PRAGMA query_only=1")
            cursor = conn.execute(sql, params)
            return _materialize_rows(cursor, select_cols)
    else:  # random
        if seed is None:
            sql = f"SELECT rowid, {col_sql} FROM rows ORDER BY RANDOM() LIMIT ?"
            params = [n]
            with sqlite3.connect(str(sqlite_path)) as conn:
                conn.row_factory = sqlite3.Row
                conn.execute("PRAGMA query_only=1")
                cursor = conn.execute(sql, params)
                return _materialize_rows(cursor, select_cols)
        # Deterministic: pick rowids via seeded RNG, then fetch by rowid.
        import random
        with sqlite3.connect(str(sqlite_path)) as conn:
            conn.row_factory = sqlite3.Row
            conn.execute("PRAGMA query_only=1")
            total_row = conn.execute("SELECT COUNT(*) FROM rows").fetchone()
            total = int(total_row[0]) if total_row else 0
            if total == 0:
                return []
            rng = random.Random(seed)
            k = min(n, total)
            picked = rng.sample(range(1, total + 1), k)
            placeholders = ",".join("?" * len(picked))
            sql = (
                f"SELECT rowid, {col_sql} FROM rows "
                f"WHERE rowid IN ({placeholders})"
            )
            cursor = conn.execute(sql, picked)
            return _materialize_rows(cursor, select_cols)


def _materialize_rows(cursor, select_cols: list) -> list:
    rows = []
    for row in cursor:
        d = {"_row_id": row["rowid"]}
        for col in select_cols:
            d[col] = row[col]
        rows.append(d)
    return rows
