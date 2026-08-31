"""The published schema-token counts carry their basis, and the tier ladder is real.

Suite parity with jcodemunch-mcp v1.108.312's `tests/test_tier_switch_cost.py`
basis bindings. ⚠ jcm's OTHER 2026-08-30 release (1.108.311, refusing a
mid-session tier switch that cannot repay the prompt cache it invalidates) is
deliberately NOT ported: this server resolves `JDATAMUNCH_TOOL_PROFILE` once at
startup and has no runtime switch, so there is no invalidation to price. The
fourth test below is the ratchet that keeps that true.
"""

import ast
import re
from pathlib import Path

from jdatamunch_mcp import server as server_mod

_SRC = Path(server_mod.__file__).resolve().parent
_SERVER_PY = Path(server_mod.__file__).resolve()


# --------------------------------------------------------------------------- #
# 1. The published counts carry their basis
# --------------------------------------------------------------------------- #

def test_every_schema_token_figure_ships_with_its_basis():
    """⚠⚠ A bare `schema_tokens_avoided` has no TIME basis, and a reader
    supplies the wrong one: per request. The tool-schema block is stable, so it
    is paid at full rate roughly once and at cache-read rates thereafter —
    jcm's `benchmarks/codex_surface/` measured 86% of baseline input cached.
    Read as a per-request saving, the count overstates the dollar impact by
    roughly an order of magnitude, in the direction that flatters us.

    ⚠ Asserted as CO-PRESENCE, not as a string: any consumer reading a count
    also receives the basis. The wording is free to improve.
    """
    from jdatamunch_mcp.schema_token_basis import SCHEMA_TOKENS_BASIS

    stats = server_mod._tool_surface_stats(top_n=3)
    counts = [k for k in stats if k.startswith("schema_tokens_") and "basis" not in k]
    assert counts, "no schema token figures found — this test asserts nothing"
    assert stats["schema_tokens_basis"] == SCHEMA_TOKENS_BASIS
    note = stats["schema_tokens_basis_note"].lower()
    assert "cache" in note
    assert "not a per-request saving" in note


def test_the_count_is_not_silently_discounted():
    """⚠⚠ The fix is a LABEL, never a scaled number. A count quietly multiplied
    by the cache-read rate answers neither the payload question nor the cost
    question, and nothing on the wire would show it had happened."""
    stats = server_mod._tool_surface_stats(top_n=3)
    visible = sum(
        server_mod._schema_weight(t)
        for t in server_mod._filter_tools(server_mod._all_tools())
    )
    catalog = sum(server_mod._schema_weight(t) for t in server_mod._all_tools())
    assert stats["schema_tokens_visible"] == visible
    assert stats["schema_tokens_catalog"] == catalog
    assert stats["schema_tokens_avoided"] == max(0, catalog - visible)


def test_the_basis_constants_live_in_one_module():
    """⚠⚠ jcm deliberately did not inline these strings at the call site: a
    second copy that agrees today is exactly what makes a later divergence
    invisible. The literal must appear in the constants module and nowhere
    else in `src/`."""
    literals = []
    for path in _SRC.rglob("*.py"):
        text = path.read_text(encoding="utf-8")
        if '"one_time_at_full_rate_then_cache_read"' in text:
            literals.append(path.name)
    assert literals == ["schema_token_basis.py"], (
        f"the basis literal is defined in {literals}; it belongs in exactly one "
        "module, imported everywhere else"
    )


# --------------------------------------------------------------------------- #
# 2. There is exactly one schema weigher
# --------------------------------------------------------------------------- #

def test_there_is_exactly_one_schema_weigher():
    """Two weighers that agree today are what make a later divergence
    invisible — the benchmark would then price a surface the server does not
    publish. jcm's first tier measurement was wrong by three tools in every
    tier for the neighbouring reason."""
    tree = ast.parse(_SERVER_PY.read_text(encoding="utf-8"))
    weighers = [
        node.name
        for node in ast.walk(tree)
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
        and re.fullmatch(r"_?(schema_)?weight(_tool|_schema)?", node.name)
    ]
    assert weighers == ["_schema_weight"], (
        f"expected exactly one module-level schema weigher, found {weighers}"
    )


# --------------------------------------------------------------------------- #
# 3. The tier ladder is real (the control for everything above)
# --------------------------------------------------------------------------- #

def _tier_weight(monkeypatch, profile: str) -> tuple[int, int]:
    """Weigh what a client actually RECEIVES under `profile`.

    ⚠⚠ Not the raw catalog filtered by the tier bundle — that ignores
    `_ALWAYS_PRESENT_TOOLS` and prices a surface no client is ever sent.
    Goes through `_filter_tools`, the same path `list_tools` uses.
    """
    monkeypatch.setenv("JDATAMUNCH_TOOL_PROFILE", profile)
    stats = server_mod._tool_surface_stats(top_n=1)
    return stats["visible_tools"], stats["schema_tokens_visible"]


def test_tier_weights_are_distinct_and_ordered(monkeypatch):
    """Without this, every other assertion here is satisfied by a weigher that
    returns the same number for every tier."""
    core_n, core_t = _tier_weight(monkeypatch, "core")
    std_n, std_t = _tier_weight(monkeypatch, "standard")
    full_n, full_t = _tier_weight(monkeypatch, "full")

    assert core_n < std_n < full_n, (core_n, std_n, full_n)
    assert core_t < std_t < full_t, (core_t, std_t, full_t)


def test_always_present_tools_survive_every_tier(monkeypatch):
    """A tier that drops the guide tool is a tier that cannot tell an agent how
    to use what is left."""
    for profile in ("core", "standard", "full"):
        monkeypatch.setenv("JDATAMUNCH_TOOL_PROFILE", profile)
        names = {t.name for t in server_mod._filter_tools(server_mod._all_tools())}
        assert server_mod._ALWAYS_PRESENT_TOOLS <= names, profile


# --------------------------------------------------------------------------- #
# 4. The absence stays absent
# --------------------------------------------------------------------------- #

_TIER_CHANGE_SIGNALS = (
    "send_tool_list_changed",
    "notifications/tools/list_changed",
    "tool_list_changed",
)


def _unpriced_tier_switch_sites(root: Path) -> list[str]:
    """Files under `root` that invalidate the published tool list without a
    switch-cost helper beside them. Takes the root as an argument so the
    non-vacuity test can point it at a tree that really does offend."""
    offenders = []
    for path in root.rglob("*.py"):
        text = path.read_text(encoding="utf-8")
        if not any(sig in text for sig in _TIER_CHANGE_SIGNALS):
            continue
        if "breakeven_requests" in text or "tier_switch_cost" in text:
            continue
        offenders.append(path.relative_to(root).as_posix())
    return offenders


def test_no_runtime_tier_switch_without_a_pricing_helper():
    """⚠⚠ jcm 1.108.311 exists because a mid-session tier switch invalidates
    the whole prompt cache and can cost far more than the schema tokens it
    saves. This server has no runtime switch, so it has no such defect — and
    that is a property of the tree, not a law. If someone adds the
    notification, they must add the pricing beside it.

    Cheap now; it is the only thing between a future contributor and jcm's
    exact defect.
    """
    offenders = _unpriced_tier_switch_sites(_SRC)
    assert not offenders, (
        "runtime tool-list invalidation appears in "
        f"{offenders} with no switch-cost helper beside it. A mid-session tier "
        "switch throws away the prompt cache; price it before shipping it "
        "(see jcodemunch-mcp v1.108.311)."
    )


def test_the_tier_switch_ratchet_can_actually_fire(tmp_path):
    """Non-vacuity: a guard that has only ever seen a clean tree is not a
    guard. ⚠ Also pins that a priced site is EXEMPT — a ratchet with false
    positives is one nobody believes."""
    (tmp_path / "leak.py").write_text(
        "async def broadcast(session):\n"
        "    await session.send_tool_list_changed()\n",
        encoding="utf-8",
    )
    assert _unpriced_tier_switch_sites(tmp_path) == ["leak.py"]

    (tmp_path / "leak.py").write_text(
        "from .tier_switch_cost import breakeven_requests\n\n"
        "async def broadcast(session):\n"
        "    if breakeven_requests() < 100:\n"
        "        await session.send_tool_list_changed()\n",
        encoding="utf-8",
    )
    assert _unpriced_tier_switch_sites(tmp_path) == []


# --------------------------------------------------------------------------- #
# 5. The tier benchmark measures the surface the server actually publishes
# --------------------------------------------------------------------------- #

def test_the_tier_harness_agrees_with_the_shipped_receipt(monkeypatch):
    """⚠⚠ The harness must price what a client RECEIVES, not the raw catalog
    filtered by the tier bundle — jcm's first attempt did the latter and was
    wrong by three tools in every tier, because it dropped force-included
    tools. Bound as agreement with `_tool_surface_stats`, so the two cannot
    diverge without a red test.

    ⚠ Counts are NOT pinned here. Pinning them would turn every legitimate tool
    addition into a failure, and the artifact is regenerable for that reason.
    """
    import importlib.util

    harness_path = (
        Path(server_mod.__file__).resolve().parents[2]
        / "benchmarks" / "harness" / "run_tier_surface.py"
    )
    spec = importlib.util.spec_from_file_location("_tier_harness", harness_path)
    harness = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(harness)

    for profile in harness.TIERS:
        measured = harness.measure_tier(profile)
        monkeypatch.setenv("JDATAMUNCH_TOOL_PROFILE", profile)
        shipped = server_mod._tool_surface_stats(top_n=1)
        assert measured["visible_tools"] == shipped["visible_tools"], profile
        assert (
            measured["schema_tokens_visible"] == shipped["schema_tokens_visible"]
        ), profile
        assert (
            measured["schema_tokens_avoided"] == shipped["schema_tokens_avoided"]
        ), profile
