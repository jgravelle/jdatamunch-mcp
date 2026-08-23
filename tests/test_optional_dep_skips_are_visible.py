"""A skipped module must not hide how many tests it holds.

`pytest.importorskip` at module scope raises `Skipped` during IMPORT, so the
entire file collapses to one `1 skipped` line however many tests are in it.

⚠⚠ **Measured here on 2026-08-23: `tests/test_excel_parser.py` reported as one
skip while holding 29 tests, 19 of which PASS on this machine.** The suite
summary read `817 passed, 2 skipped` and would have read exactly the same if
those 19 had run. The gap only surfaced by comparing TOTALS between two
interpreters: 819 local against 839 under `uv run --python 3.13`.

⚠ The construct being banned was also inert on its own terms:

    @pytest.mark.skipif(
        not pytest.importorskip("xlrd", reason="...") or False,
        reason="xlrd not installed",
    )

`importorskip` returns the MODULE, so `not module` is False and `False or
False` is False. **The skipif condition never fired.** Every skip came from the
import side effect, which is precisely the part that hides the count.

`importlib.util.find_spec` asks the same question without raising: the module
imports, every test is collected, and only the tests needing the absent package
report as skipped.

⚠ This bans it at MODULE scope only. Inside a fixture or a test body,
`importorskip` is correct and visible -- it skips that one test, and the count
reflects it. `conftest.py` uses it that way deliberately.
"""

from __future__ import annotations

import ast
import pathlib

TESTS_DIR = pathlib.Path(__file__).resolve().parent


def _module_scope_importorskip(tree: ast.AST) -> list[int]:
    """Line numbers of importorskip calls not nested in a function or class."""
    hits: list[int] = []

    def walk(node: ast.AST, inside_body: bool) -> None:
        for child in ast.iter_child_nodes(node):
            is_body = isinstance(
                child, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)
            )
            if isinstance(child, ast.Call) and not inside_body:
                fn = child.func
                name = getattr(fn, "attr", None) or getattr(fn, "id", None)
                if name == "importorskip":
                    hits.append(child.lineno)
            walk(child, inside_body or is_body)

    walk(tree, False)
    return hits


def test_no_module_scope_importorskip():
    offenders = []
    for path in sorted(TESTS_DIR.rglob("test_*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for line in _module_scope_importorskip(tree):
            offenders.append(f"{path.name}:{line}")

    assert not offenders, (
        "module-scope pytest.importorskip collapses a whole file to one "
        "'1 skipped' line, so the suite summary reads the same whether its "
        "tests ran or not:\n  "
        + "\n  ".join(offenders)
        + "\nUse `importlib.util.find_spec(...) is None` in a skipif instead, "
        "so the tests are collected and skipped individually."
    )


def test_the_optional_dep_modules_actually_collect():
    """The outcome, not the mechanism.

    ⚠ Asserts these files yield real test items rather than asserting how they
    guard themselves. A future rewrite that hides them a different way fails
    here even if it passes the scan above.
    """
    import subprocess
    import sys

    root = TESTS_DIR.parent
    for name in ("test_excel_parser.py", "test_parquet_parser.py"):
        out = subprocess.run(
            [sys.executable, "-m", "pytest", f"tests/{name}",
             "--collect-only", "-q"],
            cwd=root, capture_output=True, text=True, timeout=300,
            env={**__import__("os").environ, "PYTHONPATH": "src"},
        )
        collected = out.stdout.count("::")
        assert collected > 1, (
            f"{name} collected {collected} test item(s). A module whose tests "
            "vanish at import reports as one skip and hides everything it "
            f"holds.\n{out.stdout[-800:]}"
        )
