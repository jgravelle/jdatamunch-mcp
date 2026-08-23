"""Tests for Parquet parser and index_local integration."""

import pytest
from importlib.util import find_spec

from jdatamunch_mcp.tools.index_local import index_local
from jdatamunch_mcp.storage.data_store import DataStore

# ⚠⚠ `find_spec`, NOT a module-scope `pytest.importorskip`. importorskip RAISES
# during import, so the whole file collapses to one "1 skipped" line however
# many tests it holds -- 21 of them here, reported as 1. A suite summary then
# reads the same whether those tests ran or not. find_spec asks the same
# question without raising: the module imports, every test is collected, and
# only the ones needing the absent package report as skipped.
pytestmark = pytest.mark.skipif(
    find_spec("pyarrow") is None,
    reason="pyarrow not installed",
)


def test_parse_parquet_columns(sample_parquet):
    from jdatamunch_mcp.parser.parquet_parser import parse_parquet
    parsed = parse_parquet(sample_parquet)
    assert len(parsed.columns) == 5
    assert [c.name for c in parsed.columns] == ["id", "name", "age", "city", "score"]


def test_parse_parquet_rows(sample_parquet):
    from jdatamunch_mcp.parser.parquet_parser import parse_parquet
    parsed = parse_parquet(sample_parquet)
    rows = list(parsed.row_iterator)
    assert len(rows) == 10
    assert rows[0][1] == "Alice"
    assert rows[0][0] == "1"


def test_parse_parquet_null_handling(sample_parquet):
    from jdatamunch_mcp.parser.parquet_parser import parse_parquet
    parsed = parse_parquet(sample_parquet)
    rows = list(parsed.row_iterator)
    frank = rows[5]
    assert frank[2] == ""   # age is None
    assert frank[4] == ""   # score is None


def test_parse_parquet_metadata(sample_parquet):
    from jdatamunch_mcp.parser.parquet_parser import parse_parquet
    parsed = parse_parquet(sample_parquet)
    assert parsed.metadata["estimated_rows"] == 10
    assert parsed.metadata["file_size"] > 0


def test_index_parquet(sample_parquet, storage_dir):
    result = index_local(path=sample_parquet, name="sample-parquet", storage_path=storage_dir)
    assert "error" not in result
    r = result["result"]
    assert r["rows"] == 10
    assert r["columns"] == 5
    assert r.get("skipped") is not True


def test_index_parquet_column_types(sample_parquet, storage_dir):
    index_local(path=sample_parquet, name="sample-parquet", storage_path=storage_dir)
    store = DataStore(base_path=storage_dir)
    idx = store.load("sample-parquet")
    col_types = {c["name"]: c["type"] for c in idx.columns}
    assert col_types["id"] == "integer"
    assert col_types["name"] == "string"
    assert col_types["score"] == "float"


def test_parquet_incremental(sample_parquet, storage_dir):
    index_local(path=sample_parquet, name="sample-parquet", storage_path=storage_dir)
    result2 = index_local(
        path=sample_parquet, name="sample-parquet", incremental=True, storage_path=storage_dir
    )
    assert result2["result"].get("skipped") is True


def test_parquet_missing_pyarrow(tmp_path, storage_dir, monkeypatch):
    """Graceful error when pyarrow is not installed."""
    import builtins
    real_import = builtins.__import__

    def mock_import(name, *args, **kwargs):
        if name == "pyarrow" or name == "pyarrow.parquet":
            raise ImportError("No module named 'pyarrow'")
        return real_import(name, *args, **kwargs)

    p = tmp_path / "fake.parquet"
    p.write_bytes(b"PAR1")

    monkeypatch.setattr(builtins, "__import__", mock_import)
    result = index_local(path=str(p), storage_path=storage_dir)
    assert "error" in result
    assert "parquet" in result["error"].lower() or "pyarrow" in result["error"].lower()
