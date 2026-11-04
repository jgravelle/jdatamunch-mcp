"""Tests for get_rows and sample_rows tools."""

import pytest

from jdatamunch_mcp.tools.get_rows import get_rows
from jdatamunch_mcp.tools.sample_rows import sample_rows


def _rows(out):
    """Rows as dicts, whichever row_format the tool used."""
    body = out["result"]
    if "fields" in body:
        return [dict(zip(body["fields"], r)) for r in body["rows"]]
    return body["rows"]


def test_get_rows_no_filters(indexed_sample):
    result = get_rows(dataset="sample", limit=10, storage_path=indexed_sample)
    assert "error" not in result
    r = result["result"]
    assert r["returned"] == 10
    assert r["total_matching"] == 10


def test_get_rows_eq_filter(indexed_sample):
    result = get_rows(
        dataset="sample",
        filters=[{"column": "city", "op": "eq", "value": "Hollywood"}],
        storage_path=indexed_sample,
    )
    assert "error" not in result
    rows = _rows(result)
    assert all(row["city"] == "Hollywood" for row in rows)


def test_get_rows_contains_filter(indexed_sample):
    result = get_rows(
        dataset="sample",
        filters=[{"column": "name", "op": "contains", "value": "a"}],
        storage_path=indexed_sample,
    )
    assert "error" not in result
    rows = _rows(result)
    assert all("a" in row["name"].lower() for row in rows)


def test_get_rows_gt_filter(indexed_sample):
    result = get_rows(
        dataset="sample",
        filters=[{"column": "age", "op": "gt", "value": 30}],
        storage_path=indexed_sample,
    )
    rows = _rows(result)
    for row in rows:
        assert row["age"] > 30


def test_get_rows_is_null_filter(indexed_sample):
    result = get_rows(
        dataset="sample",
        filters=[{"column": "age", "op": "is_null", "value": True}],
        storage_path=indexed_sample,
    )
    rows = _rows(result)
    assert all(row["age"] is None for row in rows)


def test_get_rows_column_projection(indexed_sample):
    result = get_rows(
        dataset="sample",
        columns=["id", "name"],
        limit=5,
        storage_path=indexed_sample,
    )
    rows = _rows(result)
    for row in rows:
        assert "id" in row
        assert "name" in row
        assert "age" not in row


def test_get_rows_ordering(indexed_sample):
    result = get_rows(
        dataset="sample",
        order_by="age",
        order_dir="asc",
        limit=10,
        storage_path=indexed_sample,
    )
    ages = [r["age"] for r in _rows(result) if r["age"] is not None]
    assert ages == sorted(ages)


def test_get_rows_limit_cap(indexed_sample):
    from jdatamunch_mcp.storage.sqlite_store import MAX_ROWS_RETURNED
    result = get_rows(
        dataset="sample",
        limit=MAX_ROWS_RETURNED + 100,
        storage_path=indexed_sample,
    )
    assert "error" in result
    assert "ROW_LIMIT_EXCEEDED" in result["error"]


def test_get_rows_invalid_column_filter(indexed_sample):
    result = get_rows(
        dataset="sample",
        filters=[{"column": "nonexistent", "op": "eq", "value": "x"}],
        storage_path=indexed_sample,
    )
    assert "error" in result


def test_get_rows_empty_result(indexed_sample):
    result = get_rows(
        dataset="sample",
        filters=[{"column": "city", "op": "eq", "value": "NoSuchCity"}],
        storage_path=indexed_sample,
    )
    assert "error" not in result
    r = result["result"]
    assert r["rows"] == []
    assert r["total_matching"] == 0


def test_get_rows_not_indexed(storage_dir):
    result = get_rows(dataset="ghost", storage_path=storage_dir)
    assert "error" in result
    assert "NOT_INDEXED" in result["error"]


def test_sample_rows_head(indexed_sample):
    result = sample_rows(dataset="sample", n=3, method="head", storage_path=indexed_sample)
    assert "error" not in result
    assert result["result"]["returned"] == 3


def test_sample_rows_tail(indexed_sample):
    result = sample_rows(dataset="sample", n=3, method="tail", storage_path=indexed_sample)
    assert result["result"]["returned"] == 3


def test_sample_rows_random(indexed_sample):
    result = sample_rows(dataset="sample", n=5, method="random", storage_path=indexed_sample)
    assert result["result"]["returned"] == 5


def test_sample_rows_invalid_method(indexed_sample):
    result = sample_rows(dataset="sample", method="middle", storage_path=indexed_sample)
    assert "error" in result
