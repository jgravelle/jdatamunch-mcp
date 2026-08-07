"""A truncated response must say so, under the DEFAULT config.

`enforce_budget` trims rows/columns to fit the token budget and records what it
dropped in `_meta.truncation`. This server strips `_meta` entirely by default
(`get_meta_fields()` returns `[]`), so that notice was deleted before any caller
saw it: measured, 600 rows trimmed to 104 with no surviving indication that 496
were gone.

A silently shortened answer is worse than a refused one, because the caller
cannot tell the difference. Same trap that forced the top-level `empty`/`hint`
keys in v1.28.0 and the post-filter re-attach of the absence ref in v1.26.0 --
a token the default config deletes is a token the agent never reads.
"""

import json

import pytest

NEWLINE = chr(10)

from jdatamunch_mcp.budget import enforce_budget
from jdatamunch_mcp.config import get_meta_fields


def _fat_rows(n=600, width=400):
    return {"result": {"rows": [{"c": "x" * width} for _ in range(n)]}}


class TestBudgetStillTruncates:
    """Control: the underlying behaviour is unchanged."""

    def test_oversized_result_is_trimmed(self):
        out = enforce_budget(_fat_rows(), "get_rows")
        assert len(out["result"]["rows"]) < 600

    def test_small_result_is_untouched(self):
        small = {"result": {"rows": [{"c": "x"}]}}
        out = enforce_budget(small, "get_rows")
        assert len(out["result"]["rows"]) == 1
        assert "truncation" not in (out.get("_meta") or {})

    def test_truncation_is_recorded_in_meta(self):
        out = enforce_budget(_fat_rows(), "get_rows")
        assert (out.get("_meta") or {}).get("truncation")


class TestDefaultConfigStripsMeta:
    """The precondition that made this invisible."""

    def test_meta_is_stripped_by_default(self, monkeypatch):
        monkeypatch.delenv("JDATAMUNCH_META_FIELDS", raising=False)
        assert get_meta_fields() == [], (
            "if this ever stops being [], re-check whether the top-level "
            "re-attach below is still necessary"
        )


@pytest.mark.asyncio
class TestDisclosureSurvivesTheDefault:
    """End to end on a real indexed dataset. The unit assertions below pin the
    mechanism; this pins what a caller actually receives."""

    @pytest.fixture
    def wide_csv(self, tmp_path):
        from jdatamunch_mcp.tools.index_local import index_local
        csv = tmp_path / "wide.csv"
        cell = "x" * 400
        lines = ["id,payload"] + [f"{i},{cell}" for i in range(600)]
        csv.write_text(NEWLINE.join(lines) + NEWLINE, encoding="utf-8")
        store = tmp_path / "store"
        out = index_local(
            path=str(csv), name="trunc_fixture",
            storage_path=str(store), use_ai_summaries=False,
        )
        inner = out.get("result") or out
        assert inner.get("dataset"), out
        return {"storage": str(store), "dataset": inner["dataset"]}

    async def test_truncated_response_says_so_top_level(self, wide_csv, monkeypatch):
        monkeypatch.delenv("JDATAMUNCH_META_FIELDS", raising=False)
        monkeypatch.setenv("DATA_INDEX_PATH", wide_csv["storage"])
        from jdatamunch_mcp.server import call_tool

        out = await call_tool("get_rows", {"dataset": wide_csv["dataset"], "limit": 500})
        body = json.loads(out[0].text)
        if "error" in body:
            pytest.fail(f"fixture did not index cleanly: {body['error']}")

        rows = (body.get("result") or body).get("rows") or []
        assert len(rows) < 500, "budget did not truncate; fixture is not large enough"
        assert body.get("truncated"), (
            "response was shortened and said nothing under the DEFAULT config"
        )
        assert "NOT complete" in body.get("truncated_note", "")

    async def test_untruncated_response_carries_no_notice(self, wide_csv, monkeypatch):
        """Omit-when-empty control: a complete answer must not claim truncation."""
        monkeypatch.delenv("JDATAMUNCH_META_FIELDS", raising=False)
        monkeypatch.setenv("DATA_INDEX_PATH", wide_csv["storage"])
        from jdatamunch_mcp.server import call_tool

        out = await call_tool("get_rows", {"dataset": wide_csv["dataset"], "limit": 2})
        body = json.loads(out[0].text)
        assert "truncated" not in body, body.get("truncated")


class TestReattachMechanism:
    """Unit-level: the server captures the record before filtering deletes it."""

    def test_server_captures_truncation_before_filtering(self):
        import inspect
        from jdatamunch_mcp import server as S
        src = inspect.getsource(S.call_tool)
        assert '_truncation = (result.get("_meta") or {}).get("truncation")' in src, (
            "the capture must happen while _meta still exists"
        )
        assert src.index("_truncation = (result") < src.index("meta_fields = get_meta_fields()"), (
            "capture must precede the filtering that deletes _meta"
        )

    def test_server_reattaches_top_level_after_filtering(self):
        import inspect
        from jdatamunch_mcp import server as S
        src = inspect.getsource(S.call_tool)
        assert 'result["truncated"] = _truncation' in src
        assert src.index("meta_fields = get_meta_fields()") < src.index('result["truncated"]'), (
            "re-attach must follow the filtering, or it is deleted again"
        )

    def test_note_names_the_knob(self):
        import inspect
        from jdatamunch_mcp import server as S
        src = inspect.getsource(S.call_tool)
        assert "JDATAMUNCH_MAX_RESPONSE_TOKENS" in src, (
            "the disclosure must name what moves the budget"
        )

    def test_note_states_it_is_not_complete(self):
        import inspect
        from jdatamunch_mcp import server as S
        assert "NOT complete" in inspect.getsource(S.call_tool)
