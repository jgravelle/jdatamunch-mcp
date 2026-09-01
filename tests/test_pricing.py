"""Value pin for the PRICING table in storage/token_tracker.py.

The rates below are RESTATED as literals, sourced from the Base Input Tokens
column of https://platform.claude.com/docs/en/about-claude/pricing (read
2026-09-01). A pin that imports the value it checks asserts nothing, so nothing
here is derived from PRICING itself.

Backstory: claude_sonnet carried 3.00, the rate scheduled to take effect on
2026-09-01 for a model that never reached it — Anthropic cancelled the increase,
and $3 is the superseded Sonnet 4.6's rate. A constant written for a FUTURE date
is wrong for the whole interval before it and reads identically to a stale one,
so a green suite proved nothing here: this repo had no test naming PRICING at
all, in either direction.

gpt5_latest is pinned at its CURRENT value only. It is not an Anthropic model
and no source was consulted for it; the pin exists so the number cannot drift
unnoticed, not as a claim that 10.00 is correct.
"""

import pytest

from jdatamunch_mcp.storage.token_tracker import PRICING, cost_avoided

# USD per 1M input tokens, restated from the pricing page.
EXPECTED_PER_MTOK = {
    "claude_opus": 5.00,    # Claude Opus 5
    "claude_sonnet": 2.00,  # Claude Sonnet 5 (superseded 4.6 was 3.00)
    "claude_haiku": 1.00,   # Claude Haiku 4.5
    "gpt5_latest": 10.00,   # not verified against a source; current value only
}


def test_pricing_keys_exact():
    """The key set is the wire contract for cost_avoided — no adds, no renames."""
    assert set(PRICING) == set(EXPECTED_PER_MTOK)


@pytest.mark.parametrize("model,per_mtok", sorted(EXPECTED_PER_MTOK.items()))
def test_pricing_rate_matches_published_value(model, per_mtok):
    assert PRICING[model] == pytest.approx(per_mtok / 1_000_000, rel=1e-12)


def test_sonnet_is_not_the_cancelled_increase():
    """Guards the specific defect: 3.00 is Sonnet 4.6, never Sonnet 5."""
    assert PRICING["claude_sonnet"] != pytest.approx(3.00 / 1_000_000)


def test_cost_avoided_round_trip():
    """One million tokens costs exactly the per-MTok rate, through the real call."""
    out = cost_avoided(1_000_000, 2_000_000)
    for model, per_mtok in EXPECTED_PER_MTOK.items():
        assert out["cost_avoided"][model] == pytest.approx(per_mtok, abs=1e-4)
        assert out["total_cost_avoided"][model] == pytest.approx(2 * per_mtok, abs=1e-4)
