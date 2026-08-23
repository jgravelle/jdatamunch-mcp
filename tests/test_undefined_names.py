"""No undefined names in src/ -- each one is a NameError waiting for its path.

Ported from jdocmunch-mcp's `test_lint_gate_regressions.py` on 2026-08-23,
after this repo demonstrated why it needed it.

⚠⚠ **The same defect was introduced into BOTH repos in the same change, and
only jdoc caught it.** The ported `_initialization_options()` logged through a
module-level `logger` that neither server.py defines. jdoc's suite went red on
F821; jdata's suite went GREEN with the identical bug in it, because this gate
did not exist here. A direct `ruff --select F821` run found it immediately.

That is the standing lesson one level up: a setting fixed in one repo of a
suite is fixed in one repo, and it applies to the GATES as much as to the code
they guard. The bug was equally present in both; only the ability to see it
differed.

⚠ Why the line would have shipped undetected: it sits on the branch taken only
when the installed MCP SDK predates the `instructions` field. Nobody here runs
that SDK, so the NameError would have surfaced on exactly the install least
able to diagnose it -- an old, unattended one.

⚠ Deliberately narrow: F821 only. A gate that also failed on this repo's
deliberate E402/F401 patterns would be switched off within a week. This asserts
the one rule that represents a runtime crash.

⚠ The lesson is not "add a linter". jcodemunch-mcp HAD the check and shipped
four consecutive releases with it red, because nobody read it. A gate is worth
exactly as much as the habit of reading it, which is why this one lives in the
suite rather than only in CI.
"""

import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from jdatamunch_mcp import server as S


class TestNoUndefinedNames:
    def test_src_has_no_undefined_names(self):
        if shutil.which("ruff") is None:
            try:
                import ruff  # noqa: F401
            except ImportError:
                pytest.skip("ruff not installed; CI runs the same check")
        root = Path(__file__).resolve().parents[1]
        out = subprocess.run(
            [sys.executable, "-m", "ruff", "check", "--select", "F821",
             "--output-format=concise", "src/"],
            cwd=root, capture_output=True, text=True, timeout=300,
        )
        assert out.returncode == 0, (
            "undefined name(s) in src/ -- each is a NameError waiting for its "
            "code path:\n" + out.stdout
        )


class TestTheModuleCanResolveWhatItLogsThrough:
    """The specific shape that got past this repo once already."""

    def test_logging_is_reachable_at_module_scope(self):
        """`logging` must be importable at module scope, not only inside the one
        function that happened to import it locally.

        ⚠ A function-local `import logging` satisfies a naive `"import logging"
        in source` check while leaving module-scope references undefined. That
        is exactly how this shipped: the check that was run matched the local
        import and reported success.
        """
        assert hasattr(S, "logging"), "server.py cannot reach `logging`"
        assert S.logging.getLogger("x") is not None
