#!/usr/bin/env python3
"""Measure what each `JDATAMUNCH_TOOL_PROFILE` tier actually costs in schema tokens.

Usage:
    python benchmarks/harness/run_tier_surface.py
    python benchmarks/harness/run_tier_surface.py --json-out benchmarks/tier_surface.json

Why this exists
---------------
`JDATAMUNCH_TOOL_PROFILE` offers three tiers (`core` / `standard` / `full`) as a
token lever, and until 2026-08-31 nobody had measured what any of them moves. A
setting that implies a saving it does not deliver is the same defect class as a
token count published with no time basis.

Methodology
-----------
⚠⚠ Weighs what a client **actually receives**, not the raw catalog filtered by
the tier bundle. Those differ: `_ALWAYS_PRESENT_TOOLS` is force-included in
every tier, and `JDATAMUNCH_DISABLED_TOOLS` subtracts from it. jcodemunch's
first attempt at this measurement filtered the catalog by hand and was wrong by
three tools in every tier. This goes through `server._filter_tools`, the same
function `list_tools` calls.

⚠⚠ Imports `server._schema_weight` rather than reimplementing it. Two weighers
that agree today are what make a later divergence invisible — the benchmark
would then price a surface the server does not publish.
`tests/test_schema_token_basis.py` fails if a second one appears.

Estimator: bytes/4 over the compact {name, description, inputSchema} JSON, the
same scale the shipped `tool_surface` receipt uses. It is an ESTIMATE, not a
tokenizer count — `run_benchmark.py` uses real tiktoken counts because it
compares against a raw file; here the point is the ratio between tiers, which
the estimator preserves.

⚠ The token figures below are payload size. They are NOT a per-request saving:
the schema block is stable, so it is paid at full rate roughly once per cache
lifetime and at cache-read rates thereafter. See
`src/jdatamunch_mcp/schema_token_basis.py`.
"""

import argparse
import json
import os
import sys
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(_REPO_ROOT / "src"))

from jdatamunch_mcp import server as server_mod  # noqa: E402
from jdatamunch_mcp.schema_token_basis import (  # noqa: E402
    SCHEMA_TOKENS_BASIS,
    SCHEMA_TOKENS_BASIS_NOTE,
)

TIERS = ("core", "standard", "full")


def measure_tier(profile: str) -> dict:
    """Weigh the surface a client is sent under `profile`.

    Reads the weights LIVE from the functions that build the published tool
    list, so this cannot drift from the surface it measures.
    """
    previous = os.environ.get("JDATAMUNCH_TOOL_PROFILE")
    os.environ["JDATAMUNCH_TOOL_PROFILE"] = profile
    try:
        catalog_tools = server_mod._all_tools()
        visible_tools = server_mod._filter_tools(catalog_tools)
        visible = {t.name: server_mod._schema_weight(t) for t in visible_tools}
        catalog = {t.name: server_mod._schema_weight(t) for t in catalog_tools}
        dropped = sorted(set(catalog) - set(visible))
        return {
            "profile": profile,
            "visible_tools": len(visible),
            "catalog_tools": len(catalog),
            "tools_dropped": len(dropped),
            "schema_tokens_visible": sum(visible.values()),
            "schema_tokens_catalog": sum(catalog.values()),
            "schema_tokens_avoided": max(
                0, sum(catalog.values()) - sum(visible.values())
            ),
            "dropped_tool_names": dropped,
        }
    finally:
        if previous is None:
            os.environ.pop("JDATAMUNCH_TOOL_PROFILE", None)
        else:
            os.environ["JDATAMUNCH_TOOL_PROFILE"] = previous


def run() -> dict:
    tiers = [measure_tier(p) for p in TIERS]
    full = next(t for t in tiers if t["profile"] == "full")
    for tier in tiers:
        base = full["schema_tokens_visible"]
        tier["payload_share_of_full_pct"] = round(
            100.0 * tier["schema_tokens_visible"] / base, 1
        )
        tier["payload_avoided_vs_full_pct"] = round(
            100.0 * tier["schema_tokens_avoided"] / base, 1
        )
    return {
        "estimator": "bytes/4",
        "weigher": "jdatamunch_mcp.server._schema_weight",
        "surface_builder": "jdatamunch_mcp.server._filter_tools",
        "always_present_tools": sorted(server_mod._ALWAYS_PRESENT_TOOLS),
        "schema_tokens_basis": SCHEMA_TOKENS_BASIS,
        "schema_tokens_basis_note": SCHEMA_TOKENS_BASIS_NOTE,
        "tiers": tiers,
    }


def main() -> None:
    parser = argparse.ArgumentParser(
        prog="run_tier_surface.py",
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "--json-out",
        default=None,
        help="write the full result JSON to this path",
    )
    args = parser.parse_args()

    results = run()

    print(f"{'tier':<10}{'tools':>7}{'dropped':>9}{'tokens':>9}{'% of full':>11}")
    for tier in results["tiers"]:
        print(
            f"{tier['profile']:<10}"
            f"{tier['visible_tools']:>7}"
            f"{tier['tools_dropped']:>9}"
            f"{tier['schema_tokens_visible']:>9}"
            f"{tier['payload_share_of_full_pct']:>10.1f}%"
        )
    print()
    print(f"basis: {results['schema_tokens_basis']}")
    print(results["schema_tokens_basis_note"])

    if args.json_out:
        out = Path(args.json_out)
        out.write_text(json.dumps(results, indent=2), encoding="utf-8")
        print(f"\nwrote {out}")


if __name__ == "__main__":
    main()
