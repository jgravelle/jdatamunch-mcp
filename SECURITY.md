# Security and Data Handling

jDataMunch profiles and queries tabular data on your machine. This document states exactly what runs locally, what leaves the machine, and how to turn each of those off.

---

## Reporting a vulnerability

Report security issues privately via [GitHub Security Advisories](https://github.com/jgravelle/jdatamunch-mcp/security/advisories/new) rather than a public issue. Please include a reproduction, the version (`pip show jdatamunch-mcp`), and your platform. Security reports are triaged ahead of feature work.

Supported versions: the current 1.x release line receives security fixes.

---

## Your data stays on your machine

**jDataMunch never uploads your data.** Files are parsed, profiled, and indexed locally. Queries — filters, aggregations, joins, SQL — execute locally against the local index. No dataset content, column name, file path, or row value is transmitted anywhere as part of normal operation.

Indexes are stored on disk under your home directory. Deleting that directory removes every trace of indexing.

---

## Background behavior, fully disclosed

Everything jDataMunch does beyond answering a tool call is listed here. All of it is visible, opt-out, and reversible.

- **Anonymous savings counter.** The server can contribute an anonymous delta to the public community meter at `j.gravelle.us` — a random install ID plus aggregate token counts. **No data, no column names, no dataset names, no file paths, no queries, no PII.** Counters only. Opt out with `JDATAMUNCH_SHARE_SAVINGS=0`.
- **Startup import of the local embedding backend.** When a native embedding provider is configured, the server imports that library at startup on the main thread, before serving. This is not polish: importing it later, on the worker thread a tool call runs in, deadlocks on the Windows loader lock and hangs the call forever. Nothing is downloaded and no network is touched — the import alone is what matters. Opt out with `JDATAMUNCH_EAGER_EMBED_IMPORT=0`.
- **Local performance ledger — off by default.** With `JDATAMUNCH_PERF_TELEMETRY` enabled, per-tool latencies are recorded to a local database that `analyze_perf` reads. **It never leaves your machine.** Off unless you turn it on; delete the file to erase it.
- **Local index storage.** Datasets and their profiles are stored under your home directory.

Beyond the user-invoked calls below, the base package makes no other network calls and leaves no persistent background processes. There is no scheduler and no background reporting.

### User-invoked network calls

Each of these happens only when you invoke it, never on import and never in the background.

- **GitHub repository indexing.** `index_repo` fetches data files from `api.github.com` using a token you supply. Private repositories require your own credential; jDataMunch stores nothing and proxies nothing.
- **Embedding providers.** When you configure one, `embed_dataset` and semantic search call that provider's API. **Column names and sampled values are sent to the provider you chose** — this is inherent to embedding them, and it is why no provider is configured by default.
- **AI summaries.** `JDATAMUNCH_USE_AI_SUMMARIES` calls your configured provider only when enabled.

⚠ **Paid cloud providers are never called automatically.** Setting a provider API key in your environment is not consent to bill it; jDataMunch requires an explicit opt-in before sending anything to a paid endpoint.

---

## Input validation controls

`security.py` enforces these on every call:

| Control | What it prevents |
|---|---|
| `validate_file_path` | Path traversal outside permitted roots |
| `validate_dataset_id` | Injection through dataset identifiers |
| `sanitize_column_name` / `validate_column_names` | Column names reaching a query engine unescaped |
| `validate_filter` | Filter expressions being used as an injection vector |
| `verify_package_integrity` | Running a tampered install |

Response size is bounded by `JDATAMUNCH_MAX_RESPONSE_TOKENS` and `JDATAMUNCH_MAX_ROWS`, so a query against a very large dataset cannot flood a context window by accident.

---

## Optional extras and what they pull in

| Extra | Adds | Additional surface |
|---|---|---|
| `excel` | `.xlsx` / `.xls` | Spreadsheet parsing libraries |
| `parquet` | `.parquet` | Columnar format libraries |
| `semantic` | Embedding-based column search | Local model runtime; no network unless a remote provider is configured |
| `anthropic` / `gemini` | AI summaries | Calls that provider's API when enabled |
| `all` | All of the above | All of the above |

The base install has none of these.

---

## Environment variables affecting data movement

| Variable | Default | Effect |
|---|---|---|
| `JDATAMUNCH_SHARE_SAVINGS` | on | `0` disables the anonymous savings counter entirely |
| `JDATAMUNCH_USE_AI_SUMMARIES` | off | Enables calls to a configured summary provider |
| `JDATAMUNCH_PERF_TELEMETRY` | off | Enables the local-only performance ledger |
| `JDATAMUNCH_EAGER_EMBED_IMPORT` | on | `0` defers the startup embedding import (see the Windows deadlock note above) |
| `JDATAMUNCH_OFFLOADABLE` | off | Adds the advisory `_meta.offloadable` label; sends nothing anywhere |

A default install with no configuration sends nothing but the anonymous counter, and that is one variable away from silent.
