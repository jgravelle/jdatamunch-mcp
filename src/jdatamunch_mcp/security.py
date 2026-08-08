"""Security utilities for path validation and column name sanitization."""

import re
import sys
from pathlib import Path


def verify_package_integrity() -> None:
    """Warn at startup if this code is running from an unofficial distribution."""
    expected_dist = "jdatamunch-mcp"
    canonical_url = "https://github.com/jgravelle/jdatamunch-mcp"
    try:
        from importlib.metadata import packages_distributions
        distributions = packages_distributions().get("jdatamunch_mcp", [])
        if not distributions:
            return
        actual_dist = distributions[0]
        if actual_dist != expected_dist:
            print(
                f"\nSECURITY WARNING: jdatamunch_mcp is running from distribution "
                f"'{actual_dist}' instead of the official '{expected_dist}'.\n"
                f"This may indicate a supply-chain attack or unofficial fork.\n"
                f"Install only from PyPI: pip install {expected_dist}\n"
                f"Official source: {canonical_url}\n",
                file=sys.stderr,
            )
    except Exception:
        pass


_SAFE_DATASET_RE = re.compile(r'^[A-Za-z0-9_\-\.]+$')
SUPPORTED_EXTENSIONS = frozenset([".csv", ".tsv", ".xlsx", ".xls"])


def validate_dataset_id(dataset_id: str) -> str:
    """Validate dataset identifier — alphanumeric, _, -, . only."""
    if not dataset_id or len(dataset_id) > 128:
        raise ValueError(f"Invalid dataset ID: {dataset_id!r}")
    if not _SAFE_DATASET_RE.fullmatch(dataset_id):
        raise ValueError(
            f"Invalid dataset ID (only alphanumeric, _, -, . allowed): {dataset_id!r}"
        )
    if dataset_id in (".", ".."):
        raise ValueError(f"Invalid dataset ID: {dataset_id!r}")
    return dataset_id


def validate_file_path(path: str) -> Path:
    """Validate and return absolute path to a supported tabular file."""
    p = Path(path).resolve()
    suffix = p.suffix.lower()
    if suffix not in SUPPORTED_EXTENSIONS:
        raise ValueError(
            f"Unsupported file format: {suffix!r}. Supported: {', '.join(sorted(SUPPORTED_EXTENSIONS))}"
        )
    if not p.exists():
        raise FileNotFoundError(f"File not found: {path}")
    if not p.is_file():
        raise ValueError(f"Not a file: {path}")
    return p


def sanitize_column_name(name: str) -> str:
    """Escape a column name for safe use inside double-quoted SQL identifiers."""
    if not name or len(name) > 256:
        raise ValueError(f"Invalid column name: {name!r}")
    # Escape embedded double quotes by doubling them (SQL standard)
    return name.replace('"', '""')


def validate_column_names(columns: list, schema_columns: list) -> list:
    """Validate that all requested column names exist in the schema.

    Returns the validated column list (unchanged).
    Raises ValueError with INVALID_COLUMN code if any column is missing.
    """
    schema_set = {c["name"] for c in schema_columns}
    for col in columns:
        if col not in schema_set:
            raise ValueError(f"INVALID_COLUMN: column {col!r} not found in schema")
    return columns


def validate_filter(f: dict, schema_columns: list) -> None:
    """Validate a single filter object against the schema."""
    VALID_OPS = {"eq", "neq", "gt", "gte", "lt", "lte", "contains", "in", "is_null", "between"}
    col = f.get("column")
    op = f.get("op")
    if not col:
        raise ValueError("INVALID_FILTER: missing 'column'")
    if not op:
        raise ValueError("INVALID_FILTER: missing 'op'")
    if op not in VALID_OPS:
        raise ValueError(f"INVALID_FILTER: unknown operator {op!r}")
    validate_column_names([col], schema_columns)

    val = f.get("value")
    if op in ("gt", "gte", "lt", "lte", "between", "contains", "in", "eq", "neq"):
        if val is None:
            raise ValueError(f"INVALID_FILTER: operator {op!r} requires a value")
    if op == "in":
        if not isinstance(val, list):
            raise ValueError("INVALID_FILTER: 'in' requires a list value")
    if op == "between":
        if not isinstance(val, list) or len(val) != 2:
            raise ValueError("INVALID_FILTER: 'between' requires [min, max] list")
