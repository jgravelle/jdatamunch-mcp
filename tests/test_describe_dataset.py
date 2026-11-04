"""Tests for describe_dataset and describe_column tools."""

import pytest

from jdatamunch_mcp.tools.describe_dataset import describe_dataset
from jdatamunch_mcp.tools.describe_column import describe_column


def test_describe_dataset_basic(indexed_sample):
    result = describe_dataset(dataset="sample", storage_path=indexed_sample)
    assert "error" not in result
    r = result["result"]
    assert r["dataset"] == "sample"
    assert r["rows"] == 10
    assert r["column_count"] == 5
    assert len(r["columns"]) == 5


def test_describe_dataset_column_filter(indexed_sample):
    result = describe_dataset(
        dataset="sample", columns=["id", "name"], storage_path=indexed_sample, detail="full"
    )
    assert "error" not in result
    names = [c["name"] for c in result["result"]["columns"]]
    assert set(names) == {"id", "name"}


def test_describe_dataset_invalid_column(indexed_sample):
    result = describe_dataset(
        dataset="sample", columns=["nonexistent"], storage_path=indexed_sample
    )
    assert "error" in result


def test_describe_dataset_not_indexed(storage_dir):
    result = describe_dataset(dataset="ghost", storage_path=storage_dir)
    assert "error" in result
    assert "NOT_INDEXED" in result["error"]


def test_describe_dataset_column_ids(indexed_sample):
    result = describe_dataset(dataset="sample", storage_path=indexed_sample, detail="full")
    for col in result["result"]["columns"]:
        assert col["id"].startswith("sample::")
        assert col["id"].endswith("#column")


def test_describe_column_basic(indexed_sample):
    result = describe_column(
        dataset="sample", column="city", storage_path=indexed_sample, detail="full"
    )
    assert "error" not in result
    r = result["result"]
    assert r["name"] == "city"
    assert r["type"] == "string"
    assert r["cardinality"] <= 10


def test_describe_column_by_id(indexed_sample):
    result = describe_column(
        dataset="sample",
        column="sample::city#column",
        storage_path=indexed_sample,
    )
    assert "error" not in result
    assert result["result"]["name"] == "city"


def test_describe_column_value_distribution(indexed_sample):
    result = describe_column(
        dataset="sample", column="city", storage_path=indexed_sample, detail="full"
    )
    assert "value_distribution" in result["result"]
    dist = result["result"]["value_distribution"]
    # All cities should appear; total count should equal non-null rows
    total_count = sum(d["count"] for d in dist)
    assert total_count == 10


def test_describe_column_numeric(indexed_sample):
    result = describe_column(
        dataset="sample", column="age", storage_path=indexed_sample
    )
    r = result["result"]
    assert r["type"] in ("integer", "float")
    assert r.get("mean") is not None
    assert r.get("min") is not None
    assert r.get("max") is not None


def test_describe_column_not_found(indexed_sample):
    result = describe_column(
        dataset="sample", column="bogus", storage_path=indexed_sample
    )
    assert "error" in result
    assert "INVALID_COLUMN" in result["error"]
