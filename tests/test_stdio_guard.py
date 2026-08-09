"""jdata#stdio — JSON-RPC owns a private stdout; everything else goes to stderr.

Suite parity with jdocmunch-mcp 1.129.0 (jdoc#110). The MCP stdio transport
writes framed JSON to stdout, so **any** other write to that stream breaks a
response.

⚠⚠ `contextlib.redirect_stdout` never covered this. It rebinds `sys.stdout`
only, so a C extension calling `write(1, ...)`, a subprocess that inherited
fd 1, and any background thread all sail past it — and those are exactly what a
model download emits. `embeddings.py` builds a SentenceTransformer inside `embed_dataset`, and
jdatamunch has no startup warmup, so nothing pulled that load off the
request path.

⚠ These tests run REAL SUBPROCESSES on purpose. An in-process test of a
file-descriptor swap would be testing the mock.
"""

import json
import os
import pathlib
import subprocess
import sys
import textwrap


def _run(body: str) -> tuple[str, str]:
    src = str(pathlib.Path(__file__).resolve().parents[1] / "src")
    env = dict(os.environ, PYTHONPATH=src, PYTHONIOENCODING="utf-8")
    proc = subprocess.run(
        [sys.executable, "-c", textwrap.dedent(body)],
        # ⚠ encoding is explicit: `text=True` alone decodes with the parent's
        # locale (cp1252 on Windows), which mojibakes UTF-8 output.
        capture_output=True, text=True, encoding="utf-8", env=env, timeout=120,
    )
    return proc.stdout, proc.stderr


def test_every_kind_of_write_is_diverted_to_stderr():
    """⚠⚠ `os.write(1, ...)` is the case redirect_stdout cannot catch."""
    out, err = _run("""
        import os, sys
        from jdatamunch_mcp.stdio_guard import claim_stdout
        stream, swapped = claim_stdout()
        assert swapped
        print("PYTHON PRINT")
        sys.stdout.write("SYS STDOUT WRITE\\n"); sys.stdout.flush()
        os.write(1, b"NATIVE FD WRITE\\n")
        stream.write("JSONRPC\\n"); stream.flush()
    """)
    assert out.strip() == "JSONRPC"
    for expected in ("PYTHON PRINT", "SYS STDOUT WRITE", "NATIVE FD WRITE"):
        assert expected in err


def test_a_child_process_cannot_reach_the_private_stream():
    out, err = _run("""
        import subprocess, sys
        from jdatamunch_mcp.stdio_guard import claim_stdout
        stream, swapped = claim_stdout()
        assert swapped
        subprocess.run([sys.executable, "-c", "print('CHILD ON STDOUT')"])
        stream.write("JSONRPC\\n"); stream.flush()
    """)
    assert out.strip() == "JSONRPC"
    assert "CHILD ON STDOUT" in err


def test_a_background_thread_cannot_reach_it_either():
    out, err = _run("""
        import threading
        from jdatamunch_mcp.stdio_guard import claim_stdout
        stream, swapped = claim_stdout()
        assert swapped
        t = threading.Thread(target=lambda: print("THREAD OUTPUT"))
        t.start(); t.join()
        stream.write("JSONRPC\\n"); stream.flush()
    """)
    assert out.strip() == "JSONRPC"
    assert "THREAD OUTPUT" in err


def test_buffered_output_is_flushed_before_the_swap():
    """⚠ Bytes buffered on stdout belong to stdout; swapping without flushing
    would silently deliver them to stderr."""
    out, _ = _run("""
        import sys
        sys.stdout.write("EARLY")
        from jdatamunch_mcp.stdio_guard import claim_stdout
        stream, swapped = claim_stdout()
        assert swapped
        stream.write("\\nJSONRPC\\n"); stream.flush()
    """)
    assert out.startswith("EARLY")
    assert "JSONRPC" in out


def test_the_private_stream_is_unbuffered():
    out, _ = _run("""
        from jdatamunch_mcp.stdio_guard import claim_stdout
        stream, _ = claim_stdout()
        stream.write("JSONRPC\\n")
        import os; os._exit(0)
    """)
    assert out.strip() == "JSONRPC"


def test_it_fails_open_when_stderr_has_no_descriptor():
    """⚠⚠ pythonw, or a harness that replaced sys.stderr. A server that starts
    with the old hazard beats one that will not start."""
    out, _ = _run("""
        import io, sys
        sys.stderr = io.StringIO()
        from jdatamunch_mcp.stdio_guard import claim_stdout
        stream, swapped = claim_stdout()
        assert stream is None and swapped is False
        print("STILL RUNNING")
    """)
    assert "STILL RUNNING" in out


def test_it_fails_open_when_stdout_has_no_descriptor():
    out, _ = _run("""
        import io, sys
        sys.stdout = io.StringIO()
        from jdatamunch_mcp.stdio_guard import claim_stdout
        stream, swapped = claim_stdout()
        assert stream is None and swapped is False
        sys.stderr.write("FAILED OPEN\\n")
    """)
    assert out == ""


def test_unicode_survives_the_private_stream():
    out, _ = _run("""
        from jdatamunch_mcp.stdio_guard import claim_stdout
        stream, _ = claim_stdout()
        stream.write('{"t":"caf\\u00e9 \\u2014 \\u65e5\\u672c\\u8a9e"}\\n'); stream.flush()
    """)
    assert json.loads(out)["t"] == "café — 日本語"
