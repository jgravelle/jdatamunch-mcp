"""The time basis stamped on every published schema-token figure.

⚠⚠ A bare count of "tokens avoided" carries no time basis, and a reader
supplies the wrong one: PER REQUEST. The tool-schema block is stable across
requests, so it is paid at full rate roughly ONCE per cache lifetime and at
cache-read rates thereafter. jcodemunch-mcp's `benchmarks/codex_surface/`
measured 86% of baseline input cached (1,938,176 of 2,247,575 tokens) and
states in its own words that any framing of "N tokens in every request" is
wrong — *and that the repository said exactly that before measuring*. Read as a
per-request saving, the figure overstates the dollar impact by roughly an order
of magnitude, in the direction that flatters us.

⚠ The COUNT is not discounted, deliberately. It answers a real question — how
much payload the tool surface carries — and a silently scaled one answers
neither that nor the cost question. Same rule as `analyze_perf`'s raw
`hit_rate`, which is kept beside its basis rather than replaced.

⚠⚠ These constants live here and are imported, never inlined at the call site.
A second copy that agrees today is exactly what makes a later divergence
invisible; `tests/test_schema_token_basis.py` fails if the literal appears in a
second module.

Suite parity with jcodemunch-mcp v1.108.312 (`tier_switch_cost.py`). ⚠ jcm's
other 2026-08-30 release, 1.108.311, refuses a mid-session tool-tier switch
that cannot repay the prompt cache it invalidates; that machinery is
deliberately absent here, because this server resolves
`JDATAMUNCH_TOOL_PROFILE` once at startup and never re-publishes its tool list.
⚠ jdocmunch-mcp still shipped the field unbased as of 2026-08-30.
"""

SCHEMA_TOKENS_BASIS = "one_time_at_full_rate_then_cache_read"

SCHEMA_TOKENS_BASIS_NOTE = (
    "The tool-schema block is stable across requests, so it is paid at full "
    "rate approximately once per cache lifetime and at cache-read rates "
    "(~0.1x) thereafter. This count is payload size, NOT a per-request saving; "
    "reading it as one overstates the cost impact by roughly an order of "
    "magnitude. Measured: jcodemunch-mcp benchmarks/codex_surface/ "
    "(86% of baseline input cached)."
)
