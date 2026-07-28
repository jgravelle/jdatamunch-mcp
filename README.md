Quickstart - https://github.com/jgravelle/jdatamunch-mcp/blob/main/QUICKSTART.md

<!-- mcp-name: io.github.jgravelle/jdatamunch-mcp -->

## FREE FOR PERSONAL USE
**Use it to make money, and Uncle J. gets a taste. Fair enough?** [details](#commercial-licenses)

---

## Documentation

| Doc | What it covers |
|-----|----------------|
| [QUICKSTART.md](QUICKSTART.md) | Zero-to-indexed in three steps |
| [USER-MANUAL.md](USER-MANUAL.md) | Full guide for analysts, ops, and non-developers |

---

## Cut spreadsheet token usage by **99.997%**

Most AI agents explore tabular data the expensive way:

dump the whole file into the prompt → skim a million irrelevant rows → repeat.

That is not "a little inefficient."
That is a **token incinerator**.

A 255 MB CSV file with 1 million rows costs **111 million tokens** if you paste it raw.
A single `describe_dataset` call answers the same orientation question in **3,849 tokens**.

That is a **25,333× reduction** — measured, not estimated, on a real 1M-row public dataset.

**jDataMunch indexes the file once and lets agents retrieve only the exact data they need**: column profiles, filtered rows, server-side aggregations, cross-dataset joins, and semantic search — with SQL precision.

> **Benchmark:** LAPD crime records — 1,004,894 rows, 28 columns, 255 MB
> Baseline (raw file): 111,028,360 tokens &nbsp;|&nbsp; jDataMunch: ~3,849 tokens &nbsp;|&nbsp; **25,333× reduction**
> [Methodology & harness](benchmarks/METHODOLOGY.md) · [Full results](benchmarks/results.md)

| Task | Traditional approach | With jDataMunch |
|------|----------------------|-----------------|
| Understand a dataset | Paste entire CSV | `describe_dataset` → column names, types, cardinality, samples |
| Find relevant columns | Read every row | `search_data` → column-level results with IDs |
| Answer a filtered question | Load millions of rows | `get_rows` with structured filters → only matching rows |
| Compute a group-by | Return all data | `aggregate` → server-side SQL, one result set |
| Compare two datasets | Load both entirely | `join_datasets` → SQL JOIN across indexed stores |
| Find column relationships | Export to spreadsheet | `get_correlations` → pairwise Pearson correlations |

Index once. Query cheaply. Keep moving.
**Precision retrieval beats brute-force context.**

---

# jDataMunch MCP

### Structured tabular data retrieval for AI agents

![License](https://img.shields.io/badge/license-dual--use-blue)
![MCP](https://img.shields.io/badge/MCP-compatible-purple)
![Local-first](https://img.shields.io/badge/local--first-yes-brightgreen)
![SQLite](https://img.shields.io/badge/storage-SQLite-9cf)
![jMRI](https://img.shields.io/badge/jMRI-Full-blueviolet)
[![DOI](https://zenodo.org/badge/DOI/10.5281/zenodo.20102349.svg)](https://doi.org/10.5281/zenodo.20102349)
[![PyPI version](https://img.shields.io/pypi/v/jdatamunch-mcp)](https://pypi.org/project/jdatamunch-mcp/)
[![PyPI - Python Version](https://img.shields.io/pypi/pyversions/jdatamunch-mcp)](https://pypi.org/project/jdatamunch-mcp/)

> ## Commercial licenses
>
> jDataMunch-MCP is **free for non-commercial use**.
>
> **Commercial use requires a paid license.**
>
> **jDataMunch-only licenses**
>
> * [Builder — $39](https://jcodemunch.com/descriptions.php#builder) — 1 developer
> * [Studio — $149](https://jcodemunch.com/descriptions.php#studio) — up to 5 developers
> * [Platform — $499](https://jcodemunch.com/descriptions.php#platform) — org-wide internal deployment
>
> **Want the full jMunch suite?**
>
> * [Munch Trio Builder Bundle — $99](https://jcodemunch.com/descriptions.php#builder)
> * [Munch Trio Studio Bundle — $449](https://jcodemunch.com/descriptions.php#studio)
> * [Munch Trio Platform Bundle — $2,499](https://jcodemunch.com/descriptions.php#platform)

**Stop paying your model to read the whole damn spreadsheet.**

jDataMunch turns tabular data exploration into **structured retrieval**.

Instead of forcing an agent to load an entire CSV, scan millions of rows, and burn through context just to find the right column name, jDataMunch lets it navigate by **what the data is** and retrieve **only what matters**.

That means:

* **25,333× lower data-reading token usage** on a 1M-row CSV (measured)
* **less irrelevant context** polluting the prompt
* **faster dataset orientation** — one call tells you everything about the schema
* **accurate filtered queries** — the agent asks for Hollywood assaults, it gets Hollywood assaults
* **server-side aggregations** — GROUP BY runs in SQLite, not inside the context window
* **cross-dataset joins** — combine two indexed files in a single SQL query
* **semantic search** — find columns by meaning, not just keyword match
* **natural-language summaries** — auto-generated descriptions of every column and dataset

It indexes your files once using a streaming parser and SQLite, stores column profiles and row data with proper type affinity, and retrieves exactly what the agent asked for instead of re-loading the entire file on every question.

---

## Supported file formats

| Format | Extensions | Install extra |
|--------|-----------|---------------|
| CSV / TSV | `.csv`, `.tsv` | — (built-in) |
| Excel | `.xlsx`, `.xls` | `pip install "jdatamunch-mcp[excel]"` |
| Parquet | `.parquet` | `pip install "jdatamunch-mcp[parquet]"` |
| JSONL / NDJSON | `.jsonl`, `.ndjson` | — (built-in) |

---

## Why agents need this

Most agents still handle spreadsheets like someone who prints the entire internet before reading one article:

* paste the whole CSV to answer a narrow question
* re-load the same file repeatedly across tool calls
* consume column headers, empty cells, malformed rows, and irrelevant records
* burn context window on data that was never part of the question

jDataMunch fixes that by giving them a structured way to:

* describe a dataset's schema before touching any row data
* search for the specific column that holds the answer — by keyword or meaning
* retrieve only the rows that match the filter
* run aggregations server-side and get back a single result set
* join two datasets without loading either into the prompt
* orient themselves with samples before committing to a full query
* detect data-quality issues and column correlations automatically

Agents do not need bigger context windows.

They need **better aim**.

---

## What you get

### Column-level retrieval

Understand a dataset's full schema — types, cardinality, null rates, value distributions, samples, and natural-language summaries — in a single sub-10ms call. No rows loaded.

### Filtered row retrieval

Structured filters with 10 operators (`eq`, `neq`, `gt`, `gte`, `lt`, `lte`, `contains`, `in`, `is_null`, `between`). All parameterized SQL — no injection surface. Hard cap of 500 rows per call to protect context budgets.

### Server-side aggregations

GROUP BY with `count`, `sum`, `avg`, `min`, `max`, `count_distinct`, `median`. The computation stays in SQLite. One compact result set comes back instead of the data the model would aggregate itself.

### Smart column search

`search_data` searches column names, value indexes, and AI summaries simultaneously. Ask for "weapon type" and get `Weapon Used Cd` back. Ask for "Hollywood" and get the column whose values contain it.

**Semantic search** (v0.8+): Enable `semantic=true` for embedding-based search. Queries like "where did the crime happen" match `AREA NAME` even without keyword overlap. Supports local embeddings (sentence-transformers), Gemini, or OpenAI as providers.

### Cross-dataset joins

`join_datasets` combines two indexed datasets via SQL `ATTACH DATABASE` — inner, left, right, or cross joins. Column projection, per-side filters, ordering, and pagination. No data leaves SQLite.

### Correlation discovery

`get_correlations` computes pairwise Pearson correlations between all numeric columns. Discover hidden relationships without manual exploration.

### Natural-language summaries

Every indexed dataset gets auto-generated summaries describing data shape, column types, ranges, cardinality, quality issues, and temporal spans — no external API calls needed.

### Data quality triage

`get_data_hotspots` ranks columns by composite risk: null rate, cardinality anomalies, and numeric outlier spread. `get_schema_drift` compares schema between two dataset versions and classifies changes as identical, additive, or breaking.

### Token savings telemetry

Every call reports `tokens_saved` and `cost_avoided` estimates. `get_session_stats` shows your cumulative savings across the session, with per-model cost breakdowns. Lifetime stats persist across sessions.

**Community savings meter (anonymous, opt-out).** Each tool call can also contribute an anonymous delta to the live global counter, POSTed to `https://j.gravelle.us/APIs/savings/post.php` and displayed at [jcodemunch.com](https://jcodemunch.com). Only two values are sent: tokens saved, and a random anonymous install ID (generated once, stored in `~/.data-index/_savings.json`). No data, no file paths, no dataset names, no schemas, no identifying material. This is the only network call the base package ever makes on its own. Disable it with:

```bash
JDATAMUNCH_SHARE_SAVINGS=0
```

### GitHub repository indexing

`index_repo` discovers and indexes data files directly from a GitHub repository — CSV, Excel, Parquet, and JSONL. Incremental by HEAD SHA. Supports private repos via `GITHUB_TOKEN`.

### Local-first speed

Indexes are stored at `~/.data-index/` by default. No cloud. No API keys required for core functionality.

### Built-in guardrails

* **Token budget enforcement** — every response is capped at a configurable token limit (default 8,000)
* **Anti-loop detection** — warns when an agent is paginating row-by-row in a tight loop
* **Wide-table pagination** — `describe_dataset` auto-paginates at 60 columns
* **Hard caps** on all parameters to prevent runaway queries

---

## How it works

jDataMunch parses local CSV, Excel, Parquet, and JSONL files using a **streaming, single-pass pipeline**:

```
CSV/Excel/Parquet/JSONL file
  → Streaming parser (never loads full file into memory)
  → Column profiler (type inference, cardinality, min/max/mean/median, value indexes)
  → Natural-language summary generator (dataset + per-column descriptions)
  → SQLite writer (10,000-row batches, WAL mode, indexes on low-cardinality columns)
  → index.json (column profiles, stats, summaries, file hash for incremental detection)
```

When an agent queries:

```
describe_dataset  →  reads index.json in memory (< 10ms)
get_rows          →  parameterized SQL on data.sqlite (< 100ms on indexed columns)
aggregate         →  GROUP BY SQL on data.sqlite (< 200ms for simple group-by)
search_data       →  scans column profiles in memory (< 50ms)
join_datasets     →  ATTACH DATABASE + cross-store SQL (< 300ms)
```

**No raw file is ever re-read after the initial index.** The SQLite database serves all row-level queries.

For a 255 MB, 1,004,894-row CSV (measured on real data):
* Index time: ~43 seconds (one-time)
* `describe_dataset`: 35 ms, **3,849 tokens** vs 111,028,360 tokens raw — **25,333×**
* `describe_column` (single column deep-dive): 22–33 ms, ~600 tokens
* `get_rows` (indexed filter): < 100 ms
* Peak indexing memory: < 500 MB

---

## Start fast

### 1. Install it

```bash
pip install jdatamunch-mcp
```

For additional format support:

```bash
pip install "jdatamunch-mcp[excel]"       # Excel (.xlsx, .xls)
pip install "jdatamunch-mcp[parquet]"     # Parquet
pip install "jdatamunch-mcp[semantic]"    # Semantic search (local embeddings)
pip install "jdatamunch-mcp[all]"         # Everything
```

### 2. Add it to your MCP client

#### Claude Code (one command)

```bash
claude mcp add jdatamunch uvx jdatamunch-mcp
```

Restart Claude Code. Confirm with `/mcp`.

#### Claude Desktop

Add to your config file (`~/Library/Application Support/Claude/claude_desktop_config.json` on macOS, `%APPDATA%\Claude\claude_desktop_config.json` on Windows):

```json
{
  "mcpServers": {
    "jdatamunch": {
      "command": "uvx",
      "args": ["jdatamunch-mcp"]
    }
  }
}
```

#### OpenClaw

**Option A — CLI:**

```bash
openclaw mcp set jdatamunch '{"command":"uvx","args":["jdatamunch-mcp"]}'
```

**Option B — Edit `~/.openclaw/openclaw.json`:**

```json
{
  "mcpServers": {
    "jdatamunch": {
      "command": "uvx",
      "args": ["jdatamunch-mcp"],
      "transport": "stdio"
    }
  }
}
```

Restart the gateway: `openclaw gateway restart`. Verify: `openclaw mcp list`.

#### Other clients (Cursor, Windsurf, Roo, etc.)

Any MCP-compatible client accepts the same JSON block in its MCP config file.

### 3. Index a file and start querying

```
index_local(path="/path/to/data.csv", name="my-dataset")
describe_dataset(dataset="my-dataset")
get_rows(dataset="my-dataset", filters=[{"column": "City", "op": "eq", "value": "Los Angeles"}], limit=10)
```

### 4. Tell your agent to actually use it

Installing jDataMunch makes the tools available. It does **not** guarantee the agent will stop pasting entire CSVs into prompts unless you tell it to use structured retrieval first.

#### Claude Code / Claude Desktop

Add this to your `CLAUDE.md` (global or project-level):

```markdown
## Data Exploration Policy
Use jdatamunch-mcp for tabular data whenever available.
Always call describe_dataset first to understand the schema.
Use get_rows with filters rather than loading raw files.
Use aggregate for any group-by or summary questions.
```

#### OpenClaw

Add the same policy to your agent's system prompt file (e.g. `~/.openclaw/agents/analyst.md`), then reference it in `~/.openclaw/openclaw.json`:

```json
{
  "agents": {
    "named": {
      "analyst": {
        "systemPromptFile": "~/.openclaw/agents/analyst.md"
      }
    }
  }
}
```

### Check your token savings

Ask your agent: *"How many tokens has jDataMunch saved me?"*

The agent will call `get_session_stats`, which returns session and lifetime token savings with per-model cost breakdowns. Lifetime stats persist to `~/.data-index/session_stats.json` across sessions.

---

## Tools

### Indexing

| Tool | What it does |
|------|-------------|
| `index_local` | Index a local CSV, Excel, Parquet, or JSONL file. Profiles columns, generates NL summaries, loads rows into SQLite. Incremental by default (skips if file unchanged). |
| `index_repo` | Index data files from a GitHub repository. Discovers CSV, Excel, Parquet, and JSONL files via the Trees API and indexes each. Incremental by HEAD SHA. Max 50 MB/file, 20 files/repo. |

### Exploration

| Tool | What it does |
|------|-------------|
| `list_datasets` | List all indexed datasets with row counts, column counts, and file sizes. |
| `list_repos` | List GitHub repositories indexed via `index_repo`. Shows repo name, HEAD SHA, dataset count, total rows. |
| `describe_dataset` | Full schema profile: every column's name, type, cardinality, null%, sample values, and NL summary. Primary orientation tool. Auto-paginates at 60 columns. |
| `describe_column` | Deep profile of one column: full value distribution, histogram bins, temporal range, NL summary. |
| `search_data` | Search column names and values by keyword or semantically. Returns column IDs — tells the agent where to look, not the data. Supports hybrid keyword + embedding search. |
| `sample_rows` | Head, tail, or random sample. Good for first-look at an unfamiliar dataset. |

### Querying

| Tool | What it does |
|------|-------------|
| `get_rows` | Filtered row retrieval with 10 operators. Parameterized SQL. 500-row hard cap. Column projection to reduce tokens. |
| `aggregate` | Server-side GROUP BY: count, sum, avg, min, max, count_distinct, median. Pre-filter support. 1,000-group cap. |
| `join_datasets` | SQL JOIN across two indexed datasets. Supports inner, left, right, cross. Per-side filters and column projection. |

### Analysis

| Tool | What it does |
|------|-------------|
| `get_correlations` | Pairwise Pearson correlations between numeric columns. Sorted by strength, with labels and pair counts. |
| `get_schema_drift` | Compare schema between two datasets. Detects added/removed columns, type changes, null-rate shifts. |
| `get_data_hotspots` | Rank columns by data-quality risk: null rate, cardinality anomalies, numeric outlier spread. |

### Management

| Tool | What it does |
|------|-------------|
| `summarize_dataset` | Regenerate NL summaries for an already-indexed dataset without re-parsing the source file. |
| `embed_dataset` | Precompute column embeddings for semantic search. Optional warm-up to eliminate first-query latency. |
| `delete_dataset` | Remove an indexed dataset and its SQLite store. Irreversible. |
| `validate_index` | Verify a dataset's on-disk integrity: SQLite `integrity_check`, row-count cross-check, schema match, `index.json` checksum, stale-lock detection. Returns `ok` / `warning` / `error`. |
| `get_dataset_history` | Return the last N profile snapshots for a dataset (appended on every successful `index_local`). Use to detect schema/content drift across re-ingests. |
| `get_session_stats` | Cumulative token savings and cost avoided across the session. Lifetime stats persist across sessions. |

---

## Stability guarantees (v1.0.0)

Earned by Phase A in `todo.md`. These are commitments, not aspirations.

**Statistical correctness**
- Means use Welford online updates with Neumaier-compensated sums — accurate
  to 1e-9 relative error across 1e-6..1e6 mixed magnitudes.
- Quantiles (`p01 / p25 / p50 / p75 / p95 / p99`) come from a streaming t-digest
  with bounded ~3 KB/column memory regardless of row count.
- Cardinality reports an exact value below 5,000 distinct keys; above the cap,
  a HyperLogLog estimate is reported with `cardinality_estimated: true` and
  ~2% standard error.

**Crash safety**
- A kill at any point during `index_local` leaves the dataset in one of two
  states only: fully indexed or absent. Never partial.
- `data.sqlite` is written to a `.tmp` and renamed only after profiles
  compute successfully. `index.json` is atomic with a SHA-256 sidecar.
- A `_lock` file marks in-progress runs. `index_local` auto-recovers from
  prior crashes by cleaning stale tmp files before starting.

**Recovery flow**
- Run `validate_index` on any dataset whose state is suspect. If it returns
  `overall_status: ok`, the dataset is consistent. Otherwise the report names
  the specific finding (row-count mismatch, checksum drift, missing SQLite,
  stale lock, etc.).

**Schema versioning**
- The on-disk index format is versioned (`INDEX_VERSION = 2` at 1.0.0).
- New profile fields are added under additive migrations registered in
  `storage/migrations.py`. Indexes from prior versions are upgraded in place
  rather than triggering silent re-indexing.
- Public profile fields documented in CHANGELOG `[1.0.0]` are stable.

**Reproducibility**
- `sample_rows(method='random', seed=N)` is deterministic.
- `index_local` produces byte-identical `index.json` (modulo timestamps + the
  resolved source path) for the same input file across runs.
- All four parsers (CSV / JSONL / Parquet / Excel) route native-typed cells
  through one normalizer, so the same logical data produces identical
  column profiles regardless of source format.

---

## Filter operators

`get_rows`, `aggregate`, and `join_datasets` accept structured filters:

```json
{"column": "AREA NAME",    "op": "eq",      "value": "Hollywood"}
{"column": "Vict Age",     "op": "between", "value": [25, 35]}
{"column": "Crm Cd Desc",  "op": "contains","value": "ASSAULT"}
{"column": "Weapon Used Cd","op": "is_null","value": true}
{"column": "AREA",         "op": "in",      "value": [1, 2, 7]}
```

| Operator | Meaning |
|----------|---------|
| `eq` | equals |
| `neq` | not equals |
| `gt`, `gte` | greater than (or equal) |
| `lt`, `lte` | less than (or equal) |
| `contains` | case-insensitive substring |
| `in` | value in list |
| `is_null` | null / not null check |
| `between` | inclusive range `[min, max]` |

Multiple filters are ANDed. No raw SQL accepted — injection surface is zero.

---

## Configuration

| Variable | Default | Purpose |
|----------|---------|---------|
| `DATA_INDEX_PATH` | `~/.data-index/` | Index storage location |
| `JDATAMUNCH_MAX_ROWS` | `5,000,000` | Row cap for indexing |
| `JDATAMUNCH_MAX_RESPONSE_TOKENS` | `8,000` | Token budget cap per response |
| `JDATAMUNCH_SHARE_SAVINGS` | `1` | Set `0` to disable anonymous token savings telemetry |
| `JDATAMUNCH_SESSION_TOKEN_BUDGET` | unset | Advisory session budget over response tokens served. When set, responses carry `_meta.budget` at >=80% (`approaching`) / >=100% (`over`) and `get_session_stats` reports the block. Never blocks or truncates. Unset/`0` = off |
| `ANTHROPIC_API_KEY` | — | AI column summaries via Claude |
| `GOOGLE_API_KEY` | — | AI column summaries via Gemini |
| `GITHUB_TOKEN` | — | Private repo access for `index_repo` |
| `JDATAMUNCH_EMBED_MODEL` | — | Local sentence-transformers model for semantic search |
| `GOOGLE_EMBED_MODEL` | — | Gemini embedding model for semantic search |
| `OPENAI_API_KEY` | — | OpenAI embeddings for semantic search |
| `OPENAI_EMBED_MODEL` | — | OpenAI embedding model for semantic search |
| `JDATAMUNCH_LAUNCH_ID` | — | Opaque launch token echoed back as `launch_id` in the `munch://runtime/identity` resource (fallback: `MUNCH_LAUNCH_ID`); omitted when unset |

`get_session_stats` also reports a `tool_surface` receipt: visible vs catalog tool counts, estimated schema tokens for each (bytes/4 scale), tokens avoided by the active tool profile, and the heaviest tool schemas. Computed inline on the stats call; nothing persisted.

---

## Runtime identity resource

The server exposes one MCP resource, `munch://runtime/identity` — a read-only `munch.runtime.identity/v1` JSON document identifying this exact server process (`product`, `version`, `transport`, `pid`, OS-derived `process_start`, per-process-lifetime `instance_id`, optional `launch_id` echo). Multi-agent harnesses use it to tell command-line-identical servers apart and detect restarts. Computed on demand with no disk reads, writes, or network; when the OS process-start probe is unavailable the timestamp is disclosed as `source: "self_recorded"`, never fabricated. Command lines, env, cwd, hostnames, and dataset paths are deliberately excluded. Same contract in jcodemunch-mcp and jdocmunch-mcp.

---

## Canonical handoff (`finalize_handoff` + `munch://handoff/<id>`)

A multi-step data audit can end with one authoritative, server-attested result. The assistant authors the analysis; `finalize_handoff` takes those sections plus `evidence_refs`, validates every reference against what this session **actually retrieved** (column ids like `<dataset>::<column>#column` or dataset names served by `search_data` / `describe_dataset` / `describe_column` — unknown refs fail closed), deterministically assembles one canonical Markdown handoff (`jdatamunch.handoff/v1`), and returns a compact receipt: `{handoff_id, resource_uri, sha256, length, canonical: true}`. The immutable body is served by the `munch://handoff/<id>` resource — repeated reads are byte-identical. Session-scoped, in-memory, never writes to your data; appendices appear exactly once; no character limit. Suite parity with jcodemunch-mcp and jdocmunch-mcp.

---

## When does it help?

| Scenario | Without jDataMunch | With jDataMunch | Measured savings |
|----------|--------------------|-----------------|---------|
| Orient on a 255 MB CSV | Paste raw file → **111M tokens** | `describe_dataset` → **3,849 tokens** | **25,333×** |
| Schema + column deep-dive | Same 111M tokens | `describe_dataset` + `describe_column` → **~4,400 tokens** | **~25,000×** |
| Find the crime-type column | Scan headers manually | `search_data("crime type")` → column ID | structural |
| Find column by meaning | No way to search semantically | `search_data("where did it happen", semantic=true)` → `AREA NAME` | structural |
| Get Hollywood assault rows | Load all 1M rows | `get_rows` with 2 filters → matching rows only | ~99%+ |
| Crime count by area | Return all rows, aggregate in LLM | `aggregate(group_by=["AREA NAME"])` → 21 rows | ~99.9% |
| Understand weapon nulls | Load column, count manually | `describe_column("Weapon Used Cd")` → `null_pct: 64.2%` | ~99.9% |
| Compare two dataset versions | Load both files | `get_schema_drift(a, b)` → breaking/additive assessment | structural |
| Find correlated columns | Export, pivot, eyeball | `get_correlations` → ranked pairs with strength labels | structural |
| Combine two datasets | Load both into prompt | `join_datasets` → SQL JOIN, only matching rows | ~99%+ |
| Re-query an unchanged file | Re-load file every time | Hash check → instant skip if unchanged | 100% of re-read cost |

The case where it doesn't help: you genuinely need every row for ML training or full exports. For that, read the file directly. For everything else — exploration, filtering, aggregation, orientation — structured retrieval wins every time.

---

## ID scheme

Every column and row gets a stable ID:

```
{dataset}::{column_name}#column     →  "lapd-crime::AREA NAME#column"
{dataset}::row_{rowid}#row          →  "lapd-crime::row_4421#row"
{dataset}::{pk_col}={value}#row     →  "lapd-crime::DR_NO=211507896#row"
```

Pass column IDs directly to `describe_column`. Row IDs are returned in `get_rows` results.

---

## Part of the jMunch family

| Product | Domain | Unit of retrieval | PyPI |
|---------|--------|-------------------|------|
| [jcodemunch-mcp](https://github.com/jgravelle/jcodemunch-mcp) | Source code | Symbols (functions, classes) | `jcodemunch-mcp` |
| [jdocmunch-mcp](https://github.com/jgravelle/jdocmunch-mcp) | Documentation | Sections (headings) | `jdocmunch-mcp` |
| **jdatamunch-mcp** | **Tabular data** | **Columns, row slices, aggregations** | **`jdatamunch-mcp`** |

All three implement [jMRI](https://github.com/jgravelle/mcp-retrieval-spec) — the open retrieval interface spec. Same response envelope, same token tracking, same telemetry pattern.

---

## Best for

* analysts, finance, ops, and consultants working with large spreadsheets
* AI agents that answer questions about CSV, Excel, Parquet, or JSONL data
* anyone paying token costs to load files they query repeatedly
* teams that want structured, auditable data access instead of raw file dumps
* developers building data-aware agents who need a drop-in retrieval layer

---

## Works with

jDataMunch plugs into any MCP-compatible agent or IDE. Tested configurations:

| Platform | Config |
|----------|--------|
| **Claude Code / Claude Desktop** | Manual config or `mcp.json` |
| **Cursor / Windsurf** | Manual `mcp.json` |
| **[Hermes Agent](https://github.com/NousResearch/hermes-agent)** | Add to `~/.hermes/config.yaml` — see [skill](https://github.com/NousResearch/hermes-agent/pull/10413) |
| **Any MCP client** | stdio: `jdatamunch-mcp` |

<details>
<summary>Hermes Agent config</summary>

```yaml
# ~/.hermes/config.yaml
mcp_servers:
  jdatamunch:
    command: "uvx"
    args: ["jdatamunch-mcp"]
```
</details>

---

## New here?

Start with the [QuickStart guide](QUICKSTART.md) — zero to indexed in three steps.

Or if you prefer learning by doing: index a file, run `describe_dataset`, and look at what comes back.

That single call — 35 milliseconds, 3,849 tokens — tells you everything that would have cost you 111 million tokens to read raw.

That's the whole idea...

## License

jDataMunch-MCP is released under the **jDataMunch-MCP Dual-Use License** — see
[LICENSE](LICENSE) for the authoritative terms. It is **free for non-commercial
use**; [commercial use requires a paid license](#commercial-licenses).

Conditions on all uses:

1. The above copyright notice and this permission notice must be retained in all
   copies or substantial portions of the Software.

2. Any modifications made to the Software must clearly indicate that they are
   derived from the original work, and the name of the original author
   (J. Gravelle) must remain intact. He's kinda full of himself.

3. Redistributions of the Software in source code form must include a prominent
   notice describing any modifications from the original version.

The Software is provided "AS IS" without warranty of any kind. The full,
controlling terms are in [LICENSE](LICENSE).

 <picture>
   <source media="(prefers-color-scheme: dark)" srcset="https://api.star-history.com/image?repos=jgravelle/jdatamunch-mcp&type=date&theme=dark&legend=top-left" />
   <source media="(prefers-color-scheme: light)" srcset="https://api.star-history.com/image?repos=jgravelle/jdatamunch-mcp&type=date&legend=top-left" />
   <img alt="Star History Chart" src="https://api.star-history.com/image?repos=jgravelle/jdatamunch-mcp&type=date&legend=top-left" />
 </picture>
</a>
