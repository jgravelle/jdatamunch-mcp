"""MCP server for jdatamunch-mcp."""

import argparse
import asyncio
import json
import os
import sys
import time
import traceback
from typing import Optional

from mcp.server import Server
from mcp.server.lowlevel.helper_types import ReadResourceContents
from mcp.types import Tool, ToolAnnotations, TextContent, Resource

from . import runtime_identity
from .tools.index_local import index_local
from .tools.list_datasets import list_datasets
from .tools.describe_dataset import describe_dataset
from .tools.describe_column import describe_column
from .tools.search_data import search_data
from .tools.get_rows import get_rows
from .tools.aggregate import aggregate
from .tools.sample_rows import sample_rows
from .tools.get_session_stats import get_session_stats
from .tools.get_schema_drift import get_schema_drift
from .tools.get_data_hotspots import get_data_hotspots
from .tools.summarize_dataset import summarize_dataset as summarize_dataset_tool
from .tools.index_repo import index_repo
from .tools.get_correlations import get_correlations
from .tools.join_datasets import join_datasets
from .tools.delete_dataset import delete_dataset
from .tools.embed_dataset import embed_dataset
from .tools.list_repos import list_repos
from .tools.validate_index import validate_index
from .runtime import ingest_sql_log_file
from .tools.find_unused_columns import find_unused_columns
from .tools.check_column_drop_safe import check_column_drop_safe
from .tools.get_schema_impact import get_schema_impact
from .tools.get_redaction_log import get_redaction_log
from .tools.data_health_radar import data_health_radar
from .tools.health_radar import diff_data_health_radar
from .tools.find_similar_columns import find_similar_columns
from .tools.get_dataset_history import get_dataset_history
from .tools.get_dataset_health import get_dataset_health
from .tools.suggest_keys import suggest_keys
from .tools.suggest_joins import suggest_joins
from .tools.get_distribution import get_distribution
from .tools.plan_query import plan_query
from .tools.run_sql import run_sql
from .tools.tune_weights import tune_weights
from .tools.check_embedding_drift import check_embedding_drift
from .tools.analyze_perf import analyze_perf
from . import perf as _perf
from .budget import enforce_budget
from .call_tracker import record_call

server = Server("jdatamunch-mcp")


# --------------------------------------------------------------------------- #
# Tool profiles: tiered sets for controlling context budget.                  #
# Mirrors jcodemunch-mcp / jdocmunch-mcp (issue #297).                        #
# Config via JDATAMUNCH_TOOL_PROFILE ("core" | "standard" | "full"; default   #
# "full"). Config via JDATAMUNCH_DISABLED_TOOLS (comma-separated tool names). #
# --------------------------------------------------------------------------- #
_TOOL_TIER_CORE: frozenset[str] = frozenset({
    # Indexing & discovery
    "index_local", "index_repo",
    "list_datasets", "list_repos",
    # Schema introspection
    "describe_dataset", "describe_column",
    # Row retrieval
    "search_data", "get_rows", "sample_rows",
    # Aggregation
    "aggregate",
})

_TOOL_TIER_STANDARD: frozenset[str] = _TOOL_TIER_CORE | frozenset({
    # Schema analysis
    "get_schema_drift", "get_schema_impact",
    "find_similar_columns", "find_unused_columns",
    "check_column_drop_safe",
    # Health & metrics
    "get_data_hotspots", "get_dataset_health",
    "data_health_radar", "diff_data_health_radar",
    "get_dataset_history", "get_correlations", "get_distribution",
    # Cross-dataset
    "join_datasets", "suggest_joins", "suggest_keys",
    # SQL
    "plan_query", "run_sql",
    # Ranking
    "tune_weights",
    # Embeddings
    "check_embedding_drift",
    # Diagnostics
    "analyze_perf",
    # Utilities
    "validate_index", "delete_dataset",
    "summarize_dataset", "embed_dataset",
    "finalize_handoff",
})

_PROFILE_TIERS: dict[str, frozenset[str] | None] = {
    "core": _TOOL_TIER_CORE,
    "standard": _TOOL_TIER_STANDARD,
    "full": None,
}

_ALWAYS_PRESENT_TOOLS: frozenset[str] = frozenset({"jdatamunch_guide"})
_UNDISABLEABLE_TOOLS: frozenset[str] = frozenset()


def _get_tool_profile() -> str:
    raw = os.environ.get("JDATAMUNCH_TOOL_PROFILE", "full").strip().lower()
    return raw if raw in _PROFILE_TIERS else "full"


def _get_disabled_tools() -> frozenset[str]:
    raw = os.environ.get("JDATAMUNCH_DISABLED_TOOLS", "").strip()
    if not raw:
        return frozenset()
    return frozenset(t.strip() for t in raw.split(",") if t.strip())


def _filter_tools(tools: list[Tool]) -> list[Tool]:
    profile = _get_tool_profile()
    allowed = _PROFILE_TIERS.get(profile)
    if allowed is not None:
        tools = [t for t in tools if t.name in allowed or t.name in _ALWAYS_PRESENT_TOOLS]
    disabled = _get_disabled_tools()
    if disabled:
        effective_disabled = disabled - _UNDISABLEABLE_TOOLS
        if effective_disabled:
            tools = [t for t in tools if t.name not in effective_disabled]
    return tools


def _tool_surface_stats(top_n: int = 15) -> dict:
    """Schema token weight of the visible tool surface vs the full catalog.

    Suite parity with jcodemunch-mcp v1.108.153 / jdocmunch-mcp v1.112.0.
    Estimated at the meter's bytes/4 scale over the {name, description,
    inputSchema} serialization. Advisory receipt only — never blocks, nothing
    persisted. jData has no Counter surface, so the block carries `profile`
    but no `surface` key.
    """
    import json as _json

    def _weight(tool: Tool) -> int:
        payload = _json.dumps(
            {
                "name": tool.name,
                "description": tool.description or "",
                "inputSchema": tool.inputSchema or {},
            },
            separators=(",", ":"),
            default=str,
        )
        return max(1, len(payload.encode("utf-8")) // 4)

    catalog_tools = _all_tools()
    visible = {t.name: _weight(t) for t in _filter_tools(catalog_tools)}
    catalog = {t.name: _weight(t) for t in catalog_tools}
    visible_total = sum(visible.values())
    catalog_total = sum(catalog.values())
    heaviest = dict(sorted(visible.items(), key=lambda kv: -kv[1])[:top_n])
    return {
        "profile": _get_tool_profile(),
        "visible_tools": len(visible),
        "catalog_tools": len(catalog),
        "schema_tokens_visible": visible_total,
        "schema_tokens_catalog": catalog_total,
        "schema_tokens_avoided": max(0, catalog_total - visible_total),
        "heaviest_tools": heaviest,
        "estimator": "bytes/4",
    }


# --- MCP read-only annotations (suite parity with jcodemunch PR #361) --------
# MCP clients that gate execution (Claude Code plan mode) prompt for approval on
# every tool they cannot prove is read-only. jData is read-only by charter apart
# from the handful of tools that index / mutate / delete a dataset, so annotate
# each tool with ToolAnnotations(readOnlyHint=...): query tools run silently,
# the write-set still prompts. Any tool that can mutate persistent state (an
# index, dataset, embedding store, tuning file, or drift canary) under ANY
# argument is non-read-only — biased conservative, since mislabeling a writer as
# read-only is the harmful direction.
_NON_READONLY_TOOLS: frozenset[str] = frozenset({
    "index_local",
    "index_repo",
    "summarize_dataset",
    "delete_dataset",
    "embed_dataset",
    "ingest_sql_log",
    "tune_weights",          # inspect reads; set/reset writes ranking_tuning.json
    "check_embedding_drift",  # reports by default; force=true re-pins the canary
    "finalize_handoff",      # persists a session handoff record (jdatamunch.handoff/v1)
})


def _apply_readonly_annotations(tools: list[Tool]) -> list[Tool]:
    """Attach ToolAnnotations(readOnlyHint=...) to any tool lacking annotations.

    Read tools (readOnlyHint=True) run silently in Claude Code plan mode; the
    write-set (_NON_READONLY_TOOLS) is marked False so those still prompt. Tools
    that already carry annotations are left untouched. Returns a new list; input
    Tool objects are copied (model_copy) rather than mutated.
    """
    annotated: list[Tool] = []
    for tool in tools:
        if tool.annotations is None:
            tool = tool.model_copy(
                update={
                    "annotations": ToolAnnotations(
                        readOnlyHint=tool.name not in _NON_READONLY_TOOLS
                    )
                }
            )
        annotated.append(tool)
    return annotated


@server.list_tools()
async def list_tools() -> list[Tool]:
    """List all available tools."""
    return _apply_readonly_annotations(_filter_tools(_all_tools()))


_DECLARED_ARG_KEYS: "Optional[dict]" = None


def _declared_arg_keys(name: str):
    """Declared inputSchema property names for a tool, or None if unknown.

    Built once from the same catalog `list_tools` publishes, so the argument
    contract can never drift from what the agent was shown. None (not an empty
    set) when the tool or its schema is missing: an absent declaration is not
    evidence that a caller's key is wrong.
    """
    global _DECLARED_ARG_KEYS
    if _DECLARED_ARG_KEYS is None:
        built = {}
        for t in _all_tools():
            props = (t.inputSchema or {}).get("properties")
            if isinstance(props, dict) and props:
                built[t.name] = frozenset(props)
        _DECLARED_ARG_KEYS = built
    return _DECLARED_ARG_KEYS.get(name)


def _all_tools() -> list[Tool]:
    """Return the unfiltered list of every tool exposed by this server."""
    return [
        Tool(
            name="index_local",
            description=(
                "Index a local data file (CSV, Excel, Parquet, or JSONL). Profiles all columns, "
                "detects types, computes statistics, and loads rows into SQLite for fast filtered "
                "retrieval. Set incremental=true (default) to skip re-indexing if file is unchanged."
            
                " CSV, Excel, Parquet and JSONL only; any other format is rejected."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "path": {
                        "type": "string",
                        "description": "Absolute path to data file (.csv, .tsv, .xlsx, .xls, .parquet, .jsonl, .ndjson)",
                    },
                    "name": {
                        "type": "string",
                        "description": "Dataset identifier override (defaults to filename stem)",
                    },
                    "incremental": {
                        "type": "boolean",
                        "description": "Skip re-index if file hash unchanged (default true)",
                        "default": True,
                    },
                    "encoding": {
                        "type": "string",
                        "description": "File encoding override (auto-detected if omitted)",
                    },
                    "delimiter": {
                        "type": "string",
                        "description": "CSV delimiter override (auto-detected if omitted)",
                    },
                    "header_row": {
                        "type": "integer",
                        "description": "Row number containing column headers, 0-indexed (default 0)",
                        "default": 0,
                    },
                    "sheet": {
                        "type": "string",
                        "description": "Excel sheet name to index (default: first sheet)",
                    },
                    "depth": {
                        "type": "string",
                        "enum": ["shallow", "standard", "deep"],
                        "description": "Profiling depth (B7). 'shallow' caps at 100k rows for fast first-look; 'standard' is the full profile (default); 'deep' additionally precomputes correlations.",
                        "default": "standard",
                    },
                },
                "required": ["path"],
            },
        ),
        Tool(
            name="index_repo",
            description=(
                "Index data files from a GitHub repository. Discovers CSV, Excel, Parquet, "
                "and JSONL files, downloads them, and indexes each via the same pipeline as "
                "index_local. Datasets are named {owner}--{repo}--{filename}. "
                "Max 50 MB per file, 20 files per repo. Set GITHUB_TOKEN env var for "
                "private repos or to avoid rate limits."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "url": {
                        "type": "string",
                        "description": "GitHub repo URL or owner/repo string (e.g. 'pandas-dev/pandas' or 'https://github.com/pandas-dev/pandas')",
                    },
                    "incremental": {
                        "type": "boolean",
                        "description": "Skip re-index if HEAD SHA unchanged (default true)",
                        "default": True,
                    },
                    "github_token": {
                        "type": "string",
                        "description": "GitHub token override (defaults to GITHUB_TOKEN env var)",
                    },
                },
                "required": ["url"],
            },
        ),
        Tool(
            name="list_datasets",
            description=(
                "List every indexed dataset with its row count, column count, and source file. Call it first to find the dataset name every other tool needs, and to confirm a file was actually indexed. Lists only datasets under the active storage_path."
            ),
            inputSchema={"type": "object", "properties": {}},
        ),
        Tool(
            name="list_repos",
            description=(
                "List GitHub repositories indexed via index_repo. Shows repo name, "
                "HEAD SHA, dataset count, total rows, and dataset names for each repo."
            
                " Covers repos indexed with index_repo only; a dataset added by index_local is not listed here."
            ),
            inputSchema={"type": "object", "properties": {}},
        ),
        Tool(
            name="describe_dataset",
            description=(
                "Primary orientation tool. Returns every column's name, type, cardinality, "
                "null%, and sample values. A single call replaces reading the entire source file. "
                "Equivalent to opening a spreadsheet and reading the column headers + stats. "
                "On wide tables (60+ columns), results are auto-paginated — use columns=[] to "
                "select specific ones, or columns_offset to page through remaining columns."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "dataset": {
                        "type": "string",
                        "description": "Dataset identifier (from list_datasets or index_local)",
                    },
                    "columns": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "Filter to specific columns (default: all)",
                    },
                    "columns_offset": {
                        "type": "integer",
                        "description": "Pagination offset for wide tables (default 0)",
                        "default": 0,
                    },
                },
                "required": ["dataset"],
            },
        ),
        Tool(
            name="describe_column",
            description=(
                "Deep profile of a single column. Full value distribution for low-cardinality "
                "columns, histogram bins for numeric, temporal range for datetime. "
                "top_n capped at 200; histogram_bins capped at 50."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "dataset": {"type": "string", "description": "Dataset identifier"},
                    "column": {
                        "type": "string",
                        "description": "Column name or column ID (e.g. 'lapd-crime::AREA NAME#column')",
                    },
                    "top_n": {
                        "type": "integer",
                        "description": "Top values to return for categorical columns (default 20)",
                        "default": 20,
                    },
                    "histogram_bins": {
                        "type": "integer",
                        "description": "Bins for numeric histograms (default 10)",
                        "default": 10,
                    },
                    "redact": {
                        "type": "boolean",
                        "description": "Scrub PII / credentials from value_distribution, top_values, and sample_values (default true). Numeric stats and counts are never altered. Set false for raw values when working with data you own.",
                        "default": True,
                    },
                    "redact_patterns": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "Additional Python regex patterns to redact on top of the built-in set.",
                    },
                },
                "required": ["dataset", "column"],
            },
        ),
        Tool(
            name="search_data",
            description=(
                "Search across column names and values. Returns column-level results with IDs "
                "— tells you where to look, not the data itself. Use before get_rows or describe_column. "
                "max_results capped at 50. Set semantic=true for embedding-based search (requires "
                "an embedding provider: JDATAMUNCH_EMBED_MODEL, GOOGLE_API_KEY, or OPENAI_API_KEY)."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "dataset": {"type": "string", "description": "Dataset identifier"},
                    "query": {
                        "type": "string",
                        "description": "Natural-language or keyword query",
                    },
                    "search_scope": {
                        "type": "string",
                        "enum": ["all", "schema", "values"],
                        "description": "Limit search to schema only, values only, or all (default 'all')",
                        "default": "all",
                    },
                    "max_results": {
                        "type": "integer",
                        "description": "Maximum results to return (default 10)",
                        "default": 10,
                    },
                    "semantic": {
                        "type": "boolean",
                        "description": "Enable semantic search via embeddings (default false). Requires embedding provider.",
                        "default": False,
                    },
                    "semantic_weight": {
                        "type": "number",
                        "description": "Weight for semantic score in hybrid ranking. 0.0 = pure keyword, 1.0 = pure semantic (default 0.5).",
                        "default": 0.5,
                    },
                    "semantic_only": {
                        "type": "boolean",
                        "description": "Skip keyword scoring entirely; use only embeddings (default false).",
                        "default": False,
                    },
                },
                "required": ["dataset", "query"],
            },
        ),
        Tool(
            name="get_rows",
            description=(
                "Filtered row retrieval via structured filters. All filters are SQL-parameterized "
                "(no injection). Operators: eq, neq, gt, gte, lt, lte, contains, in, is_null, between. "
                "Use columns=[] to project — reduces tokens significantly on wide tables. "
                "Prefer aggregate() for summaries over paginating through rows."
            
                " Returns at most limit rows (default 50); page with offset instead of raising it."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "dataset": {"type": "string", "description": "Dataset identifier"},
                    "filters": {
                        "type": "array",
                        "items": {
                            "type": "object",
                            "properties": {
                                "column": {"type": "string"},
                                "op": {
                                    "type": "string",
                                    "enum": ["eq", "neq", "gt", "gte", "lt", "lte",
                                             "contains", "in", "is_null", "between"],
                                },
                                "value": {},
                            },
                            "required": ["column", "op"],
                        },
                        "description": "Filter conditions (ANDed). E.g. [{\"column\": \"AREA NAME\", \"op\": \"eq\", \"value\": \"Hollywood\"}]",
                    },
                    "columns": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "Column projection — reduces tokens (default: all)",
                    },
                    "order_by": {"type": "string", "description": "Column to sort by"},
                    "order_dir": {
                        "type": "string",
                        "enum": ["asc", "desc"],
                        "description": "Sort direction (default 'asc')",
                        "default": "asc",
                    },
                    "limit": {
                        "type": "integer",
                        "description": "Max rows returned (default 50, hard cap 500)",
                        "default": 50,
                    },
                    "offset": {
                        "type": "integer",
                        "description": "Pagination offset (default 0)",
                        "default": 0,
                    },
                    "redact": {
                        "type": "boolean",
                        "description": "Scrub PII / credentials (emails, SSNs, Luhn-valid credit cards, JWTs, API keys, PEM blocks, AWS keys, GitHub/Slack tokens) from row cells before return (default true). Numeric cells are never altered. _meta.redaction reports cells_redacted + per-pattern counts.",
                        "default": True,
                    },
                    "redact_patterns": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "Additional Python regex patterns to layer on top of the built-in set. Invalid patterns are silently skipped (reported in _meta.redaction.invalid_custom_patterns).",
                    },
                    "redact_skip_columns": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "Column names to exempt from redaction (e.g. an `email_hashed` column where the email pattern would false-positive).",
                    },
                },
                "required": ["dataset"],
            },
        ),
        Tool(
            name="aggregate",
            description=(
                "Server-side aggregations (GROUP BY). Saves orders of magnitude in tokens "
                "vs returning rows for the LLM to aggregate. Functions: count, sum, avg, "
                "min, max, count_distinct, median. limit capped at 1000."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "dataset": {"type": "string", "description": "Dataset identifier"},
                    "aggregations": {
                        "type": "array",
                        "items": {
                            "type": "object",
                            "properties": {
                                "column": {"type": "string"},
                                "function": {
                                    "type": "string",
                                    "enum": ["count", "sum", "avg", "min", "max",
                                             "count_distinct", "median"],
                                },
                                "alias": {"type": "string"},
                            },
                            "required": ["column", "function"],
                        },
                        "description": "Aggregation specs. Use column='*' for COUNT(*).",
                    },
                    "group_by": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "Group-by columns. Empty = whole-dataset aggregate.",
                    },
                    "filters": {
                        "type": "array",
                        "items": {"type": "object"},
                        "description": "Pre-filter rows before aggregating (same syntax as get_rows)",
                    },
                    "having": {
                        "type": "array",
                        "items": {"type": "object"},
                        "description": "Post-aggregation filter on aggregation aliases (B11). Each item: {\"column\": <alias>, \"op\": eq|neq|gt|gte|lt|lte|in|between|is_null, \"value\": ...}",
                    },
                    "order_by": {"type": "string", "description": "Column or alias to sort by"},
                    "order_dir": {
                        "type": "string",
                        "enum": ["asc", "desc"],
                        "default": "desc",
                    },
                    "limit": {
                        "type": "integer",
                        "description": "Max groups returned (default 50)",
                        "default": 50,
                    },
                    "approximate": {
                        "type": "boolean",
                        "description": "Approximate-mode aggregation (C1). Routes count_distinct → HyperLogLog (~2% error), median → t-digest (~1% error), sum/avg → sampled estimator with 95% confidence interval. Whole-dataset only.",
                        "default": False,
                    },
                    "redact": {
                        "type": "boolean",
                        "description": "Scrub PII / credentials from group-by column values (default true). Aggregate values (counts, sums, etc.) are never altered.",
                        "default": True,
                    },
                    "redact_patterns": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "Additional Python regex patterns to layer on top of the built-in set.",
                    },
                    "redact_skip_columns": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "Group-by column names to exempt from redaction.",
                    },
                },
                "required": ["dataset", "aggregations"],
            },
        ),
        Tool(
            name="sample_rows",
            description=(
                "Return a sample of rows. Useful for understanding data shape "
                "without prior knowledge. Method: 'head', 'tail', or 'random'. "
                "Use columns=[] on wide tables to reduce response size. "
                "Pass seed (int) with method='random' for deterministic, "
                "reproducible sampling."
            
                " A sample shows shape, not distribution; use get_distribution when you need the spread."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "dataset": {"type": "string", "description": "Dataset identifier"},
                    "n": {
                        "type": "integer",
                        "description": "Rows to sample (default 5, max 100)",
                        "default": 5,
                    },
                    "method": {
                        "type": "string",
                        "enum": ["head", "tail", "random"],
                        "description": "Sampling method (default 'head')",
                        "default": "head",
                    },
                    "columns": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "Column projection (default: all)",
                    },
                    "seed": {
                        "type": "integer",
                        "description": "Deterministic seed for method='random' (omitted = non-deterministic)",
                    },
                    "redact": {
                        "type": "boolean",
                        "description": "Scrub PII / credentials from sampled cells before return (default true).",
                        "default": True,
                    },
                    "redact_patterns": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "Additional Python regex patterns to layer on top of the built-in set.",
                    },
                    "redact_skip_columns": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "Column names to exempt from redaction.",
                    },
                },
                "required": ["dataset"],
            },
        ),
        Tool(
            name="get_schema_drift",
            description=(
                "Compare schema (columns, types, nullability) between two indexed datasets. "
                "Detects added/removed columns, type changes, and null-rate shifts. "
                "Pure in-memory comparison — no re-reading source files. "
                "Useful for detecting schema changes between dataset versions. "
                "Assessment: 'identical' | 'additive' (only additions) | 'breaking' (removals or type changes)."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "dataset_a": {
                        "type": "string",
                        "description": "First dataset identifier (baseline)",
                    },
                    "dataset_b": {
                        "type": "string",
                        "description": "Second dataset identifier (comparison target)",
                    },
                },
                "required": ["dataset_a", "dataset_b"],
            },
        ),
        Tool(
            name="get_data_hotspots",
            description=(
                "Return the highest-risk columns in a dataset ranked by a composite score "
                "combining: null rate, cardinality anomalies, numeric outlier spread, and "
                "(v1.10.0) runtime traffic from runtime_query_calls when traces exist. "
                "When include_runtime is true but no traces are ingested, the response "
                "carries an honest-hint caveat in _meta.runtime_caveat rather than silently "
                "scoring on static signals alone. top_n capped at 50."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "dataset": {
                        "type": "string",
                        "description": "Dataset identifier",
                    },
                    "top_n": {
                        "type": "integer",
                        "description": "Number of hotspot columns to return (default 10, max 50)",
                        "default": 10,
                    },
                    "include_runtime": {
                        "type": "boolean",
                        "default": True,
                        "description": "Fuse traffic signal from runtime_query_calls when available.",
                    },
                    "window_days": {
                        "type": "integer",
                        "default": 30,
                        "description": "Lookback window for the traffic signal. Default 30.",
                    },
                },
                "required": ["dataset"],
            },
        ),
        Tool(
            name="get_correlations",
            description=(
                "Compute pairwise Pearson correlations between numeric columns. "
                "Returns pairs sorted by |r| descending, filtered to significant correlations. "
                "Use this to discover relationships in the data without manual exploration. "
                "top_n capped at 200."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "dataset": {
                        "type": "string",
                        "description": "Dataset identifier",
                    },
                    "min_abs_correlation": {
                        "type": "number",
                        "description": "Minimum |r| to include in results (default 0.3)",
                        "default": 0.3,
                    },
                    "columns": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "Restrict to specific numeric columns (default: all numeric)",
                    },
                    "top_n": {
                        "type": "integer",
                        "description": "Max pairs to return (default 20, max 200)",
                        "default": 20,
                    },
                    "method": {
                        "type": "string",
                        "enum": ["pearson", "spearman"],
                        "description": "Correlation method (default 'pearson'). Spearman is rank-based — robust to outliers and monotonic non-linear relationships (B10).",
                        "default": "pearson",
                    },
                },
                "required": ["dataset"],
            },
        ),
        Tool(
            name="join_datasets",
            description=(
                "Join two indexed datasets via SQL JOIN. Uses ATTACH DATABASE to combine "
                "two SQLite stores into one query. Supports inner, left, right, and cross joins. "
                "Use columns_a/columns_b to project — reduces tokens on wide tables. "
                "Row limit capped at 500. Prefer aggregate() on join results for summaries."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "dataset_a": {
                        "type": "string",
                        "description": "First dataset identifier (left side of join)",
                    },
                    "dataset_b": {
                        "type": "string",
                        "description": "Second dataset identifier (right side of join)",
                    },
                    "join_column_a": {
                        "type": "string",
                        "description": "Column from dataset_a to join on",
                    },
                    "join_column_b": {
                        "type": "string",
                        "description": "Column from dataset_b to join on",
                    },
                    "join_type": {
                        "type": "string",
                        "enum": ["inner", "left", "right", "cross"],
                        "description": "Join type (default 'inner')",
                        "default": "inner",
                    },
                    "columns_a": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "Columns to select from dataset_a (default: first 30)",
                    },
                    "columns_b": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "Columns to select from dataset_b (default: first 30)",
                    },
                    "filters_a": {
                        "type": "array",
                        "items": {"type": "object"},
                        "description": "Pre-filter dataset_a rows (same syntax as get_rows filters)",
                    },
                    "filters_b": {
                        "type": "array",
                        "items": {"type": "object"},
                        "description": "Pre-filter dataset_b rows (same syntax as get_rows filters)",
                    },
                    "order_by": {
                        "type": "string",
                        "description": "Column to sort results by",
                    },
                    "order_dir": {
                        "type": "string",
                        "enum": ["asc", "desc"],
                        "description": "Sort direction (default 'asc')",
                        "default": "asc",
                    },
                    "limit": {
                        "type": "integer",
                        "description": "Max rows returned (default 50, hard cap 500)",
                        "default": 50,
                    },
                    "offset": {
                        "type": "integer",
                        "description": "Pagination offset (default 0)",
                        "default": 0,
                    },
                },
                "required": ["dataset_a", "dataset_b", "join_column_a", "join_column_b"],
            },
        ),
        Tool(
            name="summarize_dataset",
            description=(
                "Generate natural-language summaries for a dataset and all its columns. "
                "Works on already-indexed datasets — reads profiles from index.json, "
                "generates summaries, and writes them back. No re-parsing of source files. "
                "Summaries are also auto-generated during index_local."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "dataset": {
                        "type": "string",
                        "description": "Dataset identifier (from list_datasets)",
                    },
                },
                "required": ["dataset"],
            },
        ),
        Tool(
            name="delete_dataset",
            description=(
                "Delete an indexed dataset and its SQLite store. Frees disk space. "
                "Irreversible — the dataset must be re-indexed to use again."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "dataset": {
                        "type": "string",
                        "description": "Dataset identifier to delete (from list_datasets)",
                    },
                },
                "required": ["dataset"],
            },
        ),
        Tool(
            name="embed_dataset",
            description=(
                "Precompute column embeddings for semantic search. Optional warm-up — "
                "search_data with semantic=true lazily embeds on first use. Running "
                "embed_dataset upfront eliminates that latency. Requires an embedding "
                "provider (JDATAMUNCH_EMBED_MODEL, GOOGLE_API_KEY, or OPENAI_API_KEY)."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "dataset": {
                        "type": "string",
                        "description": "Dataset identifier (from list_datasets)",
                    },
                    "force": {
                        "type": "boolean",
                        "description": "Recompute all embeddings even if cached (default false)",
                        "default": False,
                    },
                },
                "required": ["dataset"],
            },
        ),
        Tool(
            name="get_session_stats",
            description="Return cumulative token savings and cost avoided across all tool calls. Savings are modelled estimates, not per-call measurements.",
            inputSchema={"type": "object", "properties": {}},
        ),
        Tool(
            name="validate_index",
            description=(
                "Verify an indexed dataset's on-disk integrity. Runs SQLite "
                "PRAGMA integrity_check, cross-checks row count and column "
                "list against index.json, and verifies index.json content "
                "hash. Reports stale-lock state from interrupted index_local "
                "runs. Returns overall_status: 'ok' | 'warning' | 'error'."
            
                " Checks the integrity of the index, never the correctness of the underlying data."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "dataset": {"type": "string", "description": "Dataset identifier"},
                },
                "required": ["dataset"],
            },
        ),
        Tool(
            name="get_dataset_history",
            description=(
                "Return the last N profile snapshots for a dataset. "
                "Snapshots are appended on every successful index_local — "
                "use this to detect schema/content drift over multiple "
                "ingests of the same dataset. n capped at 50."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "dataset": {"type": "string", "description": "Dataset identifier"},
                    "n": {
                        "type": "integer",
                        "description": "Number of snapshots to return (default 10, max 50)",
                        "default": 10,
                    },
                },
                "required": ["dataset"],
            },
        ),
        Tool(
            name="get_dataset_health",
            description=(
                "Composite quality grade (A–F) for a dataset (B4). Combines "
                "null severity, type-confidence, constant-column count, "
                "primary-key presence, semantic-typing coverage, and drift "
                "history into a single score with a structured breakdown."
            
                " Grades structure and completeness, not whether the values are right."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "dataset": {"type": "string", "description": "Dataset identifier"},
                },
                "required": ["dataset"],
            },
        ),
        Tool(
            name="suggest_keys",
            description=(
                "Rank primary-key candidates for a dataset (B5). Each entry "
                "carries a confidence score plus the reasons that raised it "
                "(integer column, UUID format, no nulls, exact-count unique)."
            
                " Candidates are ranked from profile statistics, so confirm against the source system before treating one as the key."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "dataset": {"type": "string", "description": "Dataset identifier"},
                },
                "required": ["dataset"],
            },
        ),
        Tool(
            name="suggest_joins",
            description=(
                "Discover FK candidates between this dataset and other "
                "indexed datasets (B5). For each non-PK column in the source, "
                "scans up to 20 other datasets' PK candidates and proposes "
                "joins where containment ≥ 95%. Sample-based (500 distinct "
                "values per source column)."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "dataset": {"type": "string", "description": "Source dataset identifier"},
                },
                "required": ["dataset"],
            },
        ),
        Tool(
            name="get_distribution",
            description=(
                "Unified bin-counts for any column type (B8). Numeric → "
                "equal-width bins between min/max; datetime → time-bucket "
                "bins; categorical / string → top-n + 'other' bucket. "
                "Token-cheap way to ask 'what does this column look like?'."
            
                " Bin counts only (default 20 bins); it never returns the underlying rows."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "dataset": {"type": "string", "description": "Dataset identifier"},
                    "column": {"type": "string", "description": "Column name"},
                    "bins": {
                        "type": "integer",
                        "description": "Number of bins / categories to return (default 20, max 100)",
                        "default": 20,
                    },
                },
                "required": ["dataset", "column"],
            },
        ),
        Tool(
            name="plan_query",
            description=(
                "Map a natural-language intent into a ranked tool-call "
                "sequence for the given dataset (B3). Pure routing — no LLM "
                "call. Built-in intents: summarize, anomalies, compare, "
                "join, filter, trend, correlate."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "dataset": {"type": "string", "description": "Dataset identifier"},
                    "intent": {
                        "type": "string",
                        "description": "Natural-language intent (e.g. 'summarize', 'find anomalies', 'join with X', 'trend over time'). Default 'summarize'.",
                        "default": "summarize",
                    },
                },
                "required": ["dataset"],
            },
        ),
        Tool(
            name="run_sql",
            description=(
                "Read-only sandboxed SQL escape hatch (B1). Accepts a single "
                "SELECT (or WITH … SELECT) statement. The first dataset is "
                "the main connection; additional datasets are ATTACHed under "
                "schema names (e.g. `<dataset>.rows`). Statement runs under "
                "PRAGMA query_only=1 with a 10-second budget and 500-row cap. "
                "Use this for HAVING / window functions / CTEs / multi-way "
                "joins that the structured tools don't cover."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "sql": {"type": "string", "description": "SELECT or WITH … SELECT statement"},
                    "datasets": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "Indexed datasets to attach. Order matters: datasets[0] is the main connection.",
                    },
                    "limit": {
                        "type": "integer",
                        "description": "Row cap (default 500, hard max 500)",
                        "default": 500,
                    },
                    "redact": {
                        "type": "boolean",
                        "description": "Scrub PII / credentials from result cells before return (default true).",
                        "default": True,
                    },
                    "redact_patterns": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "Additional Python regex patterns to layer on top of the built-in set.",
                    },
                    "redact_skip_columns": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "Result column names to exempt from redaction.",
                    },
                },
                "required": ["sql", "datasets"],
            },
        ),
        Tool(
            name="get_schema_impact",
            description=(
                "Transitive impact of a column-level schema change (drop_column, "
                "rename_column, retype_column). Walks the inferred FK graph to "
                "max_depth, surfaces direct + transitive hits across datasets, "
                "and normalises blast_score to [0, 1]. For retype_column, also "
                "flags type_mismatch entries at FK edges whose partner type "
                "wouldn't survive the retype. Read-only."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "dataset_id": {"type": "string"},
                    "column": {"type": "string", "description": "Column name (case-insensitive)."},
                    "kind": {
                        "type": "string",
                        "enum": ["drop_column", "rename_column", "retype_column"],
                        "default": "drop_column",
                    },
                    "new_name": {
                        "type": "string",
                        "description": "Required for rename_column.",
                    },
                    "new_type": {
                        "type": "string",
                        "description": "Required for retype_column. e.g. integer / string / float.",
                    },
                    "max_depth": {
                        "type": "integer",
                        "default": 3,
                        "description": "BFS depth over the inferred FK graph.",
                    },
                    "window_days": {
                        "type": "integer",
                        "default": 30,
                        "description": "Runtime traffic look-back.",
                    },
                },
                "required": ["dataset_id", "column"],
            },
        ),
        Tool(
            name="check_column_drop_safe",
            description=(
                "Composite preflight: is this column safe to drop? Fuses four "
                "signals — primary-key status, foreign-key participation, "
                "cross-dataset name match, and runtime traffic — into a single "
                "verdict plus ranked blockers and a recommended_action. "
                "Verdict tiers: pk_blocking, fk_blocking, runtime_observed, "
                "cross_dataset_blocking, safe_to_drop. Read-only. The killer "
                "feature of the Phase-1 sibling-parity batch."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "dataset_id": {"type": "string"},
                    "column": {"type": "string", "description": "Column name (case-insensitive)."},
                    "window_days": {
                        "type": "integer",
                        "default": 30,
                        "description": "Look-back window for runtime traffic. Default 30.",
                    },
                },
                "required": ["dataset_id", "column"],
            },
        ),
        Tool(
            name="find_unused_columns",
            description=(
                "Surface columns with zero or stale runtime traffic. Reads "
                "runtime_query_calls (populated by ingest_sql_log) and surfaces "
                "columns that haven't been queried within `window_days`. "
                "Excludes primary-key candidates and audit fields (created_at / "
                "updated_at / dbt_*) by default. Refuses to run with explicit "
                "error when no runtime data has been ingested — would otherwise "
                "trivially flag every column."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "dataset_id": {"type": "string"},
                    "window_days": {
                        "type": "integer",
                        "default": 30,
                        "description": "Look-back window. Default 30.",
                    },
                    "min_calls": {
                        "type": "integer",
                        "default": 0,
                        "description": "Floor for 'considered used' within window. Default 0.",
                    },
                    "exclude_pk": {
                        "type": "boolean",
                        "default": True,
                        "description": "Skip primary-key candidates. Default true.",
                    },
                    "exclude_audit": {
                        "type": "boolean",
                        "default": True,
                        "description": "Skip audit columns (created_at, updated_at, dbt_*, etc). Default true.",
                    },
                },
                "required": ["dataset_id"],
            },
        ),
        Tool(
            name="ingest_sql_log",
            description=(
                "Ingest a SQL log file (pg_stat_statements CSV or generic JSONL, "
                ".gz transparently) into the per-dataset runtime tables. Each "
                "query is parsed for table + column refs, redacted at the "
                "chokepoint (string + numeric literals + cell-PII registry), "
                "and rolled up into runtime_query_calls keyed by "
                "(fingerprint, table, column). Tables in the log that don't "
                "match any indexed dataset count as unmapped. Foundational "
                "primitive for find_unused_columns, check_column_drop_safe, "
                "and data_health_radar (v1.6.0 sibling-parity Phase 1)."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "file_path": {
                        "type": "string",
                        "description": "Path to a CSV / JSONL / .gz log file.",
                    },
                    "source": {
                        "type": "string",
                        "description": "pg_stat_statements | jsonl | auto (default — sniff by extension).",
                        "default": "auto",
                    },
                    "redact": {
                        "type": "boolean",
                        "description": "Scrub PII / literals before persisting. Default true.",
                        "default": True,
                    },
                    "max_rows": {
                        "type": "integer",
                        "description": "Hard cap on ingested rows. Default 100000.",
                        "default": 100000,
                    },
                },
                "required": ["file_path"],
            },
        ),
        Tool(
            name="find_similar_columns",
            description=(
                "Multi-signal cross-dataset column consolidation. Fuses name "
                "(token Jaccard), type, top-value overlap, cardinality similarity, "
                "and (when present) embedding cosine into a composite score. "
                "Clusters via union-find and classifies each cluster: near_duplicate, "
                "naming_drift, parallel_definition, or overlapping_topic. Use to "
                "find duplicate columns across datasets, surface naming drift "
                "(`email` vs `email_address`), or detect the same conceptual "
                "column spread across multiple datasets. Mirrors jcm's "
                "find_similar_symbols."
            
                " Every signal is heuristic, so a high score means investigate, not merge."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "datasets": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "Datasets to scan. Omit to scan every indexed dataset.",
                    },
                    "min_score": {
                        "type": "number",
                        "default": 0.5,
                        "description": "Composite-score floor for surfacing pairs.",
                    },
                    "top_n": {
                        "type": "integer",
                        "default": 50,
                        "description": "Max clusters returned. Default 50, capped at 200.",
                    },
                    "same_type_only": {
                        "type": "boolean",
                        "default": False,
                        "description": "Drop pairs where types don't match.",
                    },
                },
            },
        ),
        Tool(
            name="data_health_radar",
            description=(
                "Six-axis health radar for a dataset: null_health, type_confidence, "
                "cardinality_health, pk_presence, semantic_coverage, schema_stability "
                "(omitted when <2 history snapshots). Optional 7th axis runtime_coverage "
                "when traces ingested. Returns 0-100 score per axis + composite + A-F "
                "grade. Pairs with diff_data_health_radar for snapshot deltas. "
                "Mirrors jcm's six-axis health radar."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "dataset": {"type": "string"},
                    "include_runtime": {
                        "type": "boolean",
                        "default": True,
                        "description": "Fuse runtime_coverage axis when traces exist.",
                    },
                    "window_days": {
                        "type": "integer",
                        "default": 30,
                        "description": "Lookback for the runtime axis. Default 30.",
                    },
                },
                "required": ["dataset"],
            },
        ),
        Tool(
            name="diff_data_health_radar",
            description=(
                "Diff two data_health_radar payloads. Pure function — pass the `radar` "
                "sub-field from two data_health_radar responses (e.g. yesterday vs today). "
                "Returns per-axis deltas, composite delta, grade change, regression and "
                "improvement lists (threshold: 3 points), one-line verdict."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "baseline": {
                        "type": "object",
                        "description": "Baseline radar payload (e.g. yesterday's snapshot).",
                    },
                    "current": {
                        "type": "object",
                        "description": "Current radar payload (e.g. today's snapshot).",
                    },
                },
                "required": ["baseline", "current"],
            },
        ),
        Tool(
            name="get_redaction_log",
            description=(
                "Forensic accounting of PII redactions for a dataset. Returns "
                "per-pattern counts from runtime_redaction_log (populated by "
                "ingest_sql_log with redact=True), so operators can verify the "
                "chokepoint is firing on production traffic. Filter by source "
                "and lookback window. Empty result with no traces ingested is "
                "not an error — it just means no scrubbing has happened yet."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "dataset_id": {"type": "string"},
                    "source": {
                        "type": "string",
                        "description": "Optional source filter. Today: 'sql_log'.",
                    },
                    "since_days": {
                        "type": "integer",
                        "default": 30,
                        "description": "Lookback window for last_seen. Default 30.",
                    },
                },
                "required": ["dataset_id"],
            },
        ),
        Tool(
            name="tune_weights",
            description=(
                "Inspect, set, or reset the weight vector search_data uses to rank "
                "columns (name/value/type match weights plus the BM25 and semantic "
                "blend scales). Omit all args to inspect the effective weights and "
                "their source. Pass set_weights (a {weight: number} object) to "
                "override, or reset=true to clear. Scope with dataset (per-dataset "
                "overrides win over the global default, which wins over built-ins). "
                "Honored by search_data at query time. Unlike jcodemunch/jdocmunch, "
                "weights are tuned explicitly here (no ranking ledger). Tunable: "
                "name_exact, name_substr, name_word, ai_summary_word, value_exact, "
                "value_substr, type_boost, bm25_scale, semantic_scale, "
                "default_semantic_weight."
            
                " Affects search_data ranking only; no other tool reads these weights."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "dataset": {
                        "type": "string",
                        "description": "Tune one dataset. Omit for the global default.",
                    },
                    "set_weights": {
                        "type": "object",
                        "description": (
                            "Weight overrides, e.g. name_exact=30. Unknown names or "
                            "non-numeric values are rejected; values are clamped to "
                            "each weight bounds."
                        ),
                        "additionalProperties": {"type": "number"},
                    },
                    "reset": {
                        "type": "boolean",
                        "default": False,
                        "description": "Clear this scope overrides.",
                    },
                },
            },
        ),
        Tool(
            name="check_embedding_drift",
            description=(
                "Detect whether the embedding provider has drifted since it was "
                "pinned. Column embeddings power semantic search_data and "
                "find_similar_columns; if the provider model changes underneath a "
                "stored index, saved vectors stop matching the live encoder and "
                "semantic ranking quietly degrades. Pins a 16-string canary in "
                "<index_path>/embed_canary.json and recomputes it on demand, "
                "reporting cosine drift. Call with force=true once to set the "
                "baseline, then again after a suspected provider change. Sibling of "
                "jcodemunch / jdocmunch check_embedding_drift."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "force": {
                        "type": "boolean",
                        "default": False,
                        "description": "Re-embed and re-pin the canary baseline (set once to establish it).",
                    },
                    "threshold": {
                        "type": "number",
                        "default": 0.05,
                        "description": "Cosine-distance alarm threshold; alarm is true when the worst canary drifts past it.",
                    },
                },
            },
        ),
        Tool(
            name="analyze_perf",
            description=(
                "Per-tool latency and cache-hit telemetry. Returns p50/p95/max "
                "latency and error rate per tool, the slowest tools by p95, and "
                "result-cache hit rates (aggregate / get_correlations / "
                "get_data_hotspots are the cached tools). window=session reads the "
                "always-on in-memory ring; window=1h/24h/7d/all reads the persistent "
                "SQLite sink (requires JDATAMUNCH_PERF_TELEMETRY=1). Sibling of "
                "jcodemunch / jdocmunch analyze_perf."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "window": {
                        "type": "string",
                        "enum": ["session", "1h", "24h", "7d", "all"],
                        "default": "session",
                        "description": "session = in-memory ring; others read the persistent perf db.",
                    },
                    "top": {
                        "type": "integer",
                        "default": 20,
                        "description": "Max slowest-tools / coldest-caches returned.",
                    },
                    "tool": {
                        "type": "string",
                        "description": "Restrict the analysis to a single tool name.",
                    },
                },
            },
        ),
        Tool(
            name="finalize_handoff",
            description=(
                "Finalize one canonical Markdown handoff for a completed data "
                "audit/analysis (jdatamunch.handoff/v1; suite parity with jCodeMunch). "
                "The server assembles YOUR sections deterministically, validates every "
                "evidence_refs entry against what this session actually retrieved "
                "(column ids like '<dataset>::<column>#column' or dataset names served "
                "by search_data / describe_dataset / describe_column — unknown refs "
                "fail closed), persists the result session-scoped, and returns a "
                "compact receipt {handoff_id, resource_uri, sha256, length, "
                "canonical:true}. Read the immutable body via the munch://handoff/<id> "
                "resource; repeated reads are byte-identical. Appendices are included "
                "exactly once; no character limit; never writes to your data."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "dataset": {
                        "type": "string",
                        "description": "Dataset the handoff is about.",
                    },
                    "task": {
                        "type": "string",
                        "description": "The task/question this handoff answers (becomes the title).",
                    },
                    "sections": {
                        "type": "array",
                        "description": "Ordered report sections, each {heading, content} (markdown). The caller authors these; the server only assembles. Optional per-section claims[] bind evidence to an individual claim instead of one global list (handoff/v2).",
                        "items": {
                            "type": "object",
                            "properties": {
                                "heading": {"type": "string"},
                                "content": {"type": "string"},
                                "claims": {
                                    "type": "array",
                                    "description": "Optional caller-authored claims, each {id, statement, evidence_refs, classification?}. Ids must be unique across the handoff; each claim's refs are attested separately and rendered beside the claim.",
                                    "items": {
                                        "type": "object",
                                        "properties": {
                                            "id": {"type": "string"},
                                            "statement": {"type": "string"},
                                            "evidence_refs": {
                                                "type": "array",
                                                "items": {"type": "string"},
                                            },
                                            "classification": {"type": "string"},
                                        },
                                        "required": ["id", "statement", "evidence_refs"],
                                    },
                                },
                            },
                            "required": ["heading"],
                        },
                    },
                    "evidence_refs": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "Column ids or dataset names retrieved this session; validated against the session retrieval record.",
                    },
                    "profile": {
                        "type": "string",
                        "default": "general",
                        "description": "Handoff profile label (e.g. data_audit).",
                    },
                    "appendices": {
                        "type": "array",
                        "description": "Optional named appendices, each {name, content, content_type?}; names must be unique.",
                        "items": {
                            "type": "object",
                            "properties": {
                                "name": {"type": "string"},
                                "content": {"type": "string"},
                                "content_type": {"type": "string"},
                            },
                            "required": ["name", "content"],
                        },
                    },
                },
                "required": ["dataset", "task", "sections", "evidence_refs"],
            },
        ),
        Tool(
            name="jdatamunch_guide",
            description=(
                "Return the version-current CLAUDE.md / AGENT.md policy snippet for "
                "jdatamunch-mcp. Lets an agent keep a one-line CLAUDE.md (e.g. \"Call "
                "jdatamunch_guide and strictly follow its instructions.\") instead of "
                "pasting a static snippet that drifts from the installed version. "
                "Idempotent, no dataset context required. Sibling of jcodemunch_guide "
                "and jdocmunch_guide."
            ),
            inputSchema={
                "type": "object",
                "properties": {},
            },
        ),
    ]


def _generate_data_md_snippet() -> str:
    """Return the recommended CLAUDE.md prompt-policy snippet for jdatamunch-mcp.

    Mirrors jcodemunch-mcp's `_generate_claude_md_snippet` and jdocmunch-mcp's
    `_generate_doc_md_snippet`. Idempotent: produces the same text on every call
    for a given installed version.
    """
    categories = [
        ("Indexing", ["index_local", "index_repo", "delete_dataset", "validate_index"]),
        ("Discovery", ["list_datasets", "list_repos"]),
        ("Schema & profile", ["describe_dataset", "describe_column", "get_dataset_history",
                               "get_dataset_health", "summarize_dataset"]),
        ("Row & cell retrieval", ["get_rows", "sample_rows", "search_data",
                                   "get_distribution", "embed_dataset"]),
        ("Analysis", ["aggregate", "get_correlations", "get_data_hotspots",
                       "find_similar_columns"]),
        ("Schema graph & joins", ["get_schema_drift", "get_schema_impact",
                                   "check_column_drop_safe", "find_unused_columns",
                                   "suggest_keys", "suggest_joins", "join_datasets"]),
        ("SQL", ["plan_query", "run_sql"]),
        ("Runtime trace ingest", ["ingest_sql_log", "get_redaction_log"]),
        ("Health metrics", ["data_health_radar", "diff_data_health_radar", "check_embedding_drift"]),
        ("Utilities", ["get_session_stats", "tune_weights", "analyze_perf"]),
        ("Self-Guide", ["jdatamunch_guide"]),
    ]
    from . import __version__ as _ver
    lines = [
        f"## jdatamunch-mcp (v{_ver})",
        "",
        "Use jdatamunch-mcp tools instead of Read/Grep/csv-by-hand for any indexed dataset.",
        "",
        "### Quick start",
        "1. `list_datasets` -- check what's indexed.",
        "   If your file isn't there: `index_local` (CSV / Excel / Parquet / JSONL).",
        "2. `describe_dataset` -- column types, null counts, cardinality, top values.",
        "3. `describe_column` -- deep stats on one column (full distribution, outliers).",
        "4. `run_sql` -- ad-hoc SQL against the SQLite-backed dataset.",
        "",
        "### All tools",
    ]
    for cat, tools in categories:
        lines.append(f"**{cat}:** " + ", ".join(f"`{t}`" for t in tools))
    lines.append("")
    lines.append("Never load a CSV into the agent context to inspect it. Use the tools above.")
    lines.append("")
    return "\n".join(lines)


@server.list_resources()
async def list_resources() -> list[Resource]:
    """Advertise the runtime identity resource (munch.runtime.identity/v1)
    plus any session-finalized canonical handoffs (jdatamunch.handoff/v1)."""
    resources = [
        Resource(
            uri=runtime_identity.IDENTITY_URI,
            name="runtime-identity",
            description=(
                "Process provenance for this server instance "
                f"({runtime_identity.IDENTITY_SCHEMA}): product, version, "
                "transport, pid, OS-derived process_start, per-process "
                "instance_id, optional launch_id echo. Read-only, no side effects."
            ),
            mimeType="application/json",
        )
    ]
    from . import handoff as _handoff_mod
    for row in _handoff_mod.list_handoff_resources():
        resources.append(
            Resource(
                uri=row["uri"],
                name=row["name"],
                description=row["description"],
                mimeType=_handoff_mod.HANDOFF_CONTENT_TYPE,
            )
        )
    return resources


@server.read_resource()
async def read_resource(uri) -> "list[ReadResourceContents]":
    if str(uri) == runtime_identity.IDENTITY_URI:
        return [
            ReadResourceContents(
                content=runtime_identity.identity_json(),
                mime_type="application/json",
            )
        ]
    from . import handoff as _handoff_mod
    rec = _handoff_mod.handoff_for_uri(str(uri))
    if rec is not None:
        return [
            ReadResourceContents(
                content=rec["body"],
                mime_type=_handoff_mod.HANDOFF_CONTENT_TYPE,
            )
        ]
    raise ValueError(f"Unknown resource: {uri}")


@server.list_prompts()
async def list_prompts() -> list:
    """Return empty prompt list for client compatibility."""
    return []


@server.call_tool()
async def call_tool(name: str, arguments: dict) -> list[TextContent]:
    """Dispatch tool calls to implementations."""
    storage_path = os.environ.get("DATA_INDEX_PATH")

    # Honor JDATAMUNCH_DISABLED_TOOLS at call time. (#297)
    _disabled_at_call = _get_disabled_tools() - _UNDISABLEABLE_TOOLS
    if name in _disabled_at_call:
        return [TextContent(type="text", text=json.dumps({
            "error": (
                f"Tool '{name}' is disabled via JDATAMUNCH_DISABLED_TOOLS. "
                f"Remove it from the env var to re-enable."
            )
        }, indent=2))]

    try:
        t0 = time.perf_counter()
        # Anti-loop detection for row-retrieval tools
        dataset_arg = arguments.get("dataset", "")
        if name in ("get_rows", "sample_rows", "aggregate", "search_data", "describe_dataset"):
            loop_warning = record_call(
                tool=name,
                dataset=dataset_arg,
                offset=arguments.get("offset", 0),
            )
        else:
            loop_warning = None

        if name == "index_local":
            result = await asyncio.to_thread(
                index_local,
                path=arguments["path"],
                name=arguments.get("name"),
                incremental=arguments.get("incremental", True),
                encoding=arguments.get("encoding"),
                delimiter=arguments.get("delimiter"),
                header_row=arguments.get("header_row", 0),
                sheet=arguments.get("sheet"),
                use_ai_summaries=arguments.get("use_ai_summaries", True),
                depth=arguments.get("depth", "standard"),
                storage_path=storage_path,
            )
        elif name == "index_repo":
            result = await index_repo(
                url=arguments["url"],
                incremental=arguments.get("incremental", True),
                github_token=arguments.get("github_token"),
                storage_path=storage_path,
            )
        elif name == "list_datasets":
            result = list_datasets(storage_path=storage_path)
        elif name == "list_repos":
            result = list_repos(storage_path=storage_path)

        elif name == "describe_dataset":
            result = describe_dataset(
                dataset=arguments["dataset"],
                columns=arguments.get("columns"),
                columns_offset=arguments.get("columns_offset", 0),
                storage_path=storage_path,
            )
        elif name == "describe_column":
            result = describe_column(
                dataset=arguments["dataset"],
                column=arguments["column"],
                top_n=arguments.get("top_n", 20),
                histogram_bins=arguments.get("histogram_bins", 10),
                redact=arguments.get("redact", True),
                redact_patterns=arguments.get("redact_patterns"),
                storage_path=storage_path,
            )
        elif name == "finalize_handoff":
            from . import handoff as _handoff_mod
            result = _handoff_mod.finalize_handoff(
                dataset=arguments["dataset"],
                task=arguments["task"],
                sections=arguments["sections"],
                evidence_refs=arguments["evidence_refs"],
                profile=arguments.get("profile", "general"),
                appendices=arguments.get("appendices"),
            )
        elif name == "search_data":
            result = search_data(
                dataset=arguments["dataset"],
                query=arguments["query"],
                search_scope=arguments.get("search_scope", "all"),
                max_results=arguments.get("max_results", 10),
                semantic=arguments.get("semantic", False),
                semantic_weight=arguments.get("semantic_weight"),
                semantic_only=arguments.get("semantic_only", False),
                storage_path=storage_path,
            )
        elif name == "get_rows":
            result = await asyncio.to_thread(
                get_rows,
                dataset=arguments["dataset"],
                filters=arguments.get("filters"),
                columns=arguments.get("columns"),
                order_by=arguments.get("order_by"),
                order_dir=arguments.get("order_dir", "asc"),
                limit=arguments.get("limit", 50),
                offset=arguments.get("offset", 0),
                redact=arguments.get("redact", True),
                redact_patterns=arguments.get("redact_patterns"),
                redact_skip_columns=arguments.get("redact_skip_columns"),
                storage_path=storage_path,
            )
        elif name == "aggregate":
            result = await asyncio.to_thread(
                aggregate,
                dataset=arguments["dataset"],
                aggregations=arguments["aggregations"],
                group_by=arguments.get("group_by"),
                filters=arguments.get("filters"),
                having=arguments.get("having"),
                order_by=arguments.get("order_by"),
                order_dir=arguments.get("order_dir", "desc"),
                limit=arguments.get("limit", 50),
                approximate=arguments.get("approximate", False),
                redact=arguments.get("redact", True),
                redact_patterns=arguments.get("redact_patterns"),
                redact_skip_columns=arguments.get("redact_skip_columns"),
                storage_path=storage_path,
            )
        elif name == "sample_rows":
            result = await asyncio.to_thread(
                sample_rows,
                dataset=arguments["dataset"],
                n=arguments.get("n", 5),
                method=arguments.get("method", "head"),
                columns=arguments.get("columns"),
                seed=arguments.get("seed"),
                redact=arguments.get("redact", True),
                redact_patterns=arguments.get("redact_patterns"),
                redact_skip_columns=arguments.get("redact_skip_columns"),
                storage_path=storage_path,
            )
        elif name == "delete_dataset":
            result = delete_dataset(
                dataset=arguments["dataset"],
                storage_path=storage_path,
            )
        elif name == "get_session_stats":
            result = get_session_stats(storage_path=storage_path)
            # Tool-surface schema receipt (v1.23.0, jcm v1.108.153 parity).
            # Advisory only — a failure here must never break the stats tool.
            try:
                result["result"]["tool_surface"] = _tool_surface_stats()
            except Exception:
                import logging
                logging.getLogger(__name__).debug(
                    "tool_surface stats failed", exc_info=True
                )
        elif name == "get_schema_drift":
            result = get_schema_drift(
                dataset_a=arguments["dataset_a"],
                dataset_b=arguments["dataset_b"],
                storage_path=storage_path,
            )
        elif name == "get_data_hotspots":
            result = get_data_hotspots(
                dataset=arguments["dataset"],
                top_n=arguments.get("top_n", 10),
                include_runtime=arguments.get("include_runtime", True),
                window_days=arguments.get("window_days", 30),
                storage_path=storage_path,
            )
        elif name == "get_correlations":
            result = await asyncio.to_thread(
                get_correlations,
                dataset=arguments["dataset"],
                min_abs_correlation=arguments.get("min_abs_correlation", 0.3),
                columns=arguments.get("columns"),
                top_n=arguments.get("top_n", 20),
                method=arguments.get("method", "pearson"),
                storage_path=storage_path,
            )
        elif name == "join_datasets":
            result = await asyncio.to_thread(
                join_datasets,
                dataset_a=arguments["dataset_a"],
                dataset_b=arguments["dataset_b"],
                join_column_a=arguments["join_column_a"],
                join_column_b=arguments["join_column_b"],
                join_type=arguments.get("join_type", "inner"),
                columns_a=arguments.get("columns_a"),
                columns_b=arguments.get("columns_b"),
                filters_a=arguments.get("filters_a"),
                filters_b=arguments.get("filters_b"),
                order_by=arguments.get("order_by"),
                order_dir=arguments.get("order_dir", "asc"),
                limit=arguments.get("limit", 50),
                offset=arguments.get("offset", 0),
                storage_path=storage_path,
            )
        elif name == "embed_dataset":
            result = await asyncio.to_thread(
                embed_dataset,
                dataset=arguments["dataset"],
                force=arguments.get("force", False),
                storage_path=storage_path,
            )
        elif name == "summarize_dataset":
            result = summarize_dataset_tool(
                dataset=arguments["dataset"],
                storage_path=storage_path,
            )
        elif name == "validate_index":
            result = validate_index(
                dataset=arguments["dataset"],
                storage_path=storage_path,
            )
        elif name == "get_dataset_history":
            result = get_dataset_history(
                dataset=arguments["dataset"],
                n=arguments.get("n", 10),
                storage_path=storage_path,
            )
        elif name == "get_dataset_health":
            result = get_dataset_health(
                dataset=arguments["dataset"],
                storage_path=storage_path,
            )
        elif name == "suggest_keys":
            result = suggest_keys(
                dataset=arguments["dataset"],
                storage_path=storage_path,
            )
        elif name == "suggest_joins":
            result = await asyncio.to_thread(
                suggest_joins,
                dataset=arguments["dataset"],
                storage_path=storage_path,
            )
        elif name == "get_distribution":
            result = await asyncio.to_thread(
                get_distribution,
                dataset=arguments["dataset"],
                column=arguments["column"],
                bins=arguments.get("bins", 20),
                storage_path=storage_path,
            )
        elif name == "plan_query":
            result = plan_query(
                dataset=arguments["dataset"],
                intent=arguments.get("intent", "summarize"),
                storage_path=storage_path,
            )
        elif name == "run_sql":
            result = await asyncio.to_thread(
                run_sql,
                sql=arguments["sql"],
                datasets=arguments["datasets"],
                limit=arguments.get("limit", 500),
                redact=arguments.get("redact", True),
                redact_patterns=arguments.get("redact_patterns"),
                redact_skip_columns=arguments.get("redact_skip_columns"),
                storage_path=storage_path,
            )
        elif name == "get_schema_impact":
            result = await asyncio.to_thread(
                get_schema_impact,
                dataset_id=arguments["dataset_id"],
                column=arguments["column"],
                kind=arguments.get("kind", "drop_column"),
                new_name=arguments.get("new_name"),
                new_type=arguments.get("new_type"),
                max_depth=arguments.get("max_depth", 3),
                window_days=arguments.get("window_days", 30),
                storage_path=storage_path,
            )
        elif name == "check_column_drop_safe":
            result = await asyncio.to_thread(
                check_column_drop_safe,
                dataset_id=arguments["dataset_id"],
                column=arguments["column"],
                window_days=arguments.get("window_days", 30),
                storage_path=storage_path,
            )
        elif name == "find_unused_columns":
            result = await asyncio.to_thread(
                find_unused_columns,
                dataset_id=arguments["dataset_id"],
                window_days=arguments.get("window_days", 30),
                min_calls=arguments.get("min_calls", 0),
                exclude_pk=arguments.get("exclude_pk", True),
                exclude_audit=arguments.get("exclude_audit", True),
                storage_path=storage_path,
            )
        elif name == "ingest_sql_log":
            result = await asyncio.to_thread(
                ingest_sql_log_file,
                file_path=arguments["file_path"],
                source=arguments.get("source", "auto"),
                redact=arguments.get("redact", True),
                max_rows=arguments.get("max_rows", 100000),
                storage_path=storage_path,
            )
        elif name == "find_similar_columns":
            result = await asyncio.to_thread(
                find_similar_columns,
                datasets=arguments.get("datasets"),
                min_score=arguments.get("min_score", 0.5),
                top_n=arguments.get("top_n", 50),
                same_type_only=arguments.get("same_type_only", False),
                storage_path=storage_path,
            )
        elif name == "data_health_radar":
            result = await asyncio.to_thread(
                data_health_radar,
                dataset=arguments["dataset"],
                include_runtime=arguments.get("include_runtime", True),
                window_days=arguments.get("window_days", 30),
                storage_path=storage_path,
            )
        elif name == "diff_data_health_radar":
            result = diff_data_health_radar(
                baseline=arguments["baseline"],
                current=arguments["current"],
            )
        elif name == "get_redaction_log":
            result = await asyncio.to_thread(
                get_redaction_log,
                dataset_id=arguments["dataset_id"],
                source=arguments.get("source"),
                since_days=arguments.get("since_days", 30),
                storage_path=storage_path,
            )
        elif name == "tune_weights":
            result = tune_weights(
                dataset=arguments.get("dataset"),
                set_weights=arguments.get("set_weights"),
                reset=arguments.get("reset", False),
                storage_path=storage_path,
            )
        elif name == "check_embedding_drift":
            result = await asyncio.to_thread(
                check_embedding_drift,
                force=arguments.get("force", False),
                threshold=arguments.get("threshold", 0.05),
                storage_path=storage_path,
            )
        elif name == "analyze_perf":
            result = analyze_perf(
                window=arguments.get("window", "session"),
                top=arguments.get("top", 20),
                tool=arguments.get("tool"),
                storage_path=storage_path,
            )
        elif name == "jdatamunch_guide":
            from . import __version__ as _ver
            result = {
                "version": _ver,
                "content": _generate_data_md_snippet(),
            }
        else:
            result = {"error": f"Unknown tool: {name}"}

        _truncation = None
        if isinstance(result, dict) and "error" not in result:
            result = enforce_budget(result, name)
            # ⚠⚠ `enforce_budget` records what it dropped in `_meta.truncation`,
            # and this server strips `_meta` by DEFAULT — so the notice was
            # deleted before the caller saw it. Measured: 600 rows trimmed to
            # 104, disclosure gone, response indistinguishable from a complete
            # one. A silently shortened answer is worse than a refused one
            # because the caller cannot tell. Captured here and re-attached
            # TOP-LEVEL after filtering, the same treatment the absence ref and
            # ignored_arguments already get for the same reason.
            _truncation = (result.get("_meta") or {}).get("truncation")
            if loop_warning:
                result.setdefault("_meta", {})["loop_warning"] = loop_warning

        # v1.26.0: absence evidence (#377 phase 3). Record a search_data verdict
        # so a handoff can cite a zero-result scan as proof. Read BEFORE the
        # meta_fields filter (default strips _meta); the citable ref is
        # re-attached AFTER filtering, below, like the budget block. jData's
        # index models no freshness, so the rendered proof discloses that in
        # band rather than silently claiming a fresh scan (DISCLOSE decision).
        # v1.29.0: argument contract (suite parity with jcm v1.108.175). Every
        # tool reads its arguments key-by-key, so a misspelled parameter is
        # dropped in silence and the call that runs is not the call that was
        # asked for. Degrade an `absent` verdict here, while `_meta.verdict`
        # still exists — MUST run before the absence block below so the
        # "only `absent` proves absence" check does the refusing. The visible
        # disclosure is attached AFTER meta_fields filtering (see `disclose`).
        _ignored_args: list = []
        try:
            from .tools import _arg_contract
            _ignored_args = _arg_contract.unrecognized_keys(
                arguments, _declared_arg_keys(name)
            )
            if _ignored_args:
                _arg_contract.degrade_absent_verdict(result, _ignored_args)
        except Exception:
            _ignored_args = []

        _absence_ref = None
        _absence_blocked = None
        if (
            name == "search_data"
            and isinstance(result, dict)
            and "error" not in result
        ):
            try:
                from . import handoff as _handoff_absence
                _v = (result.get("_meta") or {}).get("verdict")
                if isinstance(_v, dict):
                    _ref, _why = _handoff_absence.note_absence(
                        name, arguments.get("dataset"), arguments.get("query"), _v
                    )
                    if _ref:
                        _absence_ref = _ref
                    elif _why:
                        _absence_blocked = _why
            except Exception:
                pass

        if isinstance(result, dict):
            result.setdefault("_meta", {})["powered_by"] = (
                "jdatamunch-mcp by jgravelle · https://github.com/jgravelle/jdatamunch-mcp"
            )

            # meta_fields filtering (matches jcodemunch-mcp behaviour)
            from .config import get_meta_fields
            meta_fields = get_meta_fields()
            if meta_fields == []:
                result.pop("_meta", None)
            elif isinstance(meta_fields, list):
                existing_meta = result.pop("_meta", {})
                _meta: dict = {}
                if "powered_by" in meta_fields:
                    _meta["powered_by"] = existing_meta.get("powered_by", "")
                for field in meta_fields:
                    if field in existing_meta:
                        _meta[field] = existing_meta[field]
                if _meta:
                    result["_meta"] = _meta

        # Truncation disclosure, re-attached AFTER meta_fields filtering.
        # Top-level for the same reason `ignored_arguments` is: a notice the
        # default config deletes is not a notice.
        try:
            if _truncation and isinstance(result, dict):
                result["truncated"] = _truncation
                result["truncated_note"] = (
                    "This response was shortened to fit the token budget and is "
                    "NOT complete. Narrow the query, or raise "
                    "JDATAMUNCH_MAX_RESPONSE_TOKENS, before reading the result "
                    "as the full set."
                )
        except Exception:
            pass

        try:
            _perf.record(
                name,
                (time.perf_counter() - t0) * 1000.0,
                ok=not (isinstance(result, dict) and "error" in result),
                storage_path=storage_path,
            )
        except Exception:
            pass

        # v1.24.0: session retrieval record for handoff attestation
        # (jdatamunch.handoff/v1 — suite parity with jcm #374). Records the
        # column ids / dataset names this server actually served, so
        # finalize_handoff can attest evidence_refs against real retrieval.
        try:
            from . import handoff as _handoff_record
            if isinstance(result, dict) and "error" not in result:
                if name == "search_data":
                    _handoff_record.note_served_rows(
                        result.get("result") or [], dataset=arguments.get("dataset")
                    )
                elif name in ("describe_dataset", "sample_rows", "get_rows"):
                    _handoff_record.note_served_rows([], dataset=arguments.get("dataset"))
                elif name == "describe_column":
                    _handoff_record.note_served_column(
                        arguments.get("dataset"), arguments.get("column")
                    )
        except Exception:
            pass

        # v1.26.0: re-attach the absence token AFTER meta_fields filtering, so
        # it survives the token-efficient default (_meta stripped) — same shape
        # as jdoc's absence_evidence block. An `absent` scan hands the agent a
        # ref to cite; an absent-but-not-citable scan says so in band, with the
        # reason, instead of offering a token that would fail at finalize time.
        # v1.29.0: the ignored-argument disclosure rides TOP-LEVEL and is
        # attached AFTER meta_fields filtering, because `_meta` is stripped
        # entirely by default — a notice placed there would be deleted before
        # the agent saw it (same call as `empty`/`hint` in v1.28.0).
        if _ignored_args and isinstance(result, dict):
            try:
                from .tools import _arg_contract
                _arg_contract.disclose(result, _ignored_args)
            except Exception:
                pass

        if isinstance(result, dict) and "error" not in result:
            if _absence_ref:
                result.setdefault("_meta", {})["absence_evidence"] = {"ref": _absence_ref}
            elif _absence_blocked:
                result.setdefault("_meta", {})["absence_evidence"] = {
                    "citable": False,
                    "blocked_by": _absence_blocked,
                }

        # v1.21.0: advisory session budget (suite parity with jcm). Attached
        # AFTER meta_fields filtering so the warning survives the
        # token-efficient default (_meta stripped). Never blocks.
        try:
            from .storage.token_tracker import budget_status as _budget_status
            _b = _budget_status()
            if _b is not None and _b["state"] in ("approaching", "over") and isinstance(result, dict):
                result.setdefault("_meta", {})["budget"] = _b
        except Exception:
            pass

        _text = json.dumps(result, indent=2)
        try:
            from .storage.token_tracker import record_response_text as _rrt
            _rrt(_text)
        except Exception:
            pass
        return [TextContent(type="text", text=_text)]

    except Exception as e:
        try:
            _perf.record(name, (time.perf_counter() - t0) * 1000.0, ok=False, storage_path=storage_path)
        except Exception:
            pass
        print(traceback.format_exc(), file=sys.stderr)
        return [TextContent(type="text", text=json.dumps({"error": str(e)}, indent=2))]


async def run_server():
    """Run the MCP server."""
    import anyio

    from jdatamunch_mcp import __version__
    from jdatamunch_mcp.stdio_guard import claim_stdout
    from mcp.server.stdio import stdio_server

    # Suite parity with jdoc#110. Take the real stdout for JSON-RPC and point
    # fd 1 at stderr BEFORE anything else runs, so no library, thread or child
    # process can reach the framed stream. `embeddings.py` builds a
    # SentenceTransformer inside `embed_dataset`, and a first embed on a
    # machine without the model cached downloads it mid-request.
    _private_stdout, _stdout_swapped = claim_stdout()

    print(
        f"jdatamunch-mcp {__version__} by jgravelle · https://github.com/jgravelle/jdatamunch-mcp",
        file=sys.stderr,
    )
    if not _stdout_swapped:
        # ⚠ Worth saying out loud: this is the configuration where a stray
        # library write can still corrupt a response.
        print(
            "[jdatamunch-mcp] could not isolate stdout for JSON-RPC; library "
            "output on stdout may corrupt framing",
            file=sys.stderr,
        )

    _stdout_arg = (
        anyio.wrap_file(_private_stdout) if _private_stdout is not None else None
    )
    async with stdio_server(stdout=_stdout_arg) as (read_stream, write_stream):
        await server.run(
            read_stream,
            write_stream,
            server.create_initialization_options(),
        )


def _force_utf8_stdio() -> None:
    """Make CLI output UTF-8 regardless of the platform locale.

    Suite parity with jcodemunch-mcp v1.108.262 and jdocmunch-mcp.

    On Windows, `sys.stdout` is the **console** stream (already UTF-8) when
    attached to a terminal and the **locale** stream (cp1252) when piped or
    redirected. Output containing any non-ASCII character then goes out as
    cp1252 bytes the moment anything consumes it, and a character cp1252 cannot
    encode at all crashes the command outright.

    ⚠ **This repo has no such output today**, which is exactly why the fix
    belongs at the entry point rather than in the strings: the defect arrives
    with the next character someone adds, and it arrives as a crash on a user's
    machine that nobody can reproduce interactively. jcm carried it for an
    unknown number of releases before a pipe revealed it.

    ⚠ The MCP stdio transport is unaffected: it wraps `sys.stdout.buffer` in
    its own TextIOWrapper and never reads the text layer reconfigured here.

    ⚠ `errors="replace"` is deliberate -- filesystem paths can carry
    surrogates from a `surrogateescape` decode, which raise even under UTF-8.

    ⚠ `PYTHONIOENCODING` is honoured as an explicit opt-out.
    """
    if os.environ.get("PYTHONIOENCODING"):
        return
    for stream_name in ("stdout", "stderr"):
        stream = getattr(sys, stream_name, None)
        reconfigure = getattr(stream, "reconfigure", None)
        if reconfigure is None:
            continue
        current = (getattr(stream, "encoding", "") or "").lower().replace("-", "")
        if current in ("utf8", "utf8mb4"):
            continue
        try:
            reconfigure(encoding="utf-8", errors="replace")
        except (ValueError, OSError):
            pass


def main(argv: Optional[list] = None):
    """Main entry point."""
    _force_utf8_stdio()
    from .security import verify_package_integrity
    verify_package_integrity()

    parser = argparse.ArgumentParser(
        prog="jdatamunch-mcp",
        description="Run the jDataMunch MCP stdio server.",
    )
    parser.parse_args(argv)

    # Import the local embedding backend here, on the main thread, before the
    # stdio loop starts. Deferring it to the first embed call runs it inside an
    # asyncio.to_thread worker, which deadlocks on the Windows loader lock
    # (issue #3). No-op unless a sentence-transformers model is configured.
    from .embeddings import warm_up_provider
    warm_up_provider()

    asyncio.run(run_server())


if __name__ == "__main__":
    main()
