"""The honesty verdict must not be degraded by jData's own writes.

Two defects, both in the path that decides whether an absence claim is
trustworthy, and both invisible from a green suite because each produced a
verdict that merely *read* as cautious.

1. ``index_changed_since_load`` was sampled AFTER the scan. ``_semantic_scores``
   lazily embeds any column with no vector yet and persists it into the same
   ``data.sqlite`` the probe stats, so the first semantic search of every
   dataset reported the dataset as rewritten underneath itself — downgrading a
   provable ``absent`` to ``degraded``.

2. The note was keyed on the STATE alone, but two conditions produce
   ``degraded``. An index-rewritten degrade was served the semantic-unavailable
   text, naming a channel that was working and prescribing a fix — configure a
   provider — for a problem the caller did not have.

The first is a guard tripped by the work it guards. The second is a guard that
reports the wrong reason for firing. Neither fails loudly; both make the
product's headline claim ("treat this as strong evidence no such column is
present") quietly unavailable or quietly misattributed.
"""

import csv
from pathlib import Path

import pytest

from jdatamunch_mcp.verdict import (
    STATE_ABSENT,
    STATE_DEGRADED,
    STATE_OK,
    build_verdict,
)

#: Shares no token with the fixture's columns, so zero rows come back for
#: lexical reasons and the verdict turns on the index channel alone.
NO_MATCH_QUERY = "quaternion slerp interpolation"


@pytest.fixture
def fake_embedder(monkeypatch):
    """A configured, working provider — the state the defect needed to appear.

    Column vectors and the query vector are deliberately ORTHOGONAL, so every
    cosine is 0.0 and the search returns nothing. That matters: the degrade
    under test only fires at ``result_count == 0``, and a fixture whose vectors
    all matched would make the test vacuous while still looking meaningful.

    Nothing here is mocked in the search path itself. The lazy embed, the
    write to ``data.sqlite``, the mtime probe and the verdict all run for real.
    """
    import jdatamunch_mcp.embeddings as emb

    monkeypatch.setattr(emb, "detect_provider", lambda: ("fake", "fake-model"))
    monkeypatch.setattr(
        emb,
        "embed_texts",
        lambda texts, provider, model: [
            [1.0, 0.0, 0.0] if t.startswith("column:") else [0.0, 1.0, 0.0]
            for t in texts
        ],
    )


@pytest.fixture
def dataset(tmp_path):
    """A freshly indexed dataset with NO column embeddings yet.

    Freshness is the whole point: the defect fires on the first semantic
    search, when there is still something to lazily embed. A dataset reused
    across tests would have been embedded by the first one and every later
    assertion would pass against the defect.
    """
    from jdatamunch_mcp.tools.index_local import index_local

    csv_path = tmp_path / "widgets.csv"
    with open(csv_path, "w", newline="", encoding="utf-8") as fh:
        w = csv.writer(fh)
        w.writerow(["widget_id", "colour", "mass_kg"])
        for i in range(5):
            w.writerow([i, "red", i * 1.5])
    storage = str(tmp_path / ".idx")
    res = index_local(path=str(csv_path), name="widgets", storage_path=storage)
    assert "error" not in res, res
    return "widgets", storage


def _verdict(result: dict) -> dict:
    return result["_meta"]["verdict"]


# --- Defect 1: a guard tripped by the work it guards ----------------------- #


class TestProbeIsNotTrippedByOurOwnWrite:
    def test_the_first_semantic_search_still_proves_absence(
        self, dataset, fake_embedder
    ):
        """THE regression. This is the call the defect broke.

        Before the fix this returned ``degraded`` — "absence is NOT proven" —
        because lazily embedding three columns moved the mtime the probe read
        one line later.
        """
        from jdatamunch_mcp.tools.search_data import search_data

        ds, storage = dataset
        out = search_data(ds, NO_MATCH_QUERY, semantic_only=True, storage_path=storage)

        assert out.get("error") is None, out
        assert len(out.get("result") or []) == 0, "fixture must return zero rows"
        assert _verdict(out)["state"] == STATE_ABSENT
        assert "index" not in _verdict(out)["channels"]

    def test_first_and_second_search_agree(self, dataset, fake_embedder):
        """Same query, same data, run twice. The only thing that differs
        between the calls is whether jData had anything left to embed, and that
        must not be visible in the verdict."""
        from jdatamunch_mcp.tools.search_data import search_data

        ds, storage = dataset
        first = search_data(ds, NO_MATCH_QUERY, semantic_only=True, storage_path=storage)
        second = search_data(ds, NO_MATCH_QUERY, semantic_only=True, storage_path=storage)

        assert _verdict(first)["state"] == _verdict(second)["state"]
        assert _verdict(first)["channels"] == _verdict(second)["channels"]
        assert _verdict(first)["note"] == _verdict(second)["note"]

    def test_the_lazy_embed_really_did_write(self, dataset, fake_embedder):
        """Non-vacuity for the two tests above.

        If the first search stopped writing, they would pass for a reason that
        has nothing to do with the fix. This asserts the mtime the probe reads
        genuinely moves during the first search — the defect's precondition is
        still present, and the verdict survives it anyway.
        """
        from jdatamunch_mcp.storage.data_store import DataStore
        from jdatamunch_mcp.tools.search_data import search_data

        ds, storage = dataset
        db = DataStore(base_path=storage).sqlite_path(ds)

        def stamp() -> int:
            newest = 0
            for p in (db, Path(str(db) + "-wal")):
                try:
                    newest = max(newest, p.stat().st_mtime_ns)
                except OSError:
                    pass
            return newest

        before = stamp()
        search_data(ds, NO_MATCH_QUERY, semantic_only=True, storage_path=storage)
        assert stamp() != before, (
            "the lazy embed no longer writes, so the tests above no longer "
            "exercise the defect they were written for"
        )

    def test_the_probe_is_disclosed_not_silently_narrowed(
        self, dataset, fake_embedder
    ):
        """Sampling earlier trades away one thing — a rebuild that STARTS
        mid-scan — and the payload has to say so rather than leave a reader to
        assume the probe covers the whole call."""
        from jdatamunch_mcp.tools.search_data import search_data

        ds, storage = dataset
        out = search_data(ds, NO_MATCH_QUERY, semantic_only=True, storage_path=storage)
        assert "before the scan" in out["_meta"]["rewrite_probe"]

    def test_a_genuine_external_rewrite_still_degrades(self):
        """The guard must still fire for the case it exists for. A fix that
        made `index_changed` unreachable would pass every test above."""
        v = build_verdict(
            result_count=0,
            semantic_requested=True,
            semantic_available=True,
            index_changed=True,
        )
        assert v["state"] == STATE_DEGRADED
        assert v["channels"]["index"] == "rebuilding"


# --- Defect 2: a guard that reports the wrong reason for firing ------------ #


class TestDegradedNoteNamesItsActualCause:
    def test_an_index_rewrite_does_not_blame_the_embedding_channel(self):
        """The exact wrong output. The provider was configured, available and
        used; the note told the caller to go configure one."""
        v = build_verdict(
            result_count=0,
            semantic_requested=True,
            semantic_available=True,
            index_changed=True,
        )
        assert v["state"] == STATE_DEGRADED
        assert v["channels"]["semantic"] == "ok", "the channel was working"
        assert "Configure an embedding provider" not in v["note"]
        assert "embedding channel was unavailable" not in v["note"]
        assert "rewritten" in v["note"]

    def test_the_semantic_degrade_keeps_its_wording(self):
        """The other cause must be untouched. Callers parse this text, and a
        fix that improved one note by breaking the other is not a fix."""
        v = build_verdict(
            result_count=0, semantic_requested=True, semantic_available=False
        )
        assert v["state"] == STATE_DEGRADED
        assert v["channels"]["semantic"] == "unavailable"
        assert "embedding channel was unavailable" in v["note"]
        assert "Configure an embedding provider" in v["note"]

    def test_the_two_degrades_are_distinguishable(self):
        """Both are `degraded`, so the state cannot tell them apart and the
        note is the only thing that can. If these ever match again, the second
        defect is back regardless of what the state machine does."""
        semantic = build_verdict(
            result_count=0, semantic_requested=True, semantic_available=False
        )
        rewritten = build_verdict(
            result_count=0,
            semantic_requested=True,
            semantic_available=True,
            index_changed=True,
        )
        assert semantic["state"] == rewritten["state"] == STATE_DEGRADED
        assert semantic["note"] != rewritten["note"]

    def test_an_unrecognised_cause_falls_back_rather_than_lying(self):
        """A future third cause with no note of its own must be UNSPECIFIC,
        never wrong. Silence about the reason is a gap; the old behaviour
        asserted a reason that was false, which is worse."""
        from jdatamunch_mcp.verdict import _NOTES, _note_for

        assert _note_for(STATE_DEGRADED, "some_cause_added_later") == _NOTES[
            STATE_DEGRADED
        ]

    @pytest.mark.parametrize("state", [STATE_OK, STATE_ABSENT])
    def test_non_degraded_notes_are_unchanged(self, state):
        from jdatamunch_mcp.verdict import _NOTES, _note_for

        assert _note_for(state, "") == _NOTES[state]
        assert _note_for(state, "index_rewritten") == _NOTES[state], (
            "a cause tag must never leak into a non-degraded note"
        )
