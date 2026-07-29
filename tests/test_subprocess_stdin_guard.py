"""jdatamunch spawns no subprocesses, and a guard for the day that changes.

Ported from jcodemunch-mcp (jcm#392, @rknighton), and DELIBERATELY NOT the same
test. In a stdio MCP server, stdin IS the live JSON-RPC channel; a child launched
without `stdin=` inherits it and can block the server past its own timeout.

⚠ The jcm and jdoc versions of this file assert "every server-path spawn passes
`stdin=`" and back it with a non-vacuity check that the walker found spawns at
all. **jdatamunch currently spawns nothing** - no `subprocess`, no `os.system` /
`popen` / `exec*` / `spawn*`, no `asyncio.create_subprocess_*`, not even an
import. Copying the sibling test here verbatim would have produced a file that
passes because it checks nothing, which reads as coverage and provides none.

So the claim made here is the one that is actually true, and it is stronger:
jdata shells out to nothing at all. That is worth pinning on its own merits for a
tool that advertises itself as read-only and local - a new dependency on an
external binary is a change in what the product IS, not a detail. The stdin guard
rides along underneath and becomes load-bearing the moment the first spawn lands.

If you are adding a legitimate subprocess call, do not delete this file. Delete
`test_jdatamunch_spawns_no_subprocesses`, add your module to `_NOT_SERVER_PATH`
if it is CLI-only, and the remaining guards start doing the sibling repos' job.
"""

from __future__ import annotations

import ast
import pathlib

SRC_ROOT = pathlib.Path(__file__).resolve().parents[1] / "src" / "jdatamunch_mcp"

_SPAWNERS = {"run", "Popen", "call", "check_call", "check_output"}

# Names that hand work to another process. os.fork is excluded on purpose: it
# does not hand a child our stdin in a way this guard can reason about, and
# nothing here uses it.
_OS_SPAWNERS = {"system", "popen", "spawnl", "spawnle", "spawnlp", "spawnlpe",
                "spawnv", "spawnve", "spawnvp", "spawnvpe",
                "execl", "execle", "execlp", "execlpe",
                "execv", "execve", "execvp", "execvpe"}

# Exempt BY NAME, never by directory prefix, so a new server-path module cannot
# inherit an exemption by accident. Empty today because nothing spawns anything.
_NOT_SERVER_PATH: set[str] = set()


def _iter_calls():
    for path in sorted(SRC_ROOT.rglob("*.py")):
        rel = path.relative_to(SRC_ROOT).as_posix()
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.Call):
                yield rel, node


def _spawn_sites() -> list[tuple[str, int, bool]]:
    """(relative_path, lineno, passes_stdin) for every subprocess spawn in src."""
    sites: list[tuple[str, int, bool]] = []
    for rel, node in _iter_calls():
        func = node.func
        if (
            isinstance(func, ast.Attribute)
            and isinstance(func.value, ast.Name)
            and func.value.id == "subprocess"
            and func.attr in _SPAWNERS
        ):
            kwargs = {kw.arg for kw in node.keywords}
            sites.append((rel, node.lineno, "stdin" in kwargs))
    return sites


def _other_spawn_sites() -> list[str]:
    """os.system / os.popen / os.exec* / os.spawn* / asyncio.create_subprocess_*."""
    found: list[str] = []
    for rel, node in _iter_calls():
        func = node.func
        if not isinstance(func, ast.Attribute):
            continue
        if isinstance(func.value, ast.Name):
            if func.value.id == "os" and func.attr in _OS_SPAWNERS:
                found.append(f"{rel}:{node.lineno} os.{func.attr}")
            elif func.value.id == "asyncio" and func.attr.startswith("create_subprocess"):
                found.append(f"{rel}:{node.lineno} asyncio.{func.attr}")
    return found


def test_jdatamunch_spawns_no_subprocesses():
    """The invariant that actually holds today.

    jdata reads local files and SQLite. It does not shell out. If this fails, a
    dependency on an external binary just landed, which changes the product's
    install story and its airgap story - read the diff, don't relax the test.
    """
    spawns = [f"{rel}:{lineno}" for rel, lineno, _ in _spawn_sites()]
    others = _other_spawn_sites()
    assert not spawns and not others, (
        "jdatamunch now spawns a subprocess. See this file's docstring before "
        f"changing it. subprocess={spawns} other={others}"
    )


def test_no_server_path_subprocess_inherits_stdin():
    """Dormant until the first spawn lands, then it is the jcm#392 guard.

    ⚠ This assertion is VACUOUS today by construction, and that is stated rather
    than hidden: with zero spawns there is nothing to check. It exists so the
    first `subprocess.run(...)` added to this package arrives already covered,
    instead of repeating jcm's history where the convention was universal,
    unenforced, and eventually dropped by a later call site.
    """
    offenders = [
        f"{rel}:{lineno}"
        for rel, lineno, has_stdin in _spawn_sites()
        if not has_stdin and rel not in _NOT_SERVER_PATH
    ]
    assert not offenders, (
        "these run inside the MCP server process, where stdin is the live "
        "JSON-RPC channel, and must pass stdin=subprocess.DEVNULL "
        f"(jcm#392): {offenders}"
    )


def test_server_path_stdin_is_devnull():
    """`stdin=` alone isn't enough - PIPE reintroduces a blocking child."""
    wrong: list[str] = []
    for rel, node in _iter_calls():
        if rel in _NOT_SERVER_PATH:
            continue
        func = node.func
        if not (
            isinstance(func, ast.Attribute)
            and isinstance(func.value, ast.Name)
            and func.value.id == "subprocess"
            and func.attr in _SPAWNERS
        ):
            continue
        for kw in node.keywords:
            if kw.arg == "stdin" and not (
                isinstance(kw.value, ast.Attribute) and kw.value.attr == "DEVNULL"
            ):
                wrong.append(f"{rel}:{node.lineno}")
    assert not wrong, f"server-path stdin must be subprocess.DEVNULL: {wrong}"


def test_exemption_list_has_no_dead_entries():
    """A stale exemption is a hole nobody can see."""
    spawning = {rel for rel, _, _ in _spawn_sites()}
    dead = _NOT_SERVER_PATH - spawning
    assert not dead, f"exemption entries no longer spawn any process: {sorted(dead)}"


def test_the_walker_can_actually_see_calls():
    """The one non-vacuity check available here.

    Every other assertion in this file is satisfied by an empty result set, so
    if the AST walk silently stopped matching (package renamed, src layout
    moved), they would all still pass. This proves the walker is reading real
    code, which is what makes `test_jdatamunch_spawns_no_subprocesses` mean
    "nothing spawns" rather than "nothing was looked at".
    """
    calls = list(_iter_calls())
    assert len(calls) > 100, (
        f"AST walk found only {len(calls)} calls under {SRC_ROOT}; the walker or "
        "the source layout changed. Fix it rather than deleting this file."
    )
