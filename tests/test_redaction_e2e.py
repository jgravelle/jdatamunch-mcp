"""End-to-end tests for the redaction policy across all wired tools."""

import csv

import pytest

from jdatamunch_mcp.tools.aggregate import aggregate
from jdatamunch_mcp.tools.describe_column import describe_column
from jdatamunch_mcp.tools.get_rows import get_rows
from jdatamunch_mcp.tools.index_local import index_local
from jdatamunch_mcp.tools.run_sql import run_sql
from jdatamunch_mcp.tools.sample_rows import sample_rows


def _rows(out):
    """Rows as dicts, whichever row_format the tool used."""
    body = out["result"]
    if "fields" in body:
        return [dict(zip(body["fields"], r)) for r in body["rows"]]
    return body["rows"]


@pytest.fixture
def pii_csv(tmp_path):
    """CSV with deliberate PII in multiple columns."""
    path = tmp_path / "pii.csv"
    with open(path, "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["id", "email", "ssn", "city", "card"])
        w.writerows([
            [1, "alice@example.com", "123-45-6789", "Hollywood", "4111-1111-1111-1111"],
            [2, "bob@example.com", "234-56-7890", "Central", "5555-5555-5555-4444"],
            [3, "carol@example.com", "345-67-8901", "Hollywood", "4111-1111-1111-1111"],
        ])
    return str(path)


@pytest.fixture
def pii_indexed(pii_csv, storage_dir):
    result = index_local(path=pii_csv, name="pii", storage_path=storage_dir)
    assert "error" not in result
    return storage_dir


class TestGetRowsRedaction:
    def test_default_on_redacts_email_and_ssn(self, pii_indexed):
        out = get_rows(dataset="pii", limit=10, storage_path=pii_indexed)
        rows = _rows(out)
        for row in rows:
            assert "@" not in str(row["email"])
            assert "-" not in str(row["ssn"]) or row["ssn"].startswith("[REDACTED")
        red = out["_meta"]["redaction"]
        assert red["applied"] is True
        assert red["cells_redacted"] >= 3
        assert "email" in red["patterns_matched"]
        assert "ssn" in red["patterns_matched"]

    def test_opt_out_returns_raw(self, pii_indexed):
        out = get_rows(dataset="pii", limit=10, redact=False, storage_path=pii_indexed)
        rows = _rows(out)
        assert rows[0]["email"] == "alice@example.com"
        assert out["_meta"]["redaction"]["applied"] is False

    def test_skip_columns_preserves_named_column(self, pii_indexed):
        out = get_rows(
            dataset="pii",
            limit=10,
            redact_skip_columns=["email"],
            storage_path=pii_indexed,
        )
        rows = _rows(out)
        assert rows[0]["email"] == "alice@example.com"  # exempt
        assert "[REDACTED" in str(rows[0]["ssn"])  # still scrubbed


class TestSampleRowsRedaction:
    def test_default_on(self, pii_indexed):
        out = sample_rows(dataset="pii", n=3, storage_path=pii_indexed)
        for row in _rows(out):
            assert "@" not in str(row["email"])
        assert out["_meta"]["redaction"]["applied"] is True


class TestRunSqlRedaction:
    def test_email_redacted_in_sql_output(self, pii_indexed):
        out = run_sql(
            sql="SELECT email, ssn FROM rows",
            datasets=["pii"],
            storage_path=pii_indexed,
        )
        for row in _rows(out):
            assert "@" not in str(row["email"])
        assert out["_meta"]["redaction"]["applied"] is True


class TestAggregateRedaction:
    def test_group_by_email_redacted(self, pii_indexed):
        out = aggregate(
            dataset="pii",
            aggregations=[{"column": "*", "function": "count", "alias": "n"}],
            group_by=["email"],
            storage_path=pii_indexed,
        )
        groups = out["result"]["groups"]
        for grp in groups:
            assert "@" not in str(grp["email"])
        assert out["_meta"]["redaction"]["applied"] is True

    def test_cache_hit_respects_per_call_policy(self, pii_indexed):
        # First call (default redact=True) populates the cache with raw rows.
        out1 = aggregate(
            dataset="pii",
            aggregations=[{"column": "*", "function": "count", "alias": "n"}],
            group_by=["email"],
            storage_path=pii_indexed,
        )
        assert out1["_meta"]["redaction"]["applied"] is True

        # Second call with redact=False must return raw, even on a cache hit.
        out2 = aggregate(
            dataset="pii",
            aggregations=[{"column": "*", "function": "count", "alias": "n"}],
            group_by=["email"],
            redact=False,
            storage_path=pii_indexed,
        )
        emails = {grp["email"] for grp in out2["result"]["groups"]}
        assert "alice@example.com" in emails
        assert out2["_meta"]["redaction"]["applied"] is False


class TestDescribeColumnRedaction:
    def test_value_distribution_redacted(self, pii_indexed):
        out = describe_column(dataset="pii", column="email", storage_path=pii_indexed, detail="full")
        if "value_distribution" in out["result"]:
            for entry in out["result"]["value_distribution"]:
                assert "@" not in str(entry["value"])
        if "top_values" in out["result"]:
            for entry in out["result"]["top_values"]:
                assert "@" not in str(entry["value"])
        assert out["_meta"]["redaction"]["applied"] is True

    def test_opt_out(self, pii_indexed):
        out = describe_column(
            dataset="pii", column="email", redact=False, storage_path=pii_indexed, detail="full"
        )
        # At least one raw email value should appear in the distribution.
        found_raw = False
        for entry in (out["result"].get("value_distribution") or []):
            if "@example.com" in str(entry["value"]):
                found_raw = True
                break
        for entry in (out["result"].get("top_values") or []):
            if "@example.com" in str(entry["value"]):
                found_raw = True
                break
        assert found_raw
