# jdatamunch-mcp: Token Efficiency Benchmark

**Tokenizer:** `cl100k_base` (tiktoken)
**Workflow:** `describe_dataset` + `describe_column` (per task), default arguments
**Baseline:** full raw source file tokenized (minimum for a "read everything" agent)
**AI summaries:** disabled (clean retrieval-only measurement)
**Measured:** 2026-11-04 at commit `c507d87` (after the compact defaults for `describe_dataset` and `describe_column`). Regenerate with `benchmarks/harness/run_benchmark.py`; do not hand-edit the numbers.

Full methodology, limitations, and reproduction steps: [METHODOLOGY.md](METHODOLOGY.md)

## crime.csv ([source](https://catalog-beta.data.gov/dataset/crime-data-from-2020-to-present))

| Metric | Value |
|--------|-------|
| Rows | **1,004,894** |
| Columns | **28** |
| File size | **255.5 MB** |
| Baseline tokens (full file) | **111,028,360** |

| Task | Baseline&nbsp;tokens | jDataMunch&nbsp;tokens | Reduction | Ratio | Baseline&nbsp;cost | jDataMunch&nbsp;cost | Saved |
|------|---------------------:|-----------------------:|----------:|------:|-----------------:|-------------------:|------:|
| `schema overview` | 111,028,360 | 892 | **99.9992%** | 124,471.3x | $555.14 | $0.004 | **$555.14** |
| `crime type distribution` | 111,028,360 | 1,197 | **99.9989%** | 92,755.5x | $555.14 | $0.006 | **$555.14** |
| `temporal range` | 111,028,360 | 1,279 | **99.9988%** | 86,808.7x | $555.14 | $0.006 | **$555.14** |
| `victim demographics` | 111,028,360 | 1,211 | **99.9989%** | 91,683.2x | $555.14 | $0.006 | **$555.14** |
| `geographic coverage` | 111,028,360 | 1,115 | **99.9990%** | 99,577.0x | $555.14 | $0.006 | **$555.14** |
| **Average** | - | 1,139 | **99.9990%** | **99,059.1x** | **$555.14** | **$0.006** | **$555.14** |

> Costs at $5.00 / 1M input tokens (Claude Opus published rate).
> Valuation is an input to this benchmark, not a claim about your bill.

⚠ **The harness counts each response as the Python function returns it, `_meta` included.**
Over MCP with default configuration `_meta` is stripped, so an agent reads slightly
less (`describe_dataset` is 852 tokens on the wire against 892 here).

⚠ **Not 100%.** Rounding 99.999% to 100.0% turns "almost all of it" into "all of it,"
which is a different and unsupportable claim. The unrounded values are above.

⚠ **Two different statistics both get called "the ratio."** `99,059.1x` is the
**mean of the five per-task ratios**; `97,495.9x` (below) is the **ratio of the
totals** over five reads. They differ because the per-task token counts differ. The README
quotes the mean. Neither is wrong, but they are not interchangeable and should not be
swapped to whichever reads better.

⚠ **This baseline is pasting the whole file.** It is not a comparison with an agent that
reads the first rows and writes a query, which saves far less.

<details><summary>Token breakdown by tool call + latency</summary>

| Task | describe_dataset | describe_column | Column | Latency&nbsp;ms |
|------|----------------:|----------------:|--------|----------------:|
| `schema overview` | 892 | 0 | - | 40 |
| `crime type distribution` | 892 | 305 | Crm Cd Desc | 29 |
| `temporal range` | 892 | 387 | DATE OCC | 25 |
| `victim demographics` | 892 | 319 | Vict Age | 34 |
| `geographic coverage` | 892 | 223 | AREA NAME | 22 |

</details>

---

## Totals, under two baseline assumptions

The right total depends on whether the agent re-reads the file for each task,
and the difference is a factor of five. Both are given rather than picking the
flattering one.

### One read, five tasks (conservative: the honest single-session number)

An agent that reads the file once and answers all five tasks from that context:

| | Tokens | Cost @ $5/M |
|--|-------:|------------:|
| Baseline (one full read) | 111,028,360 | $555.14 |
| jDataMunch total (5 tasks) | 5,694 | $0.03 |
| **Saved** | **111,022,666** | **$555.11** |
| **Ratio** | **19,499.2x** | |

### Five independent sessions

Five separate sessions, each re-reading the file:

| | Tokens | Cost @ $5/M |
|--|-------:|------------:|
| Baseline (5 × full read) | 555,141,800 | $2,775.71 |
| jDataMunch total | 5,694 | $0.03 |
| **Saved** | **555,136,106** | **$2,775.68** |
| **Ratio** | **97,495.9x** | |

⚠ **The five-session figure assumes the 255 MB file is read from scratch five times.**
That is a real pattern (separate sessions share no context), but it is an
assumption about usage, not a measurement, and it multiplies the headline saving
by five.

> Measured with tiktoken `cl100k_base`. Baseline = full raw file tokenized.
> jDataMunch = `describe_dataset` + `describe_column` per task. AI summaries
> disabled.
