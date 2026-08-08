# Changelog

## [1.31.4] - 2026-08-07 - A lint gate

CI had no lint job. It has one now, added alongside jdocmunch-mcp.

`ruff check src/` runs once on Linux, outside the test matrix, with rule
selection and grandfathered ignores in `[tool.ruff.lint]`.

### What it found here

Nothing dangerous, which is worth saying plainly rather than implying the gate
rescued this repo. **jdata was already clean on F821 (undefined names)** -- the
rule that represents a runtime crash. jdocmunch-mcp was not, and had shipped one
inside an `except` block, which is what prompted adding gates to both.

Fixed while making the gate green: 9 unused imports, 3 f-strings with no
placeholders, and one `== None` that should be `is None` (a plain dict `.get()`,
not a pandas elementwise comparison -- checked before changing it). All
mechanical; the suite is unchanged at 794 passed.

Grandfathered with counts and reasons, not silently excluded: `E702` (8 terse
one-line guards) and `F841` (2 dead locals).

### One thing deliberately not copied from jcm

jcodemunch-mcp's lint job runs `uv sync --locked`. That would **fail here**:
`uv.lock` is gitignored in this repo, so there is no lock to honour. The job uses
plain `uv sync --group dev`, matching this repo's own test job.

Second time in two releases that copying a sibling's step verbatim would have
broken something. Copy the intent, not the step.

## [1.31.3] - 2026-08-07 - Text-mode IO and CLI output declare their encoding

Suite parity with jcodemunch-mcp, which swept three directions of the same cp1252
hazard. This repo was scanned separately; nothing about a defect living in one
server implies it lives in its siblings, and nothing implies it does not.

| Direction | Found here |
|-----------|------------|
| subprocess **input** | 0, already clean since the 2026-08-03 sweep |
| our own **output** | 0 |
| **file IO** | 7 sites |

### File IO

`open()`, `Path.read_text()` and `Path.write_text()` use the platform default
when no encoding is given, which is cp1252 on Windows. Reading a UTF-8 file then
raises on the five bytes cp1252 leaves undefined (`81 8D 8F 90 9D`) and silently
mangles everything else. Fixed at the savings tracker (4) and the repo-SHA markers (3).

⚠ Every one of these holds ASCII-only content today (JSON with ASCII keys, and git SHAs), so nothing
was corrupt. The exposure is a future non-ASCII value landing in one of them.
Stated plainly rather than dressed up as a bug fix.

### Output

Nothing non-ASCII is emitted here today, so nothing is broken. `_force_utf8_stdio()`
is added anyway, at the top of `main()`, because the defect arrives with the next
character someone adds and it arrives as a crash on a user's machine that nobody
can reproduce interactively -- jcm shipped exactly that for an unknown number of
releases, since it only appears through a pipe. The tests assert the mechanism
rather than a repaired symptom, and say so.

### Guards

Two AST guards ported from jcodemunch-mcp, each with an **empty ratchet**: a new
unencoded call fails, and a listed exemption that gets fixed must be deleted so
the set cannot decay into a permanent excuse.

The file-IO scanner matches the file mode **by value** rather than by argument
position, because `open(file, mode)`, `path.open(mode)` and `wave.open(file,
mode)` put it in three different slots -- and two earlier position-based versions
each produced a different class of false positive. It is tested in both
directions: correct code must not be flagged, broken code must be. A guard with
false positives is one nobody believes, and a ratchet nobody believes collects
exemptions.

⚠ The non-vacuity floor is sized to THIS repo's tree (60+ files), not copied
from jcm. A floor larger than the tree fails forever; a floor of 1 passes over a
scan that collapsed to nothing.

### A finding that turned out not to be one

While bumping version pins I noticed `uv.lock` recording an older version than
`pyproject.toml`, and started writing it up as drift that jcodemunch-mcp's
`test_lockfile_version_sync.py` gate would have caught.

It is not drift. **`uv.lock` is gitignored in this repo, deliberately** -- CI runs
plain `uv sync`, not `uv sync --locked`, so a committed lock would silently change
dependency resolution. The file is a local artifact and cannot drift across
releases because it was never in a release.

Recorded here because the near-miss is the useful part: porting jcm's lockfile
gate would have shipped a test that fails on a fresh clone, where no `uv.lock`
exists. jcm tracks its lock and this repo does not, and that asymmetry is a
decision, not an oversight. Suite parity is for behaviour contracts, not for
whatever the other repo happens to have in `tests/`.

## [1.31.2] - 2026-08-07 - a truncated response said so in a field the default config deletes

`enforce_budget` trims rows and columns to fit the response token budget and
records what it dropped in `_meta.truncation`. This server strips `_meta`
entirely by default (`get_meta_fields()` returns `[]` unless
`JDATAMUNCH_META_FIELDS` is set), so the notice was deleted before any caller
saw it.

Measured on a 600-row fixture: trimmed to 104 rows, disclosure gone, and the
response is indistinguishable from a complete one.

**A silently shortened answer is worse than a refused one, because the caller
cannot tell the difference.** An agent reading 104 rows as the full result will
draw conclusions about data that was never shown to it -- which for a tabular
retrieval server is the failure mode that matters most.

The truncation record is now captured before `meta_fields` filtering and
re-attached TOP-LEVEL afterwards, as `truncated` plus a `truncated_note` that
states the response is not complete and names `JDATAMUNCH_MAX_RESPONSE_TOKENS`.
That is the same treatment the absence ref (v1.26.0), the budget block
(v1.21.0) and the `empty`/`hint` keys (v1.28.0) already get, for the same
reason: a token the default config deletes is a token the agent never reads.

Truncation behaviour itself is unchanged. Omitted when nothing was trimmed.

Found while checking whether the four defects fixed in jdocmunch 1.124.0 had
siblings here. Two of the four did not (`lstrip("./")` does not appear in this
tree, and the argument contract has been present since the jcm v1.108.175
port). This one is jData's own variant of the same class.

Tests: `tests/test_truncation_disclosure.py` (10), including an end-to-end run
on a real indexed CSV asserting a caller receives `truncated` under the DEFAULT
config, and an omit-when-empty control.

## [1.31.1] - 2026-08-05 - README rewritten as a landing page; SECURITY.md added; benchmark figures corrected

- README restructured and reduced 656 -> 200 lines, with a new Limitations section. Corrected two commands that did not exist: jdatamunch-mcp has no CLI subcommands, so `index-local` and `--version` were wrong.
- SECURITY.md added. The README linked a security policy and none existed. Documents the four network destinations, the security.py validators, and every JDATAMUNCH_* variable affecting data movement.
- benchmarks: reduction was published as `100.0%`; the true values are 99.9957-99.9965%. run_benchmark.py rounded to 1dp, which asserts total elimination. Fixed in the harness so a regeneration cannot reintroduce it.
- benchmarks: the grand total assumed the file is re-read for every task, multiplying the headline saving 5x. Both totals now published side by side with the assumption stated.
- benchmarks: METHODOLOGY claimed cl100k_base agrees with Claude estimates within ~5%. Claude 4.7+ uses a newer tokenizer producing ~30% more tokens; the claim is corrected.
- No code change to the server. Docs and benchmark harness only.

## [1.31.0] - 2026-08-04 - offloadable-work annotation, off by default

`describe_column` can now tell you whether the work its payload enables is
grunt-work a cheaper model can do. Set `JMUNCH_OFFLOADABLE=1` (or
`JDATAMUNCH_OFFLOADABLE=1` for this server alone) and the reply carries an
advisory `_meta.offloadable` block. Off by default.

**We label. We never route, execute, or hold model credentials.** No new
process, no network call, no new tool, no model of ours runs.

Routers classify the *prompt*. This sits downstream of retrieval and classifies
*the evidence just assembled*: whether the profile is actually in the payload,
how many datasets it spans, and whether the source-file reading came back
unknown. Tri-state, reason-coded, fails closed.

⚠ **A default call is refused, and that is correct rather than a gap.** jData
does not assert index freshness it cannot back, so the cheap reading answers
`unknown` and the criterion refuses on it. Pass `verify_source=True` — which
re-hashes the source file and is the only thing that actually establishes
currency — and a clean profile is labelled `offloadable`. `verify_with` points
at exactly that call. **The annotation is only ever as strong as the evidence
behind it, and here you can see the difference the evidence makes.**

Identical field contract in jcodemunch-mcp (symbols/files) and jdocmunch-mcp
(sections/documents): the vocabulary is *units* and *containers*, and a pinned
`CONTRACT_DIGEST` plus a generated contract test fails the build in any of the
three that drifts.

Additive: one new `_meta` key, emitted only when gated on. No tool-count, schema
or `INDEX_VERSION` change. Tests `tests/test_offload_contract.py` (23).

## [1.30.0] - 2026-08-04 - describe_column discloses whether its source still matches

A column profile is a claim about data on disk. `describe_column` returned that
profile with no indication of whether the file it describes had changed, or been
deleted, since indexing — so a caller could not tell a current profile from one
describing a file that no longer exists. It now emits `_meta.freshness` and
`_meta.verdict`.

⚠ **`fresh` is reachable ONLY through the new opt-in `verify_source=True`, and
that is the design rather than a limitation.** jData's standing product call
(2026-07-24) is that a permanent `index: "fresh"` asserts currency this product
cannot back, which is why the index channel appears only as a positive
detection. This change keeps that rule exactly:

* the cheap default reading proves `stale` or `missing_source` with a `stat`,
  and otherwise says `unknown` — it never asserts `fresh`, and it emits no index
  channel at all when it has proven nothing;
* `verify_source=True` re-hashes the source file, which is the only thing that
  actually establishes currency. Opt-in, because a source can be hundreds of
  megabytes and this runs on ordinary read calls.

A matching size does NOT prove matching content, and the `unknown` reading says
so in its own `reason` rather than leaving a caller to infer it. An in-place
edit that preserves the byte count is invisible to the cheap path and caught by
the hash — there is a test for exactly that, and it is why the two readings
exist separately.

New `DataStore.source_freshness` and `DataStore.verify_source`. A test pins that
`source_freshness` can never return `fresh`, so a later caller cannot obtain
that answer cheaply.

Additive: new `_meta` keys and one defaulted kwarg. No tool-count, schema or
INDEX_VERSION change. Tests `tests/test_source_freshness.py` (13); suite 725
passed / 1 skipped.

## [1.29.0] - 2026-07-25 - an ignored argument cannot prove absence

### Fixed

- **A misspelled parameter no longer produces a citable absence proof.** Suite
  parity with jcodemunch-mcp v1.108.175, where the defect was found live: a
  `search_text` call passed `regex=true` when the parameter is `is_regex`. Every
  tool in all three servers reads its arguments key-by-key
  (`arguments.get("limit", 20)`), so the flag was dropped in silence, the call
  that ran was not the call that was asked for, and the response still reached
  `state: "absent"` and minted a citable absence ref.
- `call_tool` now compares each call's arguments against the tool's published
  `inputSchema` and, when keys were discarded, downgrades an `absent` verdict to
  `degraded` and discloses the ignored keys.

### Notes

- **Disclose on every state, refuse only the absence CLAIM.** Rows an `ok` scan
  returned were really in the dataset and are still the best available answer;
  only the claim that nothing exists is unfounded. Because the refusal is
  expressed as a downgrade, `handoff.absence_refusal` does the refusing and
  there is no second rule to keep in sync.
- ⚠ **jData difference from jcm: the disclosure rides TOP-LEVEL
  (`ignored_arguments` + `ignored_arguments_note`), not under `_meta`.** This
  server strips `_meta` entirely by default (`get_meta_fields()` returns `[]`),
  so a notice placed there would be deleted before the agent ever saw it — the
  same trap that forced the top-level `empty`/`hint` keys in v1.28.0 and the
  post-filter re-attach of the absence ref in v1.26.0. The verdict downgrade
  runs BEFORE filtering (while `_meta.verdict` exists); the disclosure is
  attached AFTER.
- **Deliberately never rejects the call.** Under the 1.x zero-surprise contract
  an unknown key has always been accepted, so a client sending a harmless extra
  must not start erroring.
- **An unknown schema accuses nobody** — an unreadable declaration returns
  nothing rather than guessing, so the check can never manufacture a warning
  about a legitimate key.
- New `tests/test_v1_29_0.py` (15). No schema or INDEX_VERSION change.

## [1.28.0] - 2026-07-25 - an empty index says so

### Added

- **`list_datasets` reports an empty store instead of returning a bare `[]`.**
  Suite parity; raised in correspondence on
  [jcodemunch-mcp#375](https://github.com/jgravelle/jcodemunch-mcp/issues/375).
  A user ran this server for months holding **zero datasets** and only found out
  by going looking: `list_datasets` returned `[]`, which reads identically to
  "installed and broken". Their words: "we would have fed both tools months
  ago."

  When nothing is indexed, the response now carries `empty: true` and a `hint`
  naming `index_local` and saying why it matters, so an empty search is not
  mistaken for evidence of absence.

  ⚠ Top-level rather than under `_meta`: `get_meta_fields()` returns `[]` by
  default here, so a nudge placed in `_meta` would be stripped before the agent
  ever saw it. Same key names as jcodemunch-mcp and jdocmunch-mcp.

  Additive and silent once anything is indexed. New `tests/test_v1_28_0.py` (4).
  Suite parity: jcodemunch-mcp v1.108.174. NO tool, schema, or INDEX_VERSION
  change.

## [1.27.0] - 2026-07-24 - a rewrite underneath a scan cannot prove absence (5th refusal rule)

Suite parity with jcodemunch-mcp v1.108.168 / jdocmunch-mcp v1.119.0 — but this
one is **enforced here, not merely disclosed**.

### Fixed

- **Absence evidence could be minted over a dataset that was being rewritten.**
  v1.26.0 shipped the absence contract with the rules jData's verdict can
  actually back: only `absent` proves absence, and a truncated row walk does
  not. Index freshness is *disclosed as untracked* rather than gated, because
  this product models none — a rule that reads as enforced and isn't would be
  worse than an honest limitation.

  A rewrite is different. "Was this rewritten while I read it" is a filesystem
  fact, not a freshness model, so unlike the stale gate it can be backed for
  real — the same way the truncation gate is real here. Zero results plus a
  detected rewrite now yields `degraded` instead of `absent`, and because
  `degraded` already cannot prove absence, the rule falls out of the existing
  "only `absent` proves absence" check.

- **`channels.index: "rebuilding"` appears only as a positive detection.** The
  siblings carry a permanent `fresh`/`stale` channel; jData does not, and adding
  one would assert currency this product cannot verify. The key is present when
  a rewrite is detected and absent otherwise: we can prove the dataset **is**
  being rewritten, we cannot prove it is current. The v1.26.0 disclosure
  ("index freshness: not tracked by this product") stands unchanged — this rule
  adds to it, never replaces it.

### Notes

- The dataset mtime deliberately spans **`data.sqlite` and its WAL**, not just
  `index.json`: the rows a search scans live in the SQLite store, so a reindex
  that rewrites rows must register even when the metadata monolith is untouched.
  A guard watching only `index.json` would have missed the common case.
- **Unknown is not changed**: an index with no stamped provenance, or one whose
  files are no longer readable, reports unchanged rather than degrading every
  verdict.
- Byte-identical for existing callers when nothing is being rewritten. NO new
  tool, NO tool-count or `INDEX_VERSION` change. New `tests/test_v1_27_0.py`
  (15).

## [1.26.0] - 2026-07-24 - absence evidence: cite a zero-result scan as proof (handoff/v2 phase 3, suite parity)

Absence evidence, suite parity with jcodemunch-mcp v1.108.166 / jdocmunch-mcp
v1.117.0 (jcodemunch-mcp#377 phase 3, design by @mightydanp). A zero-result
`search_data` could not be cited under v1 or v2: nothing was served, so there
was no id to reference. But "we searched the dataset and no such column/value
is there" is exactly the claim a data audit most needs attested, and the one
agents most often assert with no proof at all.

No new retrieval machinery. `verdict.build_verdict` already reports a state
(`ok` / `absent` / `degraded`), per-channel status, index coverage, and a
scorer pin. `handoff.note_absence` records those verdicts under a deterministic
ref (`absent:<sha256[:12]>` over `(tool, dataset, query, scope)`) and lets a
claim cite the scan itself. A `search_data` whose verdict is `absent` now
carries `_meta.absence_evidence.ref`; passing that ref to `finalize_handoff`
attests the absence. The ref is re-attached after the default `meta_fields`
strip, so it survives the token-efficient default.

**The refusal rules are the feature and they are strict.** Only `absent`
proves absence. `degraded` does not, because a keyword-only fallback is a
partial scan. A truncated row walk (`coverage.walk == "truncated"`) does not,
because the target may sit in the rows the ingest pass dropped. A refused scan
is still recorded, so citing one returns the reason (`refused_absence`, or
`refused_absence_claims` naming the claim) rather than a bare unknown-ref
error; an `absent`-but-not-citable live response says so in band via
`absence_evidence.citable: false` and `blocked_by`.

**Honest divergence from jcm/jdoc, disclosed in every rendered proof.** jData's
index does not model freshness: its verdict has no `index` channel, so the
stale-index refusal its siblings enforce cannot fire here. Rather than ship a
guarantee that reads enforced and isn't, every jData absence proof states
`index freshness: not tracked by this product` in the body. Absence stays
citable; the reader is told exactly what was and was not checked. Unknown
coverage renders as "not recorded for this index (scope unknown)" and is never
presented as a complete scope. The detail renders once, under its claim when it
has one, else in the global Evidence index.

Refs are content-addressed, so the same scan in the same scope is the same
proof and a narrowed scope is a different one. Session-scoped and in-memory,
capped, never written to disk. Receipt gains `absence_attested` when an absence
ref is cited, omitted otherwise. New `tests/test_v1_26_0.py` (25, incl. a live
`call_tool` chokepoint e2e). No new tool, no schema or tool-count change, no
`INDEX_VERSION` bump. Suite 644.

#377 stays open: phase 2 (evidence receipts) and phase 4 (caller-declared
requirement matching) remain deferred.

## [1.25.0] - 2026-07-23 - claim-scoped evidence (handoff/v2 phase 1, suite parity)

Claim-scoped evidence, suite parity with jcodemunch-mcp v1.108.165
(jcodemunch-mcp#377 phase 1, design by @mightydanp). A handoff section may now
carry caller-authored `claims`, each with its own `evidence_refs`. v1 proved a
cited ref was retrieved this session but never bound it to a sentence: refs
landed in one global block at the end of the body.

New `_validate_claims` takes `{id, statement, evidence_refs, classification?}`.
Ids are unique across the WHOLE handoff, not per section, since the id is the
citation anchor and two sections owning one id would make a citation ambiguous.
Statements and classifications are preserved verbatim; the server never
rewrites one. Each claim's refs are attested separately through the unchanged
`_validate_evidence`, so an unknown ref returns `invalid_claims:
[{claim_id, unknown_refs}]` and names the claim that cited it instead of
vanishing into one global failure list. `render_handoff` prints the claim as a
`###` heading with its evidence indented beneath.

Three decisions carried from the jcm implementation:

- The input picks the contract. No claims anywhere means the schema string
  stays `jdatamunch.handoff/v1` and the body is byte-identical to what v1 rendered;
  `claims_attested` is omitted from the receipt rather than reported as `0`.
  Any claim promotes the handoff to `jdatamunch.handoff/v2`.
- Claims can satisfy `evidence_refs`: the top-level list may be empty when
  claims carry refs, so a caller who scoped everything to claims need not
  restate it. Strictly more permissive; no existing call changes.
- Claim refs join the canonical evidence index, caller order first, so a v1
  consumer reading a v2 handoff still sees every reference where it expects.

Section `content` becomes optional only for a section carrying claims.
Additive/1.x, no INDEX_VERSION or tool-count change.

Known limit, disclosed on the tracking issue before anyone builds against it:
phase 1 does not narrow what counts as a match. Attestation still accepts a
broader reference than the claim, so citing a whole dataset attests
even when only one unrelated member of it was served. Narrowing that is phase
2 (evidence receipts), which is deferred.

Tests `tests/test_v1_25_0.py` (18, incl. the byte-identical v1 guard); suite 598.

## [1.24.0] - 2026-07-23 - canonical handoff contract: finalize_handoff + munch://handoff/<id> (suite parity, jcodemunch-mcp #374)

New tool `finalize_handoff` (`jdatamunch.handoff/v1`) + resource
`munch://handoff/<id>` — suite parity with jcodemunch-mcp v1.108.162 and
jdocmunch-mcp v1.114.2. A multi-step data audit ends with one authoritative,
server-owned Markdown handoff: the assistant authors the analysis; the server
deterministically assembles the caller's sections plus optional named
appendices (each exactly once, duplicates rejected), validates every
`evidence_refs` entry against the session's actual retrieval record (column
ids `<dataset>::<column>#column` and dataset names served by `search_data` /
`describe_dataset` / `describe_column`, recorded at the response chokepoint;
unknown refs fail closed with an `unknown_refs` list), persists session-scoped
in memory, and returns a compact receipt `{handoff_id, resource_uri, sha256,
length, canonical: true}`. The resource serves the immutable body with
byte-identical repeated reads; `canonical: true` is advisory metadata only.
No character limit; never writes to your data; standard tier;
`readOnlyHint: false`. Tool count 38 → 39. Additive/1.x, no INDEX_VERSION
bump. Tests `tests/test_v1_24_0.py` (15); full suite 580.

## [1.23.1] - 2026-07-23 - BM25 tokenizer: Unicode word splitting + CJK character bigrams

`bm25.tokenize` used `[A-Za-z0-9_]+`, so every non-ASCII character acted as a
separator: CJK column names, summaries, and sample values produced zero
tokens — `search_data`'s keyword ranking contributed nothing for those
datasets — and accented Latin was mangled (`café` → `caf`). The tokenizer now
splits on Unicode word boundaries (`\w+`, underscore still kept inside tokens
so `user_id` stays whole) and expands CJK runs (Hangul, Hiragana/Katakana,
Han) into overlapping character bigrams, applied identically at index and
query time so bigram overlap is the match signal. Pure-ASCII tokenization is
unchanged. Suite parity with jdocmunch-mcp v1.114.1 (#91 there) and
jcodemunch-mcp v1.108.161. No reindex needed — tokenization happens at
search time.

## [1.23.0] - 2026-07-21 - tool-surface schema receipt in session stats (suite parity, jcodemunch-mcp v1.108.153)

`get_session_stats` now carries an advisory `tool_surface` block inside
`result`: visible vs catalog tool counts (after `JDATAMUNCH_TOOL_PROFILE` +
`JDATAMUNCH_DISABLED_TOOLS` filtering), estimated schema tokens for each,
`schema_tokens_avoided` by the active profile, and the top-15 heaviest tool
schemas. Estimated at the meter's bytes/4 scale over the `{name, description,
inputSchema}` serialization. jData has no Counter surface, so the block
carries `profile` but no `surface` key. Read-only, computed inline on the
stats call only, nothing persisted; a probe failure omits the block rather
than failing the call. Additive/1.x — no new tool, no schema change, no
INDEX_VERSION bump. Tests: `tests/test_v1_23_0.py` (5).

## [1.22.0] - 2026-07-21 - runtime identity resource (suite parity, jcodemunch-mcp#371)

New MCP resource `munch://runtime/identity` — a read-only
`munch.runtime.identity/v1` JSON document giving multi-agent harnesses process
provenance for this server instance: `schema`, `product`, `version`,
`transport`, `pid`, `process_start {value, source}`, `instance_id`, and an
optional `launch_id` echo. `process_start` is OS-derived when obtainable
(Windows `GetProcessTimes`; Linux `/proc/self/stat` starttime + btime) with
`source: "os"`; when the OS probe is unavailable the value is the module's own
first-read clock, disclosed as `source: "self_recorded"` — never presented as
OS evidence. `instance_id` is a uuid4 minted once per process lifetime, so a
restart (even with a reused PID) yields a new identity. `launch_id` echoes
`JDATAMUNCH_LAUNCH_ID` (fallback `MUNCH_LAUNCH_ID`) and is omitted when unset.
Deliberately excluded: command lines, env, cwd, hostnames, dataset paths, task
data. Delivered as a resource, not a tool — no tool-count or schema change and
zero cost when unused; on-demand read only, no background or network behavior.
Additive/1.x. New module `runtime_identity.py`; tests `tests/test_v1_22_0.py`
(11). Same contract ships in jcodemunch-mcp v1.108.152 and jdocmunch-mcp
v1.111.0.

## [1.21.0] - 2026-07-19 - advisory session token budget

Suite parity with jcodemunch-mcp v1.108.146 / jdocmunch-mcp v1.104.0. Set
`JDATAMUNCH_SESSION_TOKEN_BUDGET` to an advisory ceiling over response
tokens served (counted at the response chokepoint, bytes/4 — the savings
meter's scale). Once the session crosses 80% of the limit, every response
carries `_meta.budget = {limit, spent, state}` (`approaching` at >=80%,
`over` at >=100%) — attached AFTER meta_fields filtering so the warning
survives the token-efficient default that strips `_meta`.
`get_session_stats` gains `session_response_tokens` and, when configured,
the `budget` block in all three states. Never blocks, throttles, or
truncates (distinct from the existing per-response `enforce_budget`
truncator, which bounds a single response's size; this tracks cumulative
session spend and only ever warns). Unset/`0` disables; wire is
byte-identical. Additive/1.x; inline compute, no new background or network
behavior, no INDEX_VERSION bump. Tests: `tests/test_v1_21_0.py` (9).

## [1.20.0] - 2026-07-19 - coverage contract on absence claims

Suite parity with the sibling code MCP's coverage contract, prompted by
community feedback on the retrieval-verdict article: an `absent` verdict backed
only by scan counts lies by omission when data was excluded at index time.

### Added

- **Index-time coverage block persisted in `index.json`.** Every `index_local`
  ingest records `coverage`: `walk` (`full` or `truncated`), `rows_indexed`,
  `skip_counts` by reason (nonzero only), and `recorded_at` (UTC). Skip reasons
  currently tallied: `malformed_rows` (JSONL lines that fail to parse or
  aren't objects) and `rows_over_cap` (rows beyond `JDATAMUNCH_MAX_ROWS`,
  drain-counted exactly). In `depth="shallow"` mode the truncation is recorded
  without a count rather than fabricating one, since draining the remainder
  would defeat the shallow speed tradeoff. The incremental fast path leaves the
  block untouched; a full re-ingest overwrites it (self-heals). Additive field
  with a None default, so no `INDEX_VERSION` bump; legacy indexes load fine.
- **`index_repo` discovery skip counts.** Files excluded at repo discovery are
  tallied by reason (`unsupported_extension`, `skipped_path`, `oversize`,
  `over_file_limit`, `download_failed`, `index_error`) and returned in a
  repo-level `coverage` block (`walk`, `datasets_indexed`, `skip_counts`,
  `recorded_at`), persisted as a `.repo-coverage-<owner>--<repo>.json` sidecar
  next to the existing `.repo-sha` marker.
- **Query-time coverage disclosure on non-ok verdicts.** `search_data`'s
  `_meta.verdict` now attaches a `coverage` block to `absent` / `degraded`
  states only: `generation` (`indexed_at`, `index_version`), `rows_indexed`,
  and `excluded` (nonzero skip reasons). `ok` verdicts stay lean. When the
  index predates the contract the block is omitted entirely; empty means
  unknown and is never fabricated. Empty fields are omitted.
- **`scorer` version pin on `_meta.verdict`** (integer, starts at 1). Bumps
  when the score semantics or the verdict state machine change, so an agent
  can tell "the data changed" apart from "the scorer changed".

All additive and 1.x wire-compatible. Tests: `tests/test_v1_20_0.py`.

## [1.19.1] - 2026-07-16 - docs only

Documentation wording only. No code, wire-format, or behavior change from 1.19.0.

## [1.19.0] - 2026-07-16 - update model price constants to current Anthropic pricing

Updates the model input-price constants used by the `cost_avoided` dollar
estimate to Anthropic's current published rates: Opus $5/MTok, Sonnet $3/MTok,
Haiku $1/MTok. Anthropic has reduced input pricing across the Opus line since
these models launched, so the constants now track current pricing.

Token savings are measured in tokens and valued at the applicable model rate,
so the underlying savings are unchanged; only the price constants now reflect
current pricing.

### Changed
- `claude_opus` input rate set to the current $5/MTok (comment cites the dated
  source, anthropic.com/pricing 2026-06-24).

### Added
- `claude_sonnet` ($3/MTok) and `claude_haiku` ($1/MTok) entries, so
  `cost_avoided` / `total_cost_avoided` show the full current model set (parity
  with the sibling code MCP's price table). Additive keys only; the existing
  `claude_opus` and `gpt5_latest` keys are unchanged in name, so the wire shape
  stays 1.x-compatible.

`cost_avoided` does not touch the public token counter (which stores tokens and
values them at display time). No INDEX_VERSION bump, no tool add/rename. Suite
parity: jcm v1.108.130 (receipt table) + jdoc v1.97.0 (same constants).

## [1.18.0] - 2026-07-10 - suite-parity retrieval verdict (`_meta.verdict` on search_data)

### Added

- **`search_data` now emits `_meta.verdict`** — the same agent-facing honesty
  contract the sibling code and doc MCPs ship on their search tools. An empty
  column search is positive, token-saving evidence: the index can attest "no
  column matches this" instead of leaving the agent to reformulate. Taxonomy:
  `ok` / `absent` / `degraded`.
- **`degraded`** fires when semantic search is requested but the embedding
  channel falls back to keyword-only, so absence is not proven. It takes
  precedence over `absent`.
- **`absent`** carries a `did_you_mean` list of column names containing a query
  term, so a miss redirects the agent instead of repeating the same empty query.

Honest divergence from the sibling search tools: jData scores are rank-normalized
(top hit always 1.0), so there is no calibrated confidence signal — `search_data`
emits no `low_confidence` state (it would be fabricated). Clean-room jData
implementation (new top-level `verdict.py`); only the wire shape is shared — no
cross-suite import. Additive and 1.x-compatible: `_meta.verdict` is a new key,
every existing response field is unchanged, no `INDEX_VERSION` bump, inline
compute. Tests: `tests/test_v1_18_0.py` (9).

## [1.17.0] - 2026-07-07 - MCP readOnlyHint annotations (suite parity with jcodemunch PR #361)

### Added

- **Every tool advertises `ToolAnnotations(readOnlyHint=...)`.** MCP clients that
  gate execution (Claude Code plan mode) prompted for approval on every jData
  call because tools carried no annotations. Read tools are now
  `readOnlyHint=True` (plan mode runs them silently) and the write-set is
  `False`. Applied at the `list_tools` chokepoint via a non-mutating
  `model_copy`. The write-set (`index_local`, `index_repo`, `summarize_dataset`,
  `delete_dataset`, `embed_dataset`, `ingest_sql_log`, `tune_weights`,
  `check_embedding_drift`) is any tool that can mutate persistent state under any
  argument — biased conservative, since mislabeling a writer as read-only is the
  harmful direction. Suite parity with jcodemunch-mcp (PR #361) and
  jdocmunch-mcp. Additive, 1.x-compatible (new `tools/list` field only). Tests:
  `tests/test_v1_17_0.py` (4).

## [1.16.0] - 2026-06-16 - `analyze_perf`: per-tool latency + cache-hit telemetry

Completes the sibling-parity trio (jcodemunch-mcp and jdocmunch-mcp both ship
`analyze_perf`). jData previously had no latency telemetry and an uninstrumented
result cache; this adds both, then surfaces them.

- New `perf.py`: an in-memory per-tool latency ring (always populated when
  `call_tool` fires; 500 samples/tool) plus an optional persistent SQLite sink
  at `<index_path>/perf_telemetry.db`, gated by `JDATAMUNCH_PERF_TELEMETRY=1`
  (FIFO-capped by `JDATAMUNCH_PERF_TELEMETRY_MAX_ROWS`, default 100000).
  Recording is best-effort and never breaks a tool call.
- `server.py` records each dispatch's wall-clock latency and ok/error flag.
- `storage/result_cache.py` gained per-tool hit/miss counters (`cache_stats()`);
  `get()` takes an optional `tool` so `aggregate` / `get_correlations` /
  `get_data_hotspots` attribute their cache hits.
- New `analyze_perf` tool: `window=session` reads the in-memory ring;
  `window=1h|24h|7d|all` reads the persistent sink. Returns per-tool
  p50/p95/max/error_rate, the slowest tools by p95, and result-cache hit rates
  (totals + coldest-by-tool). `tool=` narrows to one tool; `top=` caps the
  rankings.

Tool count 37 -> 38. **jData now has full agent-facing tool parity with its
siblings.** 12 new tests (`tests/test_v1_16_0.py`).

## [1.15.0] - 2026-06-16 - `check_embedding_drift`: detect silent embedding-provider drift

Column embeddings power semantic `search_data` and `find_similar_columns`. If
the embedding provider's model changes underneath a stored index (a model
revision bump, a reweight under the same name, a swapped local
sentence-transformers model), the vectors saved at index time stop matching what
the live query encoder produces and semantic ranking quietly degrades. The new
`check_embedding_drift` tool catches that, closing a sibling-parity gap
(jcodemunch-mcp and jdocmunch-mcp both ship one).

- New `embed_drift.py` pins a 16-string **canary** — deterministic strings
  spanning tabular / column semantics (identifiers, money, dates, geo,
  categories, free text), tailored to the domain jData embeds rather than code
  tokens — embedded with the active provider and stored in
  `<index_path>/embed_canary.json` (`{provider, model, dim, captured_at,
  strings, vectors}`).
- New `check_embedding_drift` tool: `force=true` re-embeds and re-pins the
  baseline; otherwise it recomputes the canary and reports `max_drift` /
  `mean_drift` / per-canary cosine, raising `alarm` when the worst canary drifts
  past `threshold` (cosine distance, default 0.05). A provider swap is reported
  even when the cosine comparison still runs.
- Reuses jData's own `embeddings.detect_provider` / `embed_texts` /
  `cosine_similarity` so the canary never drifts from the live encoder.

Tool count 36 -> 37. 9 new tests (`tests/test_v1_15_0.py`).

## [1.14.0] - 2026-06-16 - `tune_weights`: tunable search_data ranking weights

`search_data` ranks columns with a small weight vector (name / value / type
match weights plus the BM25 and semantic blend scales). Until now those weights
were hardcoded module constants and there was no way to adjust them. The new
`tune_weights` tool makes the vector tunable and persistable, closing a
sibling-parity gap (jcodemunch-mcp and jdocmunch-mcp both ship a `tune_weights`
tool over their ranker).

- New `tuning.py` holds `DEFAULT_WEIGHTS` (single source of truth for the
  vector) and `load_effective_weights()`, which resolves defaults < global
  overrides < per-dataset overrides. Overrides persist in
  `<index_path>/ranking_tuning.json` (atomic write; a corrupt file degrades to
  defaults).
- New `tune_weights` tool: omit all args to inspect the effective weights and
  their source; pass `set_weights` (a `{weight: number}` object) to override;
  pass `reset=true` to clear. Scope with `dataset`. Overrides are validated
  (unknown names / non-numeric values rejected) and clamped to each weight's
  bounds.
- `search_data` resolves the effective weights once per query and now honors a
  tuned `default_semantic_weight` when the caller omits `semantic_weight`.
  Default behavior is unchanged when no overrides exist.
- Honest divergence from the siblings: jdatamunch-mcp keeps no ranking-events
  ledger (`call_tracker` is ephemeral loop-detection), so weights are tuned
  explicitly here rather than learned from usage.

Tunable weights: `name_exact`, `name_substr`, `name_word`, `ai_summary_word`,
`value_exact`, `value_substr`, `type_boost`, `bm25_scale`, `semantic_scale`,
`default_semantic_weight`. Tool count 35 -> 36. 14 new tests
(`tests/test_v1_14_0.py`); full suite 498 passed / 10 skipped.

## [1.13.1] - 2026-06-10 - disclose the community savings meter in README

Docs-only patch. The anonymous community savings meter (random install ID +
tokens-saved counter POSTed to j.gravelle.us, default on, opt out with
`JDATAMUNCH_SHARE_SAVINGS=0`) was implemented and opt-out-able but never
described in the README. Added a "Community savings meter" disclosure to the
Token savings telemetry section, mirroring jdocmunch-mcp's README. Prompted by
PyPI's quarantine-exit guidance on the sibling package: long-term or
out-of-band operations must be disclosed in the README. No code change.

## [1.13.0] - 2026-05-14 - `tool_profile` + `disabled_tools` config (#297)

Reported by @AlexJ-StL in jcm#297: Google Antigravity caps MCP-server
tool counts at 50, and the full munch suite ships 81 + 60 + 35 = 176
tools. Sibling-parity gap with jcm. jdata now ships the same knobs
that jdoc gained in v1.64.0:

- `JDATAMUNCH_TOOL_PROFILE=core|standard|full` (default `full`).
  - `core` (10 tools): index + describe + the row-retrieval essentials.
  - `standard` (~30 tools): core + analysis tools.
  - `full` (35 tools): everything, current behavior.
- `JDATAMUNCH_DISABLED_TOOLS=tool1,tool2,...` removes named tools from
  both the listed schema and the call dispatcher.

Filtering enforced in `list_tools()` AND `call_tool()` so cached
schemas get a clear error. `jdatamunch_guide` survives tier filtering
but honors `disabled_tools` (documentation, not a control surface).

Antigravity users running all three munches can now do:

```jsonc
"jdatamunch": { "env": { "JDATAMUNCH_TOOL_PROFILE": "core" } }
"jdocmunch":  { "env": { "JDOCMUNCH_TOOL_PROFILE":  "core" } }
"jcodemunch": { "env": { "JCM_TOOL_PROFILE":        "core" } }  // or tool_profile in .jcodemunch.jsonc
```

Suite total drops to 10 + 13 + 17 = 40 tools, comfortably under 50.

## [1.12.2] - 2026-05-13 - `jdatamunch_guide` sibling-parity tool

Adds `jdatamunch_guide`, the data-MCP sibling of `jcodemunch_guide` (in
jcm since v1.84.0) and `jdocmunch_guide` (in jdoc v1.63.3). Returns the
version-current CLAUDE.md / AGENT.md policy snippet for jdatamunch-mcp
so an agent can keep a one-line CLAUDE.md (`"Call jdatamunch_guide and
strictly follow its instructions."`) instead of pasting a static block
that drifts from the installed version.

Backstory: GitHub issue #296 (Codex Desktop compatibility report on
jcodemunch-mcp) flagged the parity gap in the doc-MCP. Once jdoc shipped
its guide, jdata was the only suite member still missing one. Tool surface
is grouped into 11 categories with a quick-start path (list_datasets ->
index_local -> describe_dataset -> describe_column -> run_sql).

Tool count 35 -> 36. No tool, schema, or wire-format change for existing
tools. 465 tests pass, 1 skipped (459 baseline + 6 new in `test_v1_12_2.py`).

## [1.12.1] - 2026-05-12 - drift-proof __version__ via importlib.metadata

`src/jdatamunch_mcp/__init__.py` now derives `__version__` from
`importlib.metadata.version("jdatamunch-mcp")` instead of a hardcoded
literal. pyproject.toml is the single source of truth; the wheel's
metadata is read at import time, so the runtime version string and
the packaging version string cannot disagree by construction.

Mirrors the jcodemunch-mcp pattern (in place since v1.84.0) and
jdocmunch-mcp v1.63.2.

Backstory: v1.12.0 shipped with `__version__` hardcoded at 1.9.0,
three minors stale. Nothing failed because the runtime version string
is rarely consulted; the bug would have surfaced as wrong telemetry
labels or wrong baseline filenames in future work that branched on it.

Source-checkout callers without `pip install` see
`__version__ = "unknown"`.

No tool, schema, or wire-format changes.

## [1.12.0] — 2026-05-12 — `find_similar_columns` (Phase-2 jData COMPLETE)

Multi-signal cross-dataset column consolidation tool. Mirrors jcm's
`find_similar_symbols` and jdoc's `find_similar_sections` — fuses
several similarity signals into a composite score, clusters via
union-find, classifies each cluster into a verdict tier.

### Signal fusion

| Signal      | Source                                                    |
|-------------|-----------------------------------------------------------|
| name        | Token-overlap Jaccard (snake + camel split + lowercase)   |
| type        | 1.0 same type, 0.5 same numeric family, 0.0 otherwise     |
| value       | Jaccard on top_values when both columns are low-cardinality |
| cardinality | 1 - abs(ratio_a - ratio_b) where ratio = card/row_count   |
| embedding   | Cosine on column embeddings (when present on both sides)  |

Weighting:

- with embeddings:    `emb 0.50 + name 0.20 + value 0.15 + type 0.10 + card 0.05`
- without embeddings: `name 0.45 + value 0.30 + type 0.15 + card 0.10`

### Verdict tiers

- `near_duplicate`      — composite ≥ 0.85 and types match
- `naming_drift`        — composite ≥ 0.70 and name_sim < 0.5
- `parallel_definition` — composite ≥ 0.70 and name_sim ≥ 0.7
- `overlapping_topic`   — composite ≥ 0.50

### Use cases

- Find duplicate columns to consolidate before a migration.
- Surface naming drift across teams (`email` vs `email_address`).
- Detect the same conceptual column spread across multiple datasets
  (`users.email` and `customers.email`) that probably wants one source
  of truth.

`differs_by` breakdown per pair calls out which signals fired weakly so
the verdict is auditable. Returns full clusters with members + pairs +
per-cluster strongest verdict.

### Stats

- Tool count: 35 (`find_similar_columns` new)
- Tests: 470 passed, 10 skipped (+15 new — 7 pure-function + 8 integration)

This completes Phase 2 for jData. Remaining Phase-2 work: jDoc's
`doc_health_radar` + `diff_doc_health_radar` and `get_doc_pr_risk_profile`.

---

## [1.11.0] — 2026-05-12 — `data_health_radar` + `diff_data_health_radar`

Six-axis health radar for tabular datasets, plus a pure-function diff
helper for snapshot-to-snapshot comparisons. Mirrors jcm's
`health_radar.py` shape (six-axis + optional seventh runtime axis +
A-F grade + axis-by-axis diff).

### New: `data_health_radar` MCP tool

Composes per-column signals from index.json + history snapshots into a
0-100 score across six axes plus a composite + letter grade:

| Axis              | Source                                          |
|-------------------|-------------------------------------------------|
| null_health       | 100 − mean(null_pct) across columns             |
| type_confidence   | mean(type_confidence) × 100                     |
| cardinality_health| linear penalty per constant column              |
| pk_presence       | has PK candidate → 100, else 50                 |
| semantic_coverage | semantic_type detected / typeable candidates    |
| schema_stability  | drift-free between first/last history snapshot  |
| runtime_coverage  | (optional) % of columns with traffic in window  |

`schema_stability` is omitted when fewer than two history snapshots
exist. `runtime_coverage` is omitted when no runtime traces are
ingested or `include_runtime=False`. Omitted axes appear in the
`omitted_axes` list, never silently — they don't count toward the
composite so radars stay comparable across datasets with different
ingest states.

### New: `diff_data_health_radar` MCP tool

Pure function: takes two radar payloads and returns per-axis deltas,
composite delta, grade change, lists of regressions and improvements
(threshold: 3 points), and a one-line verdict. No I/O — pass radar
payloads from disk, CI artifacts, or two consecutive
`data_health_radar` calls.

### Stats

- Tool count: 34 (+ `data_health_radar`, `diff_data_health_radar`)
- Tests: 455 passed, 10 skipped (+13 new across the two tools)

---

## [1.10.0] — 2026-05-12 — `get_redaction_log` + `get_data_hotspots` v2 (Phase-2 opener)

First Phase-2 release. Two thin tools bundled because both are reads off
already-populated tables with no behavior to bake individually.

### New: `get_redaction_log` MCP tool

Forensic accounting of PII redactions per dataset. Reads
`runtime_redaction_log` (populated by `ingest_sql_log` with
`redact=True`, the default) and surfaces per-pattern counts so operators
can verify the redaction chokepoint is actually firing on production
traffic.

- Filters by `source` (today: `sql_log`) and `since_days` window.
- Returns `{dataset, sources, since_iso, patterns[], total_redactions}`.
- Empty patterns list is **not** an error — it's a valid "nothing
  scrubbed yet" state, distinguished from invalid-source / unknown-
  dataset refusals which return structured `reason` codes.
- Mirrors jcodemunch-mcp's `get_redaction_log` (Phase 6) but keyed on
  `dataset_id` and reads jData's `(pattern, count, source, last_seen)`
  table shape.

### Enhanced: `get_data_hotspots` v2 (runtime traffic fusion)

Adds a 4th signal — **runtime traffic** — when traces have been
ingested. Score becomes
`null(0.30) + cardinality(0.20) + outlier(0.20) + traffic(0.30)`. The
traffic axis is normalised by the most-called column in the dataset,
amplifying risk on heavily-queried problematic columns. A 100%-null
column nobody queries is now correctly less urgent than a 30%-null
column queried 10k times a day.

When `include_runtime=True` (default) but no traces are ingested, the
response carries an **honest-hint caveat** in `_meta.runtime_caveat`
rather than silently falling back to v1 scoring without disclosure.
`runtime_data_present` is surfaced on every response. v1 scoring is
preserved bit-for-bit when traces are absent or `include_runtime=False`.
Honest-hint pattern lifted from `check_column_drop_safe` v1.8.0 and
jcm's `check_delete_safe` v1.108.6.

### Stats

- Tool count: 32 (1.10.0 adds `get_redaction_log`)
- Tests: 442 passed, 10 skipped (+ 8 new across the two tools)

---

## [1.9.0] — 2026-05-12 — `get_schema_impact` (Phase-1 COMPLETE)

Fourth and final Phase-1 sibling-parity tool. Walks the inferred FK
graph to surface transitive impact of a column-level schema change.
Inspired by jcodemunch-mcp's `get_blast_radius`, ported to jData's
FK-graph + runtime-traffic shape.

### New: `get_schema_impact` MCP tool

Three change kinds:

- **`drop_column`** (default) — surface every dataset / runtime query
  that *might* reference this column.
- **`rename_column`** — same surfaces; `recommended_action` references
  `new_name` for cascade planning.
- **`retype_column`** — additionally checks `new_type` compatibility
  against each FK-related column's type. Cross-family changes (e.g.
  `integer` → `string`) surface in `summary.type_mismatches`.

### Output

- `direct_impact` (depth 1) — fk_source, fk_target,
  cross_dataset_name_match, runtime_traffic entries.
- `transitive_impact` (depth ≥ 2) — BFS through the FK graph,
  capped at `_MAX_IMPACT_ITEMS = 50`.
- `summary` — `datasets_affected`, `fk_edges_broken`,
  `runtime_calls_in_window`, `type_mismatches`,
  `cross_dataset_name_matches`.
- `blast_score` ∈ [0, 1] — soft-normalised against index size so a
  5-edge impact in a 50-dataset warehouse scores higher than the same
  5 in a 500-dataset one.
- `recommended_action` — verb tracks the change kind ("drop" / "rename
  to X" / "retype to Y").

### Stats

- Tool count: 30 → 31
- Tests: 418 → 434 (+16 new)

### Phase-1 sibling-parity batch complete

| Tool | Version |
|---|---|
| `ingest_sql_log` (foundational runtime primitive) | v1.6.0 |
| `find_unused_columns` | v1.7.0 |
| `check_column_drop_safe` (killer feature) | v1.8.0 |
| `get_schema_impact` | v1.9.0 |

Phase 2 (deferred until user signal): `data_health_radar`,
`find_similar_columns`, `data_pr_risk_profile`, `get_redaction_log`.

Inspired by `get_blast_radius` in jcodemunch-mcp (see
`C:/MCPs/PRD_sibling_parity_v1.md` §5.3).

## [1.8.0] — 2026-05-12 — `check_column_drop_safe` (Phase-1 #3 — killer feature)

The killer feature of the Phase-1 sibling-parity batch. Composite
preflight that fuses four channels — PK status, FK heuristics, cross-
dataset name match, and runtime traffic — into a single verdict plus
ranked blockers and a one-line `recommended_action`.

### New: `check_column_drop_safe` MCP tool

Verdict tiers (highest-severity-first):

- **`pk_blocking`** — column is a primary-key candidate
- **`fk_blocking`** — likely foreign-key participation (source or target)
- **`runtime_observed`** — `runtime_query_calls` in last 30 days (window configurable)
- **`cross_dataset_blocking`** — another indexed dataset has a same-named column
- **`safe_to_drop`** — none of the above

### Channels

1. **PK status** — `is_primary_key_candidate` from the static profile.
2. **FK source** — heuristic name-match (`user_id` → dataset `users` with PK `id`) plus direct PK name-match across other indexed datasets. Cheap structural check; no value-containment scan.
3. **FK target** — mirror of #2: this column is a PK and other datasets carry plausible FK-shaped columns (`<self>_id` / `<singular>_id`).
4. **Runtime traffic** — sum of `calls` in `runtime_query_calls` over `window_days` (default 30).
5. **Cross-dataset name match** — case-insensitive same-name lookup across `list_datasets()`. Capped at 10 hits.

### Honest hint when runtime data is absent

When no `ingest_sql_log` has run against the dataset, `safe_to_drop`
verdicts carry an explicit caveat in `recommended_action` pointing the
operator at `ingest_sql_log`. The static channels alone can prove
*risk*, but not *safety*.

### Stats

- Tool count: 29 → 30
- Tests: 406 → 418 (+12 new)

Inspired by `check_delete_safe` in jcodemunch-mcp (see
`C:/MCPs/PRD_sibling_parity_v1.md` §5.2).

## [1.7.0] — 2026-05-12 — `find_unused_columns` (Phase-1 #2)

Second Phase-1 tool from the sibling-parity PRD. The first consumer of
the `runtime_query_calls` table populated by `ingest_sql_log` (v1.6.0).
Answers: *which columns in this dataset have no recent query traffic?*

### New: `find_unused_columns` MCP tool

Surfaces columns with zero or stale runtime reads over a configurable
window. Three reason classifications:

- **`zero_hits`** — column never appeared in any query, in or out of window
- **`stale`** — column has appeared at some point, but never within the requested window
- **`below_min_calls`** — column has hits in window but fewer than `min_calls`

### Refusal-by-design

When the dataset has zero rows in `runtime_query_calls`, the tool
**refuses** with an explicit `refused_no_runtime_data` error rather
than silently flagging every column. The hint directs the operator at
`ingest_sql_log`. Mirrors the same guard in jcodemunch-mcp's
`find_unused_paths`.

### Defaults

- **`exclude_pk`** (default true) — skips columns flagged as
  `is_primary_key_candidate` by the static profiler. PKs are almost
  always read by JOINs but may not always surface in extracted column
  tokens.
- **`exclude_audit`** (default true) — skips `created_at`,
  `updated_at`, `_dbt_*`, `etl_*`, and other scaffolding patterns.
- **`window_days=30`**, **`min_calls=0`** — single observed call counts
  as used.

### Stats

- Tool count: 28 → 29
- Tests: 392 → 406 (+14 new)

Inspired by `find_unused_paths` in jcodemunch-mcp (see
`C:/MCPs/PRD_sibling_parity_v1.md` §5.4).

## [1.6.0] — 2026-05-12 — Runtime SQL-log ingest (Phase-1 sibling-parity foundation)

First Phase-1 deliverable from the sibling-parity PRD. Adds the
foundational runtime-traffic primitive that downstream tools
(`find_unused_columns`, `check_column_drop_safe`, `data_health_radar`)
will read from. Inspired by jcodemunch-mcp's `runtime/` pipeline but
written fresh against jData's per-dataset SQLite shape.

### New: `ingest_sql_log` MCP tool

Ingests a SQL log file (pg_stat_statements CSV or generic JSON-Lines,
`.gz` transparent) into the per-dataset runtime tables. Each query is:

1. **Parsed** — table + column refs extracted via regex over SELECT /
   WHERE / ON / GROUP BY / ORDER BY / HAVING clauses. Schema-qualified
   names and quoted identifiers (double-quote, backtick, bracket) all
   normalise to the trailing identifier.
2. **Redacted** at the chokepoint — string literals → `'?'`, numeric
   literals → `?`, plus the cell-PII registry on any residual text.
   `redact=False` opt-out for synthetic data only.
3. **Resolved** — for each (table, column) tuple, find the indexed
   dataset whose name matches the table (case-insensitive, exact). Over-
   emitted column tokens that aren't in the dataset's schema drop out.
4. **Upserted** — `ON CONFLICT(query_fingerprint, table_ref,
   column_ref, source)` accumulates `calls` and `total_time_ms` and
   refreshes `last_seen`. Per-pattern redaction counts persist to
   `runtime_redaction_log` so operators can verify the chokepoint
   actually fires on production traffic.

Unmapped queries (tables that don't match any indexed dataset) count
toward the response's `unmapped_queries` but aren't persisted.

### New: `redact_sql_query_text` and `redact_trace_message` public helpers

Trace-level extensions of the cell-PII redaction module shipped in
v1.5.0:

- `redact_sql_query_text(query, ...)` — strips string + numeric literals
  (so query fingerprints survive but values don't), then applies the
  cell registry. `credit_card` is off by default for SQL text — Luhn-
  valid 13–19 digit sequences inside arbitrary tokens are nearly always
  false positives once literals are scrubbed.
- `redact_trace_message(text, ...)` — IPv4 sweep plus the cell registry,
  for free-form trace / log message bodies.

### Schema migration

`INDEX_VERSION` bumped 2 → 3. The migration is **additive only** — no
profile recompute, no forced reindex. Legacy v2 indexes gain empty
runtime tables on first `ingest_sql_log` call.

### What's NOT in this release

The dependent tools (`find_unused_columns`, `check_column_drop_safe`,
`get_schema_impact`) ship in the **next** Phase-1 batch — they need
`ingest_sql_log` to bake first.

### Stats

- Tool count: 27 → 28
- Tests: 351 → 392 (+41 new across redact + parser + ingest)
- New module: `jdatamunch_mcp/runtime/` (sql_log, ingest, tables)

Inspired by `import_runtime_signal` in jcodemunch-mcp (see
`C:/MCPs/PRD_sibling_parity_v1.md` §5.1).

## [1.5.0] — Cell-level redaction on the output side

Tabular tools now scrub PII and credentials from cells before returning
them to MCP clients. CSV / Excel / Parquet / JSONL data routinely carry
emails, SSNs, credit-card numbers, API keys, and PEM bodies in raw
columns — those cells would otherwise flow straight into LLM context
where they may be cached, logged, or reflected to a tool downstream.
The default policy is ON; callers opt out per call.

### New
- **`src/jdatamunch_mcp/redact.py`** — single-chokepoint redaction module.
  Built-in patterns: `email`, `ssn` (SSA-rule validated), `credit_card`
  (Luhn-checked post-match), `jwt`, `private_key` (full PEM blocks),
  `aws_access_key`, `github_pat`, `slack_token`, `api_key_prefixed`
  (Stripe `sk_live_…` / `sk_test_…` / `rk_…`), `api_key_openai` (`sk-…`).
  Numeric cells are never scrubbed — agents rarely treat numbers as PII.
- **`redact`, `redact_patterns`, `redact_skip_columns`** params on
  `get_rows`, `sample_rows`, `run_sql`, `aggregate`, and `describe_column`.
  `redact=True` by default. `redact_patterns` layers additional Python
  regex onto the built-in set; invalid patterns are silently skipped and
  surfaced via `_meta.redaction.invalid_custom_patterns`.
  `redact_skip_columns` exempts named columns (e.g. an `email_hashed`
  column where the email pattern would false-positive).
- **`_meta.redaction`** block on every wired tool response —
  `{"applied": bool, "cells_redacted": int, "patterns_matched": {kind: count}}`.
  Surfaced even when `applied=False` so the absence of redaction is
  auditable from the wire.
- **34 new tests** (`test_redact.py` + `test_redaction_e2e.py`).
  351 passed, 1 skipped — fully backward-compatible.

### Notes
- `aggregate` caches the raw, un-redacted result; the redaction policy
  is enforced at read time so flipping `redact=False` on a cache hit
  still returns raw cells.
- `describe_column` redacts `value_distribution`, `top_values`, and
  `sample_values`. Numeric stats (min / max / mean / median / histogram)
  are never altered.
- `search_data` is deliberately not wired — the user is explicitly
  searching, so redacting matches would defeat the search.

---

## [1.4.0] — Phase C (optional post-V1 polish)

Closes the Phase C list in `todo.md`. 317 tests passing. Fully backward-compatible.

### Aggregation
- **`aggregate(approximate=True)`** (C1) — new approximate-mode path. Routes
  `count_distinct` → HyperLogLog (~2% standard error), `median` → t-digest
  (~1% accuracy at extreme quantiles), `sum`/`avg` → sampled estimator with
  95% confidence-interval half-width reported in `result.confidence`.
  Whole-dataset only (no group_by/having/order_by). Useful for very large
  joined datasets where exact aggregations are expensive.

### Index metadata
- **Dataset content fingerprint** (C2) — `index.json` now carries
  `fingerprint = sha256(sorted(column_names) + first_1000_row_hash)`.
  Independent of filename / path: two physically distinct files with
  identical logical content share the same fingerprint. Surfaced in
  `list_datasets`.
- **Per-dataset learned null tokens** (C3) — new `profiler/null_learner.py`
  scans completed profiles for sentinel-looking tokens that recur across
  multiple columns at non-trivial frequency (e.g. `TBD`, `999`, `----`,
  `UNKNOWN`). Surfaced as `index.learned_null_tokens` so agents can decide
  whether to treat them as nulls in downstream filters. Informational only —
  profiling behavior is unchanged.

### Summarization
- **Coarse domain classification** (C4) — `summarize_dataset` now appends a
  `Likely domain: …` line when evidence supports it: `geo`, `financial`,
  `log`, `event`, or `temporal`. Driven by column-name tokens + semantic
  types. Conservative — emits nothing when evidence is weak.

### Telemetry
- **Per-tool token-savings attribution** (C5) — `_savings.json` now records
  `per_tool[<tool>] = {tokens_saved, calls}`. Surfaced via
  `get_session_stats.result.per_tool` sorted by tokens saved descending.
  Lets you see which tools contribute most to the savings number.

### Cache
- **Cross-session aggregate cache** (C6) — formalized: the result cache
  shipped in 1.1.0 (`storage/result_cache.py`) already persists across
  sessions as JSON files under `~/.data-index/{dataset}/_cache/`, keyed on
  `(tool, source_hash, normalized_args)`. Re-indexing invalidates.

### Migrations
- **v1 → v2 migration extended** to populate `fingerprint` (None) and
  `learned_null_tokens` ([]) on legacy indexes. Idempotent. No behavior
  change for indexes already at v2.

### Tests
- 16 new tests across `test_fingerprint`, `test_per_tool_savings`,
  `test_domain_classification`, `test_null_learner`,
  `test_approximate_aggregate`. Total: **317 passing**.

## [1.1.0] — Phase B (recommended polish)

Adds the eight Phase-B items from `todo.md`. 301 tests passing. Fully
backward-compatible — every new capability is additive.

### New tools (B1, B3, B4, B5, B8)
- **`run_sql`** — read-only sandboxed SQL escape hatch. Accepts a single
  `SELECT` (or `WITH … SELECT`) over one or more datasets, ATTACHed under
  schema names. `PRAGMA query_only=1`, 10 s budget, 500-row cap, forbidden-
  keyword guard. The supported way to express HAVING / window functions /
  CTEs / multi-way joins that the structured tools don't cover.
- **`plan_query`** — natural-language intent → ranked tool-call sequence.
  Pure routing; no LLM call. Built-in intents: summarize, anomalies,
  compare, join, filter, trend, correlate.
- **`get_dataset_health`** — composite quality grade (A–F) combining null
  severity, type-confidence, constant-column count, primary-key presence,
  semantic-typing coverage, and drift history.
- **`suggest_keys`** — ranks primary-key candidates with confidence scores
  and reasons (integer column, UUID format, no nulls, exact-count unique).
- **`suggest_joins`** — discovers FK candidates by sampling 500 distinct
  values from each non-PK column and scanning up to 20 other indexed
  datasets' PK candidates for ≥ 95% containment.
- **`get_distribution`** — unified bin-counts: numeric → equal-width bins,
  datetime → time-bucket bins, categorical → top-n + 'other'.

### Existing-tool extensions
- **`aggregate(having=[…])`** (B11) — post-aggregation filters on aggregation
  aliases. Supports eq/neq/gt/gte/lt/lte/in/between/is_null. Substitutes
  the aggregate expression into HAVING so it works even when an alias
  collides with a source column name.
- **`get_correlations(method='pearson'|'spearman')`** (B10) — Spearman
  uses rank-transformed values via SQL window functions, robust to
  outliers and monotonic non-linear relationships.
- **`search_data`** (B9) — keyword scoring upgraded to BM25 in the default
  `all` scope. Documents include column name + ai_summary + value index +
  semantic_type. Existing schema-only and values-only paths preserved.
- **`index_local(depth='shallow'|'standard'|'deep')`** (B7) — shallow caps
  profiling at 100k rows for fast first-look; deep additionally pre-warms
  the correlation cache.

### Performance / infrastructure
- **Aggregate result cache** (B2) — `aggregate`, `get_correlations`, and
  `get_data_hotspots` cache results under `~/.data-index/{dataset}/_cache/`
  keyed on `(tool, source_hash, normalized_args)`. Invalidated on every
  re-index. `_meta.cache_hit` reports hit/miss.
- **Parquet schema pushdown** (B6) — Parquet parser now exposes per-column
  logical types via `metadata['column_types']`. `index_local` skips the
  10k-row sample-based type inference when the source already carries
  authoritative type metadata.
- **MEMORY journal during ingest** — bulk-load uses `PRAGMA
  journal_mode=MEMORY` instead of WAL. The tmp file is disposable on crash
  (A4 invariant), so no on-disk journal is needed; this also clears the
  Windows rename race that prior WAL sidecars caused.

### Tests
- 35 new tests across `test_having`, `test_spearman`, `test_bm25`,
  `test_health_keys_joins`, `test_distribution`, `test_plan_query`,
  `test_aggregate_cache`, `test_run_sql`, `test_depth`. Total: **301 passing**.

## [1.0.0] — Phase A complete (V1 closure)

This release completes the Phase A roadmap that earns a stable 1.x.x. The full
plan and rationale lives in `todo.md`. Headline guarantees added in this release:

### Statistical correctness
- **Welford online mean + Neumaier-compensated sum** (A1) — replaces the naive
  `num_sum += num` accumulator. Mean stays accurate at 1e-9 relative error
  across 1e-6..1e6 mixed magnitudes.
- **t-digest streaming quantiles** (A2) — every numeric column now exposes
  `p01 / p25 / p50 / p75 / p95 / p99` in addition to min/max/mean/median, plus
  `std_dev` and `variance` from Welford. Bounded ~3 KB/column regardless of
  row count. Replaces the order-biased 10k reservoir.
- **HyperLogLog approximate cardinality** (A3) — once the 5,000-distinct
  exact-count cap is hit, columns now report `cardinality_approx` from a
  2,048-register HLL (~2% standard error). `cardinality_estimated: true` flags
  the difference.

### Schema intelligence
- **Semantic column types** (A6) — 13 detectors (`email`, `url`, `uuid`,
  `iso_currency`, `phone_e164`, `ipv4`, `ipv6`, `iso_country`, `lat`, `lon`,
  `zip_us`, `boolean_text`, `percentage`) populate `semantic_type` +
  `semantic_confidence` on each column profile.
- **Type-inference confidence + violation samples** (A7) — every column carries
  `type_confidence` (fraction of values matching the dominant type) and up to
  five `type_violation_samples` so agents can spot mixed-type columns.

### Crash safety
- **Atomic ingest** (A4) — `data.sqlite` is written to `data.sqlite.tmp` first
  and renamed only after profiles compute successfully. `index.json` gets a
  sidecar `index.json.sha256`. A `_lock` file marks in-progress runs;
  `index_local` auto-recovers from prior crashes by cleaning stale tmp files.
  WAL + `synchronous=NORMAL` replace the previous `synchronous=OFF`.
- **`validate_index` tool** (A5) — runs `PRAGMA integrity_check`, cross-checks
  row count and schema against `index.json`, verifies the checksum sidecar,
  and reports stale-lock state. Returns `overall_status: ok | warning | error`.

### Reproducibility & freshness
- **`get_dataset_history` tool + profile snapshots** (A8) — every successful
  `index_local` appends a compact snapshot (timestamp, source hash, schema
  digest) to `_history.jsonl`. Bounded to the last 50 snapshots. Use this to
  observe drift across re-ingests of the same dataset.
- **Deterministic random sampling** (A9) — `sample_rows` accepts a `seed`
  parameter (when `method='random'`) for reproducible selection.
- **Cross-parser normalization contract** (A10) — `parser/normalize.py`
  funnels all native-typed cells (JSONL / Parquet / Excel) through one path,
  guaranteeing CSV / JSONL / Parquet produce identical column profiles for
  the same logical data.

### Schema versioning
- **Index migration framework** (A11) — `INDEX_VERSION` bumped to 2. Indexes
  written under v1 are now upgraded in place via a registered migration
  rather than silently triggering a full re-index. Future bumps register a
  new migration in `storage/migrations.py`.

### Test infrastructure (A12)
- New test modules: `test_welford`, `test_tdigest`, `test_hll`,
  `test_semantic_types`, `test_crash_safety`, `test_validate_index`,
  `test_dataset_history`, `test_migrations`, `test_determinism`,
  `test_normalize`, `test_aggregate_correctness`. Test count: **266 passing**.

### Stability guarantees declared as of 1.0.0
- Profile fields documented above are part of the public on-disk schema.
- New fields will be added under additive migrations only.
- Crash semantics: a kill at any point during `index_local` leaves the
  dataset in one of two states — fully indexed or absent. Never partial.
- `validate_index` is the canonical recovery flow; if it returns `ok`, the
  dataset is consistent.

## [0.8.4] — 2026-04-15

### Documentation
- **Hermes Agent integration** — added "Works with" section to README with Hermes Agent config example; submitted optional skill PR to [NousResearch/hermes-agent#10413](https://github.com/NousResearch/hermes-agent/pull/10413)

## [0.8.3] — 2026-04-09

### New features

- **`meta_fields` support** — control which `_meta` fields appear in tool responses via `JDATAMUNCH_META_FIELDS` env var. Matches jcodemunch-mcp's `meta_fields` affordance. Values: unset/`[]` = strip `_meta` entirely (default, maximum token savings), `null`/`all`/`*` = include all fields, comma-separated list = include only those fields (e.g. `timing_ms,powered_by`).

### Tests

- 11 new tests for meta_fields config parsing and filtering (228 total, 10 skipped for optional deps)

## [0.8.2] — 2026-04-08

### Documentation

- **README.md rewrite** — added documentation index, file format table, all 18 tools organized by category (indexing, exploration, querying, analysis, management), semantic search, cross-dataset joins, correlations, NL summaries, data quality tools, built-in guardrails, full configuration reference
- **QUICKSTART.md** — new beginner-friendly guide: install, connect, index, query in three steps. Plain-English examples throughout.
- **USER-MANUAL.md** — comprehensive manual for non-developer users (analysts, finance, ops). Covers all 18 tools with plain-language explanations, real-world "ask your AI" examples, tips, best practices, and troubleshooting.

## [0.8.1] — 2026-04-08

### New features

- **`list_repos()` tool** — list GitHub repositories indexed via `index_repo`. Shows repo name, HEAD SHA (truncated to 12 chars), dataset count, total rows, total size, and dataset names for each repo.

### Tests

- 8 new tests (217 total, 10 skipped for optional deps)

## [0.8.0] — 2026-04-08

### New features

- **Semantic / embedding search** — `search_data` now supports `semantic=true` for embedding-based column search. Queries like "where did the crime happen" match `AREA NAME` even without keyword overlap. Three new parameters: `semantic` (enable), `semantic_weight` (blend ratio, default 0.5), `semantic_only` (skip keyword scoring). Lazily embeds columns on first semantic query; embeddings cached persistently in SQLite.
- **`embed_dataset(dataset)` tool** — precompute column embeddings for a dataset. Optional warm-up so the first `search_data` semantic query returns immediately. Supports `force=true` to recompute.
- **Three embedding providers** (first configured wins): sentence-transformers (local, free via `JDATAMUNCH_EMBED_MODEL`), Gemini (`GOOGLE_API_KEY` + `GOOGLE_EMBED_MODEL`), OpenAI (`OPENAI_API_KEY` + `OPENAI_EMBED_MODEL`). All imports are lazy — zero impact when semantic search is not used.
- **`[semantic]` optional dependency** — `pip install jdatamunch-mcp[semantic]` installs sentence-transformers

### Tests

- 32 new tests (209 total, 10 skipped for optional deps)

## [0.7.1] — 2026-04-08

### New features

- **`delete_dataset(dataset)` tool** — remove an indexed dataset and its SQLite store, freeing disk space. Returns rows/columns removed and bytes freed.
- **`join_datasets(dataset_a, dataset_b, join_column_a, join_column_b)` tool** — SQL JOIN across two indexed datasets via SQLite `ATTACH DATABASE`. Supports `inner`, `left`, `right`, and `cross` join types. Column projection (`columns_a`/`columns_b`), per-side filters (`filters_a`/`filters_b`), ordering, and pagination. Handles column-name collisions with `__b` suffix. Row limit capped at 500, 30 columns per side. Right joins emulated via table swap (SQLite limitation).

### Bug fixes

- Fixed unclosed SQLite connections in `create_table` and `create_indexes` that caused `PermissionError` on Windows when deleting datasets (WAL file locks)

### Tests

- 26 new tests (177 total, 10 skipped for optional deps)

## [0.6.0] — 2026-04-08

### New features

- **`get_correlations(dataset)` tool** — compute pairwise Pearson correlations between all numeric columns via SQLite. Returns pairs sorted by |r| descending with strength labels (`very strong`, `strong`, `moderate`, `weak`, `negligible`), direction, and pair counts. Configurable `min_abs_correlation` threshold (default 0.3), optional column filter, `top_n` cap (default 20, max 200). Caps at 50 numeric columns to avoid O(n^2) blowup.

### Tests

- 13 new tests (151 total, 10 skipped for optional deps)

## [0.5.0] — 2026-04-08

### New features

- **`index_repo(url)` tool** — index data files directly from a GitHub repository. Discovers CSV, Excel, Parquet, and JSONL files via the GitHub Trees API, downloads each to a temp directory, and indexes via the existing `index_local` pipeline. Datasets are named `{owner}--{repo}--{filename}`.
  - Incremental: caches HEAD SHA to skip entirely when repo is unchanged
  - Limits: 50 MB per file, 20 files per repo
  - Concurrent downloads (semaphore-limited to 5)
  - Supports `GITHUB_TOKEN` env var for private repos and rate limits

### Tests

- 18 new tests for index_repo (138 total, 10 skipped for optional deps)

## [0.4.0] — 2026-04-08

### New features

- **Natural-language summaries** — every `index_local` call now auto-generates a dataset-level summary and per-column summaries from profiled statistics. Summaries describe data shape, types, ranges, cardinality, quality issues, and temporal spans — no external API calls needed.
- **`summarize_dataset(dataset)` tool** — regenerate summaries for an already-indexed dataset without re-parsing the source file. Useful after schema or profile changes.

### Improvements

- `describe_dataset` now includes `dataset_summary` and per-column `ai_summary` fields in responses
- Column summaries surface cardinality labels (unique identifier, categorical, binary, constant, etc.), null-rate warnings, and value previews for low-cardinality columns

### Tests

- 18 new tests (120 total, 10 skipped for optional deps)

## [0.3.0] — 2026-04-01

### New tools

- **`get_schema_drift(dataset_a, dataset_b)`** — compare schema metadata between two indexed datasets: detects added/removed columns, type changes, and null-rate shifts (≥1% delta). Assessment: `identical` | `additive` | `breaking`. Pure in-memory comparison of indexed profiles — no re-reading source files.
- **`get_data_hotspots(dataset, top_n=10)`** — rank columns by composite data-quality risk combining null rate, cardinality anomalies, and numeric outlier spread (coefficient of variation). Per-column `assessment: low|medium|high`. Top-N capped at 50. Analogous to jcodemunch's `get_hotspots`.

### Tests

- 23 new tests (91 total, 1 skipped for optional deps)

## [0.2.1] — 2026-03-31

### Housekeeping

- Added `LICENSE` file (dual-use: free for non-commercial, paid for commercial)

## [0.2.0] — 2026-03-31

### New features

- **Parquet support** — `.parquet` files indexed and queried via `pyarrow`
- **JSONL/NDJSON support** — `.jsonl` and `.ndjson` files parsed line-by-line; schema inferred from first N rows
- **Token budget enforcement** (`budget.py`) — every tool response is capped at a configurable token limit (`JDATAMUNCH_MAX_RESPONSE_TOKENS`, default 8 000); falls back to generic list-field trimming when needed
- **Anti-loop call tracker** (`call_tracker.py`) — detects and warns when an LLM agent is paginating through a dataset row-by-row in a tight loop
- **Wide-table pagination** — `describe_dataset` auto-paginates at 60 columns; new `columns_offset` parameter lets callers page through remaining columns

### Improvements

- Hard caps added for all tool parameters: `top_n` ≤ 200, `histogram_bins` ≤ 50, `search_data` max_results ≤ 50, `aggregate` limit ≤ 1 000
- `get_rows` / `sample_rows` auto-project to 30 columns on wide tables; caller can override with explicit `columns` list
- `describe_dataset` tool description updated to document pagination behaviour
- `describe_column` and `search_data` tool descriptions document their caps
- Improved test fixtures (`tests/conftest.py`)

### Housekeeping

- Added `LICENSE` file (dual-use: free for non-commercial, paid for commercial)
- `index_local` description updated to list all supported formats

## [0.1.2] — 2026-03-27

### Performance

- Bulk SQLite insert, string fast-path, corrected `is_unique` detection for high-cardinality columns

## [0.1.1] — 2026-03-26

### Bug fixes

- Fixed token cost calculations in benchmark results (were off by 1 000×)

## [0.1.0] — 2026-03-25

### Initial release

- CSV and Excel (.xlsx/.xls) indexing via SQLite
- Tools: `index_local`, `list_datasets`, `describe_dataset`, `describe_column`, `search_data`, `get_rows`, `sample_rows`, `aggregate`, `get_session_stats`
- jMRI-Full compliant
