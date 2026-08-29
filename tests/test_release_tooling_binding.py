"""The release instructions a HUMAN follows must name tooling that works on a dev box.

CLAUDE.md's `## Releasing` section told a maintainer to run `python -m build`
and `twine upload`. Neither works here: `build` is not in the project venv, and
the global `twine` validates metadata with a `packaging` that predates the
metadata version hatchling now emits, so the upload aborts. That combination
stopped jdocmunch-mcp 1.132.0 mid-release on 2026-08-12.

These tests bind the documented commands to the constraint they must satisfy,
so the drift fails CI instead of failing a release.

Two boundaries matter and both are asserted:

* The `.github/workflows/release.yml` bullet describes CI, which installs
  `build` and `twine` fresh in a clean runner. Its `python -m build` is
  CORRECT. Nothing here may ban that string file-wide.
* The assertions name properties, not sentences. A reworded bullet that still
  names the right commands passes; a bullet that quietly loses the
  `twine check` gate does not.
"""

from __future__ import annotations

import re
from pathlib import Path

REPO_ROOT = Path(__file__).parent.parent
CLAUDE_MD = REPO_ROOT / "CLAUDE.md"


def _text() -> str:
    return CLAUDE_MD.read_text(encoding="utf-8")


def _releasing_section() -> str:
    """The `## Releasing` section only, up to the next top-level heading."""
    text = _text()
    match = re.search(r"^## Releasing\s*$(.*?)(?=^## )", text, re.M | re.S)
    assert match, (
        "CLAUDE.md no longer has a `## Releasing` section. Every assertion in "
        "this file is scoped to it; without it they would all pass vacuously."
    )
    return match.group(1)


def _bullets() -> list[str]:
    """Top-level `- ` bullets of the Releasing section, with their indented body.

    A bullet owns every following line until the next top-level bullet or
    heading, so a fenced command block stays attached to the prose that
    introduces it.
    """
    bullets: list[str] = []
    for line in _releasing_section().splitlines():
        if line.startswith("- "):
            bullets.append(line)
        elif bullets and not line.startswith("#"):
            bullets[-1] += "\n" + line
    return bullets


def _commands(bullet: str) -> list[str]:
    """The command lines of the bullet's fenced blocks.

    Assertions about what a maintainer is told to RUN belong here, not in the
    prose: the prose legitimately names `python -m build` to say it does not
    work, and a guard that cannot tell an instruction from a prohibition is a
    guard nobody believes.
    """
    commands: list[str] = []
    inside = False
    for line in bullet.splitlines():
        if line.strip().startswith("```"):
            inside = not inside
            continue
        if inside and line.strip():
            commands.append(line.strip())
    return commands


def _manual_pypi_bullet() -> str:
    candidates = [b for b in _bullets() if "PyPI" in b and "twine" in b]
    assert len(candidates) == 1, (
        f"Expected exactly one Releasing bullet describing the manual PyPI "
        f"upload; found {len(candidates)}. The other tests here identify that "
        "bullet the same way, so an ambiguous match makes them target the "
        "wrong text."
    )
    return candidates[0]


def _workflow_bullet() -> str:
    candidates = [b for b in _bullets() if "workflows/release.yml" in b]
    assert len(candidates) == 1, (
        f"Expected exactly one Releasing bullet describing release.yml; found "
        f"{len(candidates)}. Matching on the bare filename also catches prose "
        "that merely refers to that bullet."
    )
    return candidates[0]


# --- 1. the manual bullet names tooling that runs on this box ----------------


def test_manual_pypi_bullet_builds_through_uvx() -> None:
    bullet = "\n".join(_commands(_manual_pypi_bullet()))
    assert "uvx --from build" in bullet, (
        "The manual PyPI bullet must build with `uvx --from build "
        "pyproject-build`. `python -m build` is not installed in the project "
        "venv (`uv run python -m build` -> No module named build), so the "
        "documented first step of a release cannot be run as written."
    )


def test_manual_pypi_bullet_runs_twine_through_uvx() -> None:
    bullet = "\n".join(_commands(_manual_pypi_bullet()))
    assert "uvx --from twine" in bullet, (
        "The manual PyPI bullet must invoke twine through `uvx --from twine`. "
        "The global twine validates metadata with the global `packaging`, "
        "which is capped below the Metadata-Version hatchling emits, and "
        "upgrading it breaks working packages that pin `packaging<25`. uvx "
        "resolves twine and its packaging in a throwaway env."
    )


def test_manual_pypi_bullet_keeps_the_twine_check_gate() -> None:
    bullet = "\n".join(_commands(_manual_pypi_bullet()))
    assert "twine check" in bullet, (
        "The manual PyPI bullet lost its `twine check` gate. check is the "
        "load-bearing command, not upload: without it a metadata failure is "
        "discovered DURING upload, i.e. possibly after the wheel is on PyPI "
        "and the sdist is not. A half-published version cannot be re-uploaded."
    )
    assert bullet.index("twine check") < bullet.index("twine upload"), (
        "`twine check` is documented after `twine upload`. A gate that runs "
        "after the irreversible step is not a gate."
    )


def test_manual_pypi_bullet_does_not_send_a_human_to_python_dash_m_build() -> None:
    commands = _commands(_manual_pypi_bullet())
    assert commands, (
        "The manual PyPI bullet has no fenced command block. Every assertion "
        "about what it tells a human to RUN reads that block, so an inline "
        "`python -m build` written into the prose would be invisible to them. "
        "Keep the commands fenced."
    )
    offenders = [c for c in commands if "python -m build" in c]
    assert not offenders, (
        "The manual PyPI bullet tells a human to RUN `python -m build`, which "
        "is not installed here. Scoped to the commands of that ONE bullet: the "
        "release.yml bullet's `python -m build` describes CI and is correct, "
        "and this bullet's prose may name the command to say it does not work. "
        "Offending commands:\n" + "\n".join(offenders)
    )


def test_the_ci_workflow_bullet_is_left_alone() -> None:
    """Guards the fix from over-reaching, not the file from drifting."""
    assert "python -m build" in _workflow_bullet(), (
        "The release.yml bullet no longer says the workflow builds with "
        "`python -m build`. CI installs build and twine fresh in a clean "
        "runner, so that command is correct there. Changing it to uvx "
        "documents something the workflow does not do."
    )


# --- 2. no human-facing line reaches for the global twine -------------------


def test_no_human_instruction_names_python_dash_m_twine() -> None:
    offenders = [
        command
        for bullet in _bullets()
        for command in _commands(bullet)
        if "python -m twine" in command
    ]
    assert not offenders, (
        "A line instructs a human to run `python -m twine`, which resolves the "
        "global interpreter's twine and its capped `packaging`. That is the "
        "failure this file exists to prevent. Offending lines:\n"
        + "\n".join(offenders)
    )


# --- 3. read CI before the step that cannot be undone -----------------------


def test_releasing_tells_the_reader_to_read_ci_before_the_manual_upload() -> None:
    section = _releasing_section()
    assert "gh run list" in section, (
        "`## Releasing` does not tell the maintainer to read the Tests run for "
        "the pushed SHA. The GitHub release here is gated on Tests passing, "
        "but PyPI is MANUAL, so a human can still upload against a red build "
        "and a PyPI upload cannot be taken back. Four consecutive jcm releases "
        "shipped on a red build for exactly this reason."
    )


def test_the_ci_read_names_this_repository() -> None:
    section = _releasing_section()
    command_lines = [ln for ln in section.splitlines() if "gh run list" in ln]
    assert any("jgravelle/jdatamunch-mcp" in ln for ln in command_lines), (
        "The documented CI-read command does not name this repository, so "
        "pasted from another checkout it reads a different repo's build and "
        "reports green for a SHA it never saw."
    )


def test_the_ci_read_asks_for_the_conclusion_and_the_sha() -> None:
    section = _releasing_section()
    window = section[section.index("gh run list") :] if "gh run list" in section else ""
    for field in ("headSha", "conclusion"):
        assert field in window, (
            f"The CI-read command does not request `{field}`. A run list "
            "without both the SHA and the conclusion cannot answer 'did the "
            "build for the commit I am about to publish pass'."
        )


# --- 4. the registry's nested-row trap -------------------------------------


def test_brief_warns_that_registry_rows_are_nested() -> None:
    text = _text()
    assert re.search(r"\bserver\.packages\b|packages\[\]", text), (
        "CLAUDE.md carries no warning that a registry row nests its payload "
        "under `server`. A flat row['name'] read returned 0 of 45 rows on a "
        "publish that completely succeeded."
    )
    assert "_meta" in text and "isLatest" in text, (
        "The registry warning does not say that `isLatest`/`publishedAt` live "
        'under `_meta["io.modelcontextprotocol.registry/official"]`, so a '
        "reader looking for the latest flag still reads the wrong level."
    )


def test_brief_says_a_zero_row_read_is_not_grounds_to_republish() -> None:
    text = _text().lower()
    assert "re-publish" in text or "republish" in text, (
        "CLAUDE.md does not tell the maintainer what to do on a zero-row read. "
        "This false negative survives `&limit=100`, so the documented paging "
        "remedy does not help and the symptom is indistinguishable from a "
        "failed publish. The instruction must be: fix the parse, do not "
        "re-publish."
    )
