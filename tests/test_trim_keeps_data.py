"""A trimmed reply still carries data (found beside issue 18).

With 600 rows of 400-character payloads, `enforce_budget` cut rows to 4/3 of
the budget, found the reply still over, and halved the columns. Three columns
(`_row_id`, `id`, `payload`) halved to one, so `get_rows` and `sample_rows`
sent only `_row_id`. The trimmer now cuts rows to the budget first, never
drops past the first data column, and cuts long values as the last step.
"""

import pytest

from jdatamunch_mcp.budget import CUT_MARK, _estimate_tokens, enforce_budget

from test_returned_after_trim import _call, tall  # noqa: F401  (fixture)

ROW_TOOLS = {
    "get_rows arrays": ("get_rows", lambda ds: {"dataset": ds, "limit": 500}),
    "get_rows objects": ("get_rows", lambda ds: {"dataset": ds, "limit": 500,
                                                 "row_format": "objects"}),
    "sample_rows": ("sample_rows", lambda ds: {"dataset": ds, "n": 500}),
}


def _as_dicts(res):
    if isinstance(res.get("fields"), list):
        return [dict(zip(res["fields"], r)) for r in res["rows"]]
    return res["rows"]


@pytest.mark.parametrize("case", sorted(ROW_TOOLS))
def test_trimmed_rows_keep_every_column(tall, case):  # noqa: F811
    tool, args = ROW_TOOLS[case]
    out = _call(tool, args(tall))
    res = out["result"]
    assert out.get("truncated"), "fixture must overrun the budget"
    rows = _as_dicts(res)
    assert len(rows) > 1
    for row in rows:
        assert set(row) == {"_row_id", "id", "payload"}
        assert row["payload"] == "x" * 400
    assert res["returned"] == len(rows)
    if "columns_projected" in res:
        assert res["columns_projected"] == 2
    details = out["truncated"]["details"]
    assert not any("columns per row" in d for d in details), details


# Unit checks on the trim order ------------------------------------------------

def test_column_drop_stops_at_the_first_data_column(monkeypatch):
    monkeypatch.setenv("JDATAMUNCH_MAX_RESPONSE_TOKENS", "300")
    row = {"_row_id": 1, "id": 7, "payload": "p" * 4000, "extra": "e" * 4000}
    out = enforce_budget({"result": {"rows": [row], "returned": 1,
                                     "columns_projected": 3}}, "sample_rows")
    sent = out["result"]["rows"][0]
    assert list(sent) == ["_row_id", "id"]
    assert sent["id"] == 7
    assert out["result"]["columns_projected"] == 1


def test_first_data_column_is_cut_not_dropped(monkeypatch):
    monkeypatch.setenv("JDATAMUNCH_MAX_RESPONSE_TOKENS", "300")
    body = {"fields": ["_row_id", "payload", "other"],
            "rows": [[i, "p" * 5000, "o" * 5000] for i in range(5)],
            "returned": 5, "columns_projected": 2}
    out = enforce_budget({"result": body}, "get_rows")
    res = out["result"]
    assert res["fields"] == ["_row_id", "payload"]
    assert res["rows"], "at least one row must be sent"
    for r in res["rows"]:
        assert len(r) == 2
        assert r[1].startswith("p") and r[1].endswith(CUT_MARK)
        assert 0 < len(r[1]) < 5000
    assert _estimate_tokens(out) <= 300 + 120  # the truncation note itself
    details = out["_meta"]["truncation"]["details"]
    assert any("string values cut" in d for d in details)
    assert res["returned"] == len(res["rows"])
    assert res["columns_projected"] == 1


def test_rows_regrow_after_a_column_drop(monkeypatch):
    monkeypatch.setenv("JDATAMUNCH_MAX_RESPONSE_TOKENS", "1500")
    rows = [{"_row_id": i, "a": i, **{f"c{j:02d}": "z" * 300 for j in range(30)}}
            for i in range(50)]
    out = enforce_budget({"result": {"rows": rows, "returned": 50,
                                     "columns_projected": 31}}, "get_rows")
    res = out["result"]
    assert "c00" in res["rows"][0], "drop only what one row cannot carry"
    assert len(res["rows"]) >= 1
    assert res["returned"] == len(res["rows"])
    assert all(v == "z" * 300 for k, v in res["rows"][0].items() if k.startswith("c"))


def test_row_id_not_first_still_keeps_a_data_column(monkeypatch):
    monkeypatch.setenv("JDATAMUNCH_MAX_RESPONSE_TOKENS", "200")
    row = {"name": "n" * 3000, "_row_id": 1, "b": "b" * 3000}
    out = enforce_budget({"result": {"rows": [row]}}, "join_datasets")
    sent = out["result"]["rows"][0]
    assert list(sent) == ["name"]
    assert sent["name"].endswith(CUT_MARK)


def test_reply_that_fits_after_a_row_cut_is_left_whole(monkeypatch):
    monkeypatch.setenv("JDATAMUNCH_MAX_RESPONSE_TOKENS", "2000")
    rows = [{"_row_id": i, "id": i, "payload": "x" * 400} for i in range(100)]
    out = enforce_budget({"result": {"rows": rows, "returned": 100}}, "sample_rows")
    res = out["result"]
    assert 1 < len(res["rows"]) < 100
    assert all(r["payload"] == "x" * 400 for r in res["rows"])
    assert out["_meta"]["truncation"]["details"] == [f"rows truncated to {len(res['rows'])}"]
