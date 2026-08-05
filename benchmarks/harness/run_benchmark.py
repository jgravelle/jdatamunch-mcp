#!/usr/bin/env python3
"""jdatamunch-mcp benchmark harness.

Measures real (tiktoken cl100k_base) token counts for the jdatamunch
retrieval workflow vs a "read entire file" baseline on identical tasks.

Usage:
    python benchmarks/harness/run_benchmark.py <path/to/file.csv> [...]
    python benchmarks/harness/run_benchmark.py --out benchmarks/results.md /c/MCPs/crime.csv

Methodology
-----------
Baseline:   the raw source file tokenized in full.
            This is the minimum tokens a "read everything first" agent would use.

jDataMunch: for each task query —
              1. call describe_dataset                  -> count JSON response tokens
              2. call describe_column on the key column -> count response tokens
            Total = describe_dataset response + describe_column response.

Tokenizer:  tiktoken cl100k_base (GPT-4 / Claude family ballpark; consistent
            across runs regardless of model).
"""

import argparse
import json
import sys
import time
import tempfile
from pathlib import Path
from typing import Optional

# ---------------------------------------------------------------------------
# Bootstrap: add src/ to path so we can import jdatamunch_mcp directly
# ---------------------------------------------------------------------------
_REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(_REPO_ROOT / "src"))

try:
    import tiktoken
except ImportError:
    sys.exit("tiktoken not found — run: pip install tiktoken")

from jdatamunch_mcp.tools.index_local import index_local
from jdatamunch_mcp.tools.describe_dataset import describe_dataset
from jdatamunch_mcp.tools.describe_column import describe_column
from jdatamunch_mcp.tools.sample_rows import sample_rows
from jdatamunch_mcp.tools.search_data import search_data

# ---------------------------------------------------------------------------
# Task corpus — representative analytical questions for a crime dataset
# ---------------------------------------------------------------------------

TASKS = [
    {
        "query": "schema overview",
        "description": "What columns does this dataset have and what are their types?",
        "workflow": "describe_dataset",
        "column": None,
    },
    {
        "query": "crime type distribution",
        "description": "What are the most common crime types?",
        "workflow": "describe_dataset+describe_column",
        "column": "Crm Cd Desc",
    },
    {
        "query": "temporal range",
        "description": "What is the date range of crimes in this dataset?",
        "workflow": "describe_dataset+describe_column",
        "column": "DATE OCC",
    },
    {
        "query": "victim demographics",
        "description": "What are the victim age, sex, and descent distributions?",
        "workflow": "describe_dataset+describe_column",
        "column": "Vict Age",
    },
    {
        "query": "geographic coverage",
        "description": "What areas and coordinates are covered in this dataset?",
        "workflow": "describe_dataset+describe_column",
        "column": "AREA NAME",
    },
]

TOKENIZER = "cl100k_base"

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_enc = tiktoken.get_encoding(TOKENIZER)


def count_tokens(text: str) -> int:
    return len(_enc.encode(text))


def _serialize(obj) -> str:
    return json.dumps(obj, separators=(",", ":"), default=str)


# ---------------------------------------------------------------------------
# Baseline measurement — tokenize the raw source file
# ---------------------------------------------------------------------------

def measure_baseline(csv_path: str) -> dict:
    """Count tokens in the raw file. This is the minimum for a naive agent."""
    p = Path(csv_path)
    file_bytes = p.stat().st_size
    t0 = time.perf_counter()
    content = p.read_text(encoding="utf-8", errors="replace")
    read_ms = (time.perf_counter() - t0) * 1000
    tokens = count_tokens(content)
    return {"tokens": tokens, "bytes": file_bytes, "read_ms": round(read_ms, 1)}


# ---------------------------------------------------------------------------
# Index the dataset (once per benchmark run)
# ---------------------------------------------------------------------------

def ensure_indexed(csv_path: str, storage_path: str, dataset_id: str) -> dict:
    """Index csv_path, returning the index result."""
    t0 = time.perf_counter()
    result = index_local(
        path=csv_path,
        name=dataset_id,
        incremental=True,
        use_ai_summaries=False,
        storage_path=storage_path,
    )
    elapsed = time.perf_counter() - t0
    if "error" in result:
        return result
    result["_index_elapsed_s"] = round(elapsed, 1)
    return result


# ---------------------------------------------------------------------------
# jDataMunch workflow measurement — one task
# ---------------------------------------------------------------------------

def measure_jdatamunch(dataset_id: str, task: dict, storage_path: str) -> dict:
    """
    jDataMunch workflow for one task:
      1. describe_dataset  -> count JSON response tokens
      2. describe_column (if task specifies a column) -> count tokens
    """
    t0 = time.perf_counter()
    ds_result = describe_dataset(dataset=dataset_id, storage_path=storage_path)
    describe_ms = (time.perf_counter() - t0) * 1000

    if "error" in ds_result:
        return {"error": ds_result["error"]}

    ds_tokens = count_tokens(_serialize(ds_result))

    col_tokens = 0
    col_ms = 0.0
    col_name = task.get("column")
    if col_name and "describe_column" in task.get("workflow", ""):
        t1 = time.perf_counter()
        col_result = describe_column(
            dataset=dataset_id,
            column=col_name,
            storage_path=storage_path,
        )
        col_ms = (time.perf_counter() - t1) * 1000
        if "error" not in col_result:
            col_tokens = count_tokens(_serialize(col_result))

    total = ds_tokens + col_tokens
    return {
        "tokens": total,
        "describe_dataset_tokens": ds_tokens,
        "describe_column_tokens": col_tokens,
        "describe_ms": round(describe_ms, 1),
        "col_ms": round(col_ms, 1),
        "column": col_name,
    }


# ---------------------------------------------------------------------------
# Full benchmark for one dataset file
# ---------------------------------------------------------------------------

def benchmark_file(csv_path: str, storage_path: str) -> dict:
    p = Path(csv_path)
    if not p.exists():
        return {"error": f"File not found: {csv_path}"}

    dataset_id = p.stem.lower().replace(" ", "-")
    print(f"  indexing {p.name} ...", end=" ", flush=True)
    idx = ensure_indexed(csv_path, storage_path, dataset_id)
    if "error" in idx:
        print(f"FAILED: {idx['error']}")
        return {"error": idx["error"], "file": p.name}
    elapsed_s = idx.get("_index_elapsed_s", 0)
    result_meta = idx.get("result", {})
    rows = result_meta.get("rows", 0)
    cols = result_meta.get("columns", 0)
    print(f"done ({elapsed_s}s, {rows:,} rows, {cols} cols)", flush=True)

    print(f"  measuring baseline (tokenizing raw file) ...", end=" ", flush=True)
    baseline = measure_baseline(csv_path)
    print(f"done ({baseline['tokens']:,} tokens)", flush=True)

    task_rows = []
    for task in TASKS:
        print(f"    task: {task['query']} ...", end=" ", flush=True)
        jdm = measure_jdatamunch(dataset_id, task, storage_path)
        if "error" in jdm:
            print(f"ERROR: {jdm['error']}")
            task_rows.append({"query": task["query"], "error": jdm["error"]})
            continue
        reduction_pct = (1 - jdm["tokens"] / baseline["tokens"]) * 100 if baseline["tokens"] > 0 else 0
        ratio = baseline["tokens"] / jdm["tokens"] if jdm["tokens"] > 0 else float("inf")
        print(f"{jdm['tokens']:,} tokens  ({ratio:.1f}x reduction)")
        task_rows.append({
            "query": task["query"],
            "description": task["description"],
            "baseline_tokens": baseline["tokens"],
            "jdatamunch_tokens": jdm["tokens"],
            # 4dp, NOT 1dp. At these ratios 1dp rounds 99.9965 to "100.0",
            # which asserts total elimination rather than near-total.
            "reduction_pct": round(reduction_pct, 4),
            "ratio": round(ratio, 1),
            "describe_dataset_tokens": jdm["describe_dataset_tokens"],
            "describe_column_tokens": jdm["describe_column_tokens"],
            "describe_ms": jdm["describe_ms"],
            "col_ms": jdm["col_ms"],
            "column": jdm["column"],
        })

    return {
        "file": p.name,
        "dataset": dataset_id,
        "file_bytes": baseline["bytes"],
        "baseline_tokens": baseline["tokens"],
        "rows": rows,
        "cols": cols,
        "tasks": task_rows,
    }


# ---------------------------------------------------------------------------
# Markdown rendering
# ---------------------------------------------------------------------------

def render_markdown(results: list[dict], tokenizer: str) -> str:
    lines = []
    lines.append("# jdatamunch-mcp — Token Efficiency Benchmark")
    lines.append("")
    lines.append(f"**Tokenizer:** `{tokenizer}` (tiktoken)  ")
    lines.append(f"**Workflow:** `describe_dataset` + `describe_column` (per task)  ")
    lines.append(f"**Baseline:** full raw source file tokenized (minimum for \"read everything\" agent)  ")
    lines.append(f"**AI summaries:** disabled (clean retrieval-only measurement)  ")
    lines.append("")

    grand_baseline = 0
    grand_jdm = 0
    grand_tasks = 0

    for res in results:
        if "error" in res:
            lines.append(f"## {res.get('file', '?')} — ERROR")
            lines.append(f"> {res['error']}")
            lines.append("")
            continue

        lines.append(f"## {res['file']}")
        lines.append("")
        lines.append("| Metric | Value |")
        lines.append("|--------|-------|")
        lines.append(f"| Rows | **{res['rows']:,}** |")
        lines.append(f"| Columns | **{res['cols']}** |")
        lines.append(f"| File size | **{res['file_bytes'] / 1_000_000:.1f} MB** |")
        lines.append(f"| Baseline tokens (full file) | **{res['baseline_tokens']:,}** |")
        lines.append("")

        lines.append("| Task | Baseline&nbsp;tokens | jDataMunch&nbsp;tokens | Reduction | Ratio |")
        lines.append("|------|---------------------:|-----------------------:|----------:|------:|")

        valid_tasks = [t for t in res["tasks"] if "error" not in t]
        for t in valid_tasks:
            lines.append(
                f"| `{t['query']}` "
                f"| {t['baseline_tokens']:,} "
                f"| {t['jdatamunch_tokens']:,} "
                f"| **{t['reduction_pct']:.4f}%** "
                f"| {t['ratio']}x |"
            )
            grand_jdm += t["jdatamunch_tokens"]
            grand_baseline += t["baseline_tokens"]
            grand_tasks += 1

        if valid_tasks:
            avg_reduction = sum(t["reduction_pct"] for t in valid_tasks) / len(valid_tasks)
            avg_ratio = sum(t["ratio"] for t in valid_tasks) / len(valid_tasks)
            lines.append(
                f"| **Average (mean of per-task ratios)** | — | — "
                f"| **{avg_reduction:.4f}%** "
                f"| **{avg_ratio:.1f}x** |"
            )
        lines.append("")

        # Detail breakdown
        lines.append("<details><summary>Token breakdown by tool call + latency</summary>")
        lines.append("")
        lines.append("| Task | describe_dataset | describe_column | Column | Latency&nbsp;ms |")
        lines.append("|------|----------------:|----------------:|--------|----------------:|")
        for t in valid_tasks:
            col_display = t["column"] or "—"
            latency = t["describe_ms"] + t["col_ms"]
            lines.append(
                f"| `{t['query']}` "
                f"| {t['describe_dataset_tokens']:,} "
                f"| {t['describe_column_tokens']:,} "
                f"| {col_display} "
                f"| {latency:.0f} |"
            )
        lines.append("")
        lines.append("</details>")
        lines.append("")

    # Grand summary
    if grand_tasks > 0:
        grand_reduction = (1 - grand_jdm / grand_baseline) * 100
        grand_ratio = grand_baseline / grand_jdm
        lines.append("---")
        lines.append("")
        lines.append("## Grand Summary")
        lines.append("")
        lines.append("| | Tokens |")
        lines.append("|--|-------:|")
        lines.append(
            f"| Baseline total ({grand_tasks} task-runs, assumes one full "
            f"read PER TASK) | {grand_baseline:,} |"
        )
        lines.append(f"| jDataMunch total | {grand_jdm:,} |")
        lines.append(f"| **Reduction** | **{grand_reduction:.4f}%** |")
        lines.append(f"| **Ratio (of totals)** | **{grand_ratio:.1f}x** |")
        lines.append(
            f"| Single-read baseline (one read, {grand_tasks} tasks) | "
            f"{grand_baseline // grand_tasks:,} |"
        )
        lines.append("")
        lines.append(
            f"> Measured with tiktoken `{tokenizer}`. "
            "Baseline = full raw file tokenized. "
            "jDataMunch = describe_dataset + describe_column per task. "
            "AI summaries disabled. The grand baseline assumes the file is "
            "re-read for every task; a single session reads it once, which is "
            "the single-read row. Quote which one you mean."
        )

    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("files", nargs="+", metavar="FILE", help="CSV or Excel file(s) to benchmark")
    parser.add_argument("--out", metavar="FILE", help="write markdown results to FILE")
    parser.add_argument("--json", metavar="FILE", dest="json_out", help="write raw JSON results to FILE")
    parser.add_argument(
        "--storage",
        metavar="DIR",
        default=None,
        help="custom index storage directory (default: ~/.data-index/)",
    )
    args = parser.parse_args()

    storage_path = args.storage or str(Path.home() / ".data-index" / "benchmark")

    print(f"jdatamunch-mcp benchmark harness  |  tokenizer: {TOKENIZER}", flush=True)
    print(f"Files: {', '.join(args.files)}", flush=True)
    print(f"Tasks: {len(TASKS)} queries × {len(args.files)} files = {len(TASKS) * len(args.files)} measurements", flush=True)
    print(f"Storage: {storage_path}", flush=True)
    print()

    results = []
    for f in args.files:
        print(f"Benchmarking: {f}", flush=True)
        t0 = time.perf_counter()
        res = benchmark_file(f, storage_path)
        elapsed = time.perf_counter() - t0
        if "error" in res:
            print(f"  ERROR: {res['error']}")
        else:
            valid = [t for t in res["tasks"] if "error" not in t]
            avg_r = sum(t["reduction_pct"] for t in valid) / len(valid) if valid else 0
            avg_ratio = sum(t["ratio"] for t in valid) / len(valid) if valid else 0
            print(f"  done ({elapsed:.1f}s total)  avg reduction {avg_r:.1f}%  ({avg_ratio:.1f}x)")
        results.append(res)
        print()

    md = render_markdown(results, TOKENIZER)
    print(md)

    if args.out:
        Path(args.out).write_text(md, encoding="utf-8")
        print(f"\nResults written to: {args.out}")

    if args.json_out:
        Path(args.json_out).write_text(json.dumps(results, indent=2, default=str), encoding="utf-8")
        print(f"JSON written to: {args.json_out}")


if __name__ == "__main__":
    main()
