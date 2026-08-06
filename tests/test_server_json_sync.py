"""server.json must stay in lockstep with pyproject.toml.

The MCP registry entry is what most downstream aggregators (mcp.so, MCPFind,
mcprepository, PulseMCP) display. A stale version there advertises an
abandoned-looking project regardless of what actually ships to PyPI.

Both registry entries sat frozen at their 2026-03-21 publish for five months
because nothing failed when the two files drifted. This test is that failure.
"""

import json
import re
from pathlib import Path

REPO_ROOT = Path(__file__).parent.parent
SERVER_JSON = REPO_ROOT / "server.json"
PYPROJECT = REPO_ROOT / "pyproject.toml"


def _pyproject_field(field):
    """Read a top-level [project] string field without requiring tomllib (3.11+)."""
    text = PYPROJECT.read_text(encoding="utf-8")
    match = re.search(rf'^{field}\s*=\s*"([^"]+)"', text, re.M)
    assert match, f"Could not read {field} from {PYPROJECT}"
    return match.group(1)


def test_server_json_exists():
    assert SERVER_JSON.exists(), (
        "server.json is the MCP registry manifest; without it the server "
        "cannot be published to registry.modelcontextprotocol.io"
    )


def test_server_json_version_matches_pyproject():
    manifest = json.loads(SERVER_JSON.read_text(encoding="utf-8"))
    expected = _pyproject_field("version")
    assert manifest["version"] == expected, (
        f"server.json version {manifest['version']!r} != pyproject.toml "
        f"{expected!r}. Bump both, then republish with mcp-publisher."
    )


def test_description_within_registry_limit():
    """The registry rejects a publish with description > 100 chars (HTTP 422).

    jdatamunch's first publish attempt failed on exactly this at 139 chars,
    after the version sync was already done. Catch it at test time instead of
    at the end of a release.
    """
    manifest = json.loads(SERVER_JSON.read_text(encoding="utf-8"))
    description = manifest["description"]
    assert len(description) <= 100, (
        f"server.json description is {len(description)} chars; the MCP "
        f"registry rejects anything over 100"
    )


def test_package_entries_match_pyproject():
    manifest = json.loads(SERVER_JSON.read_text(encoding="utf-8"))
    expected_version = _pyproject_field("version")
    expected_name = _pyproject_field("name")

    packages = manifest.get("packages") or []
    assert packages, "server.json must declare at least one package entry"

    for pkg in packages:
        assert pkg["version"] == expected_version, (
            f"server.json package {pkg.get('identifier')!r} pins version "
            f"{pkg['version']!r}, expected {expected_version!r}"
        )
        if pkg.get("registryType") == "pypi":
            assert pkg["identifier"] == expected_name, (
                f"server.json pypi identifier {pkg['identifier']!r} != "
                f"pyproject name {expected_name!r}"
            )
