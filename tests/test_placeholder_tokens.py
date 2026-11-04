"""Placeholder tokens ("-", ".", ...) are values in text columns, nulls elsewhere.

Found by a realistic-agent benchmark: the LAPD crime file has one row whose
Vict Sex is "-". It was counted as null, and the row store held NULL for it,
so no tool could show what the file said. pandas, DuckDB and SQL keep it.
"""

import csv
import sqlite3

from jdatamunch_mcp.profiler.column_profiler import (
    _ColAcc,
    _HARD_NULL_VALUES,
    _TEXT_PLACEHOLDERS,
    finalize_profile,
    update_acc,
)
from jdatamunch_mcp.storage.data_store import DataStore
from jdatamunch_mcp.tools.aggregate import aggregate
from jdatamunch_mcp.tools.describe_column import describe_column
from jdatamunch_mcp.tools.index_local import index_local


def _profile(values):
    acc = _ColAcc("c", 0)
    for v in values:
        update_acc(acc, v)
    return finalize_profile(acc)


def test_sets_are_disjoint():
    assert not (_HARD_NULL_VALUES & _TEXT_PLACEHOLDERS)


def test_dash_is_a_value_in_a_text_column():
    p = _profile(["M", "F", "", "-", "X", "M", ""])
    assert p.type == "string"
    assert p.null_count == 2
    assert p.count == 5
    assert p.value_index["-"] == 1
    assert p.cardinality == 4
    assert p.null_tokens is None  # only empty cells were null


def test_dash_is_null_in_a_numeric_column():
    p = _profile(["1", "2", "-", "3", "."])
    assert p.type == "integer"
    assert p.null_count == 2
    assert p.null_tokens == {"-": 1, ".": 1}
    assert p.min == 1 and p.max == 3


def test_dash_is_null_in_a_datetime_column():
    p = _profile(["2024-01-01", "-", "2024-02-01"])
    assert p.type == "datetime"
    assert p.null_count == 1


def test_placeholders_do_not_vote_on_type():
    # Before, "-" was a null and could not push a numeric column to text.
    # It still cannot: it does not enter type inference at all.
    p = _profile(["10", "-", "20"])
    assert p.type == "integer"


def test_hard_null_tokens_are_disclosed():
    p = _profile(["a", "N/A", "NULL", "", "b"])
    assert p.null_count == 3
    assert p.null_tokens == {"N/A": 1, "NULL": 1}


def test_finalize_twice_does_not_double_count():
    acc = _ColAcc("c", 0)
    for v in ["a", "-", "-"]:
        update_acc(acc, v)
    first = finalize_profile(acc)
    second = finalize_profile(acc)
    assert first.count == second.count == 3


def _write(path, header, rows):
    with open(path, "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(header)
        w.writerows(rows)


def test_row_store_and_aggregate_keep_the_literal(tmp_path):
    src = tmp_path / "v.csv"
    _write(src, ["id", "sex", "age"], [
        [1, "M", 30], [2, "F", "-"], [3, "", 40], [4, "-", 50], [5, "M", 20],
    ])
    store_path = str(tmp_path / "s")
    index_local(path=str(src), name="v", storage_path=store_path)

    db = DataStore(base_path=store_path).sqlite_path("v")
    with sqlite3.connect(str(db)) as conn:
        rows = dict(conn.execute('SELECT id, sex FROM rows').fetchall())
        ages = dict(conn.execute('SELECT id, age FROM rows').fetchall())
    assert rows[4] == "-"
    assert rows[3] is None
    assert ages[2] is None  # "-" in a numeric column stays NULL

    r = aggregate(dataset="v", group_by=["sex"], aggregations=[{"column": "*", "function": "count"}],
                  storage_path=store_path)["result"]
    counts = {g["sex"]: next(v for k, v in g.items() if k != "sex") for g in r["groups"]}
    assert counts["-"] == 1
    assert counts[None] == 1

    age = describe_column(dataset="v", column="age", storage_path=store_path)["result"]
    assert age["null_count"] == 1
    assert age["null_tokens"] == {"-": 1}
