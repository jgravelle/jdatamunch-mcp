"""The sdist must not bundle credentials.

Ported from jcodemunch-mcp, where this guard exists because the leak already
happened: `.claude/settings.local.json` stores approved Bash commands with
inline tokens, hatchling bundles every file in the project directory rather
than only the git-tracked ones, and jcm v0.2.0-0.2.5 shipped tokens to PyPI
and had to be yanked.

⚠⚠ **Ported by INTENT, not verbatim, and the difference is the whole point.**
jcm's version asserts `.claude/` appears in the repo `.gitignore`. Here that
assertion is FALSE and would fail: this repo's `.claude/settings.local.json`
is ignored through the developer's GLOBAL gitignore, so `git status` never
shows it, a fresh clone never has it, and **a CI guard that greps the checked-
out tree cannot fire.** The pyproject exclude is not one of two defences here,
it is the only one — which is exactly what this repo's own
`[tool.hatch.build.targets.sdist]` comment says, and why the guard has to
inspect a real artifact rather than a config file.

⚠ It leaks only from a LOCAL build, and a local build is how twine uploads
happen. So the decisive test builds the sdist itself instead of waiting for
one to be lying around.

Non-vacuity is the hazard specific to this file. `.claude/settings.local.json`
must actually EXIST in the working tree when the test runs, or an sdist that
omits it proves nothing — absence of output would merely reflect absence of
input. That precondition is asserted, and skipped VISIBLY when it does not
hold rather than passing quietly.
"""

from __future__ import annotations

import re
import shutil
import subprocess
import tarfile
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).parent.parent

#: The file that caused the original leak, and the canary for this guard.
CANARY = REPO_ROOT / ".claude" / "settings.local.json"

#: Paths that must never appear in a published sdist.
SENSITIVE_PATTERNS = re.compile(
    r"(\.claude[/\\]|\.env$|\.pypirc|\.aws[/\\]|\.ssh[/\\]"
    r"|id_rsa|id_ed25519|\.pem$|\.key$|credentials\.json)"
)

#: Content that must never appear either. A path check alone would miss a
#: token pasted into a file with an innocent name.
SECRET_CONTENT = [
    ("pypi token", re.compile(rb"pypi-[A-Za-z0-9_\-]{20,}")),
    ("github token", re.compile(rb"gh[pousr]_[A-Za-z0-9]{30,}")),
    ("openai key", re.compile(rb"sk-[A-Za-z0-9]{32,}")),
    ("google key", re.compile(rb"AIza[0-9A-Za-z_\-]{35}")),
]

#: `AKIAIOSFODNN7EXAMPLE` is AWS's own published documentation placeholder and
#: is a fixture in `tests/test_redact.py` — the redaction tests need a
#: well-formed key to redact. Scanning for AWS keys without this exemption
#: fails on our own test data, and a guard that cries wolf collects exemptions
#: until it means nothing.
AWS_KEY = re.compile(rb"AKIA[0-9A-Z]{16}")
AWS_DOC_PLACEHOLDER = b"AKIAIOSFODNN7EXAMPLE"

#: The directory, not the prefix. See the note in `test_the_sdist_omits_...`.
_CLAUDE_DIR = re.compile(r"[/\\]\.claude[/\\]")

_HAS_UV = shutil.which("uv") is not None


@pytest.fixture(scope="module")
def built_sdist(tmp_path_factory) -> Path:
    """Build the real sdist from the real pyproject, once.

    ``uv build`` rather than ``python -m build``: neither ``build`` nor
    ``hatchling`` is installed in this project's venv (both are fetched into an
    isolated env at build time), while ``uv`` is this repo's toolchain and is
    already required to run the suite. Measured at well under two seconds.
    """
    if not _HAS_UV:
        pytest.skip("uv is not on PATH, so the real sdist cannot be built here")
    out = tmp_path_factory.mktemp("sdist")
    proc = subprocess.run(
        ["uv", "build", "--sdist", "--out-dir", str(out), str(REPO_ROOT)],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    assert proc.returncode == 0, f"uv build failed:\n{proc.stdout}\n{proc.stderr}"
    tarballs = list(out.glob("*.tar.gz"))
    assert len(tarballs) == 1, f"expected one sdist, got {tarballs}"
    return tarballs[0]


def _members(sdist: Path) -> list[str]:
    with tarfile.open(sdist, "r:gz") as tf:
        return [m.name for m in tf.getmembers() if m.isfile()]


# --- The config half -------------------------------------------------------- #


def test_pyproject_excludes_the_claude_dir():
    """The only line of defence in this repo, so its absence is the defect.

    ⚠⚠ Parses the TOML rather than grepping the file, and that is not
    fastidiousness. The first version of this test asserted `".claude/" in
    text` — and on the non-vacuity pass, with `exclude` emptied to `[]`, it
    PASSED, because `.claude/` also appears in the explanatory COMMENT three
    lines above the setting. A text-scanning ratchet that a comment satisfies
    is worse than no ratchet: it reads as coverage while asserting nothing.
    """
    try:
        import tomllib
    except ModuleNotFoundError:  # pragma: no cover - Python < 3.11
        import tomli as tomllib

    with open(REPO_ROOT / "pyproject.toml", "rb") as fh:
        cfg = tomllib.load(fh)
    sdist = cfg.get("tool", {}).get("hatch", {}).get("build", {}).get(
        "targets", {}
    ).get("sdist")
    assert sdist is not None, (
        "missing [tool.hatch.build.targets.sdist] in pyproject.toml"
    )
    excluded = sdist.get("exclude") or []
    assert any(e.strip().rstrip("/\\") == ".claude" for e in excluded), (
        f".claude/ must be in the sdist exclude list; got {excluded!r}"
    )


def test_the_repo_gitignore_is_not_what_protects_us():
    """Documents why jcm's version of this file was not copied verbatim.

    If `.claude/` is ever added to this repo's `.gitignore`, that is fine — but
    it must not be mistaken for the protection, and the pyproject exclude must
    not be removed on the strength of it. A gitignore governs what git tracks;
    hatchling bundles the DIRECTORY. They are different questions, and the
    leak this file exists for came from answering the second with the first.
    """
    gitignore = REPO_ROOT / ".gitignore"
    text = gitignore.read_text(encoding="utf-8") if gitignore.exists() else ""
    if ".claude" in text:
        pyproject = (REPO_ROOT / "pyproject.toml").read_text(encoding="utf-8")
        assert ".claude/" in pyproject, (
            ".claude/ was added to .gitignore and removed from the sdist "
            "exclude. Git tracking and build inclusion are different "
            "questions; hatchling bundles the directory regardless of git."
        )


# --- The artifact half, which is the one that can actually prove it --------- #


class TestRealSdist:
    def test_the_canary_exists_so_this_suite_is_not_vacuous(self):
        """Precondition for the test below, asserted rather than assumed.

        On a fresh clone or a CI runner the file is absent, and an sdist that
        omits it would prove nothing. Skipping VISIBLY is the honest outcome;
        passing quietly is the failure this repo spent 1.31.10 fixing in a
        different guise.
        """
        if not CANARY.exists():
            pytest.skip(
                f"{CANARY.relative_to(REPO_ROOT)} is absent (fresh clone or CI), "
                "so exclusion cannot be demonstrated on this machine"
            )
        assert CANARY.stat().st_size > 0

    def test_the_sdist_omits_the_claude_dir(self, built_sdist):
        """THE test. The canary is present in the working tree and absent from
        the artifact, so the exclude demonstrably fired."""
        if not CANARY.exists():
            pytest.skip("canary absent; see the precondition test above")
        # ⚠ `.claude/` with a separator, never the substring `.claude`.
        # `.claude-plugin/plugin.json` is a legitimately shipped MCP manifest
        # and a substring match flags it — caught on this test's first run.
        # A guard with false positives is one nobody believes, and a ratchet
        # nobody believes collects exemptions until it means nothing.
        leaked = [n for n in _members(built_sdist) if _CLAUDE_DIR.search(n)]
        assert not leaked, "sdist bundles .claude/:\n" + "\n".join(leaked)

    def test_the_sdist_has_no_sensitive_paths(self, built_sdist):
        bad = [n for n in _members(built_sdist) if SENSITIVE_PATTERNS.search(n)]
        assert not bad, "sensitive paths in sdist:\n" + "\n".join(bad)

    def test_the_sdist_has_no_secret_content(self, built_sdist):
        """A path check misses a token pasted into an innocently named file."""
        hits: list[str] = []
        with tarfile.open(built_sdist, "r:gz") as tf:
            for m in tf.getmembers():
                if not m.isfile() or m.size > 2_000_000:
                    continue
                data = tf.extractfile(m).read()
                for label, pat in SECRET_CONTENT:
                    if pat.search(data):
                        hits.append(f"{m.name}: {label}")
                for found in AWS_KEY.findall(data):
                    if found != AWS_DOC_PLACEHOLDER:
                        hits.append(f"{m.name}: aws key")
        assert not hits, "secret-shaped content in sdist:\n" + "\n".join(hits)

    def test_the_sdist_actually_contains_the_package(self, built_sdist):
        """Non-vacuity for every assertion above: an empty or truncated
        tarball would satisfy all of them."""
        members = _members(built_sdist)
        assert len(members) > 50, f"suspiciously small sdist: {len(members)} files"
        assert any(
            n.endswith("src/jdatamunch_mcp/__init__.py") for n in members
        ), "sdist does not contain the package source"


# --- The already-built case ------------------------------------------------- #


def test_any_prebuilt_sdist_in_dist_is_clean():
    """Covers the window this guard is really for: built locally, sitting in
    dist/, about to be uploaded. Skips visibly when dist/ is empty rather than
    returning quietly — a silent pass and an absent test read identically."""
    dist = REPO_ROOT / "dist"
    sdists = sorted(dist.glob("*.tar.gz")) if dist.exists() else []
    if not sdists:
        pytest.skip("no sdist in dist/; nothing built locally to check")
    for sdist in sdists:
        bad = [n for n in _members(sdist) if SENSITIVE_PATTERNS.search(n)]
        assert not bad, f"sensitive paths in {sdist.name}:\n" + "\n".join(bad)
