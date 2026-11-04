"""Per-call response trimming: compact describe_column, array rows, smaller _meta.

On a realistic-agent benchmark, describe_column was 865 tokens on the wire for
a range question (558 of them 20 top values written as repeated-key objects),
and every tool's _meta carried eight dollar figures plus a running total.
Design note: hq/projects/jdatamunch/per-call-trim-design.md.
"""

import csv
import json

import pytest

from jdatamunch_mcp.budget import enforce_budget
from jdatamunch_mcp.storage.token_tracker import per_call_costs
from jdatamunch_mcp.tools.aggregate import aggregate
from jdatamunch_mcp.tools.describe_column import COMPACT_TOP_N, describe_column
from jdatamunch_mcp.tools.get_rows import get_rows, rows_to_arrays
from jdatamunch_mcp.tools.index_local import index_local


def _write(path, header, rows):
    with open(path, "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(header)
        w.writerows(rows)


def _size(obj):
    return len(json.dumps(obj, separators=(",", ":")).encode("utf-8"))


# ---- describe_column ----

def test_describe_column_default_is_compact(indexed_sample):
    r = describe_column(dataset="sample", column="city", storage_path=indexed_sample)["result"]
    assert r["detail"] == "compact"
    assert r["top_values_fields"] == ["value", "count"]
    assert r["top_values"][0] == ["Hollywood", 4]
    assert r["top_values_complete"] is True
    for gone in ("id", "sample_values", "ai_summary", "value_distribution", "cardinality_is_exact"):
        assert gone not in r


def test_describe_column_full_keeps_old_shape(indexed_sample):
    r = describe_column(dataset="sample", column="city", storage_path=indexed_sample, detail="full")["result"]
    assert r["id"] == "sample::city#column"
    assert r["value_distribution"][0]["value"] == "Hollywood"
    assert "pct" in r["value_distribution"][0]


def test_describe_column_invalid_detail(indexed_sample):
    out = describe_column(dataset="sample", column="city", storage_path=indexed_sample, detail="x")
    assert "INVALID_DETAIL" in out["error"]


def test_describe_column_numeric_and_datetime(tmp_path):
    src = tmp_path / "n.csv"
    _write(src, ["v", "d"], [[i, f"2024-01-{(i % 28) + 1:02d}"] for i in range(100)])
    store = str(tmp_path / "s")
    index_local(path=str(src), name="n", storage_path=store)
    v = describe_column(dataset="n", column="v", storage_path=store)["result"]
    assert v["min"] == 0 and v["max"] == 99 and "mean" in v
    d = describe_column(dataset="n", column="d", storage_path=store)["result"]
    assert d["datetime_min"] == "2024-01-01" and d["datetime_max"] == "2024-01-28"


def test_high_cardinality_top_values_capped_and_pct_uses_row_count(tmp_path):
    src = tmp_path / "h.csv"
    # 2,000 distinct values, so top values come from the stored top list.
    rows = [[f"k{i}"] for i in range(2000)] + [["hot"]] * 100
    _write(src, ["k"], rows)
    store = str(tmp_path / "s")
    index_local(path=str(src), name="h", storage_path=store)
    c = describe_column(dataset="h", column="k", storage_path=store)["result"]
    assert len(c["top_values"]) == COMPACT_TOP_N
    assert c["top_values"][0] == ["hot", 100]
    assert c["top_values_complete"] is False
    f = describe_column(dataset="h", column="k", storage_path=store, detail="full")["result"]
    top = f["top_values"][0]
    # 100 of 2,100 rows, not 100 of the stored top values' sum.
    assert top["pct"] == pytest.approx(100 / 2100 * 100, abs=0.01)


def test_compact_describe_column_is_much_smaller(tmp_path):
    src = tmp_path / "c.csv"
    _write(src, ["cat"], [[f"category number {i % 300}"] for i in range(3000)])
    store = str(tmp_path / "s")
    index_local(path=str(src), name="c", storage_path=store)
    c = describe_column(dataset="c", column="cat", storage_path=store)["result"]
    f = describe_column(dataset="c", column="cat", storage_path=store, detail="full")["result"]
    assert _size(c) < _size(f) / 2


def test_compact_describe_column_redacts(tmp_path):
    src = tmp_path / "p.csv"
    _write(src, ["email"], [["alice@example.com"], ["bob@example.com"], ["alice@example.com"]])
    store = str(tmp_path / "s")
    index_local(path=str(src), name="p", storage_path=store)
    r = describe_column(dataset="p", column="email", storage_path=store)["result"]
    assert r["top_values"] and not any("@example.com" in str(v) for v, _ in r["top_values"])


def test_budget_trim_marks_compact_top_values_incomplete():
    res = {"result": {"top_values": [[f"value {i}" * 20, i] for i in range(200)], "top_values_complete": True}}
    import os
    old = os.environ.get("JDATAMUNCH_MAX_RESPONSE_TOKENS")
    os.environ["JDATAMUNCH_MAX_RESPONSE_TOKENS"] = "500"
    try:
        out = enforce_budget(res, "describe_column")
    finally:
        if old is None:
            os.environ.pop("JDATAMUNCH_MAX_RESPONSE_TOKENS", None)
        else:
            os.environ["JDATAMUNCH_MAX_RESPONSE_TOKENS"] = old
    if len(out["result"]["top_values"]) < 200:
        assert out["result"]["top_values_complete"] is False


# ---- get_rows ----

def test_get_rows_arrays_by_default(indexed_sample):
    r = get_rows(dataset="sample", limit=3, storage_path=indexed_sample)["result"]
    assert isinstance(r["fields"], list) and "city" in r["fields"]
    assert len(r["rows"]) == 3 and all(isinstance(row, list) for row in r["rows"])
    assert all(len(row) == len(r["fields"]) for row in r["rows"])
    assert r["returned"] == 3


def test_get_rows_objects_opt_out(indexed_sample):
    r = get_rows(dataset="sample", limit=2, storage_path=indexed_sample, row_format="objects")["result"]
    assert "fields" not in r
    assert isinstance(r["rows"][0], dict)


def test_get_rows_invalid_format(indexed_sample):
    out = get_rows(dataset="sample", storage_path=indexed_sample, row_format="csv")
    assert "INVALID_ROW_FORMAT" in out["error"]


def test_arrays_carry_the_same_values(indexed_sample):
    a = get_rows(dataset="sample", limit=10, storage_path=indexed_sample)["result"]
    o = get_rows(dataset="sample", limit=10, storage_path=indexed_sample, row_format="objects")["result"]
    assert [dict(zip(a["fields"], r)) for r in a["rows"]] == o["rows"]
    assert _size(a) < _size(o)


def test_single_row_costs_about_the_same(indexed_sample):
    a = get_rows(dataset="sample", limit=1, storage_path=indexed_sample)["result"]
    o = get_rows(dataset="sample", limit=1, storage_path=indexed_sample, row_format="objects")["result"]
    assert _size(a) <= _size(o) + 20


def test_rows_to_arrays_keeps_column_order_and_missing_keys():
    fields, rows = rows_to_arrays([{"a": 1, "b": 2}, {"b": 3, "c": 4}])
    assert fields == ["a", "b", "c"]
    assert rows == [[1, 2, None], [None, 3, 4]]


def test_budget_trims_array_columns():
    res = {"result": {"fields": [f"c{i}" for i in range(40)],
                      "rows": [["x" * 50] * 40]}}
    import os
    os.environ["JDATAMUNCH_MAX_RESPONSE_TOKENS"] = "200"
    try:
        out = enforce_budget(res, "get_rows")
    finally:
        os.environ.pop("JDATAMUNCH_MAX_RESPONSE_TOKENS", None)
    body = out["result"]
    assert len(body["fields"]) == len(body["rows"][0]) < 40


# ---- _meta ----

def test_per_call_costs_off_by_default(monkeypatch):
    monkeypatch.delenv("JDATAMUNCH_META_COSTS", raising=False)
    assert per_call_costs(100, 1000) == {}


def test_per_call_costs_opt_in(monkeypatch):
    monkeypatch.setenv("JDATAMUNCH_META_COSTS", "1")
    out = per_call_costs(1_000_000, 2_000_000)
    assert out["total_tokens_saved"] == 2_000_000
    assert set(out) == {"total_tokens_saved", "cost_avoided", "total_cost_avoided"}


def test_tool_meta_has_no_cost_blocks_by_default(monkeypatch, indexed_sample):
    monkeypatch.delenv("JDATAMUNCH_META_COSTS", raising=False)
    m = aggregate(dataset="sample", aggregations=[{"column": "*", "function": "count"}],
                  storage_path=indexed_sample)["_meta"]
    assert "tokens_saved" in m
    for gone in ("cost_avoided", "total_cost_avoided", "total_tokens_saved"):
        assert gone not in m


@pytest.mark.parametrize("tool,body", [
    # Big enough that the tool-specific trims cannot reach the budget, so the
    # generic fallback runs.
    ("get_rows", {"fields": [f"c{i}" for i in range(60)], "rows": [["v" * 300] * 60 for _ in range(3)]}),
    ("describe_dataset", {"fields": ["name", "type", "null_pct", "distinct", "range_or_top_values"],
                          "columns": [[f"col{i}", "string", 0.0, 5, ["x" * 3000]] for i in range(80)]}),
])
def test_budget_never_trims_a_header_alone(monkeypatch, tool, body):
    monkeypatch.setenv("JDATAMUNCH_MAX_RESPONSE_TOKENS", "300")
    out = enforce_budget({"result": body}, tool)["result"]
    rows = out.get("rows") or out.get("columns")
    for row in rows:
        assert len(row) == len(out["fields"])
