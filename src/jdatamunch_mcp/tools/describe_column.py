"""describe_column tool: Deep profile of a single column."""

import json
import time
from typing import Optional

from ..config import get_index_path, HARD_CAP_DESCRIBE_COLUMN_TOP_N, HARD_CAP_DESCRIBE_COLUMN_BINS
from ..profiler.histogram import compute_histogram
from ..redact import (
    merge_summary,
    redact_scalar_list,
    redact_value_distribution,
    redaction_meta,
)
from ..storage.data_store import DataStore
from ..storage.token_tracker import estimate_savings, record_savings, cost_avoided


def _offload():
    """The offloadable-work annotator, or None when it cannot be imported.

    Off by default; the module's own env gate decides whether anything is
    emitted. Imported lazily and tolerant of absence so a build without it
    degrades to "no annotation" rather than breaking column profiling.
    """
    try:
        from .. import offload

        return offload
    except ImportError:
        return None



def describe_column(
    dataset: str,
    column: str,
    top_n: int = 20,
    histogram_bins: int = 10,
    redact: bool = True,
    redact_patterns: Optional[list] = None,
    storage_path: Optional[str] = None,
    verify_source: bool = False,
) -> dict:
    """Return a deep profile of a single column.

    Full value distribution for low-cardinality; histogram bins for numeric;
    temporal range for datetime.

    ``value_distribution``, ``top_values``, and ``sample_values`` are scrubbed
    for PII / credentials by default. Counts and percentages are never altered.
    Pass ``redact=False`` to inspect raw cell values when working with data
    you own.
    """
    t0 = time.time()
    top_n = min(max(1, top_n), HARD_CAP_DESCRIBE_COLUMN_TOP_N)
    histogram_bins = min(max(1, histogram_bins), HARD_CAP_DESCRIBE_COLUMN_BINS)
    store = DataStore(base_path=storage_path or str(get_index_path()))

    idx = store.load(dataset)
    if idx is None:
        return {"error": f"NOT_INDEXED: dataset {dataset!r} is not indexed."}

    # Resolve column by name or ID (e.g. "lapd-crime::AREA NAME#column")
    if "#column" in column:
        col_name = column.split("::")[-1].replace("#column", "")
    else:
        col_name = column

    col_data = next((c for c in idx.columns if c["name"] == col_name), None)
    if col_data is None:
        return {"error": f"INVALID_COLUMN: column {col_name!r} not found in dataset {dataset!r}"}

    sample_values = col_data.get("sample_values", []) or []
    sample_summary: Optional[dict] = None
    if redact and sample_values:
        sample_values, sample_summary = redact_scalar_list(
            sample_values, custom_patterns=redact_patterns
        )

    result: dict = {
        "id": f"{dataset}::{col_name}#column",
        "name": col_name,
        "type": col_data["type"],
        "count": col_data["count"],
        "null_count": col_data["null_count"],
        "null_pct": col_data["null_pct"],
        "cardinality": col_data["cardinality"],
        "cardinality_is_exact": col_data["cardinality_is_exact"],
        "is_unique": col_data["is_unique"],
        "sample_values": sample_values,
    }

    vd_summary: Optional[dict] = None
    tv_summary: Optional[dict] = None

    # Value distribution (for low-cardinality)
    if col_data.get("value_index"):
        # Sort by count descending, limit to top_n
        sorted_vals = sorted(
            col_data["value_index"].items(),
            key=lambda x: x[1],
            reverse=True,
        )
        total = sum(c for _, c in sorted_vals)
        value_distribution = [
            {"value": v, "count": c, "pct": round(c / total * 100, 2) if total else 0}
            for v, c in sorted_vals[:top_n]
        ]
        if redact:
            value_distribution, vd_summary = redact_value_distribution(
                value_distribution, custom_patterns=redact_patterns
            )
        result["value_distribution"] = value_distribution
        result["unique_values_truncated"] = len(sorted_vals) > top_n

    elif col_data.get("top_values"):
        total = sum(tv["count"] for tv in col_data["top_values"])
        top_values = [
            {"value": tv["value"], "count": tv["count"],
             "pct": round(tv["count"] / total * 100, 2) if total else 0}
            for tv in col_data["top_values"][:top_n]
        ]
        if redact:
            top_values, tv_summary = redact_value_distribution(
                top_values, custom_patterns=redact_patterns
            )
        result["top_values"] = top_values

    # Numeric stats + histogram
    if col_data["type"] in ("integer", "float"):
        result["min"] = col_data.get("min")
        result["max"] = col_data.get("max")
        result["mean"] = col_data.get("mean")
        result["median"] = col_data.get("median")

        # Rebuild histogram from value_index if available (low-cardinality numeric)
        if col_data.get("value_index") and histogram_bins > 0:
            numeric_vals = []
            for val_str, cnt in col_data["value_index"].items():
                try:
                    v = float(val_str)
                    numeric_vals.extend([v] * min(cnt, 1000))  # cap to avoid huge lists
                except (ValueError, TypeError):
                    pass
            if numeric_vals:
                result["histogram"] = compute_histogram(
                    numeric_vals, bins=histogram_bins,
                    col_min=col_data.get("min"), col_max=col_data.get("max"),
                )

    # Datetime range
    if col_data["type"] == "datetime":
        result["datetime_min"] = col_data.get("datetime_min")
        result["datetime_max"] = col_data.get("datetime_max")
        result["datetime_format"] = col_data.get("datetime_format")

    if col_data.get("ai_summary"):
        result["ai_summary"] = col_data["ai_summary"]

    response_bytes = len(json.dumps(result).encode("utf-8"))
    tokens_saved = estimate_savings(idx.source_size_bytes, response_bytes)
    total_saved = record_savings(tokens_saved, str(store.base_path), tool="describe_column")

    combined_summary = merge_summary(sample_summary or {}, vd_summary or {}, tv_summary or {})

    # Freshness of the SOURCE FILE this profile describes. A column profile is
    # a claim about data on disk, so a caller deserves to know when that file
    # has changed or vanished underneath the index.
    #
    # ⚠ `fresh` is reachable ONLY via verify_source=True, which re-hashes the
    # file. The cheap reading proves `stale` / `missing_source` or says
    # `unknown`; it never asserts currency. That is jData's standing rule —
    # the index channel is a positive detection, never a standing claim.
    freshness = (
        store.verify_source(idx) if verify_source else store.source_freshness(idx)
    )
    verdict = {
        "state": "degraded" if freshness["state"] in ("stale", "missing_source") else "ok",
        "scorer": 1,
        "channels": {},
        "note": (
            "The source file changed since indexing; this profile describes "
            "the indexed snapshot, not the file as it stands now."
            if freshness["state"] in ("stale", "missing_source")
            else "Profile returned from the indexed snapshot."
        ),
    }
    if freshness["state"] in ("stale", "missing_source"):
        verdict["channels"]["index"] = "stale"
    elif freshness["state"] == "fresh":
        verdict["channels"]["index"] = "fresh"

    out = {
        "result": result,
        "_meta": {
            "timing_ms": round((time.time() - t0) * 1000, 1),
            "freshness": freshness,
            "verdict": verdict,
            "tokens_saved": tokens_saved,
            "total_tokens_saved": total_saved,
            **cost_avoided(tokens_saved, total_saved),
            "redaction": redaction_meta(
                applied=redact,
                summary=combined_summary,
                custom_patterns=redact_patterns,
            ),
        },
    }
    _mod = _offload()
    if _mod is not None:
        # ⚠ A column's "body" is its PROFILE, not raw rows, and `type` is the
        # field every profile carries whether or not the column has samples.
        # Keying on `sample_values` would read a legitimately empty column as a
        # missing body, and redaction can empty that list on purpose.
        #
        # The dataset is this column's container, supplied as a VIEW so the
        # served `result` never grows a field just to satisfy the annotator.
        _mod.annotate(
            out,
            units=[dict(result, dataset=dataset)],
            retrieval_mode=_mod.MODE_IDENTITY,
            body_field="type",
            container_field="dataset",
            verify_with={
                "tool": "describe_column",
                "args": {"dataset": dataset, "column": column, "verify_source": True},
            },
        )
    return out
