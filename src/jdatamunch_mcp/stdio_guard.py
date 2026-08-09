"""Give JSON-RPC a private stdout so nothing else can corrupt the framing.

Suite parity with jdocmunch-mcp 1.129.0 (jdoc#110). This is a transport
contract, not a copied file: all three servers speak MCP over stdio and share
the hazard, so they should share the guarantee.

The MCP stdio transport writes framed JSON to stdout, and **any** other write
to that stream breaks a response.

⚠⚠ ``contextlib.redirect_stdout`` cannot close this. It rebinds ``sys.stdout``
and nothing more, so it never covers:

  - a C extension calling ``write(1, ...)`` — tqdm, tokenizers, torch,
  - a subprocess that inherited fd 1,
  - another thread, since the rebinding is process-global and unscoped.

``embeddings.py`` constructs a ``SentenceTransformer`` inside a tool call
(``embed_dataset``), so a first embed on a machine without the model cached
downloads it mid-request and its native progress output goes straight at the
JSON-RPC stream. jdatamunch has no startup warmup, so nothing pulled that load
off the request path.

Duplicating stdout and pointing fd 1 at stderr makes the guarantee structural:
afterwards fd 1 *is* stderr for the whole process, and the framed stream is
reachable only through the handle the transport holds.

⚠ Chatter written by a launcher *before* this process starts is already in the
pipe and cannot be retracted after exec. This closes everything written from
our own process onward.
"""

import os
import sys
from io import TextIOWrapper
from typing import Optional, TextIO


def claim_stdout() -> tuple[Optional[TextIO], bool]:
    """Hand back a private stdout stream, and point fd 1 at stderr.

    Returns ``(stream, swapped)``. ``stream`` is a UTF-8 text handle on the
    original stdout, or None when the swap could not be performed — in which
    case the caller should let the transport use ``sys.stdout`` as before.

    ⚠⚠ Fails OPEN. Under pythonw, a harness that replaced ``sys.stderr`` with a
    non-file object, or any environment without real file descriptors, this
    returns ``(None, False)`` and changes nothing. A server that starts with
    the old hazard beats a server that will not start.
    """
    try:
        stdout_fd = sys.stdout.fileno()
        stderr_fd = sys.stderr.fileno()
    except (AttributeError, OSError, ValueError):
        return None, False

    try:
        # Flush first: buffered bytes belong to the real stdout, and after the
        # swap they would be delivered to stderr instead.
        sys.stdout.flush()
    except (OSError, ValueError):
        pass

    try:
        private_fd = os.dup(stdout_fd)
    except OSError:
        return None, False

    try:
        os.dup2(stderr_fd, stdout_fd)
    except OSError:
        os.close(private_fd)
        return None, False

    try:
        # buffering=0 on the binary layer: a framed message must reach the
        # client when written, not when a buffer happens to fill.
        stream = TextIOWrapper(
            os.fdopen(private_fd, "wb", buffering=0),
            encoding="utf-8",
            write_through=True,
        )
    except (OSError, ValueError):
        try:
            os.close(private_fd)
        except OSError:
            pass
        return None, False

    return stream, True
