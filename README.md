<!-- mcp-name: io.github.jgravelle/jdatamunch-mcp -->

# jDataMunch MCP: Tabular Data Retrieval for AI Agents

**jDataMunch is an MCP server for coding agents and analysts that answers questions about CSV, Excel, Parquet, and JSONL files without pasting the rows into the context window.**

Index a dataset once, then retrieve column profiles, filtered rows, server-side aggregations, and cross-dataset joins — so a million-row file costs thousands of tokens instead of millions.

[**Install**](#install) · [**Quickstart**](#quickstart) · [**Benchmarks**](benchmarks/results.md) · [**Commercial licensing**](#licensing-and-commercial-use)

[![PyPI version](https://img.shields.io/pypi/v/jdatamunch-mcp)](https://pypi.org/project/jdatamunch-mcp/)
[![PyPI - Python Version](https://img.shields.io/pypi/pyversions/jdatamunch-mcp)](https://pypi.org/project/jdatamunch-mcp/)
![License](https://img.shields.io/badge/license-dual--use-blue)
![MCP](https://img.shields.io/badge/MCP-compatible-purple)
![Local-first](https://img.shields.io/badge/local--first-yes-brightgreen)

**Free for personal use.** Commercial use requires a paid license — [terms below](#licensing-and-commercial-use).

---

## Why jDataMunch?

**The problem.** The default way an agent explores a spreadsheet is to paste it into the prompt. A 255 MB CSV with a million rows costs roughly **111 million tokens** that way, and the model still has to reason through a million rows to answer "what columns are in here?"

**The mechanism.** jDataMunch profiles the file once — columns, types, cardinality, null rates, distributions — and stores that locally. Queries then run *against the data*, not against a copy of it in the prompt: filters, aggregations, and joins execute server-side and return only results.

**The outcome.** Orientation questions are answered from the profile. Row-level questions return matching rows. The raw file never enters the context window.

---

## Evidence

Measured on a real public dataset, not estimated. Full harness and per-query results in [`benchmarks/`](benchmarks/).

> **Corpus:** LAPD crime records — 1,004,894 rows, 28 columns, 255 MB
> **Baseline:** 111,028,360 tokens to paste the raw file
> **`describe_dataset`:** ~3,849 tokens — a **25,333× reduction**
> [Methodology & harness](benchmarks/METHODOLOGY.md) · [Full results](benchmarks/results.md)

| Task | Without jDataMunch | With jDataMunch | Reduction |
|------|--------------------|-----------------|-----------|
| Understand a dataset's shape | Paste 111M tokens | `describe_dataset` → ~3,849 tokens | ~25,000× |
| Schema + one column deep-dive | Paste 111M tokens | `describe_dataset` + `describe_column` → ~4,400 tokens | ~25,000× |
| Filter to matching rows | Load all 1M rows | `get_rows` with filters → matching rows only | ~99%+ |
| Count by category | Return all rows, aggregate in the model | `aggregate(group_by=[...])` → 21 rows | ~99.9% |

**What these numbers are and are not.** The reduction is measured against pasting the complete file, which is what a naive agent does and what the token bill reflects. It is *not* measured against a competent human analyst who would never paste a 255 MB CSV. The multiple scales with file size: a 200-row spreadsheet has far less to save, and the honest figure there is closer to "no meaningful difference."

Typical latencies from the same run: `describe_column` on a single column, 22–33 ms and ~600 tokens.

---

## Install

**Requirements:** Python 3.10+, any MCP-compatible client.

```bash
pip install jdatamunch-mcp
```

> **Ubuntu 24.04+ / Debian 12+:** system Python is externally managed (PEP 668). Use `pipx install jdatamunch-mcp` or `uv tool install jdatamunch-mcp`.

**Claude Code setup:**

```bash
claude mcp add jdatamunch uvx jdatamunch-mcp
```

Restart Claude Code, then type `/mcp` — `jdatamunch` should be listed. That listing is the verification step: `jdatamunch-mcp` is a stdio MCP server with no CLI subcommands, so running it directly just waits on stdin.

Additional file formats (Excel, Parquet) pull optional extras — see [supported formats](#supported-formats). Full per-client setup, including Claude Desktop, Cursor, and Windsurf: [QUICKSTART.md](QUICKSTART.md).

---

## Quickstart

**Assumes:** jDataMunch installed and registered with your client, and a CSV to hand.

Everything happens inside your agent — there is no separate indexing command. Ask it to index:

> Using jdatamunch, index ./data/sales.csv

It calls `index_local`, which returns the dataset name, row and column counts, and detected types. Then:

> Using jdatamunch, describe the sales dataset and tell me which columns have missing values.

The agent calls `describe_dataset`, which returns column names, inferred types, cardinality, null rates, and sample values — without reading a single row into context. `_meta.tokens_saved` reports what that cost against loading the file.

**Next step:** `describe_column` for a distribution on one column, or `aggregate` to group and count server-side.

---

## What you can do

- **Orient in a dataset you have never seen.** `describe_dataset`, `describe_column`, `sample_rows`, `get_distribution`, `get_correlations`.
- **Query without loading rows.** `get_rows` with filters, `aggregate` with `group_by`, `run_sql`, and `plan_query` to preview cost before running.
- **Work across datasets.** `suggest_joins`, `suggest_keys`, `join_datasets`.
- **Find data-quality problems.** `get_dataset_health`, `data_health_radar`, `get_data_hotspots` (null rate, cardinality anomalies, outlier spread), `get_schema_drift`, `find_unused_columns`.
- **Preflight schema changes.** `check_column_drop_safe` and `get_schema_impact` before you drop or rename.
- **Search semantically.** `search_data` and `find_similar_columns` when you know what you mean but not what it is called.
- **Index from GitHub.** `index_repo` pulls CSV, Excel, Parquet, and JSONL straight from a repository, incrementally by HEAD SHA, private repos included.

39 tools in total. Full reference: [USER-MANUAL.md](USER-MANUAL.md).

---

## How it works

Everything runs locally. The dataset is profiled on your machine and the index is stored on your machine; no hosted service is involved in indexing or querying.

```text
data.csv ──► profiler ──► column stats + local index
                                    │
              MCP client ◄── query ─┘   (filters, aggregates, joins
                                         execute server-side)
```

Aggregations and filters run against the stored data rather than being simulated in the model, which is why the row count barely affects the token cost of an answer. Sampling-based statistics report their error bounds (roughly 2% standard error) rather than presenting an estimate as exact.

---

## Supported formats

| Format | Extensions | Install extra |
|---|---|---|
| CSV / TSV | `.csv`, `.tsv` | built in |
| JSON Lines | `.jsonl` | built in |
| Excel | `.xlsx`, `.xls` | `pip install "jdatamunch-mcp[excel]"` |
| Parquet | `.parquet` | `pip install "jdatamunch-mcp[parquet]"` |

---

## Security and privacy

Local-first. Your data is profiled and indexed on your machine and is not uploaded.

The base package's only default network behavior is an anonymous savings counter — a random ID plus aggregate token counts. **No data, no column names, no file paths, no PII.** Opt out completely:

```bash
JDATAMUNCH_SHARE_SAVINGS=0
```

`index_repo` reaches GitHub only when you invoke it, using a token you supply. Embedding providers are called only when you configure one. There is no scheduler and no background reporting.

Full detail, including what each optional extra pulls in: [SECURITY.md](SECURITY.md).

---

## Limitations

- **Savings scale with file size.** On a small spreadsheet the difference is negligible; the benchmark figures come from a 255 MB file.
- **Sampled statistics are sampled.** Distribution and correlation figures on very large files carry a stated error bound rather than being exact.
- **Excel and Parquet need optional extras**, which pull additional dependencies.
- **A default `describe_column` will not be labelled `offloadable`.** jDataMunch does not assert index freshness it cannot prove, so the cheap freshness reading answers `unknown` and the annotation fails closed. That is deliberate — see [the annotation section](#offloadable-work-annotation).
- **jDataMunch does not read code or prose.** Code symbols belong to [jcodemunch-mcp](https://github.com/jgravelle/jcodemunch-mcp); documentation sections to [jdocmunch-mcp](https://github.com/jgravelle/jdocmunch-mcp).

---

## Offloadable-work annotation

`JMUNCH_OFFLOADABLE=1` (suite-wide) or `JDATAMUNCH_OFFLOADABLE=1` (this server only) makes `describe_column` carry an advisory `_meta.offloadable` block marking whether the answer is simple and self-contained enough to hand to a cheaper model.

**It is a label and nothing else.** jDataMunch never calls another model, never routes the request, and never touches your API keys. Off by default; you decide what happens next.

The verdict is tri-state and reason-coded: `not_evaluated` ("we did not assess it") is not `not_offloadable` ("this is not simple work"). It fails closed — any unknown bearing on the answer disqualifies, because a false `offloadable` sends real work to a model that will confabulate over the gap. `verify_with` names the call that would adjudicate a cheaper model's answer.

Identical field contract across all three jMunch servers, with a pinned contract digest that fails the build in any one of them that drifts.

---

## Documentation

| Doc | What it covers |
|-----|----------------|
| [QUICKSTART.md](QUICKSTART.md) | Zero-to-indexed in three steps |
| [USER-MANUAL.md](USER-MANUAL.md) | Full guide for analysts, ops, and non-developers |
| [SECURITY.md](SECURITY.md) | Data handling, network behavior, vulnerability reporting |
| [benchmarks/METHODOLOGY.md](benchmarks/METHODOLOGY.md) | How the benchmark is run and what it measures |
| [CONTRIBUTING.md](CONTRIBUTING.md) | Development setup and the CLA requirement |
| [CHANGELOG.md](CHANGELOG.md) | Release history |

---

## Licensing and commercial use

Released under the **jDataMunch-MCP Dual-Use License** ([full terms](LICENSE)). **Free for non-commercial use. Commercial use requires a paid license**, one-time, sold by jMunch LLC.

**jDataMunch only:** [Builder, $39](https://jcodemunch.com/descriptions.php#builder) (1 developer) · [Studio, $149](https://jcodemunch.com/descriptions.php#studio) (up to 5) · [Platform, $499](https://jcodemunch.com/descriptions.php#platform) (org-wide internal deployment)

**Full jMunch suite (code + docs + data):** [Trio Builder, $99](https://jcodemunch.com/descriptions.php#builder) · [Trio Studio, $449](https://jcodemunch.com/descriptions.php#studio) · [Trio Platform, $2,499](https://jcodemunch.com/descriptions.php#platform)

Individual developers and non-commercial projects need no license. Organizations deploying jDataMunch across internal teams do.

---

## Support and project status

Actively maintained. Issues and bug reports: [GitHub Issues](https://github.com/jgravelle/jdatamunch-mcp/issues). Commercial licensing questions go through [jcodemunch.com](https://jcodemunch.com/).

Part of the jMunch suite alongside [jcodemunch-mcp](https://github.com/jgravelle/jcodemunch-mcp) (code symbols) and [jdocmunch-mcp](https://github.com/jgravelle/jdocmunch-mcp) (documentation sections). All three implement [jMRI](https://github.com/jgravelle/mcp-retrieval-spec), the open retrieval interface spec — same response envelope, same token accounting.
