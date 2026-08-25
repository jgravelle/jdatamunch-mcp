"""search_data tool: Search across column names, values, and metadata."""

import logging
import time
from typing import Optional

from ..bm25 import BM25, tokenize
from ..config import get_index_path, HARD_CAP_SEARCH_MAX_RESULTS
from ..storage.data_store import DataStore, index_changed_since_load
from ..storage.token_tracker import get_total_saved
from ..tuning import load_effective_weights

logger = logging.getLogger(__name__)

# Ranking weights live in ..tuning (DEFAULT_WEIGHTS) and are resolved per
# dataset at query time via load_effective_weights(), so tune_weights can
# re-tune them without touching this module.

_DATE_KEYWORDS = frozenset(["date", "time", "year", "month", "day", "datetime", "timestamp"])
_NUM_KEYWORDS = frozenset(["count", "amount", "number", "num", "total", "age", "id", "code"])


def _score_column(col: dict, query_lower: str, query_words: set, w: dict) -> tuple:
    """Score a column against a query. Returns (score, match_details)."""
    score = 0
    matched_values: list = []
    match_type = "schema"

    name_lower = col["name"].lower()

    # Column name scoring
    if query_lower == name_lower:
        score += w["name_exact"]
    elif query_lower in name_lower:
        score += w["name_substr"]
    else:
        name_words = set(name_lower.replace("_", " ").replace("-", " ").split())
        word_hits = len(query_words & name_words)
        if word_hits:
            score += word_hits * w["name_word"]

    # AI summary scoring (when available)
    if col.get("ai_summary"):
        summary_lower = col["ai_summary"].lower()
        for word in query_words:
            if word in summary_lower:
                score += w["ai_summary_word"]

    # Value index: exact match
    value_source: list = []
    if col.get("value_index"):
        value_source = list(col["value_index"].keys())
    elif col.get("top_values"):
        value_source = [tv["value"] for tv in col["top_values"]]

    for v in value_source:
        v_lower = str(v).lower()
        hit = False
        for word in query_words:
            if word == v_lower:
                score += w["value_exact"]
                if str(v) not in matched_values:
                    matched_values.append(str(v))
                match_type = "value"
                hit = True
                break
        if not hit:
            for word in query_words:
                if len(word) >= 3 and word in v_lower:
                    score += w["value_substr"]
                    if str(v) not in matched_values:
                        matched_values.append(str(v))
                    match_type = "value"
                    break

    # Type-aware boost
    if col["type"] == "datetime" and query_words & _DATE_KEYWORDS:
        score += w["type_boost"]
    elif col["type"] in ("integer", "float") and query_words & _NUM_KEYWORDS:
        score += w["type_boost"]

    return score, matched_values, match_type


def _column_doc_tokens(col: dict) -> list:
    """Build the BM25 'document' for a column (B9): name + summary + values."""
    parts: list = []
    parts.extend(tokenize(col.get("name", "")))
    parts.extend(tokenize(col.get("ai_summary", "") or ""))
    if col.get("value_index"):
        for v in list(col["value_index"].keys())[:50]:
            parts.extend(tokenize(str(v)))
    elif col.get("top_values"):
        for tv in col["top_values"][:50]:
            parts.extend(tokenize(str(tv.get("value", ""))))
    if col.get("sample_values"):
        for v in col["sample_values"][:20]:
            parts.extend(tokenize(str(v)))
    if col.get("semantic_type"):
        parts.append(str(col["semantic_type"]))
    return parts


def _column_text(col: dict) -> str:
    """Build text representation of a column for embedding."""
    parts = [
        f"column: {col['name']}",
        f"type: {col.get('type', 'unknown')}",
    ]
    if col.get("ai_summary"):
        parts.append(col["ai_summary"])
    samples = col.get("sample_values") or []
    if samples:
        parts.append(f"values: {', '.join(str(v) for v in samples[:10])}")
    if col.get("value_index"):
        top = list(col["value_index"].keys())[:10]
        parts.append(f"categories: {', '.join(str(v) for v in top)}")
    elif col.get("top_values"):
        top = [tv["value"] for tv in col["top_values"][:10]]
        parts.append(f"categories: {', '.join(str(v) for v in top)}")
    return ". ".join(parts)


def _semantic_scores(
    query: str,
    columns: list[dict],
    store: "DataStore",
    dataset: str,
) -> dict[str, float]:
    """Compute semantic similarity scores for all columns.

    Lazily embeds missing columns on first call. Returns
    {column_name: cosine_similarity}.
    """
    from ..embeddings import detect_provider, embed_texts, cosine_similarity
    from ..storage.embedding_store import ColumnEmbeddingStore

    provider_info = detect_provider()
    if provider_info is None:
        raise ValueError(
            "No embedding provider configured. Set one of: "
            "JDATAMUNCH_EMBED_MODEL (sentence-transformers, free/local), "
            "GOOGLE_API_KEY + GOOGLE_EMBED_MODEL (Gemini), or "
            "OPENAI_API_KEY + OPENAI_EMBED_MODEL (OpenAI)."
        )
    provider, model = provider_info

    db_path = store.sqlite_path(dataset)
    emb_store = ColumnEmbeddingStore(db_path)

    # Lazy embed: compute missing column embeddings
    all_embeddings = emb_store.get_all()
    missing = [c for c in columns if c["name"] not in all_embeddings]
    if missing:
        texts = [_column_text(c) for c in missing]
        vecs = embed_texts(texts, provider, model)
        new_embeddings = {missing[i]["name"]: vecs[i] for i in range(len(vecs))}
        emb_store.set_many(new_embeddings)
        if vecs:
            emb_store.set_meta(dim=len(vecs[0]), model=model)
        all_embeddings.update(new_embeddings)

    # Embed the query
    query_vec = embed_texts([query], provider, model)[0]

    # Compute cosine similarity for each column
    scores: dict[str, float] = {}
    for col in columns:
        col_vec = all_embeddings.get(col["name"])
        if col_vec:
            scores[col["name"]] = cosine_similarity(query_vec, col_vec)
    return scores


def search_data(
    dataset: str,
    query: str,
    search_scope: str = "all",
    max_results: int = 10,
    semantic: bool = False,
    semantic_weight: Optional[float] = None,
    semantic_only: bool = False,
    storage_path: Optional[str] = None,
) -> dict:
    """Search across column names, values, and metadata.

    Returns column-level results with IDs — tells the agent where to look,
    not the data itself.

    When semantic=true, uses embedding-based similarity alongside keyword
    scoring. semantic_weight controls the blend (0.0 = pure keyword,
    1.0 = pure semantic). semantic_only=true skips keyword scoring entirely.
    """
    t0 = time.time()
    max_results = min(max(1, max_results), HARD_CAP_SEARCH_MAX_RESULTS)
    store = DataStore(base_path=storage_path or str(get_index_path()))

    idx = store.load(dataset)
    if idx is None:
        return {"error": f"NOT_INDEXED: dataset {dataset!r} is not indexed."}

    # ⚠⚠ Sampled HERE, before any channel runs, and never after.
    #
    # The probe compares `data.sqlite`'s mtime against the value stamped at
    # load. `_semantic_scores` below LAZILY EMBEDS any column with no vector
    # yet and PERSISTS it (`set_many`/`set_meta`) into that same file, so a
    # sample taken after the scan reports the dataset as rewritten underneath
    # us — about a write we performed ourselves.
    #
    # Measured before this moved: on the FIRST semantic search of a dataset a
    # zero-result query returned `degraded` ("absence is NOT proven"); the
    # SECOND identical query, with nothing left to embed, returned `absent`
    # ("strong evidence no such column/value is present"). Same query, same
    # data, opposite verdicts — and the wrong one is the one that fires on
    # every dataset's first search, downgrading the absence claim this product
    # exists to make.
    #
    # Sampling earlier does not weaken the signal. An mtime is a PROXY for
    # "rows were rewritten by someone else", and our own write is a known
    # false positive for it, so excluding it repairs the proxy rather than
    # relaxing the guard. A rebuild already in flight when the scan starts is
    # still caught; one that begins mid-scan is not, and `_meta.rewrite_probe`
    # says so rather than leaving it to be inferred.
    index_changed_at_start = index_changed_since_load(idx)

    w = load_effective_weights(dataset, storage_path)
    if semantic_weight is None:
        semantic_weight = w["default_semantic_weight"]
    semantic_weight = max(0.0, min(1.0, semantic_weight))

    # Semantic scoring (if requested)
    sem_scores: dict[str, float] = {}
    if semantic or semantic_only:
        try:
            sem_scores = _semantic_scores(query, idx.columns, store, dataset)
        except ValueError as exc:
            return {"error": "no_embedding_provider", "message": str(exc)}
        except Exception as exc:
            logger.warning("Semantic search failed: %s", exc)
            if semantic_only:
                return {"error": f"SEMANTIC_FAILED: {exc}"}
            # Fall back to keyword-only

    query_lower = query.lower().strip()
    query_words = set(query_lower.split())
    query_terms = tokenize(query_lower)

    # Pre-build BM25 corpus for the 'all' scope (B9)
    bm25_index: Optional[BM25] = None
    if not semantic_only and search_scope == "all" and idx.columns:
        bm25_index = BM25([_column_doc_tokens(c) for c in idx.columns])

    scored: list = []
    for col_idx, col in enumerate(idx.columns):
        bm25_score = 0.0
        mv: list = []
        mt = "schema"

        if not semantic_only:
            if search_scope == "schema":
                name_lower = col["name"].lower()
                if query_lower == name_lower:
                    bm25_score = w["name_exact"]
                elif query_lower in name_lower:
                    bm25_score = w["name_substr"]
                else:
                    name_words = set(name_lower.replace("_", " ").split())
                    bm25_score = len(query_words & name_words) * w["name_word"]
                if bm25_score > 0:
                    mt = "schema"
            elif search_scope == "values":
                value_source = []
                if col.get("value_index"):
                    value_source = list(col["value_index"].keys())
                elif col.get("top_values"):
                    value_source = [tv["value"] for tv in col["top_values"]]
                for v in value_source:
                    v_lower = str(v).lower()
                    for word in query_words:
                        if word == v_lower:
                            bm25_score += w["value_exact"]
                            mv.append(str(v))
                            break
                        elif len(word) >= 3 and word in v_lower:
                            bm25_score += w["value_substr"]
                            mv.append(str(v))
                            break
                if bm25_score > 0:
                    mt = "value"
            else:
                # 'all' scope: BM25 ranking over name + summary + values (B9),
                # combined with the legacy weighted score for value-match
                # provenance + type-aware boosts.
                legacy_score, mv, mt = _score_column(col, query_lower, query_words, w)
                bm25_raw = bm25_index.score(query_terms, col_idx) if bm25_index else 0.0
                # Scale BM25 into the legacy score range so blending stays sane.
                bm25_score = legacy_score + bm25_raw * w["bm25_scale"]
                if bm25_raw > 0 and legacy_score == 0:
                    mt = "schema"

        # Combine scores
        sem = sem_scores.get(col["name"], 0.0) if sem_scores else 0.0

        if semantic_only:
            combined = sem
            if sem > 0:
                mt = "semantic"
        elif sem_scores:
            # Normalize BM25 score to [0, 1] range for blending
            combined = (1 - semantic_weight) * bm25_score + semantic_weight * (sem * w["semantic_scale"])
            if bm25_score == 0 and sem > 0.3:
                mt = "semantic"
        else:
            combined = bm25_score

        if combined > 0:
            scored.append((combined, col, mv, mt))

    scored.sort(key=lambda x: x[0], reverse=True)

    results = []
    max_score = scored[0][0] if scored else 1
    for sc, col, mv, mt in scored[:max_results]:
        r: dict = {
            "id": f"{dataset}::{col['name']}#column",
            "name": col["name"],
            "type": col["type"],
            "cardinality": col["cardinality"],
            "null_pct": col["null_pct"],
            "match_type": mt,
            "score": round(sc / max_score, 2),
        }
        if mv:
            r["matched_values"] = mv[:10]
        if col.get("ai_summary"):
            r["ai_summary"] = col["ai_summary"]
        results.append(r)

    total_saved = get_total_saved(str(store.base_path))

    meta: dict = {
        "timing_ms": round((time.time() - t0) * 1000, 1),
        "tokens_saved": 0,
        "total_tokens_saved": total_saved,
    }
    if sem_scores:
        meta["semantic_enabled"] = True
    meta["rewrite_probe"] = (
        "sampled before the scan; a rebuild starting mid-scan is not "
        "visible, because the semantic channel writes to the same file"
    )

    # Suite-parity honesty verdict. degraded = semantic requested but the
    # embedding channel fell back to keyword-only; absent = zero matches.
    # Non-ok verdicts carry a coverage disclosure (1.20.0): what the ingest
    # pass excluded, so an absence claim can't lie by omission. Indexes that
    # predate the coverage contract yield no block (unknown, never fabricated).
    from ..verdict import build_coverage_disclosure, build_verdict, suggest_columns
    semantic_requested = bool(semantic or semantic_only)
    meta["verdict"] = build_verdict(
        result_count=len(results),
        semantic_requested=semantic_requested,
        semantic_available=bool(sem_scores),
        lexical_used=not semantic_only,
        index_changed=index_changed_at_start,
        did_you_mean=suggest_columns(query, idx.columns) if not results else None,
        coverage=build_coverage_disclosure(
            idx.coverage,
            indexed_at=idx.indexed_at,
            index_version=idx.index_version,
        ),
    )

    return {
        "result": results,
        "_meta": meta,
    }
