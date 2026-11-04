"""Response-level token budget enforcer for jdatamunch-mcp.

Prevents any tool response from exceeding the configured token budget by
applying tool-specific truncation strategies, then falls back to generic
list-field trimming.
"""

import json
from typing import Any

from .config import get_max_response_tokens


def _estimate_tokens(obj: Any) -> int:
    """Estimate token count from a Python object via JSON serialisation."""
    return len(json.dumps(obj, separators=(",", ":")).encode("utf-8")) // 4


def _trim_list_field(result: dict, field: str, target_tokens: int) -> bool:
    """Binary-search trim a list field to fit within target_tokens. Returns True if trimmed."""
    lst = result.get(field)
    if not lst or not isinstance(lst, list):
        return False
    lo, hi = 1, len(lst)
    while lo < hi:
        mid = (lo + hi + 1) // 2
        result[field] = lst[:mid]
        if _estimate_tokens(result) <= target_tokens:
            lo = mid
        else:
            hi = mid - 1
    result[field] = lst[:lo]
    return lo < len(lst)


def enforce_budget(result: dict, tool_name: str) -> dict:
    """Truncate *result* (in-place) so its JSON fits within the token budget.

    Returns the (possibly modified) result with truncation details injected
    into result["_meta"]["truncation"] when any trimming occurred.
    """
    budget = get_max_response_tokens()
    if _estimate_tokens(result) <= budget:
        return result

    truncations: list[str] = []
    inner = result.get("result", {})

    # Tool-specific strategies ------------------------------------------------
    if tool_name == "describe_dataset" and isinstance(inner, dict):
        cols = inner.get("columns", [])
        if isinstance(cols, list) and len(cols) > 1:
            _trim_list_field(inner, "columns", budget * 4 // 3)
            truncations.append(f"columns truncated to {len(inner['columns'])}")

    elif tool_name in ("get_rows", "sample_rows", "join_datasets") and isinstance(inner, dict):
        rows = inner.get("rows", [])
        if isinstance(rows, list) and len(rows) > 1:
            # First try trimming rows
            original_rows = len(rows)
            _trim_list_field(inner, "rows", budget * 4 // 3)
            if len(inner["rows"]) < original_rows:
                truncations.append(f"rows truncated to {len(inner['rows'])}")
        # If still over budget, trim columns within each row
        if _estimate_tokens(result) > budget and isinstance(inner.get("rows"), list):
            all_rows = inner["rows"]
            if all_rows and isinstance(all_rows[0], dict):
                col_names = list(all_rows[0].keys())
                keep = max(1, len(col_names) // 2)
                inner["rows"] = [{k: row[k] for k in col_names[:keep] if k in row} for row in all_rows]
                truncations.append(f"columns per row trimmed to {keep}")
            elif all_rows and isinstance(all_rows[0], list) and isinstance(inner.get("fields"), list):
                keep = max(1, len(inner["fields"]) // 2)
                inner["fields"] = inner["fields"][:keep]
                inner["rows"] = [row[:keep] for row in all_rows]
                truncations.append(f"columns per row trimmed to {keep}")

    elif tool_name == "aggregate" and isinstance(inner, dict):
        groups = inner.get("groups", [])
        if isinstance(groups, list) and len(groups) > 1:
            _trim_list_field(inner, "groups", budget * 4 // 3)
            truncations.append(f"groups truncated to {len(inner['groups'])}")

    elif tool_name == "describe_column" and isinstance(inner, dict):
        for field in ("value_distribution", "top_values"):
            lst = inner.get(field)
            if isinstance(lst, list) and len(lst) > 1:
                before = len(lst)
                _trim_list_field(inner, field, budget * 4 // 3)
                truncations.append(f"{field} truncated to {len(inner[field])}")
                if len(inner[field]) < before and "top_values_complete" in inner:
                    inner["top_values_complete"] = False
                break

    elif tool_name == "search_data" and isinstance(result.get("result"), list):
        lst = result["result"]
        if len(lst) > 1:
            original = len(lst)
            lo, hi = 1, len(lst)
            while lo < hi:
                mid = (lo + hi + 1) // 2
                result["result"] = lst[:mid]
                if _estimate_tokens(result) <= budget:
                    lo = mid
                else:
                    hi = mid - 1
            result["result"] = lst[:lo]
            if lo < original:
                truncations.append(f"results truncated to {lo}")

    # Generic fallback: trim the largest list field in result["result"] --------
    if _estimate_tokens(result) > budget and isinstance(inner, dict):
        # Never trim a header on its own: "fields" (get_rows, describe_dataset)
        # and "top_values_fields" name the positions in their arrays, so a
        # shorter header would mislabel every value after the cut.
        list_fields = [(k, v) for k, v in inner.items()
                       if isinstance(v, list) and not k.endswith("fields")]
        list_fields.sort(key=lambda x: len(x[1]), reverse=True)
        for field, _ in list_fields:
            _trim_list_field(inner, field, budget * 4 // 3)
            truncations.append(f"{field} trimmed (generic)")
            if _estimate_tokens(result) <= budget:
                break

    # Inject truncation metadata ----------------------------------------------
    if truncations:
        meta = result.setdefault("_meta", {})
        meta["truncation"] = {
            "applied": True,
            "details": truncations,
            "budget_tokens": budget,
            "hint": "Response exceeded token budget. Use column/row projections to get full data.",
        }

    return result
