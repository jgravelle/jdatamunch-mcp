"""Counts in a trimmed reply describe what is sent (issue 18).

`enforce_budget` shortens rows, groups and columns to fit the token budget.
The counts beside them (`returned`, `columns_projected`,
`column_pagination.returned`) were set when the result was built and kept
their pre-trim values. A caller that pages `get_rows` by `offset + returned`
skipped every row the trimmer cut.
"""

import asyncio
import json

import pytest

from jdatamunch_mcp.budget import enforce_budget

NEWLINE = chr(10)


def _call(name, args):
    from jdatamunch_mcp.server import call_tool
    return json.loads(asyncio.run(call_tool(name, args))[0].text)


def _write_csv(path, header, rows):
    path.write_text(
        NEWLINE.join([",".join(header)] + [",".join(r) for r in rows]) + NEWLINE,
        encoding="utf-8",
    )


def _index(tmp_path, monkeypatch, csv, name):
    from jdatamunch_mcp.tools.index_local import index_local
    store = tmp_path / "store"
    monkeypatch.setenv("DATA_INDEX_PATH", str(store))
    monkeypatch.delenv("JDATAMUNCH_META_FIELDS", raising=False)
    monkeypatch.delenv("JDATAMUNCH_MAX_RESPONSE_TOKENS", raising=False)
    out = index_local(path=str(csv), name=name, storage_path=str(store),
                      use_ai_summaries=False)
    inner = out.get("result") or out
    assert inner.get("dataset"), out
    return inner["dataset"]


@pytest.fixture
def tall(tmp_path, monkeypatch):
    """600 rows of 400-char payloads: every row tool overruns the budget."""
    csv = tmp_path / "tall.csv"
    _write_csv(csv, ["id", "payload"], [[str(i), "x" * 400] for i in range(600)])
    return _index(tmp_path, monkeypatch, csv, "tall")


@pytest.fixture
def wide(tmp_path, monkeypatch):
    """100 columns x 40 rows: column lists overrun the budget."""
    csv = tmp_path / "wide.csv"
    header = [f"c{j:03d}" for j in range(100)]
    rows = [[f"r{i}_c{j}_" + "y" * 60 for j in range(100)] for i in range(40)]
    _write_csv(csv, header, rows)
    return _index(tmp_path, monkeypatch, csv, "wide")


ROW_CALLS = {
    "run_sql": lambda ds: {"sql": "SELECT * FROM rows", "datasets": [ds], "limit": 500},
    "get_rows": lambda ds: {"dataset": ds, "limit": 500},
    "sample_rows": lambda ds: {"dataset": ds, "n": 500},
    "join_datasets": lambda ds: {"dataset_a": ds, "dataset_b": ds, "join_column_a": "id",
                                 "join_column_b": "id", "limit": 500},
    "aggregate": lambda ds: {"dataset": ds, "group_by": ["id"], "limit": 1000,
                             "aggregations": [{"column": "payload", "function": "max"}]},
}


@pytest.mark.parametrize("tool", sorted(ROW_CALLS))
def test_returned_matches_rows_sent(tall, tool):
    out = _call(tool, ROW_CALLS[tool](tall))
    res = out["result"]
    sent = res.get("rows") if "rows" in res else res.get("groups")
    assert out.get("truncated"), "fixture must overrun the budget"
    assert res["returned"] == len(sent)


def test_paging_get_rows_by_returned_skips_nothing(tall):
    seen = []
    offset = 0
    for _ in range(50):
        res = _call("get_rows", {"dataset": tall, "limit": 500, "offset": offset})["result"]
        if not res["rows"]:
            break
        at = res["fields"].index("_row_id")
        seen.extend(r[at] for r in res["rows"])
        offset += res["returned"]
        if offset >= res["total_matching"]:
            break
    assert seen == list(range(1, 601))


def test_get_rows_columns_projected_matches_columns_sent(wide):
    out = _call("get_rows", {"dataset": wide, "limit": 40, "row_format": "objects"})
    res = out["result"]
    assert out.get("truncated"), "fixture must overrun the budget"
    sent = [k for k in res["rows"][0] if k != "_row_id"]
    assert res["columns_projected"] == len(sent)
    assert res["returned"] == len(res["rows"])


def test_get_rows_arrays_columns_projected_matches_fields(wide):
    out = _call("get_rows", {"dataset": wide, "limit": 40, "row_format": "arrays"})
    res = out["result"]
    assert out.get("truncated"), "fixture must overrun the budget"
    sent = [f for f in res["fields"] if f != "_row_id"]
    assert res["columns_projected"] == len(sent)
    assert all(len(r) == len(res["fields"]) for r in res["rows"])


def test_describe_dataset_full_column_pagination_matches_columns_sent(wide):
    out = _call("describe_dataset", {"dataset": wide, "detail": "full"})
    res = out["result"]
    assert out.get("truncated"), "fixture must overrun the budget"
    page = res["column_pagination"]
    assert page["returned"] == len(res["columns"])
    assert page["truncated"] is True
    # Paging by columns_offset = offset + returned must reach every column.
    sent = [c["name"] for c in res["columns"]]
    assert sent + page["remaining_column_names"] == [f"c{j:03d}" for j in range(100)]


# Unit checks on the trimmer itself -------------------------------------------

def test_untrimmed_result_keeps_its_counts():
    small = {"result": {"rows": [{"id": 1}], "returned": 1, "columns_projected": 1}}
    out = enforce_budget(small, "get_rows")
    assert out["result"]["returned"] == 1
    assert out["result"]["columns_projected"] == 1


def test_join_columns_lists_follow_a_column_trim(monkeypatch):
    monkeypatch.setenv("JDATAMUNCH_MAX_RESPONSE_TOKENS", "1500")
    wide_row = {"_row_id": 0, **{f"a{j}": "z" * 300 for j in range(20)},
                **{f"b{j}": "z" * 300 for j in range(20)}}
    result = {"result": {
        "rows": [dict(wide_row) for _ in range(3)],
        "returned": 3, "offset": 0, "total_matching": 3,
        "columns_a": [f"a{j}" for j in range(20)],
        "columns_b": [f"b{j}" for j in range(20)],
    }}
    out = enforce_budget(result, "join_datasets")["result"]
    sent = set(out["rows"][0]) - {"_row_id"}
    assert set(out["columns_a"]) | set(out["columns_b"]) == sent
    assert out["returned"] == len(out["rows"])


def test_meta_column_truncation_follows_a_column_trim(monkeypatch):
    monkeypatch.setenv("JDATAMUNCH_MAX_RESPONSE_TOKENS", "1500")
    row = {"_row_id": 0, **{f"c{j:02d}": "z" * 300 for j in range(30)}}
    result = {
        "result": {"rows": [dict(row) for _ in range(3)], "returned": 3,
                   "columns_projected": 30, "offset": 0, "total_matching": 3},
        "_meta": {"column_truncation": {
            "total_columns": 40, "returned": 30,
            "omitted_columns": [f"c{j:02d}" for j in range(30, 40)],
        }},
    }
    out = enforce_budget(result, "get_rows")
    sent = [k for k in out["result"]["rows"][0] if k != "_row_id"]
    ct = out["_meta"]["column_truncation"]
    assert ct["returned"] == len(sent) == out["result"]["columns_projected"]
    assert sent + ct["omitted_columns"] == [f"c{j:02d}" for j in range(40)]
