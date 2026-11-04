"""Cross-parser normalization contract (A10).

Verifies CSV / JSONL / Parquet produce identical column profiles for
the same logical rows, after going through normalize_native.
"""

import csv
import json

import pytest

from jdatamunch_mcp.parser.normalize import normalize_native, NULL_TOKENS


def test_none_returns_empty_string():
    assert normalize_native(None) == ""


def test_nan_returns_empty_string():
    assert normalize_native(float("nan")) == ""


def test_int_round_trips():
    assert normalize_native(42) == "42"


def test_integer_valued_float_normalizes_to_int_literal():
    assert normalize_native(3.0) == "3"
    assert normalize_native(-7.0) == "-7"


def test_bool_to_python_str():
    assert normalize_native(True) == "True"
    assert normalize_native(False) == "False"


def test_canonical_null_token_collapses_to_empty():
    for token in ("N/A", "null", "  null  ", "NaN"):
        assert normalize_native(token) == ""


def test_text_placeholder_passes_through_for_the_profiler_to_decide():
    # "-" is null only in a non-text column; that is decided downstream by
    # column type, the same way a CSV cell is (PROFILE_VERSION 2).
    for token in ("-", ".", " - "):
        assert normalize_native(token) == token.strip()


def test_string_passthrough_with_strip():
    assert normalize_native("  hello  ") == "hello"


def test_datetime_iso_format():
    import datetime as dt
    assert normalize_native(dt.datetime(2024, 1, 2, 3, 4, 5)) == "2024-01-02T03:04:05"
    assert normalize_native(dt.date(2024, 1, 2)) == "2024-01-02"


def test_csv_jsonl_yield_same_profiles(tmp_path):
    """Same logical rows via CSV vs JSONL must produce identical column profiles."""
    from jdatamunch_mcp.tools.index_local import index_local
    from jdatamunch_mcp.storage.data_store import DataStore

    rows = [(1, "Alice", 9.5), (2, "Bob", 7.2), (3, "Charlie", 8.8)]

    csv_path = tmp_path / "x.csv"
    with open(csv_path, "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["id", "name", "score"])
        w.writerows(rows)

    jsonl_path = tmp_path / "x.jsonl"
    with open(jsonl_path, "w", encoding="utf-8") as f:
        for r in rows:
            f.write(json.dumps({"id": r[0], "name": r[1], "score": r[2]}) + "\n")

    storage = tmp_path / "data-index"
    storage.mkdir()

    index_local(path=str(csv_path), name="csv_v", storage_path=str(storage))
    index_local(path=str(jsonl_path), name="jsonl_v", storage_path=str(storage))

    store = DataStore(base_path=str(storage))
    csv_idx = store.load("csv_v")
    jsonl_idx = store.load("jsonl_v")

    # Compare column-level structural facts (skip dataset-level metadata that
    # differs by source format on purpose).
    def strip_summary(cols):
        out = []
        for c in cols:
            cc = dict(c)
            # ai_summary is rule-based but sample order may diverge between formats
            # only when types differ. Drop it; type/cardinality/min/max are the contract.
            cc.pop("ai_summary", None)
            cc.pop("sample_values", None)
            out.append(cc)
        return out

    assert strip_summary(csv_idx.columns) == strip_summary(jsonl_idx.columns)
