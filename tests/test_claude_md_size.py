"""`CLAUDE.md` is loaded into every session under this directory, so its size is
a per-turn cost paid by every reader forever.

⚠⚠ **This gate is installed BEFORE it is needed, which is the point.** On
2026-08-21 jcodemunch-mcp's `CLAUDE.md` reached 200,543 chars and the harness
refused to load it — while the maintenance practice governing its size was being
followed. That practice named one section, and the growth was in the sections it
did not name. **A rule that names one section licenses every other section to
grow**, and a budget stated only in prose is not a budget.

This file is 54,615 chars today, so nothing needs rotating yet. The gate exists
so the day it matters arrives as a red test rather than as a session that cannot
read its own brief.

Failure here means rotate, do not delete: closed history goes to an archive that
no session loads, and `CLAUDE.md` keeps a pointer plus whatever standing lesson
the entries earned.
"""
from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent

# The harness refuses to load a project instruction file above this. It is not a
# style preference and it is not ours to raise.
HARNESS_LIMIT = 150_000

# Where the gate fires. The gap to HARNESS_LIMIT is deliberate: a ceiling that
# fires exactly at the cliff fires for the first time in the session it breaks.
BUDGET = 130_000

# The path this repo would rotate into. Nothing is there yet; the archive tests
# below are written so that absence is a pass, not a hole.
ARCHIVE = "docs/CLAUDE-history.md"


def _claude_md() -> str:
    return (ROOT / "CLAUDE.md").read_text(encoding="utf-8")


def test_claude_md_fits_the_session_budget():
    size = len(_claude_md())
    assert size <= BUDGET, (
        f"CLAUDE.md is {size:,} chars against a {BUDGET:,} budget "
        f"({HARNESS_LIMIT:,} is where the harness stops loading it). Rotate the "
        f"oldest release entries into {ARCHIVE} rather than deleting them, and "
        f"leave a pointer behind."
    )


def test_a_pointer_and_an_archive_imply_each_other():
    """Neither may exist without the other.

    ⚠⚠ **Stated BOTH ways on purpose.** The first version of this gate in
    jcodemunch-mcp asserted only that the archive exists, which is the wrong
    half: it cannot fire in a repo that has not rotated yet, and it says nothing
    about whether the archive is reachable. Asserting the implication in both
    directions catches a deleted archive, an unreferenced archive, and a pointer
    to a file nobody wrote — and passes cleanly here, where there is neither.

    Same correction @marcelruhf made to our licence ratchet: ours was right only
    about the accident of one repo.
    """
    archive_exists = (ROOT / ARCHIVE).is_file()
    pointer_exists = ARCHIVE in _claude_md()
    assert archive_exists == pointer_exists, (
        f"{ARCHIVE} exists={archive_exists} but CLAUDE.md points at it="
        f"{pointer_exists}. An archive nobody references is unreachable from the "
        f"file every session reads; a pointer to a missing archive is worse."
    )


def test_the_archive_is_tracked_by_git_once_it_exists():
    """⚠⚠ Present on disk is not the same as kept.

    jcodemunch-mcp's rotation first wrote its archive to a gitignored directory.
    Every check passed — the file existed, the pointer resolved, the budget was
    met — while the history would have vanished on the next clone and taken CI
    red with it. **"Does the file exist" answers a question about one working
    tree, not about the repository.**
    """
    if not (ROOT / ARCHIVE).is_file():
        pytest.skip(f"{ARCHIVE} does not exist yet; nothing to track")
    try:
        rc = subprocess.run(
            ["git", "ls-files", "--error-unmatch", ARCHIVE],
            cwd=ROOT, capture_output=True, stdin=subprocess.DEVNULL, timeout=10,
        ).returncode
    except (OSError, subprocess.TimeoutExpired):
        pytest.skip("no usable git here (sdist checkout or git absent)")
    assert rc == 0, (
        f"{ARCHIVE} is not tracked by git — check .gitignore. The rotated "
        f"history exists only in this working tree and will not survive a clone."
    )
