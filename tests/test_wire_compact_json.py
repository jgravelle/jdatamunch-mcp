"""call_tool sends compact JSON.

indent=2 made describe_dataset's full view 6,878 tokens instead of 4,750 on a
28-column file (cl100k), paid by the agent on every call.
"""

import json

import pytest


@pytest.mark.asyncio
async def test_call_tool_text_is_compact_json(monkeypatch, indexed_sample):
    monkeypatch.setenv("DATA_INDEX_PATH", indexed_sample)
    from jdatamunch_mcp.server import call_tool

    for args in ({"dataset": "sample"}, {"dataset": "sample", "detail": "full"}):
        out = await call_tool("describe_dataset", args)
        text = out[0].text
        parsed = json.loads(text)
        assert "result" in parsed
        assert text == json.dumps(parsed, separators=(",", ":"))
        assert "\n" not in text
