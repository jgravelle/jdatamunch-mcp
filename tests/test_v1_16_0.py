"""analyze_perf (v1.16.0) -- per-tool latency + cache-hit telemetry.

Parity: jcm and jdoc both ship analyze_perf; jData had no latency telemetry and
an uninstrumented result cache. This adds an in-memory ring (always on) + an
opt-in SQLite sink + cache hit/miss counters.
"""

import json

import pytest

from jdatamunch_mcp import perf
from jdatamunch_mcp.storage import result_cache
from jdatamunch_mcp.tools.analyze_perf import analyze_perf


@pytest.fixture(autouse=True)
def _clean_telemetry():
    perf.reset()
    result_cache.reset_stats()
    yield
    perf.reset()
    result_cache.reset_stats()


# --------------------------------------------------------------------------- #
# perf ring                                                                   #
# --------------------------------------------------------------------------- #
def test_latency_stats_percentiles():
    for ms in [10, 20, 30, 40, 50, 60, 70, 80, 90, 100]:
        perf.record("aggregate", ms)
    perf.record("aggregate", 5, ok=False)
    stats = perf.latency_stats()["aggregate"]
    assert stats["count"] == 11
    assert stats["max_ms"] == 100.0
    assert stats["errors"] == 1
    assert 0.0 < stats["p50_ms"] <= stats["p95_ms"] <= stats["max_ms"]
    assert stats["error_rate"] == round(1 / 11, 3)


def test_reset_clears_ring():
    perf.record("x", 1.0)
    assert perf.latency_stats()
    perf.reset()
    assert perf.latency_stats() == {}


# --------------------------------------------------------------------------- #
# result-cache instrumentation                                                #
# --------------------------------------------------------------------------- #
def test_cache_hit_miss_counters(tmp_path):
    dataset_dir = tmp_path / "ds"
    dataset_dir.mkdir()
    # Miss, then populate, then hit.
    assert result_cache.get(dataset_dir, "k1", tool="aggregate") is None
    result_cache.put(dataset_dir, "k1", {"value": 42})
    assert result_cache.get(dataset_dir, "k1", tool="aggregate") == {"value": 42}
    stats = result_cache.cache_stats()
    assert stats["total_hits"] == 1
    assert stats["total_misses"] == 1
    assert stats["hit_rate"] == 0.5
    assert stats["by_tool"]["aggregate"] == {"hits": 1, "misses": 1, "hit_rate": 0.5}


def test_cache_unknown_tool_bucket(tmp_path):
    dataset_dir = tmp_path / "ds"
    dataset_dir.mkdir()
    result_cache.get(dataset_dir, "missing")
    assert "<unknown>" in result_cache.cache_stats()["by_tool"]


# --------------------------------------------------------------------------- #
# analyze_perf tool                                                           #
# --------------------------------------------------------------------------- #
def test_session_window_ranks_by_p95():
    for ms in [100, 110, 120]:
        perf.record("slow_tool", ms)
    for ms in [1, 2, 3]:
        perf.record("fast_tool", ms)
    out = analyze_perf()
    assert out["window"] == "session"
    assert out["persisted_meta"]["source"] == "in_memory_only"
    assert out["slowest_by_p95"][0]["tool"] == "slow_tool"
    assert {"slow_tool", "fast_tool"} <= set(out["in_memory_session"])


def test_tool_filter():
    perf.record("a", 10)
    perf.record("b", 20)
    out = analyze_perf(tool="a")
    assert set(out["in_memory_session"]) == {"a"}


def test_invalid_window_errors():
    out = analyze_perf(window="bogus")
    assert "error" in out


def test_window_without_telemetry_notes_disabled(tmp_path, monkeypatch):
    monkeypatch.delenv("JDATAMUNCH_PERF_TELEMETRY", raising=False)
    out = analyze_perf(window="24h", storage_path=str(tmp_path))
    assert out["persisted_meta"]["rows"] == 0
    assert "note" in out["persisted_meta"]


def test_cache_section_reflects_counters(tmp_path):
    dataset_dir = tmp_path / "ds"
    dataset_dir.mkdir()
    result_cache.get(dataset_dir, "k", tool="get_correlations")  # miss
    out = analyze_perf()
    assert out["cache"]["totals"]["misses"] == 1
    assert out["cache"]["coldest_by_tool"][0]["tool"] == "get_correlations"


# --------------------------------------------------------------------------- #
# persistent sink (opt-in)                                                    #
# --------------------------------------------------------------------------- #
def test_persistent_sink_roundtrip(tmp_path, monkeypatch):
    monkeypatch.setenv("JDATAMUNCH_PERF_TELEMETRY", "1")
    perf.record("describe_dataset", 12.0, storage_path=str(tmp_path))
    perf.record("describe_dataset", 8.0, ok=False, storage_path=str(tmp_path))
    rows = perf.perf_db_query(storage_path=str(tmp_path))
    assert len(rows) == 2
    out = analyze_perf(window="all", storage_path=str(tmp_path))
    assert out["persisted_meta"]["rows"] == 2
    assert "describe_dataset" in out["persisted"]
    assert out["persisted"]["describe_dataset"]["errors"] == 1


def test_persistent_sink_off_by_default(tmp_path, monkeypatch):
    monkeypatch.delenv("JDATAMUNCH_PERF_TELEMETRY", raising=False)
    perf.record("x", 1.0, storage_path=str(tmp_path))
    assert not (tmp_path / "perf_telemetry.db").exists()


@pytest.mark.asyncio
async def test_registered_and_dispatches(tmp_path, monkeypatch):
    from jdatamunch_mcp.server import call_tool, list_tools

    names = [t.name for t in await list_tools()]
    assert "analyze_perf" in names

    # call_tool takes the storage root from DATA_INDEX_PATH, not from the
    # arguments dict -- passing storage_path here reads the real home index.
    monkeypatch.setenv("DATA_INDEX_PATH", str(tmp_path))
    res = await call_tool("analyze_perf", {})
    payload = json.loads(res[0].text)
    assert payload["window"] == "session"
    # The dispatch itself records a latency sample for analyze_perf.
    assert "analyze_perf" in perf.latency_stats()
