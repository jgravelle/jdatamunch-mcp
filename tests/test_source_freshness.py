"""`describe_column` discloses whether its source file still matches the index.

A column profile is a claim about data on disk. `describe_column` returned that
profile with no indication of whether the file it describes had changed — or
been deleted — since indexing, so a caller had no way to tell a current profile
from one describing a file that no longer exists.

⚠ **`fresh` is reachable ONLY through `verify_source=True`.** jData's standing
product call (2026-07-24) is that a permanent `index: "fresh"` asserts currency
this product cannot back, which is why the index channel appears only as a
positive detection. This change keeps that rule exactly rather than relaxing it:

* the cheap default reading can prove `stale` / `missing_source` with a `stat`,
  and otherwise says `unknown` — never `fresh`;
* `verify_source=True` re-hashes the file, which is the only thing that
  actually establishes currency, and is opt-in because the source can be
  hundreds of megabytes.

A matching size does NOT prove matching content, and the `unknown` reading says
so in its own `reason`.
"""

from __future__ import annotations

import os

import pytest

from jdatamunch_mcp.storage.data_store import DataStore
from jdatamunch_mcp.tools.describe_column import describe_column
from jdatamunch_mcp.tools.index_local import index_local


@pytest.fixture()
def dataset(tmp_path):
    csv = tmp_path / "t.csv"
    csv.write_text("a,b\n1,x\n2,y\n3,z\n", encoding="utf-8")
    store_path = str(tmp_path / "store")
    res = index_local(path=str(csv), storage_path=store_path, use_ai_summaries=False)
    name = res.get("dataset") or (res.get("result") or {}).get("dataset")
    return {"name": name, "storage_path": store_path, "file": csv}


def _meta(dataset, **kw):
    return describe_column(
        dataset=dataset["name"], column="a",
        storage_path=dataset["storage_path"], **kw
    )["_meta"]


# --- the cheap default reading --------------------------------------------


def test_an_unchanged_source_reads_unknown_not_fresh(dataset):
    """⚠ The load-bearing assertion of this whole change.

    A matching size is not proof of matching content, so the cheap path must
    not upgrade it to `fresh`. Asserting `!= "fresh"` as well as `== "unknown"`
    because the failure that matters is the over-claim, not the label.
    """
    m = _meta(dataset)
    assert m["freshness"]["state"] == "unknown"
    assert m["freshness"]["state"] != "fresh"
    assert m["verdict"]["state"] == "ok"


def test_an_unproven_reading_emits_no_index_channel(dataset):
    """jData's index channel is a positive detection, never a standing claim."""
    assert _meta(dataset)["verdict"]["channels"] == {}


def test_the_unknown_reading_explains_itself(dataset):
    reason = _meta(dataset)["freshness"]["reason"]
    assert "does not prove" in reason
    assert "verify_source" in reason


# --- what it CAN prove cheaply --------------------------------------------


def test_a_changed_size_proves_stale(dataset):
    with open(dataset["file"], "a", encoding="utf-8") as fh:
        fh.write("4,w\n")
    m = _meta(dataset)
    assert m["freshness"]["state"] == "stale"
    assert m["freshness"]["indexed_size_bytes"] != m["freshness"]["current_size_bytes"]
    assert m["verdict"]["state"] == "degraded"
    assert m["verdict"]["channels"]["index"] == "stale"


def test_a_deleted_source_is_reported(dataset):
    os.remove(dataset["file"])
    m = _meta(dataset)
    assert m["freshness"]["state"] == "missing_source"
    assert m["verdict"]["state"] == "degraded"
    assert m["verdict"]["channels"]["index"] == "stale"


def test_a_degraded_verdict_says_what_it_means(dataset):
    os.remove(dataset["file"])
    note = _meta(dataset)["verdict"]["note"]
    assert "indexed snapshot" in note


# --- the expensive, opt-in reading ----------------------------------------


def test_verify_source_is_the_only_path_to_fresh(dataset):
    m = _meta(dataset, verify_source=True)
    assert m["freshness"]["state"] == "fresh"
    assert m["freshness"]["verified"] == "content_hash"
    assert m["verdict"]["channels"]["index"] == "fresh"


def test_verify_source_still_detects_a_changed_file(dataset):
    with open(dataset["file"], "a", encoding="utf-8") as fh:
        fh.write("4,w\n")
    m = _meta(dataset, verify_source=True)
    assert m["freshness"]["state"] == "stale"
    assert m["freshness"]["verified"] == "content_hash"


def test_verify_source_catches_a_same_size_edit_that_the_cheap_path_cannot(dataset):
    """⚠ The case that justifies the two readings existing separately.

    An in-place edit preserving the byte count is invisible to `stat`. The
    cheap path must say `unknown` — not `fresh` — and the hash must catch it.
    """
    original = dataset["file"].read_text(encoding="utf-8")
    edited = original.replace("1,x", "9,q")
    assert len(edited) == len(original), "fixture must keep the size identical"
    dataset["file"].write_text(edited, encoding="utf-8")

    assert _meta(dataset)["freshness"]["state"] == "unknown"
    assert _meta(dataset, verify_source=True)["freshness"]["state"] == "stale"


def test_verify_source_defaults_off(dataset):
    """It re-hashes the whole source file; that must never be the default."""
    import inspect

    sig = inspect.signature(describe_column)
    assert sig.parameters["verify_source"].default is False


# --- the store API directly ------------------------------------------------


def test_source_freshness_never_returns_fresh(dataset):
    """Pinned at the API, so a later caller cannot obtain `fresh` cheaply."""
    store = DataStore(base_path=dataset["storage_path"])
    idx = store.load(dataset["name"])
    assert store.source_freshness(idx)["state"] != "fresh"


def test_source_freshness_handles_a_missing_source_path(dataset):
    store = DataStore(base_path=dataset["storage_path"])
    idx = store.load(dataset["name"])
    idx.source_path = ""
    out = store.source_freshness(idx)
    assert out["state"] == "unknown"
    assert "no source path" in out["reason"]


def test_the_profile_itself_is_unchanged(dataset):
    """Disclosure is additive. The answer must not move."""
    out = describe_column(
        dataset=dataset["name"], column="a", storage_path=dataset["storage_path"]
    )
    assert set(out) == {"result", "_meta"}
    assert out["result"]["name"] == "a"
    assert out["result"]["count"] == 3
