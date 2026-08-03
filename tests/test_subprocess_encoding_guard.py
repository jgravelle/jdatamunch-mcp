"""Preventive guard: a text-mode subprocess call must name its encoding.

⚠ **There is no live defect in this repository, and this test did not fix
one.** It was added when the same sweep that produced it found 36 offending
call sites in jcodemunch-mcp, 9 in jdocmunch-mcp and 1 in jragmunch-cli, and
**zero here**. jdatamunch's `src/` already satisfied the convention. This locks
that in rather than leaving it to luck — read a green run as "still clean", not
as "a bug was caught".

The hazard it holds off: ``subprocess.run(..., text=True)`` with no
``encoding=`` decodes the child's output with ``locale.getpreferredencoding()``.
On a stock Windows box that is **cp1252**; git emits UTF-8 unconditionally. What
happened in the siblings, measured:

* **Crash** on a byte undefined in cp1252 — only ``81 8d 8f 90 9d``. A smart
  closing quote ``”`` is ``e2 80 9d``, so an ordinary commit subject is enough.
  ``UnicodeDecodeError`` is raised inside ``subprocess``'s **reader thread**, so
  no ``try/except`` around the call catches it, ``stdout`` comes back ``None``,
  and the caller dies on ``.splitlines()`` naming neither git nor encoding.
* **Silent corruption** on a byte cp1252 *does* define. ``🎉`` is
  ``f0 9f 8e 89`` — all four defined — and came back as ``ðŸŽ‰`` with nothing
  reporting a problem.

The rule: any call to ``run``/``Popen``/``check_output``/``check_call``/``call``
that passes ``text=`` or ``universal_newlines=`` must also pass ``encoding=``.
``errors="replace"`` alongside it is the house default.

⚠ **This is invisible to CI and to any UTF-8 development box.** CI is Linux, so
nothing here would go red the day a new git call arrives without the kwarg. That
is precisely why the convention needs a test and not a habit.
"""

import ast
import pathlib

import pytest

REPO_ROOT = pathlib.Path(__file__).resolve().parents[1]

# Trees that ship or run against a real user's repository.
GUARDED_TREES = ("src", "benchmarks")

# EXEMPT BY NAME, with a reason — not a blanket skip.
#
# `tests/` drives git against fixture repositories this suite creates itself,
# with ASCII authors and ASCII commit subjects, so the kwarg would assert
# nothing about any code a user runs. It is listed here rather than merely left
# out of GUARDED_TREES so that adding a tree is a decision and not an oversight.
EXEMPT_TREES = ("tests",)

SUBPROCESS_FUNCS = {"run", "Popen", "check_output", "check_call", "call"}
TEXT_KWARGS = {"text", "universal_newlines"}

# KNOWN GAP — a ratchet, not an exemption. Empty because it has always been
# empty here; nothing was fixed to make it so. Kept rather than deleted: it is
# the mechanism that forces a future gap to be declared here instead of
# shipping silently, and `test_known_gap_does_not_rot` fails if a listed entry
# turns out to be compliant, so a stale entry cannot decay into an excuse.
KNOWN_UNENCODED: set[str] = set()


def _call_name(node: ast.Call) -> str:
    f = node.func
    if isinstance(f, ast.Attribute):
        return f.attr
    if isinstance(f, ast.Name):
        return f.id
    return ""


def _offenders_in_source(source: str, label: str) -> list[str]:
    """Return "label:lineno" for each text-mode call with no encoding."""
    try:
        tree = ast.parse(source)
    except SyntaxError:
        return []
    out = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call) or _call_name(node) not in SUBPROCESS_FUNCS:
            continue
        kwargs = {k.arg for k in node.keywords if k.arg}
        if not (kwargs & TEXT_KWARGS):
            continue
        if "encoding" in kwargs:
            continue
        out.append(f"{label}:{node.lineno}")
    return out


def _offenders_in(path: pathlib.Path) -> list[str]:
    return _offenders_in_source(
        path.read_text(encoding="utf-8", errors="replace"),
        path.relative_to(REPO_ROOT).as_posix(),
    )


def _guarded_files() -> list[pathlib.Path]:
    files: list[pathlib.Path] = []
    for tree in GUARDED_TREES:
        root = REPO_ROOT / tree
        if root.is_dir():
            files.extend(sorted(root.rglob("*.py")))
    return files


def _all_offenders() -> list[str]:
    found: list[str] = []
    for path in _guarded_files():
        found.extend(_offenders_in(path))
    return found


def test_guarded_trees_exist():
    """A renamed tree must break this test, not silently stop being scanned."""
    present = [t for t in GUARDED_TREES if (REPO_ROOT / t).is_dir()]
    assert "src" in present, "src/ must be scanned — it is the shipped package"
    assert _guarded_files(), "the guard scanned zero files; its scope is wrong"


def test_no_text_mode_subprocess_without_encoding():
    offenders = [o for o in _all_offenders() if o not in KNOWN_UNENCODED]
    assert not offenders, (
        "text-mode subprocess call with no encoding= — decodes git output with "
        "the platform default (cp1252 on Windows) and dies in subprocess's "
        "reader thread on any non-ASCII byte. Add "
        'encoding="utf-8", errors="replace":\n  ' + "\n  ".join(offenders)
    )


def test_known_gap_does_not_rot():
    """A listed gap that is actually fixed must be removed from the list."""
    live = set(_all_offenders())
    stale = KNOWN_UNENCODED - live
    assert not stale, (
        "KNOWN_UNENCODED lists call sites that are now compliant; delete them "
        "so the set cannot decay into a permanent exemption: " + ", ".join(sorted(stale))
    )


def test_exempt_trees_are_named_not_forgotten():
    """The exemption is a decision on the record, and it must not overlap scope."""
    assert not (set(EXEMPT_TREES) & set(GUARDED_TREES))
    for tree in EXEMPT_TREES:
        assert (REPO_ROOT / tree).is_dir(), f"exempt tree {tree!r} does not exist"


@pytest.mark.parametrize(
    "source,expected_hits",
    [
        ('subprocess.run(["git"], text=True)', 1),
        ('subprocess.run(["git"], universal_newlines=True)', 1),
        ('subprocess.run(["git"], text=True, encoding="utf-8")', 0),
        ('subprocess.Popen(["git"], text=True)', 1),
        ('subprocess.check_output(["git"], text=True)', 1),
        ('run(["git"], text=True)', 1),
        ('subprocess.run(["git"], capture_output=True)', 0),  # bytes mode is safe
        ('other.run(["git"], text=True)', 1),  # attribute match is deliberate
    ],
)
def test_detector_is_not_vacuous(source, expected_hits):
    """The scanner itself has to actually fire — a guard that finds nothing on
    a known-bad input passes forever. This drives the SAME function the
    repo-wide check uses, so the two cannot diverge."""
    assert len(_offenders_in_source(source, "sample.py")) == expected_hits
