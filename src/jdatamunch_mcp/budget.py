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


def _column_name(col: Any) -> Any:
    """Name of a describe_dataset column entry (a dict, or a compact array row)."""
    if isinstance(col, dict):
        return col.get("name")
    if isinstance(col, list) and col:
        return col[0]
    return None


def _note_dropped_columns(result: dict, inner: dict, dropped: list) -> None:
    """Keep column lists and the column_truncation note in step with a per-row
    column trim (get_rows, sample_rows, join_datasets)."""
    gone = set(dropped)
    for key in ("columns_a", "columns_b"):
        if isinstance(inner.get(key), list):
            inner[key] = [c for c in inner[key] if c not in gone]
    note = (result.get("_meta") or {}).get("column_truncation")
    if isinstance(note, dict):
        cut = [c for c in dropped if c != "_row_id"]
        note["returned"] = max(0, note.get("returned", 0) - len(cut))
        note["omitted_columns"] = cut + list(note.get("omitted_columns") or [])


def _recount(inner: dict, columns_before: Any) -> None:
    """Recount every count the trim could have made stale (issue 18).

    `returned` beside rows or groups is what callers page by
    (`offset + returned`), so a stale count skips the rows that were cut.
    """
    for field in ("rows", "groups"):
        if isinstance(inner.get(field), list) and "returned" in inner:
            inner["returned"] = len(inner[field])
            break

    rows = inner.get("rows")
    if "columns_projected" in inner and isinstance(rows, list) and rows:
        if isinstance(inner.get("fields"), list):
            names = inner["fields"]
        elif isinstance(rows[0], dict):
            names = list(rows[0].keys())
        else:
            names = None
        if names is not None:
            inner["columns_projected"] = len([n for n in names if n != "_row_id"])

    page = inner.get("column_pagination")
    cols = inner.get("columns")
    if isinstance(page, dict) and isinstance(cols, list) and columns_before is not None:
        cut = [_column_name(c) for c in columns_before[len(cols):]]
        if cut:
            page["returned"] = len(cols)
            page["truncated"] = True
            page["remaining_column_names"] = cut + list(page.get("remaining_column_names") or [])


_ROW_ID = "_row_id"
CUT_MARK = "..."


def _cap_cell(value: Any, cap: Any) -> Any:
    if cap is not None and isinstance(value, str) and len(value) > cap:
        return value[:cap] + CUT_MARK
    return value


def _shape_rows(rows: list, names: list, arrays: bool, cap: Any) -> list:
    """Project rows onto the first len(names) columns and cap long strings."""
    if arrays:
        keep = len(names)
        return [[_cap_cell(v, cap) for v in row[:keep]] for row in rows]
    return [{k: _cap_cell(row[k], cap) for k in names if k in row} for row in rows]


def _fit_rows(result: dict, inner: dict, budget: int, truncations: list) -> None:
    """Fit get_rows / sample_rows / join_datasets rows to the budget.

    Trim order, each step only when the one before cannot fit:

    1. Cut rows. The cut aims at the budget itself, so when one row fits the
       reply ships every column. (It used to aim at 4/3 of the budget, which
       left the reply over budget and always fell through to step 2.)
    2. Drop trailing columns, but never past the first data column, so a row
       always carries data and not only `_row_id`.
    3. Cut long string values to the longest length that fits, marked with
       CUT_MARK.

    After a column or value cut, rows are cut again from the full list, so
    the room the cut freed goes to more rows.
    """
    original = inner.get("rows")
    if not isinstance(original, list) or not original:
        return

    if len(original) > 1:
        _trim_list_field(inner, "rows", budget)
    if _estimate_tokens(result) <= budget:
        if len(inner["rows"]) < len(original):
            truncations.append(f"rows truncated to {len(inner['rows'])}")
        return

    arrays = isinstance(original[0], list) and isinstance(inner.get("fields"), list)
    if arrays:
        names = list(inner["fields"])
    elif isinstance(original[0], dict):
        names = list(original[0].keys())
    else:
        if len(inner["rows"]) < len(original):
            truncations.append(f"rows truncated to {len(inner['rows'])}")
        return

    def fits_one(keep: int, cap: Any) -> bool:
        if arrays:
            inner["fields"] = names[:keep]
        inner["rows"] = _shape_rows(original[:1], names[:keep], arrays, cap)
        return _estimate_tokens(result) <= budget

    # Step 2: the most columns a single row can carry, down to the floor.
    data_at = next((i for i, n in enumerate(names) if n != _ROW_ID), None)
    floor = len(names) if data_at is None else data_at + 1
    lo, hi = floor, len(names)
    while lo < hi:
        mid = (lo + hi + 1) // 2
        if fits_one(mid, None):
            lo = mid
        else:
            hi = mid - 1
    keep = lo

    # Step 3: still over at the floor, so cut long values instead.
    cap = None
    if not fits_one(keep, None):
        longest = max(
            (len(v) for row in original[:1]
             for v in (row[:keep] if arrays else [row.get(k) for k in names[:keep]])
             if isinstance(v, str)),
            default=0,
        )
        if longest:
            lo, hi = 0, longest
            while lo < hi:
                mid = (lo + hi + 1) // 2
                if fits_one(keep, mid):
                    lo = mid
                else:
                    hi = mid - 1
            cap = lo

    if arrays:
        inner["fields"] = names[:keep]
    inner["rows"] = _shape_rows(original, names[:keep], arrays, cap)
    if len(inner["rows"]) > 1:
        _trim_list_field(inner, "rows", budget)

    if len(inner["rows"]) < len(original):
        truncations.append(f"rows truncated to {len(inner['rows'])}")
    if keep < len(names):
        truncations.append(f"columns per row trimmed to {keep}")
        _note_dropped_columns(result, inner, names[keep:])
    if cap is not None:
        truncations.append(
            f"string values cut to {cap} characters, marked with a trailing {CUT_MARK!r}"
        )


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
    columns_before = (
        list(inner["columns"])
        if tool_name == "describe_dataset" and isinstance(inner, dict)
        and isinstance(inner.get("columns"), list)
        else None
    )

    # Tool-specific strategies ------------------------------------------------
    if tool_name == "describe_dataset" and isinstance(inner, dict):
        cols = inner.get("columns", [])
        if isinstance(cols, list) and len(cols) > 1:
            _trim_list_field(inner, "columns", budget * 4 // 3)
            truncations.append(f"columns truncated to {len(inner['columns'])}")

    elif tool_name in ("get_rows", "sample_rows", "join_datasets") and isinstance(inner, dict):
        _fit_rows(result, inner, budget, truncations)

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

    # Counts taken before the trim must describe what is sent ------------------
    if truncations and isinstance(inner, dict):
        _recount(inner, columns_before)

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
