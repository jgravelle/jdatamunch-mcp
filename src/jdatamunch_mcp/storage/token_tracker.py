"""Persistent token savings tracker for jdatamunch-mcp.

Records cumulative tokens saved across all tool calls by comparing
raw file sizes against actual MCP response sizes.

Stored in ~/.data-index/_savings.json
"""

import json
import os
import threading
import uuid
from pathlib import Path
from typing import Optional

_SAVINGS_FILE = "_savings.json"
_BYTES_PER_TOKEN = 4
_TELEMETRY_URL = "https://j.gravelle.us/APIs/savings/post.php"
_SAVINGS_LOCK = threading.Lock()

# Input-token prices, USD per token. Verified 2026-09-01 against
# https://platform.claude.com/docs/en/about-claude/pricing (Base Input Tokens).
# The retired Opus 4.0/4.1 were $15.00/1M; current Opus (5/4.8/4.7/4.6) is $5.00/1M.
# A key that names a FAMILY inherits whichever member's price someone last looked
# at, so each line names exactly ONE model. Pinned by tests/test_pricing.py.
PRICING = {
    "claude_opus":   5.00 / 1_000_000,  # Claude Opus 5 — $5.00 / 1M input tokens
    "claude_sonnet": 2.00 / 1_000_000,  # Claude Sonnet 5 — $2.00 / 1M input (superseded 4.6 was $3)
    "claude_haiku":  1.00 / 1_000_000,  # Claude Haiku 4.5 — $1.00 / 1M input tokens
    "gpt5_latest":  10.00 / 1_000_000,  # GPT-5 flagship — $10.00 / 1M input tokens
}


def _savings_path(base_path: Optional[str] = None) -> Path:
    root = Path(base_path) if base_path else Path.home() / ".data-index"
    root.mkdir(parents=True, exist_ok=True)
    return root / _SAVINGS_FILE


def _get_or_create_anon_id(data: dict) -> str:
    if "anon_id" not in data:
        data["anon_id"] = str(uuid.uuid4())
    return data["anon_id"]


def _share_savings(delta: int, anon_id: str) -> None:
    def _post() -> None:
        try:
            import httpx
            httpx.post(
                _TELEMETRY_URL,
                json={"delta": delta, "anon_id": anon_id, "source": "jdatamunch-mcp"},
                timeout=3.0,
            )
        except Exception:
            pass

    threading.Thread(target=_post, daemon=True).start()


def record_savings(
    tokens_saved: int,
    base_path: Optional[str] = None,
    tool: Optional[str] = None,
) -> int:
    """Add tokens_saved to the running total (and to the per-tool breakdown).

    Returns the new cumulative total.

    `tool` (C5): when provided, also increments
    `data["per_tool"][tool] = {"tokens_saved": int, "calls": int}` so callers
    can see which tool contributes most to the savings number.
    """
    path = _savings_path(base_path)
    with _SAVINGS_LOCK:
        try:
            data = json.loads(path.read_text(encoding="utf-8", errors="replace")) if path.exists() else {}
        except Exception:
            data = {}

        delta = max(0, tokens_saved)
        total = data.get("total_tokens_saved", 0) + delta
        data["total_tokens_saved"] = total

        if tool:
            per_tool = data.setdefault("per_tool", {})
            entry = per_tool.setdefault(tool, {"tokens_saved": 0, "calls": 0})
            entry["tokens_saved"] += delta
            entry["calls"] += 1

        if delta > 0 and os.environ.get("JDATAMUNCH_SHARE_SAVINGS", "1") != "0":
            anon_id = _get_or_create_anon_id(data)
            _share_savings(delta, anon_id)

        try:
            path.write_text(json.dumps(data), encoding="utf-8")
        except Exception:
            pass

    return total


def get_per_tool_savings(base_path: Optional[str] = None) -> dict:
    """Return the per-tool breakdown (C5)."""
    path = _savings_path(base_path)
    try:
        return json.loads(path.read_text(encoding="utf-8", errors="replace")).get("per_tool", {})
    except Exception:
        return {}


def get_total_saved(base_path: Optional[str] = None) -> int:
    """Return the current cumulative total without modifying it."""
    path = _savings_path(base_path)
    try:
        return json.loads(path.read_text(encoding="utf-8", errors="replace")).get("total_tokens_saved", 0)
    except Exception:
        return 0


# ---------------------------------------------------------------------------
# v1.21.0 — advisory session token budget (suite parity with jcm v1.108.146)
#
# Counts response tokens SERVED (the context this server injects into the
# agent) against an advisory ceiling. Never blocks or truncates — awareness
# only. Wire shape: _meta.budget = {limit, spent, state} with state
# ok / approaching (>=80%) / over (>=100%).
# ---------------------------------------------------------------------------

_SESSION_RESPONSE_LOCK = threading.Lock()
_SESSION_RESPONSE_TOKENS = 0
_BUDGET_APPROACHING_PCT = 0.8


def record_response_text(text: str) -> int:
    """Count a serialized tool response toward the session budget.

    Returns the cumulative session response tokens (bytes/4 estimate,
    same scale as the savings meter).
    """
    global _SESSION_RESPONSE_TOKENS
    try:
        tokens = len(text.encode("utf-8")) // _BYTES_PER_TOKEN
    except Exception:
        tokens = len(text) // _BYTES_PER_TOKEN
    with _SESSION_RESPONSE_LOCK:
        _SESSION_RESPONSE_TOKENS += max(0, tokens)
        return _SESSION_RESPONSE_TOKENS


def get_session_response_tokens() -> int:
    """Cumulative response tokens served this session (process lifetime)."""
    with _SESSION_RESPONSE_LOCK:
        return _SESSION_RESPONSE_TOKENS


def budget_status() -> Optional[dict]:
    """Session budget snapshot ({limit, spent, state}) or None when unset.

    Configured via ``JDATAMUNCH_SESSION_TOKEN_BUDGET`` (int response tokens;
    unset/0/garbage = disabled).
    """
    raw = os.environ.get("JDATAMUNCH_SESSION_TOKEN_BUDGET", "")
    try:
        limit = int(raw.strip())
    except (ValueError, AttributeError):
        return None
    if limit <= 0:
        return None
    spent = get_session_response_tokens()
    if spent >= limit:
        state = "over"
    elif spent >= limit * _BUDGET_APPROACHING_PCT:
        state = "approaching"
    else:
        state = "ok"
    return {"limit": limit, "spent": spent, "state": state}


def reset_session_response_tokens() -> None:
    """Test hook — clear the session response-token counter."""
    global _SESSION_RESPONSE_TOKENS
    with _SESSION_RESPONSE_LOCK:
        _SESSION_RESPONSE_TOKENS = 0


def estimate_savings(raw_bytes: int, response_bytes: int) -> int:
    """Estimate tokens saved: (raw - response) / bytes_per_token."""
    return max(0, (raw_bytes - response_bytes) // _BYTES_PER_TOKEN)


def cost_avoided(tokens_saved: int, total_tokens_saved: int) -> dict:
    """Return cost avoided estimates for this call and the running total."""
    return {
        "cost_avoided": {
            model: round(tokens_saved * rate, 4)
            for model, rate in PRICING.items()
        },
        "total_cost_avoided": {
            model: round(total_tokens_saved * rate, 4)
            for model, rate in PRICING.items()
        },
    }
