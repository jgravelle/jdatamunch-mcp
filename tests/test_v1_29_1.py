"""v1.29.1 — eager local-embedding import (issue #3).

The first `import sentence_transformers` used to happen inside an
`asyncio.to_thread` worker on the first embed call. On Windows that loads
torch's native DLLs from a non-main thread while the main thread has a pending
stdio pipe read, and deadlocks on the loader lock: `embed_dataset` /
`check_embedding_drift` never return. The import now happens on the main
thread in `main()`, before the stdio loop starts.
"""

from __future__ import annotations

import ast
import importlib.util
import inspect
from pathlib import Path

import pytest

from jdatamunch_mcp import server
from jdatamunch_mcp.embeddings import warm_up_provider

ST_INSTALLED = importlib.util.find_spec("sentence_transformers") is not None


@pytest.fixture
def fake_st(monkeypatch):
    """Make `import sentence_transformers` succeed without the real library.

    torch is a ~2 GB dependency, so CI does not install it. Stubbing the module
    lets the success path of the warm-up be tested where it actually runs
    instead of skipping there.
    """
    import sys
    import types

    if ST_INSTALLED:
        yield
        return
    monkeypatch.setitem(sys.modules, "sentence_transformers", types.ModuleType("sentence_transformers"))
    yield


# ── warm_up_provider ─────────────────────────────────────────────────────


class TestWarmUpProvider:
    def test_no_provider_configured_is_a_noop(self, monkeypatch):
        for var in (
            "JDATAMUNCH_EMBED_MODEL",
            "GOOGLE_API_KEY",
            "GOOGLE_EMBED_MODEL",
            "OPENAI_API_KEY",
            "OPENAI_EMBED_MODEL",
            "JDATAMUNCH_EAGER_EMBED_IMPORT",
        ):
            monkeypatch.delenv(var, raising=False)
        assert warm_up_provider() is False

    @pytest.mark.parametrize("provider", ["gemini", "openai"])
    def test_network_providers_stay_lazy(self, monkeypatch, provider):
        """They load no native code, so there is nothing to pay startup for."""
        monkeypatch.delenv("JDATAMUNCH_EAGER_EMBED_IMPORT", raising=False)
        assert warm_up_provider(provider) is False

    def test_opt_out_env_disables_it(self, monkeypatch):
        monkeypatch.setenv("JDATAMUNCH_EAGER_EMBED_IMPORT", "0")
        assert warm_up_provider("sentence_transformers") is False

    def test_imports_sentence_transformers(self, monkeypatch, fake_st):
        """CI has no sentence-transformers, so the success path is stubbed.

        Gating this on the real library would leave the whole point of the fix
        unverified everywhere it actually runs — a skip is not a pass.
        """
        import sys

        monkeypatch.delenv("JDATAMUNCH_EAGER_EMBED_IMPORT", raising=False)
        assert warm_up_provider("sentence_transformers") is True
        assert "sentence_transformers" in sys.modules

    def test_detects_provider_from_env(self, monkeypatch, fake_st):
        monkeypatch.delenv("JDATAMUNCH_EAGER_EMBED_IMPORT", raising=False)
        monkeypatch.setenv("JDATAMUNCH_EMBED_MODEL", "all-MiniLM-L6-v2")
        assert warm_up_provider() is True

    @pytest.mark.skipif(not ST_INSTALLED, reason="sentence-transformers not installed")
    def test_imports_the_real_library_when_present(self, monkeypatch):
        """Non-vacuity for the stubbed pair above, on a machine that has it."""
        import sys

        monkeypatch.delenv("JDATAMUNCH_EAGER_EMBED_IMPORT", raising=False)
        assert warm_up_provider("sentence_transformers") is True
        assert "sentence_transformers" in sys.modules

    def test_broken_backend_does_not_stop_startup(self, monkeypatch):
        """A missing or half-installed torch must not take the server down."""
        import builtins

        real_import = builtins.__import__

        def boom(name, *args, **kwargs):
            if name == "sentence_transformers":
                raise ImportError("simulated broken install")
            return real_import(name, *args, **kwargs)

        monkeypatch.delenv("JDATAMUNCH_EAGER_EMBED_IMPORT", raising=False)
        monkeypatch.setattr(builtins, "__import__", boom)
        monkeypatch.delitem(
            __import__("sys").modules, "sentence_transformers", raising=False
        )
        assert warm_up_provider("sentence_transformers") is False


# ── Wiring: it has to run BEFORE the event loop ──────────────────────────


class TestMainWiring:
    def _main_body(self) -> ast.FunctionDef:
        tree = ast.parse(Path(inspect.getsourcefile(server)).read_text(encoding="utf-8"))
        for node in tree.body:
            if isinstance(node, ast.FunctionDef) and node.name == "main":
                return node
        pytest.fail("server.main not found")

    def _call_lines(self, fn: ast.FunctionDef, name: str) -> list[int]:
        return [
            n.lineno
            for n in ast.walk(fn)
            if isinstance(n, ast.Call)
            and isinstance(n.func, ast.Name)
            and n.func.id == name
        ]

    def test_main_warms_the_provider_up(self):
        assert self._call_lines(self._main_body(), "warm_up_provider")

    def test_warm_up_precedes_asyncio_run(self):
        """After asyncio.run the import is back on a worker thread — the bug."""
        fn = self._main_body()
        warm = self._call_lines(fn, "warm_up_provider")
        run = [
            n.lineno
            for n in ast.walk(fn)
            if isinstance(n, ast.Call)
            and isinstance(n.func, ast.Attribute)
            and n.func.attr == "run"
        ]
        assert warm and run
        assert min(warm) < min(run)
