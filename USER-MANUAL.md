# jDataMunch User Manual

A complete guide to using jDataMunch MCP for exploring and querying your data through AI.

**Who this is for:** Analysts, finance professionals, operations teams, consultants, and anyone who works with spreadsheets and wants their AI assistant to handle data questions efficiently. No programming experience required.

---

## Table of Contents

1. [What is jDataMunch?](#what-is-jdatamunch)
2. [Key Concepts](#key-concepts)
3. [Getting Started](#getting-started)
4. [Indexing Your Data](#indexing-your-data)
5. [Exploring a Dataset](#exploring-a-dataset)
6. [Searching for Columns](#searching-for-columns)
7. [Retrieving Specific Rows](#retrieving-specific-rows)
8. [Aggregating Data](#aggregating-data)
9. [Sampling Rows](#sampling-rows)
10. [Joining Two Datasets](#joining-two-datasets)
11. [Finding Correlations](#finding-correlations)
12. [Data Quality Checks](#data-quality-checks)
13. [Comparing Dataset Versions](#comparing-dataset-versions)
14. [Indexing from GitHub](#indexing-from-github)
15. [Managing Your Datasets](#managing-your-datasets)
16. [Tracking Token Savings](#tracking-token-savings)
17. [Configuration](#configuration)
18. [Tips and Best Practices](#tips-and-best-practices)
19. [Troubleshooting](#troubleshooting)
20. [Tool Reference](#tool-reference)

---

## What is jDataMunch?

jDataMunch is a plugin for AI assistants (like Claude) that makes them dramatically better at working with spreadsheets and data files.

**The problem:** When you ask an AI to analyze a CSV file, it typically reads the entire file into its context window. A 255 MB spreadsheet with a million rows would cost over 111 million tokens — and most of that data has nothing to do with your question.

**The solution:** jDataMunch indexes your file once (like building a table of contents for a book), then lets the AI look up exactly what it needs. Instead of reading a million rows to answer "how many crimes happened in Hollywood?", it runs a precise query and returns just the answer.

The result: **99.997% less token usage** on real-world data. Your questions get answered faster, cheaper, and more accurately.

---

## Key Concepts

### Datasets

A **dataset** is an indexed copy of your data file. When you index a file, jDataMunch creates a dataset with a name you choose (or it uses the filename). All queries reference this dataset name.

Example: If you index `Q1-sales.csv` as `q1-sales`, all your queries will use `dataset="q1-sales"`.

### Indexing

**Indexing** is the one-time process of reading your file and building a fast-query database. jDataMunch:

1. Reads the file row by row (never loads the whole thing into memory)
2. Detects column types (text, numbers, dates)
3. Computes statistics for each column (min, max, average, null rates, unique values)
4. Generates plain-English descriptions of each column
5. Stores everything in a local SQLite database for instant queries

After indexing, the original file is never re-read. If the file hasn't changed, re-indexing is automatically skipped.

### Filters

**Filters** are how you tell jDataMunch which rows you want. Instead of writing code, you describe conditions:

* "City equals Los Angeles" → `{"column": "City", "op": "eq", "value": "Los Angeles"}`
* "Amount greater than 10000" → `{"column": "Amount", "op": "gt", "value": 10000}`
* "Age between 25 and 35" → `{"column": "Age", "op": "between", "value": [25, 35]}`

You don't need to write these yourself — just describe what you want in plain English and your AI will create the right filter.

### Tokens

**Tokens** are the units AI models use to process text. More tokens = higher cost and slower responses. jDataMunch's entire purpose is to minimize the number of tokens your AI needs to answer data questions.

---

## Getting Started

### Installation

Open a terminal or command prompt and run:

```bash
pip install jdatamunch-mcp
```

If you work with Excel files, also install:

```bash
pip install "jdatamunch-mcp[excel]"
```

For Parquet files:

```bash
pip install "jdatamunch-mcp[parquet]"
```

Or install everything at once:

```bash
pip install "jdatamunch-mcp[all]"
```

### Connecting to Your AI

See the [QuickStart guide](QUICKSTART.md#step-2--add-to-your-ai-tool) for setup instructions for Claude Code, Claude Desktop, OpenClaw, Cursor, Windsurf, and other MCP clients.

### Setting Up the Data Policy

To make sure your AI uses jDataMunch instead of reading raw files, add this to your `CLAUDE.md`:

```markdown
## Data Exploration Policy
Use jdatamunch-mcp for tabular data whenever available.
Always call describe_dataset first to understand the schema.
Use get_rows with filters rather than loading raw files.
Use aggregate for any group-by or summary questions.
```

---

## Indexing Your Data

Before you can query a file, you need to index it. Just tell your AI:

> "Index the file at C:/Data/sales-2025.csv and call it sales"

### What happens during indexing

1. The file is read in a single streaming pass (even a 255 MB file uses less than 500 MB of memory)
2. Column types are detected automatically — you don't need to specify anything
3. Statistics are computed: min, max, mean, median, null rates, cardinality, distributions
4. Plain-English summaries are generated for each column and the dataset as a whole
5. Everything is stored in a local SQLite database at `~/.data-index/`

### Indexing options

| What you can control | How to ask | Default |
|---------------------|-----------|---------|
| Dataset name | "Call it sales" | Uses the filename |
| File encoding | "The file uses Latin-1 encoding" | Auto-detected |
| CSV delimiter | "It's pipe-delimited" | Auto-detected |
| Header row | "Headers are on row 2" | Row 0 (first row) |
| Excel sheet | "Index the Revenue sheet" | First sheet |

### Re-indexing

If your file changes, just ask to index it again. jDataMunch checks the file's hash — if nothing changed, it skips instantly. If the file did change, it re-indexes automatically.

### Supported formats

| Format | Extensions | Notes |
|--------|-----------|-------|
| CSV | `.csv`, `.tsv` | Any delimiter, auto-detected encoding |
| Excel | `.xlsx`, `.xls` | Requires `[excel]` extra. Specify sheet name if needed. |
| Parquet | `.parquet` | Requires `[parquet]` extra. Column-native format. |
| JSONL | `.jsonl`, `.ndjson` | One JSON object per line. Schema inferred from first rows. |

### File size limits

* Default row cap: 5,000,000 rows
* Tested on files up to 255 MB with 1 million+ rows
* Indexing a 255 MB CSV takes about 43 seconds (one-time cost)

---

## Exploring a Dataset

Once indexed, the first thing to do is understand what's in the data.

### Get the full schema

> "What's in the sales dataset?"

This calls `describe_dataset`, which returns one row per column (the default, `detail="compact"`):

* Every column's name and detected type
* What percentage of values are null/missing
* How many distinct values each column has (`~N` when estimated)
* The min and max for numbers and dates, or the most frequent values for text (every value when there are 10 or fewer)
* The text format of each date column, and which columns are unique

Ask for `detail="full"` to add sample values, top values with counts, mean, median and a plain-English summary of each column.

On the 28-column, 1M-row LAPD crime file the compact view is about 850 tokens and the full view about 4,800 (cl100k, as sent over MCP). The raw file is 111 million.

### Deep-dive on one column

> "Tell me more about the Revenue column"

This calls `describe_column`, which gives you:

* Full value distribution for columns with few unique values (e.g., Status: Active 42%, Inactive 31%, Pending 27%)
* Histogram bins for numeric columns (showing the distribution shape)
* Date range for datetime columns
* Detailed statistics: min, max, mean, median, standard deviation

### Sample some rows

> "Show me the first 5 rows of the sales dataset"

This calls `sample_rows`. You can ask for head (first rows), tail (last rows), or random rows. Useful when you want to see what the actual data looks like before filtering.

---

## Searching for Columns

When you don't know which column holds the information you need, use search.

### Keyword search

> "Which column has information about crime locations?"

This calls `search_data`, which searches across:

* Column names (exact and fuzzy matching)
* Column values (finds columns containing specific terms)
* Column descriptions (the auto-generated summaries)

It returns column IDs and match scores — telling the AI where to look, not dumping data.

### Semantic search

> "Find the column that describes where something happened"

With semantic search enabled, jDataMunch understands meaning, not just keywords. "Where something happened" can match a column called `AREA NAME` even though those words don't overlap.

Semantic search requires an embedding provider:

* **Free, local:** Install `jdatamunch-mcp[semantic]` — runs on your machine, no API key needed
* **Cloud:** Set `GOOGLE_API_KEY` (Gemini) or `OPENAI_API_KEY` (OpenAI)

To use it, just ask naturally. Your AI adds `semantic=true` to the search call. For best results, pre-warm embeddings:

> "Precompute embeddings for the sales dataset"

This calls `embed_dataset` and caches all column embeddings so future semantic searches are instant.

---

## Retrieving Specific Rows

The most common operation: getting exactly the rows that match your criteria.

### Basic filtering

> "Show me all sales in California where the amount is over $10,000"

Behind the scenes:

```
get_rows(dataset="sales", filters=[
  {"column": "State", "op": "eq", "value": "California"},
  {"column": "Amount", "op": "gt", "value": 10000}
])
```

### Available filter operators

| What you want | Operator | Example |
|--------------|----------|---------|
| Equals | `eq` | "State equals California" |
| Not equals | `neq` | "Status is not Cancelled" |
| Greater than | `gt` | "Amount greater than 10000" |
| Greater than or equal | `gte` | "Age at least 18" |
| Less than | `lt` | "Price under 50" |
| Less than or equal | `lte` | "Score no more than 100" |
| Contains text | `contains` | "Name contains Smith" (case-insensitive) |
| In a list | `in` | "Region is East, West, or Central" |
| Is null/missing | `is_null` | "Phone number is missing" |
| Between two values | `between` | "Age between 25 and 35" |

When you specify multiple conditions, they are all applied together (AND logic).

### Column projection

On wide datasets (many columns), you can ask for just the columns you need:

> "Show me just the Name, City, and Amount for California sales"

This uses the `columns` parameter to return only those three columns, saving tokens.

### Sorting

> "Show me the top 10 sales by amount, highest first"

Results can be sorted by any column, ascending or descending.

### Pagination

Results are capped at 500 rows per call (to protect your token budget). If you need more, you can page through results:

> "Show me the next 50 rows"

The AI uses `offset` and `limit` parameters to page through results. jDataMunch will also warn your AI if it detects a loop pattern (repeatedly fetching pages), suggesting it use `aggregate` instead.

---

## Aggregating Data

For summary questions, aggregation runs the computation inside SQLite and returns a compact result — orders of magnitude cheaper than returning all rows for the AI to count.

### Basic aggregation

> "How many sales are there in each region?"

```
aggregate(dataset="sales",
  aggregations=[{"column": "*", "function": "count"}],
  group_by=["Region"])
```

### Available functions

| Function | What it computes |
|----------|-----------------|
| `count` | Number of rows |
| `sum` | Total of a numeric column |
| `avg` | Average value |
| `min` | Minimum value |
| `max` | Maximum value |
| `count_distinct` | Number of unique values |
| `median` | Median value |

### Multiple aggregations at once

> "What's the total revenue, average order size, and number of orders per region?"

```
aggregate(dataset="sales",
  aggregations=[
    {"column": "Revenue", "function": "sum"},
    {"column": "Revenue", "function": "avg"},
    {"column": "*", "function": "count"}
  ],
  group_by=["Region"])
```

### Filtered aggregation

> "What's the average revenue by region, but only for orders over $1,000?"

Filters are applied before the aggregation, so you get accurate results on just the subset you care about.

### Sorting aggregation results

> "Show me the top 5 regions by total revenue"

Results can be sorted by any aggregation result, and limited to a specific number of groups.

---

## Joining Two Datasets

When your data spans multiple files, `join_datasets` combines them without loading either into the prompt.

### How it works

> "Join the sales dataset with the customers dataset on CustomerID"

```
join_datasets(
  dataset_a="sales",
  dataset_b="customers",
  join_column_a="CustomerID",
  join_column_b="CustomerID"
)
```

### Join types

| Type | What it returns |
|------|----------------|
| **inner** (default) | Only rows that match in both datasets |
| **left** | All rows from the first dataset, with matches from the second |
| **right** | All rows from the second dataset, with matches from the first |
| **cross** | Every combination of rows from both datasets (use with caution) |

### Column projection on joins

On wide datasets, specify which columns you need from each side:

> "Join sales and customers on CustomerID, but only show the OrderDate and Amount from sales, and the Name and City from customers"

### Filtering before joining

You can filter each dataset before the join runs:

> "Join sales from 2025 with California customers"

This applies filters to each side first, making the join faster and the results smaller.

---

## Finding Correlations

Discover numeric relationships in your data automatically.

> "What columns are correlated in the sales dataset?"

This calls `get_correlations`, which computes Pearson correlations between all numeric column pairs and returns:

* Correlation coefficient (r value)
* Strength label: `very strong`, `strong`, `moderate`, `weak`, or `negligible`
* Direction: positive or negative
* Number of non-null pairs used

Results are sorted by strength (|r|), so the most interesting relationships appear first.

### Filtering correlations

> "Show me only strong correlations (above 0.7)"

You can set a minimum threshold and limit to specific columns.

---

## Data Quality Checks

### Column risk ranking

> "Which columns have the most data quality issues?"

This calls `get_data_hotspots`, which ranks columns by a composite risk score based on:

* **Null rate** — how much data is missing
* **Cardinality anomalies** — columns that are suspiciously uniform or suspiciously unique
* **Numeric outlier spread** — how much variation exists (coefficient of variation)

Each column gets an assessment: `low`, `medium`, or `high` risk.

---

## Comparing Dataset Versions

When you get an updated version of a file, compare the schemas before and after.

> "Compare the schema of sales-q1 with sales-q2"

This calls `get_schema_drift`, which detects:

* **Added columns** — new columns in the second version
* **Removed columns** — columns that disappeared
* **Type changes** — a column changed from text to number, etc.
* **Null-rate shifts** — significant changes in missing data rates

The overall assessment is:

* **identical** — schemas match exactly
* **additive** — only additions (safe)
* **breaking** — removals or type changes (needs attention)

---

## Indexing from GitHub

You can index data files directly from a GitHub repository without downloading them manually.

> "Index data files from the pandas-dev/pandas repository on GitHub"

This calls `index_repo`, which:

1. Discovers CSV, Excel, Parquet, and JSONL files in the repo
2. Downloads each file (up to 50 MB per file, 20 files per repo)
3. Indexes each through the same pipeline as local files
4. Names datasets as `{owner}--{repo}--{filename}`

### Incremental updates

The repo's HEAD SHA is cached. If you re-index and nothing has changed, it skips instantly.

### Private repositories

For private repos, set the `GITHUB_TOKEN` environment variable. This also helps avoid GitHub API rate limits.

### Listing indexed repos

> "Which GitHub repos have I indexed?"

This calls `list_repos`, which shows each repo's name, HEAD SHA, dataset count, total rows, and dataset names.

---

## Managing Your Datasets

### List all datasets

> "What datasets do I have?"

Shows all indexed datasets with row counts, column counts, and file sizes.

### Regenerate summaries

> "Regenerate the descriptions for the sales dataset"

If you want to refresh the auto-generated column summaries without re-indexing, use `summarize_dataset`.

### Delete a dataset

> "Delete the old-sales dataset"

This removes the index and SQLite store, freeing disk space. The action is irreversible — you'd need to re-index the file to get it back.

### Precompute embeddings

> "Precompute embeddings for the sales dataset"

If you plan to use semantic search, pre-warming embeddings with `embed_dataset` eliminates the latency on the first semantic query.

---

## Tracking Token Savings

Every jDataMunch call tracks how many tokens it saved compared to loading the raw file.

> "How many tokens has jDataMunch saved me?"

This returns:

* **Session stats** — savings since your AI started this conversation
* **Lifetime stats** — cumulative savings across all conversations
* **Per-model cost breakdown** — estimated dollars saved based on model pricing

Lifetime stats persist to `~/.data-index/session_stats.json`.

---

## Configuration

jDataMunch works out of the box with zero configuration. These settings are available if you need them:

| Setting | Default | What it controls |
|---------|---------|-----------------|
| `DATA_INDEX_PATH` | `~/.data-index/` | Where indexes are stored on disk |
| `JDATAMUNCH_MAX_ROWS` | `5,000,000` | Maximum rows to index per file |
| `JDATAMUNCH_MAX_RESPONSE_TOKENS` | `8,000` | Token budget cap per tool response |
| `JDATAMUNCH_SHARE_SAVINGS` | `1` | Anonymous telemetry (set `0` to disable) |

### AI-enhanced features (optional)

| Setting | What it enables |
|---------|----------------|
| `ANTHROPIC_API_KEY` | AI-powered column summaries via Claude |
| `GOOGLE_API_KEY` | AI-powered column summaries via Gemini; also enables Gemini embeddings |
| `OPENAI_API_KEY` | OpenAI embeddings for semantic search |
| `GITHUB_TOKEN` | Access to private GitHub repos for `index_repo` |

### Embedding providers for semantic search

jDataMunch supports three embedding providers. The first one it finds is used:

1. **sentence-transformers** (local, free) — install with `pip install "jdatamunch-mcp[semantic]"`. Set `JDATAMUNCH_EMBED_MODEL` to choose a model.
2. **Gemini** — set `GOOGLE_API_KEY`. Optionally set `GOOGLE_EMBED_MODEL`.
3. **OpenAI** — set `OPENAI_API_KEY`. Optionally set `OPENAI_EMBED_MODEL`.

Set environment variables in your terminal, shell profile, or MCP client config (some clients support `env` blocks).

---

## Tips and Best Practices

### Always start with `describe_dataset`

Before asking data questions, have your AI call `describe_dataset`. It costs almost nothing and gives the AI full context about your data structure, preventing wrong-column mistakes.

### Use `aggregate` instead of fetching rows to count

If you want "how many sales per region" or "average price by category," use `aggregate`. It runs the computation in SQLite and returns a tiny result. Fetching all rows and counting them in the AI's context is hundreds of times more expensive.

### Use column projection on wide tables

If your dataset has 50+ columns, always specify which columns you need. "Show me the Name and Revenue columns" is much cheaper than returning all 50 columns per row.

### Use `search_data` when you don't know the column name

Column names in real datasets are often cryptic (`Crm Cd Desc`, `Weapon Used Cd`, `Premis Cd`). Instead of guessing, search: "Which column has information about weapons?" The search checks names, values, and descriptions simultaneously.

### Index once, query forever

Indexing is the only slow operation (seconds to a minute for large files). After that, every query is sub-100ms. Don't re-index unless your file has actually changed — jDataMunch detects unchanged files automatically.

### Set up the data policy

Without the CLAUDE.md policy, your AI might still try to `cat` or read the raw CSV. The policy ensures it uses jDataMunch tools first, every time.

---

## Troubleshooting

### "Dataset not found"

The dataset name is case-sensitive. Use `list_datasets` to see the exact names of your indexed datasets.

### "File not found" during indexing

Use an absolute path (starting with `/` on Mac/Linux or `C:\` on Windows). Relative paths may not resolve correctly from the AI's working directory.

### Indexing seems slow

First-time indexing is expected to take:

* Small files (< 10 MB): a few seconds
* Medium files (10–100 MB): 10–30 seconds
* Large files (100–300 MB): 30–60 seconds

After the first index, re-runs on unchanged files return instantly.

### Excel file won't index

Make sure you installed the Excel extra:

```bash
pip install "jdatamunch-mcp[excel]"
```

If your data is on a specific sheet, mention the sheet name when indexing.

### Parquet file won't index

Install the Parquet extra:

```bash
pip install "jdatamunch-mcp[parquet]"
```

### Semantic search returns no results

Semantic search requires an embedding provider. Either:

* Install local embeddings: `pip install "jdatamunch-mcp[semantic]"`
* Set `GOOGLE_API_KEY` or `OPENAI_API_KEY`

### AI still reads raw files instead of using jDataMunch

Add the data exploration policy to your `CLAUDE.md`. See [Setting Up the Data Policy](#setting-up-the-data-policy).

### Encoding errors during indexing

If a CSV has unusual encoding, specify it when indexing: "Index the file using Latin-1 encoding." jDataMunch auto-detects encoding in most cases, but explicit overrides are available.

### "Token budget exceeded" warnings

jDataMunch caps each response at 8,000 tokens by default to protect your context window. If you need more data, use filters to narrow your query or aggregate to summarize.

---

## Tool Reference

Quick reference for all 18 tools. For each tool, the "Ask your AI" column shows how to invoke it in plain English.

### Indexing tools

| Tool | Ask your AI | What it does |
|------|------------|-------------|
| `index_local` | "Index the file at /path/to/data.csv" | Index a local CSV, Excel, Parquet, or JSONL file |
| `index_repo` | "Index data files from owner/repo on GitHub" | Discover and index data files from a GitHub repository |

### Exploration tools

| Tool | Ask your AI | What it does |
|------|------------|-------------|
| `list_datasets` | "What datasets do I have?" | List all indexed datasets with summary stats |
| `list_repos` | "Which GitHub repos have I indexed?" | List GitHub repos indexed via `index_repo` |
| `describe_dataset` | "What's in the sales dataset?" | Full schema profile with types, stats, and summaries |
| `describe_column` | "Tell me more about the Revenue column" | Deep column profile: distribution, histogram, range |
| `search_data` | "Which column has location info?" | Search columns by keyword or meaning |
| `sample_rows` | "Show me the first 5 rows" | Head, tail, or random row sample |

### Query tools

| Tool | Ask your AI | What it does |
|------|------------|-------------|
| `get_rows` | "Show me California sales over $10k" | Filtered row retrieval (10 operators, 500-row cap) |
| `aggregate` | "Total revenue by region" | Server-side GROUP BY (count, sum, avg, min, max, median) |
| `join_datasets` | "Join sales with customers on CustomerID" | SQL JOIN across two indexed datasets |

### Analysis tools

| Tool | Ask your AI | What it does |
|------|------------|-------------|
| `get_correlations` | "What columns are correlated?" | Pairwise Pearson correlations between numeric columns |
| `get_schema_drift` | "Compare sales-q1 with sales-q2 schema" | Detect column additions, removals, type changes |
| `get_data_hotspots` | "Which columns have quality issues?" | Rank columns by data-quality risk |

### Management tools

| Tool | Ask your AI | What it does |
|------|------------|-------------|
| `summarize_dataset` | "Regenerate descriptions for sales" | Refresh auto-generated NL summaries |
| `embed_dataset` | "Precompute embeddings for sales" | Warm up semantic search embeddings |
| `delete_dataset` | "Delete the old-sales dataset" | Remove index and free disk space (irreversible) |
| `get_session_stats` | "How many tokens has jDataMunch saved?" | Session and lifetime token savings report |
