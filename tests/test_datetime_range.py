"""Datetime min/max are compared as dates, and 12-hour AM/PM text is detected.

Found by a realistic-agent benchmark on the LAPD crime file: DATE OCC reported
a latest date of "12/31/2023 12:00:00 AM" while the file runs to 2024-12-30.
"12/31/2023" is the alphabetically last US date string, so the range was a
text comparison. The same response called the format 24-hour.
"""

import csv

from jdatamunch_mcp.profiler.column_profiler import (
    PROFILE_VERSION,
    _ColAcc,
    _datetime_sort_key,
    _get_datetime_format,
    finalize_profile,
    update_acc,
)
from jdatamunch_mcp.storage.data_store import DataStore
from jdatamunch_mcp.tools.describe_column import describe_column
from jdatamunch_mcp.tools.index_local import index_local


def _profile(values):
    acc = _ColAcc("d", 0)
    for v in values:
        update_acc(acc, v)
    return finalize_profile(acc)


def test_us_dates_range_is_chronological_not_alphabetical():
    p = _profile([
        "01/01/2020 12:00:00 AM",
        "12/31/2023 12:00:00 AM",   # alphabetically last
        "12/30/2024 12:00:00 AM",   # chronologically last
        "06/15/2022 12:00:00 AM",
    ])
    assert p.type == "datetime"
    assert p.datetime_min == "01/01/2020 12:00:00 AM"
    assert p.datetime_max == "12/30/2024 12:00:00 AM"


def test_earliest_is_not_just_the_alphabetical_first():
    # "01/05/2024" sorts before "02/01/2019" as text.
    p = _profile(["01/05/2024", "02/01/2019", "11/30/2020"])
    assert p.datetime_min == "02/01/2019"
    assert p.datetime_max == "01/05/2024"


def test_12_hour_clock_orders_am_pm_correctly():
    p = _profile([
        "03/04/2021 01:00:00 AM",
        "03/04/2021 12:00:00 AM",   # midnight, earliest
        "03/04/2021 11:59:00 AM",
        "03/04/2021 12:30:00 PM",   # noon
        "03/04/2021 11:00:00 PM",   # latest
    ])
    assert p.datetime_min == "03/04/2021 12:00:00 AM"
    assert p.datetime_max == "03/04/2021 11:00:00 PM"


def test_am_pm_format_is_reported_as_12_hour():
    assert _get_datetime_format("01/15/2020 12:00:00 AM") == "%m/%d/%Y %I:%M:%S %p"
    assert _get_datetime_format("01/15/2020 13:00:00") == "%m/%d/%Y %H:%M:%S"
    p = _profile(["01/15/2020 12:00:00 AM", "02/15/2020 03:00:00 PM"])
    assert p.datetime_format == "%m/%d/%Y %I:%M:%S %p"


def test_iso_dates_still_correct():
    p = _profile(["2024-01-02", "2023-12-31", "2024-11-30"])
    assert p.datetime_min == "2023-12-31"
    assert p.datetime_max == "2024-11-30"
    p = _profile(["2024-01-02T10:00:00", "2024-01-02T09:59:59"])
    assert p.datetime_min == "2024-01-02T09:59:59"
    assert p.datetime_max == "2024-01-02T10:00:00"


def test_sort_key_compares_across_iso_and_us_text():
    assert _datetime_sort_key("12/31/2023") < _datetime_sort_key("2024-01-01")
    assert _datetime_sort_key("12/30/2024 11:00:00 PM") > _datetime_sort_key("12/30/2024 10:59:59 PM")
    assert _datetime_sort_key("not a date") is None


def _write_csv(path, rows):
    with open(path, "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["id", "when"])
        w.writerows(rows)


def test_describe_column_end_to_end(tmp_path):
    src = tmp_path / "events.csv"
    _write_csv(src, [
        [1, "01/01/2020 12:00:00 AM"],
        [2, "12/31/2023 12:00:00 AM"],
        [3, "12/30/2024 12:00:00 AM"],
    ])
    store = str(tmp_path / "store")
    index_local(path=str(src), name="events", storage_path=store)
    r = describe_column(dataset="events", column="when", storage_path=store)["result"]
    assert r["datetime_max"] == "12/30/2024 12:00:00 AM"
    assert r["datetime_min"] == "01/01/2020 12:00:00 AM"
    assert r["datetime_format"] == "%m/%d/%Y %I:%M:%S %p"


def test_old_profile_version_forces_reprofile_of_unchanged_file(tmp_path):
    """An index made under older rules must not survive incremental=True."""
    src = tmp_path / "events.csv"
    _write_csv(src, [[1, "12/31/2023"], [2, "12/30/2024"]])
    store_path = str(tmp_path / "store")
    index_local(path=str(src), name="events", storage_path=store_path)
    store = DataStore(base_path=store_path)
    assert store.load("events").profile_version == PROFILE_VERSION
    assert store.needs_reindex("events", str(src)) is False

    # Simulate an index written before the fix: old version, wrong max.
    import json
    ip = store.index_path("events")
    d = json.loads(ip.read_text(encoding="utf-8"))
    d.pop("profile_version")
    for c in d["columns"]:
        if c["name"] == "when":
            c["datetime_max"] = "12/31/2023"
    ip.write_text(json.dumps(d), encoding="utf-8")
    store.index_checksum_path("events").unlink(missing_ok=True)

    assert store.load("events").profile_version == 1
    assert store.needs_reindex("events", str(src)) is True
    res = index_local(path=str(src), name="events", storage_path=store_path)
    assert not res["result"].get("skipped")
    r = describe_column(dataset="events", column="when", storage_path=store_path)["result"]
    assert r["datetime_max"] == "12/30/2024"
