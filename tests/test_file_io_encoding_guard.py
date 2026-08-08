"""Text-mode file IO declares its encoding (suite parity with jcodemunch-mcp v1.108.264).

Read side of the cp1252 hazard. `open()`, `Path.read_text()` and
`Path.write_text()` use the platform default when no encoding is given, which is
cp1252 on Windows. Reading a UTF-8 file then raises on the five bytes cp1252
leaves undefined (`81 8D 8F 90 9D`) and silently mangles everything else it can
map. Writing produces a file the rest of the world cannot read.

Ported from jcodemunch-mcp, which swept the same three directions:
  - jcm v1.108.230  subprocess INPUT
  - jcm v1.108.262  our own OUTPUT (piped stdout)
  - jcm v1.108.264  file IO, this file

⚠ The jcm sweep was announced against jcm only. Nothing about a defect living in
one server implies it lives in its siblings, and nothing implies it does not --
this repo was scanned separately and its own count stands on that scan.

⚠⚠ The scanner is POSITIONAL-AWARE, and that is the whole reason this file
exists in this shape. `Path.read_text(encoding=None, errors=None)` takes encoding
as its FIRST POSITIONAL parameter, so `read_text("utf-8", errors="replace")` is
already correct. A keyword-only check counted 28 correct call sites as broken and
produced a published figure of "45 unencoded sites" that was wrong by a factor of
three. `test_positional_encoding_is_recognized` pins that, because the
false-positive direction is what discredits a guard: a ratchet nobody believes
gets exemptions added to it.
"""

import ast
import pathlib

import pytest

SRC = pathlib.Path(__file__).resolve().parents[1] / "src"

IO_FUNCS = {"open", "read_text", "write_text"}

# Callers that have no encoding parameter at all. Named individually, with the
# reason, rather than skipping a directory and hoping.
NO_ENCODING_PARAM = {
    "os": "os.open returns a file descriptor; encoding does not apply",
    "zipfile": "ZipFile.open is always binary",
    "wave": "wave.open is binary only and takes no encoding",
    "tarfile": "tarfile.open is binary",
    "shelve": "shelve.open is a pickle store, not text",
    "dbm": "dbm.open is a binary key-value store",
}

# A file mode is a short string drawn from this alphabet. Nothing else in an
# open() call looks like one, which is what makes matching on the VALUE more
# reliable than guessing its position.
_MODE_CHARS = set("rwxab+t")


def _has_positional_codec(node: ast.Call) -> bool:
    """True when a positional argument names a real codec.

    Symmetric with the mode rule: `open(p, "r", -1, "utf-8")` and
    `path.open("r", -1, "utf-8")` put encoding in different slots, so match it
    by VALUE too. `codecs.lookup` is the discriminator -- a filename is not a
    registered codec name.
    """
    import codecs
    # Skip args[0]: it is the FILE for `open`/`wave.open` and the MODE for
    # `path.open`, never an encoding in any spelling. Without this, a file
    # literally named "ascii" or "big5" reads as a codec and the call goes
    # unflagged -- a false NEGATIVE, which is the direction that lets a real
    # defect through.
    for arg in node.args[1:]:
        if not isinstance(arg, ast.Constant) or not isinstance(arg.value, str):
            continue
        v = arg.value
        if v and len(v) <= 4 and set(v) <= _MODE_CHARS:
            continue  # that is the mode, not a codec
        try:
            codecs.lookup(v)
            return True
        except (LookupError, TypeError, ValueError):
            continue
    return False


def _mode_literal(node: ast.Call) -> "str | None":
    """The file-mode string literal in a call, wherever it sits.

    Returns None when no positional mode literal is present (which includes
    `open(p)` defaulting to text, and a computed mode we cannot read).
    """
    for arg in node.args:
        if not isinstance(arg, ast.Constant) or not isinstance(arg.value, str):
            continue
        v = arg.value
        if v and len(v) <= 4 and set(v) <= _MODE_CHARS:
            return v
    for kw in node.keywords:
        if kw.arg == "mode" and isinstance(kw.value, ast.Constant):
            if isinstance(kw.value.value, str):
                return kw.value.value
    return None

# Ratchet. Empty on purpose: an entry here is a KNOWN gap with a reason, not a
# parking space. `test_known_gap_does_not_rot` deletes stale entries.
KNOWN_UNENCODED: set = set()


def _owner(node: ast.Call) -> str:
    fn = node.func
    if isinstance(fn, ast.Attribute) and fn.value is not None:
        try:
            return ast.unparse(fn.value)
        except Exception:
            return ""
    return ""


def _func_name(node: ast.Call) -> str:
    fn = node.func
    if isinstance(fn, ast.Attribute):
        return fn.attr
    if isinstance(fn, ast.Name):
        return fn.id
    return ""


def offenders_in_source(source: str, label: str) -> list[str]:
    """Text-mode IO calls that do not declare an encoding, positional or keyword."""
    try:
        tree = ast.parse(source)
    except SyntaxError:
        return []
    out = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        name = _func_name(node)
        if name not in IO_FUNCS:
            continue
        kwargs = {k.arg for k in node.keywords if k.arg}
        if "encoding" in kwargs:
            continue
        # POSITIONAL encoding. read_text(enc, errs) / write_text(data, enc, errs)
        if name == "read_text" and len(node.args) >= 1:
            continue
        if name == "write_text" and len(node.args) >= 2:
            continue
        if name == "open":
            owner = _owner(node)
            if any(owner == k or owner.startswith(k + ".") for k in NO_ENCODING_PARAM):
                continue
            if owner and owner.lower().startswith(("zf", "zip", "archive")):
                continue
            # ⚠⚠ Do NOT locate `mode` by argument POSITION. It sits in a
            # different slot for each spelling, and there are three:
            #     open(file, mode, ...)        builtin      -> args[1]
            #     path.open(mode, ...)         Path         -> args[0]
            #     wave.open(file, mode)        module       -> args[1]
            # Reading args[1] everywhere flagged `path.open("rb")`; "fixing" it
            # by branching on Name-vs-Attribute then flagged `wave.open(f,"rb")`,
            # because a module call is attribute-shaped but builtin-signatured.
            # Two false-positive classes traded for each other, both of them the
            # same positional guessing that produced the wrong "45".
            #
            # A file mode is recognisable BY VALUE, so match on that instead and
            # stop caring where it sits.
            mode = _mode_literal(node)
            if "mode" in kwargs and mode is None:
                continue  # computed mode; cannot prove it is text
            if mode and "b" in mode:
                continue
            if _has_positional_codec(node):
                continue
        out.append(f"{label}:{node.lineno}")
    return out


def _all_offenders() -> list[str]:
    found = []
    for path in sorted(SRC.rglob("*.py")):
        try:
            src = path.read_text(encoding="utf-8")
        except OSError:
            continue
        found.extend(offenders_in_source(src, path.relative_to(SRC).as_posix()))
    return found


class TestTheRatchet:
    def test_no_unencoded_text_io(self):
        offenders = [o for o in _all_offenders() if o not in KNOWN_UNENCODED]
        assert not offenders, (
            "text-mode file IO with no encoding= reads/writes with the platform "
            "default (cp1252 on Windows): it RAISES on the bytes cp1252 leaves "
            'undefined and silently mangles the rest. Add encoding="utf-8", '
            'errors="replace":\n  ' + "\n  ".join(offenders)
        )

    def test_known_gap_does_not_rot(self):
        """A listed gap that is actually fixed must be removed, so the set
        cannot decay into a permanent exemption."""
        stale = KNOWN_UNENCODED - set(_all_offenders())
        assert not stale, (
            "KNOWN_UNENCODED lists call sites that are now compliant; delete "
            "them: " + ", ".join(sorted(stale))
        )

    def test_the_tree_is_actually_being_scanned(self):
        """Non-vacuity floor: a scanner that silently walks nothing passes
        every assertion above."""
        files = list(SRC.rglob("*.py"))
        # Floor sized to THIS repo (81 files at the time of writing), not
        # copied from jcm. A floor larger than the tree fails forever; a floor of
        # 1 passes over a scan that collapsed to nothing. Either way the ratchet
        # stops meaning anything.
        assert len(files) > 60, f"only {len(files)} files found; scan is broken"


class TestTheScannerItself:
    """⚠⚠ A guard is only worth having if its FALSE POSITIVE rate is zero.

    The first version of this scan checked only the `encoding=` keyword. It
    reported 45 offenders where 14 existed, because it did not know that
    `read_text`'s first positional parameter IS encoding. That number reached a
    published changelog and release notes before anyone checked it.
    """

    @pytest.mark.parametrize("snippet", [
        'p.read_text()',
        'p.write_text(data)',
        'open(path)',
        'open(path, "r")',
        'open(path, "w")',
        'with open(path) as f: pass',
    ])
    def test_it_flags_the_real_thing(self, snippet):
        assert offenders_in_source(snippet, "x"), f"missed: {snippet}"

    @pytest.mark.parametrize("snippet", [
        'p.read_text(encoding="utf-8")',
        'p.read_text("utf-8")',
        'p.read_text("utf-8", errors="replace")',
        'p.write_text(data, encoding="utf-8")',
        'p.write_text(data, "utf-8")',
        'open(path, encoding="utf-8")',
        'open(path, "rb")',
        'open(path, "wb")',
        'open(path, "r", -1, "utf-8")',
        'os.open(path, os.O_RDWR)',
        'zf.open(name)',
        # Path.open takes mode FIRST; the builtin takes it second.
        'path.open("rb")',
        'path.open("wb")',
        'path.open(encoding="utf-8")',
        'path.open("r", -1, "utf-8")',
    ])
    def test_it_does_not_flag_correct_code(self, snippet):
        assert not offenders_in_source(snippet, "x"), f"false positive: {snippet}"

    @pytest.mark.parametrize("snippet", ['path.open()', 'path.open("r")', 'path.open("w")'])
    def test_it_still_flags_text_mode_path_open(self, snippet):
        """The other direction: fixing the false positive must not blind it."""
        assert offenders_in_source(snippet, "x"), f"missed: {snippet}"

    @pytest.mark.parametrize("snippet", [
        'open(path, "rb")',        # builtin: mode is args[1]
        'path.open("rb")',         # Path:    mode is args[0]
        'wave.open(path, "rb")',   # module:  attribute-shaped, builtin-signatured
        'gzip.open(path, "rb")',
        'open(path, mode="rb")',
    ])
    def test_binary_is_recognised_in_every_call_shape(self, snippet):
        """⚠⚠ Three spellings, three different slots for `mode`.

        Reading args[1] everywhere flagged `path.open("rb")`. Branching on
        Name-vs-Attribute to fix that then flagged `wave.open(f, "rb")`, because
        a module call is attribute-shaped but builtin-signatured. Both are the
        same positional guessing that produced the wrong published "45".

        The mode is matched BY VALUE now, so its position stops mattering.
        """
        assert not offenders_in_source(snippet, "x"), f"false positive: {snippet}"

    @pytest.mark.parametrize("snippet", ['open("ascii")', 'open("big5")', 'open("utf8")'])
    def test_a_filename_that_looks_like_a_codec_is_still_flagged(self, snippet):
        """False NEGATIVES are the direction that lets a real defect through.
        Matching a codec by value anywhere would excuse `open("ascii")`."""
        assert offenders_in_source(snippet, "x"), f"missed: {snippet}"

    def test_a_path_is_never_mistaken_for_a_mode(self):
        """The risk the value-matching rule takes on: a short filename made of
        mode characters. `_MODE_CHARS` excludes '.' and '/', so a real path
        cannot match, and a bare stem that could is not a thing open() is given
        without an extension."""
        assert offenders_in_source('open("data.txt")', "x")
        assert offenders_in_source('open(base / "war")', "x")

    def test_positional_encoding_is_recognized(self):
        """The exact bug that produced the wrong published figure. Pinned so a
        future edit to this scanner cannot reintroduce it."""
        assert not offenders_in_source('p.read_text("utf-8", errors="replace")', "x")
        import inspect
        # Unbound method, so parameters[0] is `self`; encoding is the first
        # parameter a CALLER supplies, which is what the scanner counts.
        sig = inspect.signature(pathlib.Path.read_text)
        params = [p for p in sig.parameters if p != "self"]
        assert params[0] == "encoding", (
            "read_text's first parameter is no longer encoding; this scanner's "
            "positional rule is wrong and must be re-derived"
        )


class TestTheHazardIsReal:
    """Without these, the guard above is a style rule rather than a bug fix."""

    def test_the_default_decode_raises_on_utf8_content(self, tmp_path):
        p = tmp_path / "t.txt"
        p.write_bytes("a ” b".encode("utf-8"))  # U+201D -> E2 80 9D, 9D undefined
        try:
            p.read_text()
        except UnicodeDecodeError as e:
            assert e.encoding in ("charmap", "cp1252")
        else:
            pytest.skip("this platform's default encoding can read UTF-8")

    def test_explicit_utf8_round_trips(self, tmp_path):
        p = tmp_path / "t.txt"
        original = "café — test ”"
        p.write_text(original, encoding="utf-8")
        assert p.read_text(encoding="utf-8", errors="replace") == original


# jcm carries a TestLegacyFilesStillLoad class here, covering a config file whose
# header comment held an em-dash and was therefore written as cp1252 on Windows.
# This repo has no such file: every site fixed alongside this guard holds JSON
# with ASCII keys, or a git SHA. Nothing to migrate, so nothing is asserted --
# a ported test with no subject would pass vacuously and read as coverage.
