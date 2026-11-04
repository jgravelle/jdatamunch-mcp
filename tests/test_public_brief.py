"""`CLAUDE.md` is public (it ships in the sdist), so it holds engineering rules only.

Release narrative belongs in `CHANGELOG.md`; maintainer-internal notes load
from a gitignored `CLAUDE.local.md`. These tests fail if either returns to the
public brief, or if the loader stops being ignored.
"""
from __future__ import annotations

import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent

#: Strings that mark maintainer-internal content.
INTERNAL_MARKERS = [
    r"\bjjg\b", r"\bJ's\b", r"Chief of Staff", r"Engineering-Doc",
    r"side-session-notes", r"\bCEO\b", r"\bruling\b", r"C:\\Apps",
    r"C:\\Users", r"\[\[",
]


def _brief() -> str:
    return (ROOT / "CLAUDE.md").read_text(encoding="utf-8")


def test_public_brief_carries_no_version_narrative():
    """Per-release bullets (`- **Version:** 1.2.3 ...`) belong in CHANGELOG.md."""
    narrative = re.findall(r"^- \*\*(?:Version|Prior)\b[^\n]*\d+\.\d+", _brief(), re.M)
    assert not narrative, (
        "CLAUDE.md is public and carries release narrative again; put the "
        f"lesson in 'Engineering lessons' and the narrative elsewhere: {narrative[:2]}"
    )


@pytest.mark.parametrize("marker", INTERNAL_MARKERS)
def test_public_brief_carries_no_internal_markers(marker):
    hits = [ln for ln in _brief().splitlines() if re.search(marker, ln)]
    assert not hits, (
        f"CLAUDE.md is public, but {marker!r} marks maintainer-internal content. "
        f"Move it to the private notes CLAUDE.local.md loads: {hits[:3]}"
    )


def test_the_marker_scan_fires_on_internal_text():
    """Non-vacuity: every marker matches a line it exists to catch."""
    samples = ["per jjg on the thread", "J's call", "ask the Chief of Staff",
               "Engineering-Doc wrote", "in side-session-notes", "the CEO said",
               "a ruling of 2026", "C:\\Apps\\hq", "C:\\Users\\j", "see [[memo]]"]
    for marker, sample in zip(INTERNAL_MARKERS, samples):
        assert re.search(marker, sample), (marker, sample)
    assert re.search(r"^- \*\*(?:Version|Prior)\b[^\n]*\d+\.\d+",
                     "- **Version:** 1.31.13 — text", re.M)


def test_claude_local_md_is_gitignored():
    gitignore = (ROOT / ".gitignore").read_text(encoding="utf-8")
    assert re.search(r"^CLAUDE\.local\.md\s*$", gitignore, re.M), (
        ".gitignore no longer lists CLAUDE.local.md"
    )
