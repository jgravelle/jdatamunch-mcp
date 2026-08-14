# Contributing to jDataMunch-MCP

Thanks for your interest in contributing! A few things to know before you submit a PR.

## Contributor License Agreement

This project is dual-licensed: free for non-commercial use, with paid licenses
for commercial use. To keep that model legally sound, **all contributors must
sign the CLA before their PR can be merged.**

The CLA is short and plain-English: you keep your copyright, you grant the
project the right to sublicense your contribution commercially, and you confirm
the work is yours to submit.

**[Sign the CLA](https://cla-assistant.io/jgravelle/jdatamunch-mcp)**

CLA Assistant will prompt you automatically when you open a PR. It takes about
30 seconds.

### The signing window is 24 hours

Once your PR is reviewed and green, you have **24 hours** to sign. If the CLA is
not signed by then, we implement the fix ourselves and credit you in the
CHANGELOG, the release notes and the close comment.

So the window decides whose commit it is. It does not decide whether you are
credited, and it does not decide whether your fix ships — both of those are
settled the moment the fix is right. We keep it short because a signed CLA takes
30 seconds and an unsigned one parks finished work behind a form.

If 24 hours does not work for you — you need legal review, or you are away — say
so on the PR and we will hold it. The clock exists to stop PRs going quiet, not
to catch anyone out.

## Commercial Licensing

If you're using jDataMunch in a commercial context, see the [license section in the
README](README.md) for options.

## Getting Started

Dev dependencies are declared in a PEP 735 `[dependency-groups]` block, not an
optional-dependencies extra, so there is no `.[test]` or `.[dev]` to install.

```bash
git clone https://github.com/jgravelle/jdatamunch-mcp
cd jdatamunch-mcp

# with uv
uv sync
uv run pytest tests/ -q

# or with pip
pip install -e . pytest pytest-asyncio pytest-cov
PYTHONPATH=src python -m pytest tests/ -q
```

Run the suite with `PYTHONPATH=src`. An installed copy of the package from PyPI
will otherwise shadow `src/`, and you will be testing the released code instead
of your change.

## Guidelines

- Open an issue before starting large features. Saves everyone time if direction needs discussion.
- Keep PRs focused; one feature or fix per PR
- Include tests for new functionality
- Run the full test suite before submitting

## One issue, one verdict

**An issue should be a single thing that can be judged true or false and then
closed.** If your report contains several independent findings, please open
several issues, or say so plainly and we will split it at triage.

This is not a request for less detail. Detailed, adversarial, multi-part reports
are some of the most valuable things this project receives, and none of the
scope gets dropped in a split; every part keeps its own thread, its own
reproduction, and its own credit.

It is about how they close. A report with four findings closes only when the
last one is settled, so three finished fixes sit behind one unfinished
conversation and the tracker cannot tell anyone which is which. Split into four,
three close within a day and the fourth is visibly the only thing outstanding.
That is better for you as well: your finished work ships instead of waiting.

What we do at triage:

- Split a multi-finding report into one issue per finding, cross-linked, credit
  on each.
- Keep the original as the parent only if it still has its own verdict. If it is
  purely an index of the others, we close it and say so.
- Accepted design work with no start date does not stay open as an issue at all.
  It moves to the roadmap with its close condition verbatim and its author
  credited. Parking is not rejection, and the roadmap says so.

## A release is never blocked on an open issue

**We do not hold a release hostage to an unfinished verification, including a
verification we asked for.**

When work is done, tested, and green, it ships on schedule. If review or
independent re-verification is still outstanding, the release says so in plain
language rather than waiting:

> Verified against the reviewer's pre-registered harness at a frozen SHA. Not
> independently re-verified by its author.

That wording is deliberately weaker than a sign-off and we will not blur the two
in a changelog. When the re-verification lands, whenever it lands, it counts in
full and we announce it retroactively. Nothing expires.

Every timebox we set names its default action, because a date with no stated
consequence is a wish. "Verification by X, or Y ships with disclosure Z."

**No timebox we offer runs longer than 24 hours.** That applies to all of them,
not only the CLA window above: signing a form, opening a PR you have already
written, or taking an issue you want to implement. At expiry the default action
fires — usually that we do the work ourselves — and you are credited in the
CHANGELOG, the release notes and the close comment either way.

The short clock is only fair because of that last sentence. It decides whose
commit it is. It never decides whether you are credited, and it never decides
whether the fix ships.

If 24 hours does not fit — you want the weekend for it, you are away, the change
is large — **say so and we will hold it.** An extension you ask for is not the
same as a default we hand out, and we would rather you told us than went quiet.
Timeboxes already posted are honoured as posted; we do not shorten a promise
after making it.

The point of this rule is that a reviewer's thoroughness should never become a
veto. If being careful can stall a release, then careful review is expensive to
accept, and that is the opposite of what we want. This way your findings are an
upgrade that can arrive at any time, and neither of us is negotiating under a
clock.
