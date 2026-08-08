"""Value index builder for low-cardinality columns.

Low-cardinality columns (cardinality <= VALUE_INDEX_CARDINALITY_LIMIT) get a
full inverted index: {value_str: count}. This index is stored in index.json
and used by search_data for fast value matching without touching SQLite.
"""

# Re-export the constant so callers can import from this module


def build_value_search_index(profiles: list) -> dict:
    """Build a search-optimised view of all value indexes.

    Returns:
        {column_name: {"low_cardinality": bool, "values": [str, ...], "top_values": [...]}}

    Used by search_data to quickly find columns whose values match a query.
    """
    result = {}
    for p in profiles:
        if p.value_index is not None:
            result[p.name] = {
                "low_cardinality": True,
                "values": list(p.value_index.keys()),
            }
        elif p.top_values is not None:
            result[p.name] = {
                "low_cardinality": False,
                "values": [tv["value"] for tv in p.top_values],
            }
    return result
