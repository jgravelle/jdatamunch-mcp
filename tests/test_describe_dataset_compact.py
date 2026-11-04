"""describe_dataset's compact orientation (the default).

A realistic-agent benchmark found the full profile (4,870 tokens on a
28-column file, 6,878 on the wire with indent=2) cost more than reading the
file's header plus 20 rows (2,274). The compact view is one row per column.
Design note: hq/projects/jdatamunch/compact-orientation-design.md.
"""

import csv
import json
import random

import pytest

from jdatamunch_mcp.tools.describe_dataset import (
    COMPACT_ALL_VALUES_UP_TO,
    COMPACT_FIELDS,
    COMPACT_TOP_VALUES,
    COMPACT_VALUE_CHARS,
    describe_dataset,
)
from jdatamunch_mcp.tools.index_local import index_local


def _rows(result):
    r = result["result"]
    return {row[0]: dict(zip(r["fields"], row)) for row in r["columns"]}


def test_default_is_compact(indexed_sample):
    r = describe_dataset(dataset="sample", storage_path=indexed_sample)["result"]
    assert r["detail"] == "compact"
    assert r["fields"] == COMPACT_FIELDS
    assert r["rows"] == 10 and r["column_count"] == 5
    assert [row[0] for row in r["columns"]] == ["id", "name", "age", "city", "score"]
    assert all(len(row) == len(COMPACT_FIELDS) for row in r["columns"])


def test_full_keeps_the_old_shape(indexed_sample):
    r = describe_dataset(dataset="sample", storage_path=indexed_sample, detail="full")["result"]
    col = r["columns"][0]
    assert col["id"] == "sample::id#column"
    assert "sample_values" in col


def test_invalid_detail(indexed_sample):
    res = describe_dataset(dataset="sample", storage_path=indexed_sample, detail="tiny")
    assert "INVALID_DETAIL" in res["error"]


def test_numeric_range_and_text_values(indexed_sample):
    rows = _rows(describe_dataset(dataset="sample", storage_path=indexed_sample))
    assert rows["age"]["type"] == "integer"
    assert rows["age"]["range_or_top_values"] == [22, 45]
    assert rows["age"]["null_pct"] > 0
    city = rows["city"]
    # 4 distinct values <= the cut-off: every value, most frequent first.
    assert city["distinct"] == 4
    assert city["range_or_top_values"][0] == "Hollywood"
    assert sorted(city["range_or_top_values"]) == sorted(["Hollywood", "Central", "Pacific", "N Hollywood"])


def test_unique_columns_listed(indexed_sample):
    r = describe_dataset(dataset="sample", storage_path=indexed_sample)["result"]
    assert "id" in r["unique"]


def test_column_filter(indexed_sample):
    r = describe_dataset(dataset="sample", columns=["city"], storage_path=indexed_sample)["result"]
    assert [row[0] for row in r["columns"]] == ["city"]


def _write(path, header, rows):
    with open(path, "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(header)
        w.writerows(rows)


def test_many_values_truncated_and_capped(tmp_path):
    src = tmp_path / "t.csv"
    long = "X" * 100
    rows = [[i, f"cat{i % 30}", long + str(i % 3)] for i in range(300)]
    _write(src, ["id", "cat", "longtext"], rows)
    store = str(tmp_path / "s")
    index_local(path=str(src), name="t", storage_path=store)
    rows = _rows(describe_dataset(dataset="t", storage_path=store))
    assert rows["cat"]["distinct"] == 30
    assert len(rows["cat"]["range_or_top_values"]) == COMPACT_TOP_VALUES
    lt = rows["longtext"]["range_or_top_values"]
    assert len(lt) == 3  # 3 distinct, all listed
    assert all(len(v) == COMPACT_VALUE_CHARS and v.endswith("...") for v in lt)


def test_list_is_complete_exactly_when_small(tmp_path):
    src = tmp_path / "c.csv"
    n = COMPACT_ALL_VALUES_UP_TO
    _write(src, ["a", "b"], [[f"v{i % n}", f"w{i % (n + 1)}"] for i in range(200)])
    store = str(tmp_path / "s")
    index_local(path=str(src), name="c", storage_path=store)
    rows = _rows(describe_dataset(dataset="c", storage_path=store))
    assert len(rows["a"]["range_or_top_values"]) == n == rows["a"]["distinct"]
    assert len(rows["b"]["range_or_top_values"]) == COMPACT_TOP_VALUES


def test_datetime_range_and_format(tmp_path):
    src = tmp_path / "d.csv"
    _write(src, ["when"], [["12/31/2023 12:00:00 AM"], ["12/30/2024 01:00:00 PM"], ["01/01/2020 12:00:00 AM"]])
    store = str(tmp_path / "s")
    index_local(path=str(src), name="d", storage_path=store)
    r = describe_dataset(dataset="d", storage_path=store)["result"]
    row = _rows({"result": r})["when"]
    assert row["range_or_top_values"] == ["01/01/2020 12:00:00 AM", "12/30/2024 01:00:00 PM"]
    assert r["datetime_formats"] == {"when": "%m/%d/%Y %I:%M:%S %p"}


def test_text_values_are_redacted(tmp_path):
    src = tmp_path / "p.csv"
    _write(src, ["contact"], [["alice@example.com"], ["bob@example.com"], ["alice@example.com"]])
    store = str(tmp_path / "s")
    index_local(path=str(src), name="p", storage_path=store)
    res = describe_dataset(dataset="p", storage_path=store)
    vals = _rows(res)["contact"]["range_or_top_values"]
    assert vals and not any("@example.com" in v for v in vals)
    assert res["_meta"]["redaction"]["cells_redacted"] >= 1


@pytest.mark.parametrize("seed", [1, 2])
def test_compact_is_smaller_than_head_21_on_a_wide_file(tmp_path, seed):
    """Bytes, as the repo's own estimator counts them (bytes/4)."""
    rnd = random.Random(seed)
    header = [f"Column Name {i}" for i in range(30)]
    words = ["ALPHA", "BRAVO", "CHARLIE STREET", "DELTA AVENUE", "ECHO", "FOXTROT LANE"]
    rows = []
    for r in range(500):
        row = []
        for i in range(30):
            k = i % 5
            if k == 0:
                row.append(rnd.randint(0, 10**8))
            elif k == 1:
                row.append(f"{rnd.random() * 100:.4f}")
            elif k == 2:
                row.append(f"{rnd.randint(1, 12):02d}/{rnd.randint(1, 28):02d}/2024 0{rnd.randint(1, 9)}:00:00 PM")
            elif k == 3:
                row.append(rnd.choice(words[:4]))
            else:
                row.append(" ".join(rnd.choice(words) for _ in range(4)))
        rows.append(row)
    src = tmp_path / "w.csv"
    _write(src, header, rows)
    store = str(tmp_path / "s")
    index_local(path=str(src), name="w", storage_path=store)
    res = describe_dataset(dataset="w", storage_path=store)
    compact = len(json.dumps({"result": res["result"]}, separators=(",", ":")).encode("utf-8"))
    with open(src, "rb") as f:
        head = sum(len(f.readline()) for _ in range(21))
    assert compact < head, (compact, head)
    full = describe_dataset(dataset="w", storage_path=store, detail="full")
    full_bytes = len(json.dumps({"result": full["result"]}, separators=(",", ":")).encode("utf-8"))
    assert compact < full_bytes / 3
