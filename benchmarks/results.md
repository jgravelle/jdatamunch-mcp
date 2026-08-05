# jdatamunch-mcp — Token Efficiency Benchmark

**Tokenizer:** `cl100k_base` (tiktoken)
**Workflow:** `describe_dataset` + `describe_column` (per task)
**Baseline:** full raw source file tokenized (minimum for a "read everything" agent)
**AI summaries:** disabled (clean retrieval-only measurement)

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
| `schema overview` | 111,028,360 | 3,849 | **99.9965%** | 28,846.0x | $555.14 | $0.019 | **$555.12** |
| `crime type distribution` | 111,028,360 | 4,630 | **99.9958%** | 23,980.2x | $555.14 | $0.023 | **$555.12** |
| `temporal range` | 111,028,360 | 4,736 | **99.9957%** | 23,443.5x | $555.14 | $0.024 | **$555.12** |
| `victim demographics` | 111,028,360 | 4,442 | **99.9960%** | 24,995.1x | $555.14 | $0.022 | **$555.12** |
| `geographic coverage` | 111,028,360 | 4,371 | **99.9961%** | 25,401.1x | $555.14 | $0.022 | **$555.12** |
| **Average** | — | 4,406 | **99.9960%** | **25,333.2x** | **$555.14** | **$0.022** | **$555.12** |

> Costs at $5.00 / 1M input tokens (Claude Opus 4.8 / 4.7 / 4.6 published rate, as of 2026-08-05).
> Valuation is an input to this benchmark, not a claim about your bill.

⚠ **These figures were previously published as a flat `100.0%` reduction.** They are
not 100%. Rounding 99.996% to 100.0% turns "almost all of it" into "all of it,"
which is a different and unsupportable claim. The unrounded values are above.

⚠ **Two different statistics both get called "the ratio."** `25,333.2x` is the
**mean of the five per-task ratios**; `25,201.6x` (below) is the **ratio of the
totals**. They differ because the per-task token counts differ. The README quotes
25,333x. Neither is wrong, but they are not interchangeable and should not be
swapped to whichever reads better.

<details><summary>Token breakdown by tool call + latency</summary>

| Task | describe_dataset | describe_column | Column | Latency&nbsp;ms |
|------|----------------:|----------------:|--------|----------------:|
| `schema overview` | 3,849 | 0 | — | 35 |
| `crime type distribution` | 3,847 | 783 | Crm Cd Desc | 22 |
| `temporal range` | 3,849 | 887 | DATE OCC | 24 |
| `victim demographics` | 3,848 | 594 | Vict Age | 33 |
| `geographic coverage` | 3,849 | 522 | AREA NAME | 24 |

</details>

---

## Totals, under two baseline assumptions

The right total depends on whether the agent re-reads the file for each task,
and the difference is a factor of five. Both are given rather than picking the
flattering one.

### One read, five tasks (conservative — the honest single-session number)

An agent that reads the file once and answers all five tasks from that context:

| | Tokens | Cost @ $5/M |
|--|-------:|------------:|
| Baseline (one full read) | 111,028,360 | $555.14 |
| jDataMunch total (5 tasks) | 22,028 | $0.11 |
| **Saved** | **111,006,332** | **$555.03** |
| **Ratio** | **5,040.3x** | |

### Five independent sessions (the figure previously published as the headline)

Five separate sessions, each re-reading the file:

| | Tokens | Cost @ $5/M |
|--|-------:|------------:|
| Baseline (5 × full read) | 555,141,800 | $2,775.71 |
| jDataMunch total | 22,028 | $0.11 |
| **Saved** | **555,119,772** | **$2,775.60** |
| **Ratio** | **25,201.6x** | |

⚠ **The $2,775.60 figure assumes the 255 MB file is read from scratch five times.**
That is a real pattern — separate sessions share no context — but it is an
assumption about usage, not a measurement, and it multiplies the headline saving
by five. Earlier revisions presented it as the grand total without stating the
assumption.

> Measured with tiktoken `cl100k_base`. Baseline = full raw file tokenized.
> jDataMunch = `describe_dataset` + `describe_column` per task. AI summaries
> disabled.
