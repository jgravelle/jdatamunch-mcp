"""CLI output is UTF-8 even when piped.

Suite parity with jcodemunch-mcp v1.108.262, where this was a live crash.

On Windows `sys.stdout` is the CONSOLE stream (already UTF-8) when attached to a
terminal and the LOCALE stream (cp1252) when piped or redirected. So output
containing any non-ASCII character works interactively and goes out as cp1252
bytes the moment anything consumes it.

⚠ This repo emits NO non-ASCII output today, so nothing is broken here yet. The
fix is preventive and the tests say so: they assert the mechanism, not a repaired
symptom. jcm shipped the crash for an unknown number of releases because it only
appears through a pipe, and jdoc carried the silent mojibake half. Putting the
guard in before the first non-ASCII character arrives is the cheap moment.

⚠ These run the CLI in a SUBPROCESS with a pipe, because that is the only
configuration that reproduces it. In-process, pytest's capture layer accepts any
string and the test cannot fail.
"""

import os
import subprocess
import sys
from pathlib import Path

import pytest

SRC = str(Path(__file__).resolve().parents[1] / "src")


def _run(args, env_extra=None):
    env = dict(os.environ)
    env["PYTHONPATH"] = SRC
    env.pop("PYTHONIOENCODING", None)
    if env_extra:
        env.update(env_extra)
    return subprocess.run(
        [sys.executable, "-m", "jdatamunch_mcp.server", *args],
        capture_output=True, env=env, timeout=300,
    )


class TestOutputIsUtf8:
    @pytest.mark.parametrize("args", [["--help"], ["--version"]])
    def test_output_decodes_as_utf8(self, args):
        out = _run(args)
        out.stdout.decode("utf-8")  # raises if we emitted cp1252
        assert b"UnicodeEncodeError" not in out.stderr

    def test_no_encode_error_on_any_command(self):
        out = _run(["--help"])
        assert out.returncode == 0, out.stderr.decode("utf-8", "replace")[-1500:]


class TestTheHelperItself:
    def test_it_does_not_raise_without_reconfigure(self, monkeypatch):
        """Captured buffers replace sys.stdout with objects that have no
        reconfigure(). The CLI must still start."""
        import io
        from jdatamunch_mcp import server as S
        monkeypatch.delenv("PYTHONIOENCODING", raising=False)
        monkeypatch.setattr(sys, "stdout", io.StringIO())
        monkeypatch.setattr(sys, "stderr", io.StringIO())
        S._force_utf8_stdio()

    def test_a_refusing_stream_is_survivable(self, monkeypatch):
        from jdatamunch_mcp import server as S

        class Stubborn:
            encoding = "cp1252"

            def reconfigure(self, **kw):
                raise OSError("no")

        monkeypatch.delenv("PYTHONIOENCODING", raising=False)
        monkeypatch.setattr(sys, "stdout", Stubborn())
        monkeypatch.setattr(sys, "stderr", Stubborn())
        S._force_utf8_stdio()

    def test_an_already_utf8_stream_is_left_alone(self, monkeypatch):
        from jdatamunch_mcp import server as S
        calls = []

        class Fine:
            encoding = "UTF-8"

            def reconfigure(self, **kw):
                calls.append(kw)

        monkeypatch.delenv("PYTHONIOENCODING", raising=False)
        monkeypatch.setattr(sys, "stdout", Fine())
        monkeypatch.setattr(sys, "stderr", Fine())
        S._force_utf8_stdio()
        assert calls == []

    def test_a_cp1252_stream_is_reconfigured_with_replace(self, monkeypatch):
        from jdatamunch_mcp import server as S
        calls = []

        class Locale:
            encoding = "cp1252"

            def reconfigure(self, **kw):
                calls.append(kw)

        monkeypatch.delenv("PYTHONIOENCODING", raising=False)
        monkeypatch.setattr(sys, "stdout", Locale())
        monkeypatch.setattr(sys, "stderr", Locale())
        S._force_utf8_stdio()
        assert len(calls) == 2
        assert all(c["encoding"] == "utf-8" and c["errors"] == "replace" for c in calls)

    def test_pythonioencoding_is_honoured(self, monkeypatch):
        from jdatamunch_mcp import server as S
        calls = []

        class Locale:
            encoding = "cp1252"

            def reconfigure(self, **kw):
                calls.append(kw)

        monkeypatch.setenv("PYTHONIOENCODING", "cp1252")
        monkeypatch.setattr(sys, "stdout", Locale())
        S._force_utf8_stdio()
        assert calls == [], "an operator who named an encoding made a decision"

    def test_main_calls_it_first(self):
        import inspect
        from jdatamunch_mcp import server as S
        src = inspect.getsource(S.main)
        assert "_force_utf8_stdio()" in src
        body = src[src.index('"""Main entry point."""'):]
        assert body.index("_force_utf8_stdio()") < 200


class TestTheMcpTransportIsNotAffected:
    def test_stdio_transport_uses_the_binary_layer(self):
        """The reason this is safe to apply unconditionally in main()."""
        import inspect
        import mcp.server.stdio as stdio_mod
        assert "sys.stdout.buffer" in inspect.getsource(stdio_mod), (
            "the MCP stdio transport no longer wraps the binary layer; re-check "
            "whether _force_utf8_stdio can still run for `serve`"
        )
