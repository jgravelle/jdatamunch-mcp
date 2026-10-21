# jdatamunch-mcp — Project Brief

**Tests:** `PYTHONPATH=src python -m pytest tests/ -q` (without `PYTHONPATH`, an
installed copy shadows `src/`). CI syncs first: `uv sync --group dev --extra
excel --extra parquet`, then runs pytest; compare TOTAL test counts across
interpreters, never only the passed count.

Maintainers may also have a `CLAUDE.local.md` here (gitignored), which loads
private working notes. Nothing in this file depends on it.

## Project facts
- **GitHub:** `jgravelle/jdatamunch-mcp`
- **Python:** >=3.10
- **Index format:** INDEX_VERSION = 3 (v1→v2→v3 migrations registered in
  `storage/migrations.py`; v3 is additive — new runtime tables are created on
  first ingest, legacy v2 indexes load fine)
- **Version:** `pyproject.toml` is the single source; `__version__` comes from
  `importlib.metadata`, so it reads `"unknown"` under `PYTHONPATH=src`.
- Per-release notes live in `CHANGELOG.md` and the GitHub releases.

## Engineering lessons

Rules that outlived the releases that produced them. Each names the version it
came from.

**Honest output**

- ⚠⚠ **A count with no time basis gets the wrong one supplied by the
  reader**, usually "per request". `tool_surface.schema_tokens_avoided` now
  carries `SCHEMA_TOKENS_BASIS` / `_NOTE` beside it (the schema block is
  cache-read after the first hit). The fix is a LABEL, never a silently scaled
  number. Constants live in ONE module (`schema_token_basis.py`); a test fails if
  a literal appears in a second. (1.31.13)
- ⚠ **Weigh what the client RECEIVES** (`_filter_tools`, including
  force-included tools), through the one weigher `server._schema_weight`. Tier
  sizes are measured by `benchmarks/harness/run_tier_surface.py` into a committed
  `tier_surface.json`, never hand-typed. (1.31.13)
- ⚠ **A setting that implies a saving it does not deliver is a defect.** The
  `standard` tier drops three tools and 5.6% of the schema payload; it stays as a
  capability bundle and is documented as not a token saving. (1.31.13)
- ⚠⚠ **A disclosure inside `_meta` is deleted by the default config**
  (`get_meta_fields()` returns `[]`). Anything the caller must see is re-attached
  TOP-LEVEL after filtering: `truncated` + `truncated_note` (1.31.2), the
  absence-evidence ref (1.26.0), the session budget block (1.21.0). A silently
  shortened answer is worse than a refused one.
- ⚠ **Tool descriptions say what they do NOT grade.** `get_dataset_health`
  grades structure and completeness, not whether values are right;
  `validate_index` checks the index, not the data; `suggest_keys` ranks from
  profile statistics. Every clause is derived from the tool's own guards or
  schema defaults. (1.31.7)
- ⚠ **Pricing-constant changes track vendor prices.** A price reduction is not a
  correction of an inflated figure; never describe it as one. Price keys are wire
  format and never rename on 1.x. (1.19.0)

**Verdicts, freshness and absence**

- ⚠⚠ **Sample a probe BEFORE the work it guards.** `index_changed_since_load`
  was read after a semantic search that lazily embeds and persists columns into
  the same `data.sqlite`, so every dataset's first search reported itself as
  rewritten. Notes are keyed on the CAUSE of `degraded`, not the state; an
  unknown cause falls back to the state note. (1.31.11)
- ⚠⚠ **Only `absent` proves absence.** `degraded` (keyword-only, partial scan)
  does not; a truncated walk does not. A refused scan is still recorded so citing
  it returns the reason. jData has no index-freshness channel, so every absence
  proof states `index freshness: not tracked by this product`. (1.26.0)
- ⚠ **A rewrite underneath a scan is a filesystem fact and IS enforced**: zero
  results + detected rewrite ⇒ `degraded`. The dataset mtime spans `data.sqlite`
  and its `-wal`, not just `index.json`. Unknown is not changed: an unstamped
  index or an unreadable mtime reads as unchanged. `channels.index: "rebuilding"`
  appears only as a positive detection, never a standing `fresh`. (1.27.0)
- ⚠⚠ **`fresh` is reachable only via `verify_source=True`**, which re-hashes the
  source. The cheap default proves `stale` / `missing_source` with a `stat` and
  otherwise says `unknown`; a matching size does not prove matching content.
  `source_freshness` can never return `fresh` (a test pins it). (1.30.0)
- ⚠ **Coverage: empty means unknown, never fabricated.** `depth="shallow"`
  records `walk:"truncated"` without a count rather than draining or guessing.
  (1.20.0)
- ⚠ **No `low_confidence` verdict state**: jData scores are rank-normalized (the
  top is always 1.0), so there is no calibrated signal to threshold. (1.18.0)
- ⚠ **Handoff claim ids are unique across the whole handoff** (they are citation
  anchors). With no claims, a handoff stays `jdatamunch.handoff/v1` and
  byte-identical. (1.25.0)
- ⚠ **The offloadable annotation LABELS and never routes**; it fails closed, and
  a default call is refused because freshness is unknown. `offload` is GENERATED
  from jcodemunch-mcp's module and a pinned `CONTRACT_DIGEST`; never hand-edit it.
  (1.31.0)

**Process, stdio and platforms**

- ⚠⚠ **JSON-RPC owns a private stdout.** `redirect_stdout` rebinds `sys.stdout`
  only; a C extension, an inherited fd 1 or another thread still writes to the
  framed stream. `stdio_guard.claim_stdout()` dups the real stdout for the
  transport and points fd 1 at stderr. Its tests run REAL subprocesses. (1.31.6)
- ⚠⚠ **The first `import sentence_transformers` happens on the MAIN thread,
  before the stdio loop** (`embeddings.warm_up_provider()`). Imported inside an
  `asyncio.to_thread` worker, torch's native DLLs deadlock on the Windows loader
  lock. Opt out with `JDATAMUNCH_EAGER_EMBED_IMPORT=0`. (1.29.1)
- ⚠ **`call_tool` takes the storage root from `DATA_INDEX_PATH`, not from the
  arguments.** A test that passes `storage_path` in the arguments reads the real
  `~/.data-index`. (1.29.1)
- ⚠ **The `initialize` response carries an `instructions` string** (cap 1,000
  chars), the one steering text that survives a host deferring tool schemas, and
  `Server(..., version=__version__)` so `serverInfo` reports ours.
  `tests/test_mcp_instructions.py` binds the prose to the catalog. (1.31.9)
- ⚠ **Text IO and CLI output declare their encoding.** Without `encoding=`,
  Windows uses cp1252; `sys.stdout` is UTF-8 on a terminal and the locale stream
  when piped. `_force_utf8_stdio()` runs first in `main()`. (1.31.3)
- ⚠ **BM25 tokenization is Unicode-aware**: `\w+` keeps `user_id` whole, and CJK
  text gets overlapping character bigrams, identically at index and query time.
  (1.23.1)
- ⚠ **Mislabeling a writer as read-only is the harmful direction.** Any tool that
  can mutate persistent state under any argument is in `_NON_READONLY_TOOLS`.
  (1.17.0)

**Tests, gates and packaging**

- ⚠⚠ **Module-scope `pytest.importorskip` hides how many tests a file holds**: it
  raises during import, so a 29-test file reports as one skip. Use
  `importlib.util.find_spec`; `tests/test_optional_dep_skips_are_visible.py`
  bans the pattern. A whole module missing is invisible from the passed count and
  the skip count; only the TOTAL shows it. (1.31.10)
- ⚠⚠ **ruff's rule set is explicit** (`select = ["E4","E7","E9","F"]`) and ruff
  is in the dev group. An `ignore`-only config means whatever ruff shipped that
  week (446 findings where 10 were intended). Widening the set is a decision, not
  an upgrade. `tests/test_undefined_names.py` is the F821 gate. (1.31.4, 1.31.5,
  1.31.9)
- ⚠⚠ **`uv.lock` is gitignored and must stay untracked**: CI runs plain
  `uv sync`, not `--locked`. Copy a sibling repo's INTENT, not its steps (its
  `--locked` would fail here). (1.29.1, 1.31.4)
- ⚠⚠ **The sdist credential guard builds a REAL sdist** (`tests/test_build.py`)
  and scans it. `.claude/` is excluded only by `pyproject.toml`, so the exclude is
  the only defense. The config check parses TOML, because a comment containing
  `.claude/` satisfied a text scan; `.claude-plugin/` must not false-positive.
  (1.31.12)
- ⚠ **The license is declared on every surface with one identifier**:
  `LicenseRef-jDataMunch-Dual-Use` in `pyproject.toml` (PEP 639, plus
  `license-files`), `plugin.json` and the mcpb manifest. A `Version` line and an
  identifier suffix imply each other both ways. PyPI metadata is immutable per
  version. (1.31.8)
- ⚠ **Run every text-scanning guard against the defect PUT BACK**, and add a
  positive test for a correct shape it must not flag. (1.31.3, 1.31.12)

## Issue + release policy

**1. One issue, one verdict.** A multi-finding report gets SPLIT at triage into
one issue per finding, cross-linked, credit on each. Detail is not discouraged;
the reason is closure mechanics. A 4-finding issue closes only when the last one
settles, so three finished fixes sit behind one unfinished conversation.
⚠ **Do not optimize the open COUNT** — consolidating five issues into one gate
is what produced a single artifact able to block a release.

**2. A release is NEVER blocked on an open issue**, including a verification we
asked for. Done + tested + green ships on schedule, carrying a plain
verification-status line that is deliberately WEAKER than a sign-off; never blur
the two in a changelog. Late re-verification counts IN FULL and is announced
retroactively. **Every timebox names its default action.**
⚠ **A reviewer's thoroughness must never become a veto.**

**3. A contributor's PR is never the only path.** Timebox and keep our own path warm.

**3a. NO TIMEBOX WE OFFER RUNS LONGER THAN 24 HOURS.** It covers every shape:
signing the CLA, opening a PR already written, and taking an issue to implement.

⚠⚠ **The window is only fair BECAUSE the default action preserves credit.** At
expiry we implement the fix ourselves and credit them in the CHANGELOG, the
release notes and the close comment. So the 24 hours decide whose COMMIT it is,
never whether they are credited and never whether the fix ships. **Quote the
default in the same comment as the deadline** — a clock with an unstated
consequence reads as a threat, and it is not one.

⚠ **An extension the contributor ASKS FOR is not the same as a default we hand
out**, and CONTRIBUTING.md invites the ask by name. Hold it when they ask; the
clock exists to stop work going quiet, not to catch anyone out.

⚠ **Do not shorten a timebox already posted.** State the new window on new PRs.

**3b. A MERGEABLE contributor PR merges BEFORE any changelog-touching work of
our own.** Every entry we add occupies the same `[Unreleased]` block a
contributor's entry occupies, so each of our merges conflicts their branch — and
**a CONFLICTING fork PR has no `refs/pull/N/merge` and therefore gets NO CI AT
ALL.**

```bash
GITHUB_TOKEN="" gh pr list --state open --json number,author,mergeable,mergeStateStatus   --jq '.[] | select(.author.login != "jgravelle") | "#\(.number) \(.author.login) \(.mergeable) \(.mergeStateStatus)"'
```

⚠ **The boundary:** a BLOCKED PR cannot go first. Then we ship anyway (policy 2)
and **we own the resolution** — push the merge to their branch and say on the
thread that the conflict was ours.

⚠⚠ **`license/cla` is a required status check on `master`.** `enforce_admins:
false` is deliberate (it lets a merge be pushed to a contributor's fork), and
`strict: false` avoids forcing a rebase after every release. Our push to a fork
wipes `license/cla` from the new head, which reads as `BLOCKED` until the bot
re-posts.

⚠⚠ **A FORK PR SHOWING ONLY `license/cla` HAS NOT BEEN TESTED — IT HAS BEEN
SILENTLY HELD**, and `gh pr checks` lists only checks that RAN.

```bash
GITHUB_TOKEN="" gh api "repos/jgravelle/<repo>/actions/runs?status=action_required&per_page=30"   --jq '.workflow_runs[] | "\(.id)|\(.name)|\(.head_branch)"'
GITHUB_TOKEN="" gh api --method POST "repos/jgravelle/<repo>/actions/runs/<id>/approve"
```

⚠⚠ **Never answer "an issue is stuck" with aggregate stats** — the cost of a
blocked issue is CONCENTRATED, not distributed; design at the OUTLIER.

Surfaces: `CONTRIBUTING.md` + `.github/ISSUE_TEMPLATE/`.

⚠ **Dev deps are a PEP 735 `[dependency-groups]` block, NOT an extra** — there is
no `.[test]` or `.[dev]`. Use `uv sync` (or `pip install -e . pytest
pytest-asyncio pytest-cov`). CI installs with `uv sync` and never runs the
command the docs give a human, so check that command by hand.

## Key Files
```
src/jdatamunch_mcp/
  server.py                    # MCP tool definitions + call_tool dispatcher
  config.py                    # Index path, max rows env vars
  perf.py                      # (1.16.0) Per-tool latency telemetry: in-memory ring (always on) + opt-in SQLite sink (<index_path>/perf_telemetry.db, JDATAMUNCH_PERF_TELEMETRY=1). record()/latency_stats()/perf_db_query(). Backs analyze_perf; call_tool records each dispatch.
  embed_drift.py               # (1.15.0) Embedding-drift canary: 16 data-flavored strings pinned in <index_path>/embed_canary.json; capture_canary + check_drift (cosine drift vs the live provider). Reuses embeddings.detect_provider/embed_texts/cosine_similarity. Backs the check_embedding_drift tool.
  tuning.py                    # (1.14.0) Ranking-weight overrides for search_data. DEFAULT_WEIGHTS (single source of truth for the weight vector) + load_effective_weights() (defaults < global < per-dataset); persisted in <index_path>/ranking_tuning.json. The tune_weights tool's backing store.
  security.py                  # Path validation
  redact.py                    # (1.5.0) Cell-level redaction. Built-in patterns: email, ssn (SSA-rule), credit_card (Luhn-checked), jwt, private_key (PEM blocks), aws_access_key, github_pat, slack_token, api_key_prefixed (Stripe), api_key_openai. Public API: redact_rows / redact_value_distribution / redact_scalar_list / merge_summary / redaction_meta. Wired into get_rows/sample_rows/run_sql/aggregate/describe_column with redact=True default and `_meta.redaction` audit block on every response. Numeric cells are never scrubbed. (1.6.0) Adds redact_sql_query_text (strips string + numeric literals, applies cell registry) and redact_trace_message (IPv4 + cell registry) for the runtime ingest chokepoint.
  runtime/                     # (1.6.0) Phase-1 runtime traffic ingest. sql_log.py = pg_stat_statements CSV + generic JSONL parser (.gz transparent), extracts table + column refs via regex. ingest.py = orchestrator (parse → redact → resolve → upsert) — per-dataset SQLite, ON CONFLICT accumulates calls + total_time. tables.py = runtime_query_calls + runtime_redaction_log schemas + idempotent ensure_runtime_tables.
  embeddings.py                # Provider detection (sentence-transformers/Gemini/OpenAI), embed_texts(), cosine_similarity()
  parser/
    normalize.py               # Cross-parser native→string normalization (1.0.0)
  profiler/
    column_profiler.py         # Per-column type inference, Welford+Neumaier stats, finalize_profile()
    tdigest.py                 # Streaming quantile estimator (1.0.0)
    hll.py                     # HyperLogLog approximate cardinality (1.0.0)
    semantic_types.py          # Semantic column-type detectors (1.0.0)
  storage/
    data_store.py              # DataStore: load/save DatasetIndex (index.json) + crash-safety helpers
    migrations.py              # INDEX_VERSION migration registry (1.0.0)
    sqlite_store.py            # SQLite backend: create_table, insert_batch, create_indexes
    embedding_store.py         # ColumnEmbeddingStore: column embedding CRUD in dataset SQLite
    token_tracker.py           # estimate_savings, record_savings, cost_avoided
  tools/
    index_local.py             # Index a local CSV/Excel file (single-pass profiling + SQLite load)
    list_datasets.py           # List indexed datasets
    describe_dataset.py        # Full schema profile (primary orientation tool)
    describe_column.py         # Deep stats for one column
    sample_rows.py             # Sample rows (optionally filtered)
    get_rows.py                # Rows by index range or filter
    search_data.py             # Search rows by column value / pattern. Ranking weights resolved per-query via tuning.load_effective_weights (tunable by tune_weights).
    analyze_perf.py            # (1.16.0) Per-tool latency (p50/p95/max/error_rate) + result-cache hit rates; window=session (ring) or 1h/24h/7d/all (sink). Mirrors jcm/jdoc analyze_perf.
    check_embedding_drift.py   # (1.15.0) Canary-based embedding-drift detector (force=re-pin, else report cosine drift + alarm). Thin wrapper over embed_drift.py. Mirrors jcm/jdoc check_embedding_drift.
    tune_weights.py            # (1.14.0) Inspect / set / reset the search_data ranking weights. No ledger, so tuning is explicit (vs jcm/jdoc's learned). Mirrors their tune_weights agent-facing contract.
    aggregate.py               # Aggregate (count/sum/mean/min/max) with optional groupby
    get_session_stats.py       # Session token savings stats
    get_schema_drift.py        # get_schema_drift: compare schema between two datasets (added/removed/type/nullability)
    get_data_hotspots.py       # get_data_hotspots: rank columns by data-quality risk. v1: null + cardinality + outlier. v2 (1.10.0): adds traffic signal from runtime_query_calls when present (weights null=0.30, card=0.20, outlier=0.20, traffic=0.30); honest-hint caveat in _meta.runtime_caveat when include_runtime=True but no traces ingested; v1 scoring preserved when traces absent or include_runtime=False.
    delete_dataset.py          # delete_dataset: remove indexed dataset and SQLite store
    embed_dataset.py           # embed_dataset: precompute column embeddings for semantic search
    get_correlations.py        # get_correlations: pairwise Pearson correlations between numeric columns
    join_datasets.py           # join_datasets: cross-dataset SQL JOIN via ATTACH DATABASE
    summarize_dataset.py       # summarize_dataset: regenerate NL summaries for indexed dataset
    index_repo.py              # index_repo: index data files from a GitHub repository
    list_repos.py              # list_repos: list GitHub repositories indexed via index_repo
    validate_index.py          # validate_index: integrity check on dataset (1.0.0)
    get_dataset_history.py     # get_dataset_history: profile snapshots over time (1.0.0)
    find_unused_columns.py     # (1.7.0) Runtime-driven dead-column detection. Reads runtime_query_calls + dataset schema; surfaces columns with reason ∈ {zero_hits, stale, below_min_calls}. Refuses when no runtime data exists. PK + audit-field exclusion on by default. Audit patterns: created_at, updated_at, _dbt_*, etl_*, etc.
    check_column_drop_safe.py  # (1.8.0) Composite preflight: is this column safe to drop? Fuses PK status + FK heuristics (name-match + stem-match like `user_id` → `users.id`) + cross-dataset name match + runtime_query_calls in window. Verdict tiers: pk_blocking, fk_blocking, runtime_observed, cross_dataset_blocking, safe_to_drop. Ranked blockers (≤5) + recommended_action. Mirrors jcm's check_delete_safe.
    get_schema_impact.py       # (1.9.0) Transitive impact of a column-level schema change (drop_column / rename_column / retype_column). Walks the inferred FK graph to max_depth, classifies each hit as fk_source / fk_target / cross_dataset_name_match / runtime_traffic. For retype_column, flags type_mismatch entries at FK edges whose partner type wouldn't survive. Returns direct_impact + transitive_impact + summary + normalised blast_score ∈ [0, 1]. Mirrors jcm's get_blast_radius.
    health_radar.py            # (1.11.0) Pure-function core: compute_radar() builds the six (or seven) axis payload from raw signals; diff_radar() computes axis-by-axis deltas + verdict; diff_data_health_radar() is the MCP entry point that validates the input shape. No I/O.
    find_similar_columns.py    # (1.12.0) Multi-signal cross-dataset column similarity. Token Jaccard (snake+camel split) + type + top_values Jaccard + cardinality ratio + embedding cosine (when present). Embedding-aware weights (emb=0.50,name=0.20,value=0.15,type=0.10,card=0.05) vs lexical-only (name=0.45,value=0.30,type=0.15,card=0.10). Union-find clusters. Verdict tiers: near_duplicate / naming_drift / parallel_definition / overlapping_topic. differs_by breakdown per pair. Mirrors jcm's find_similar_symbols.
    data_health_radar.py       # (1.11.0) Six-axis dataset health radar: null_health, type_confidence, cardinality_health, pk_presence, semantic_coverage, schema_stability (+ optional runtime_coverage). Reads index.json + history + runtime_query_calls; produces a 0-100 score per axis + composite + A-F grade. Omitted axes never silently penalise the composite — they appear in `omitted_axes`.
    get_redaction_log.py       # (1.10.0) Forensic accounting of PII redactions for a dataset. Reads runtime_redaction_log (populated by ingest_sql_log with redact=True) and returns per-pattern counts + sources + last_seen. Filters by source ('sql_log') and since_days. Empty result is not an error — distinguished from unknown-dataset / invalid-source via structured `reason` codes. Mirrors jcm's get_redaction_log keyed on dataset_id.
```

## Architecture Notes
- `index_local` does a two-phase single pass: type inference on first 10k rows,
  then full pass for profiling + SQLite load.
- `describe_dataset` returns schema + stats from `index.json` (no SQLite query needed).
- `describe_column` returns deeper stats including full top-value distribution.
- Token tracker uses byte approximation (`raw_bytes / 4`) for zero-dependency speed.

## Releasing
- **Tests** (`.github/workflows/test.yml`): matrix ubuntu+windows x py3.10-3.13 on push/PR to master; pytest + sdist sensitive-path check.
- **Release** (`.github/workflows/release.yml`, added v1.16.0): on every push to master, *after Tests passes*, auto-tags + creates a GitHub release when `pyproject.toml`'s version has no release yet. **Builds the wheel + sdist (`python -m build`) and attaches both to the release** — the console one-click installer reads the latest release's `.whl`, so a release MUST carry it. No-op for docs-only / non-bump pushes. Gated via `workflow_run` + `conclusion == 'success'` so a red commit never gets tagged. **Don't hand-tag on a version bump** — the workflow does it (hand-creating a release first just makes the workflow no-op, which is fine). (Asset-attaching added 2026-06-28 after v1.15.0/v1.16.0 shipped bare and broke the console installer; v1.16.0's wheel was backfilled by hand.)
- **Read the Tests run for the pushed SHA BEFORE the manual upload.** The GitHub
  release is gated (`workflow_run` + `conclusion == 'success'`), so a red commit
  never gets tagged — **but PyPI is manual and a human can still upload against a
  red build**, and a PyPI upload cannot be taken back. Four consecutive jcm
  releases (.259–.262) were published, tagged and uploaded red because nobody
  read the check; the very next release caught a real failure on all 8 matrix
  jobs with nothing shipped.
  ```bash
  GITHUB_TOKEN="" gh run list --repo jgravelle/jdatamunch-mcp --limit 3 \
    --json headSha,conclusion,status,name
  ```
- **PyPI is still manual**, and it runs through `uvx`, not through a dev box's
  interpreters. CI has no PyPI credential. To automate, add a publish job using
  PyPI Trusted Publishing (OIDC, no stored secret) once the publisher is
  configured on pypi.org.
  ```bash
  uvx --from build pyproject-build
  uvx --from twine twine check  dist/*X.Y.Z*   # gate: fails BEFORE anything ships
  uvx --from twine twine upload dist/*X.Y.Z*
  ```
  ⚠⚠ **Neither `python -m build` nor the global `twine` works here, and the
  failure is not hypothetical — it stopped jdoc 1.132.0 mid-release
  (2026-08-12), and that release's notes named this repo as the next victim.**
  `build` is not in the project venv (`uv run python -m build` → *No module named
  build*), and the global twine validates metadata with the global `packaging`,
  which tops out below the `Metadata-Version` an always-latest hatchling emits
  (`InvalidDistribution: '2.5' is not a valid metadata version`).
  ⚠⚠ **`pip install -U packaging` is the WRONG fix.** That interpreter is a
  kitchen sink and installed packages pin `packaging<25` — upgrading breaks
  working packages to satisfy a release tool. `uvx` resolves twine and its
  `packaging` in a throwaway env, leaves the global alone, and cannot rot when
  the next metadata version arrives.
  ⚠ **`twine check` is the load-bearing line, not the upload.** Without it the
  metadata failure is discovered *during* upload — possibly after the wheel is on
  PyPI and the sdist is not, which is a half-published version that cannot be
  re-uploaded.
  ⚠ The `python -m build` in the release.yml bullet above is **correct and must
  stay**: CI installs `build` and `twine` fresh in a clean runner, where none of
  this applies. The defect was only ever in the instruction aimed at a human on
  a dev box. `tests/test_release_tooling_binding.py` pins both directions.
- **MCP registry** (published since 2026-08-06, nine-plus versions). When you
  read back a publish:
  ⚠⚠ **Each row is `{server: {...}, _meta: {...}}` (schema 2025-12-11).** `name`,
  `version` and `packages[]` live under **`server`**; `isLatest` and
  `publishedAt` live under
  `_meta["io.modelcontextprotocol.registry/official"]`. **A flat `row["name"]`
  read returns ZERO rows on a publish that completely succeeded** — measured on a
  confirmed-good publish where the flat parse found 0 of 45 rows.
  ⚠⚠ This is a SECOND false negative on top of the known paging trap, and unlike
  that one it **survives `&limit=100`**, so the documented remedy does not help
  and the symptom is indistinguishable from a failed publish. **Never re-publish
  on a zero-row read — fix the parse.** Confirm `server.packages[].version`
  advanced, not only `server.version`; an entry can move one and not the other.
  ⚠ Read-after-write lag is real: a good publish read back as absent ~90 seconds
  later and appeared on the next poll. **Absence is not evidence of failure.**

## Standing lessons (suite-wide)

Drawn from jcodemunch-mcp 1.108.291 (2026-08-22) and recorded here because each
one is about how we WORK, not about that repo's code. ⚠ **The byte-mass defect itself was CHECKED HERE and is ABSENT** (2026-08-22),
so do not re-run the audit: every `estimate_savings` call site passes
`idx.source_size_bytes` (or `meta["file_size"]`) — the file's own size, one
authoritative number — instead of summing over rows, columns or any other nested
entity. **Clean by construction, and it is the pattern jcm had to be corrected
INTO**: ask the authority rather than reconstructing its answer. Keep it that way
if a future tool needs a savings baseline.

- **A competitor's fix list is a free defect probe.** A rival shipped
  `fix(gini): measure a file's lines as its own span, not the sum of every node`
  and named the defect precisely enough to check ours in one query — jcm's
  byte-concentration metric summed nested symbol spans and read 2.85x the real
  size of the files it described. Read competitors' `fix(...)` TITLES against
  whatever we built the same way; it is minutes, and it finds what our own tests
  were written not to see.
- **A ratchet can pass against the defect it names.** The guard written for that
  fix used a depth-limited regex and walked straight past
  `sum(int(s.get("byte_length", 0) or 0) for ...)`, two parens deep. **A green
  ratchet and an absent ratchet look identical**, because the tree is clean when
  you write it. Run every text-scanning guard against the defect PUT BACK, and
  add a positive test pinning a correct shape it must not flag — otherwise the
  ratchet becomes standing pressure to "fix" working code.
- **A defect is not evidence against the number it did not produce.** The same
  release nearly published a claim that the correction put a basis change behind
  our public savings figure. It did not: the number quoted was one seat's local
  meter, the site publishes a separate opt-in aggregate, and the tools feeding it
  were already correct. **Trace the path from a defect to the SPECIFIC figure
  before implicating it**, and never net coverage-conservatism (who reports)
  against a per-call arithmetic error (one call's maths) — different axes,
  opposite directions. If the apportionment cannot be computed, the honest output
  is "not computable" plus the bound, never a restated number. **Not restating is
  the conservative action, not the convenient one.**
- **A metric that credits us is the one direction a defect must never sit.** When
  the same shape turned up in jcm's token-savings baseline, the correction
  LOWERED our own reported numbers and shipped in the same release rather than
  as follow-up work. Where history could not be recomputed it was disclosed
  (`lifetime_unattributed`, a basis generation) rather than quietly carried or
  quietly rewritten — **a recomputed history is a guess wearing a measurement's
  clothes.**

## Maintaining this file

⚠⚠ **`CLAUDE.md` is public** (it ships in the sdist), so it holds engineering
rules only: no release narrative, no internal process notes.
`tests/test_public_brief.py` fails if either returns. Per-release detail belongs
in `CHANGELOG.md`; write a durable rule into "Engineering lessons" in neutral
wording.

⚠⚠ **It is budgeted at 130,000 chars, gated by `tests/test_claude_md_size.py`.**
The harness stops loading a project brief at 150,000; the gap is deliberate,
because a ceiling that fires at the cliff fires for the first time in the session
it breaks. ⚠ **A rule that names one section licenses every other section to
grow**: jcodemunch-mcp's brief hit 200,543 chars and stopped loading while its
size practice was being followed.

## Benchmarks
Real production dataset (LAPD crime records, 1M rows):

| Dataset | Rows | Cols | File Size | Avg Ratio |
|---------|-----:|-----:|----------:|----------:|
| crime.csv | 1,004,894 | 28 | 255.5 MB | **25,333x** |

Baseline = full raw CSV tokenized. jDataMunch = `describe_dataset` + `describe_column`.
Benchmark harness: `python benchmarks/harness/run_benchmark.py <file.csv>`

Tool descriptions are scored suite-wide against the rubric in arXiv:2602.14878; the
scorer, both scoring frames and the dated numbers live in jcodemunch-mcp under
`benchmarks/description_smells/`. When you add a tool here, give it two substantive
sentences and one stated boundary, and re-run that scorer with a fresh
`jdata_tools.json` dump.
