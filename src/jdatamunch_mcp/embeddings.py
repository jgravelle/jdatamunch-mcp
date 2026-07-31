"""Embedding provider detection and text embedding.

Supports three providers (first configured wins):
1. sentence-transformers (local, free) — JDATAMUNCH_EMBED_MODEL env var
2. Gemini — GOOGLE_API_KEY + GOOGLE_EMBED_MODEL
3. OpenAI — OPENAI_API_KEY + OPENAI_EMBED_MODEL

All imports are lazy — no mandatory dependencies.
"""

import logging
import math
import os
from typing import Optional

logger = logging.getLogger(__name__)


# ── Provider detection ──────────────────────────────────────────────────────


def detect_provider() -> Optional[tuple[str, str]]:
    """Return (provider_name, model_name) or None when nothing is configured."""
    st_model = os.environ.get("JDATAMUNCH_EMBED_MODEL", "").strip()
    if st_model:
        return ("sentence_transformers", st_model)

    google_key = os.environ.get("GOOGLE_API_KEY", "").strip()
    google_model = os.environ.get("GOOGLE_EMBED_MODEL", "").strip()
    if google_key and google_model:
        return ("gemini", google_model)

    openai_key = os.environ.get("OPENAI_API_KEY", "").strip()
    openai_model = os.environ.get("OPENAI_EMBED_MODEL", "").strip()
    if openai_key and openai_model:
        return ("openai", openai_model)

    return None


# ── Eager backend warm-up (Windows loader-lock guard) ────────────────────


def warm_up_provider(provider: Optional[str] = None) -> bool:
    """Import the active provider's native backend on the *calling* thread.

    sentence-transformers pulls in torch and its native DLLs (torch_cpu.dll,
    MKL/oneDNN, ...). On Windows, loading those from an `asyncio.to_thread`
    worker while the main thread has a pending stdio pipe read deadlocks on the
    loader lock: the first `embed_dataset` / `check_embedding_drift` call never
    returns (issue #3). Doing that first import up front, on the main thread
    before the stdio loop starts servicing requests, sidesteps it.

    Costs a few seconds of startup, so it only runs when a local
    sentence-transformers model is actually configured. The network-backed
    providers load no native code and are left lazy. Set
    `JDATAMUNCH_EAGER_EMBED_IMPORT=0` to opt out.

    Returns True when the backend was imported. Never raises — a missing or
    broken install must not stop the server from starting.
    """
    if os.environ.get("JDATAMUNCH_EAGER_EMBED_IMPORT", "").strip() == "0":
        return False

    if provider is None:
        detected = detect_provider()
        if detected is None:
            return False
        provider = detected[0]

    if provider != "sentence_transformers":
        return False

    try:
        import sentence_transformers  # noqa: F401
    except Exception as exc:  # pragma: no cover - depends on local install
        logger.debug("sentence-transformers warm-up skipped: %s", exc)
        return False
    return True


# ── Per-provider embedding functions (all lazy-imported) ─────────────────


def _embed_sentence_transformers(texts: list[str], model_name: str) -> list[list[float]]:
    try:
        from sentence_transformers import SentenceTransformer  # type: ignore[import]
    except ImportError as exc:
        raise ImportError(
            "sentence-transformers is not installed. "
            "Run: pip install 'jdatamunch-mcp[semantic]'"
        ) from exc
    model = SentenceTransformer(model_name)
    raw = model.encode(texts, convert_to_numpy=False, show_progress_bar=False)
    return [list(map(float, e)) for e in raw]


def _embed_gemini(texts: list[str], model_name: str) -> list[list[float]]:
    try:
        import google.generativeai as genai  # type: ignore[import]
    except ImportError as exc:
        raise ImportError(
            "google-generativeai is not installed. "
            "Run: pip install 'jdatamunch-mcp[gemini]'"
        ) from exc
    api_key = os.environ.get("GOOGLE_API_KEY", "")
    genai.configure(api_key=api_key)
    results = []
    for text in texts:
        resp = genai.embed_content(model=model_name, content=text)
        results.append(list(map(float, resp["embedding"])))
    return results


def _embed_openai(texts: list[str], model_name: str) -> list[list[float]]:
    try:
        from openai import OpenAI  # type: ignore[import]
    except ImportError as exc:
        raise ImportError(
            "openai package is not installed. Run: pip install openai"
        ) from exc
    client = OpenAI(api_key=os.environ.get("OPENAI_API_KEY", ""))
    response = client.embeddings.create(model=model_name, input=texts)
    return [list(map(float, item.embedding)) for item in response.data]


def embed_texts(texts: list[str], provider: str, model: str) -> list[list[float]]:
    """Embed a list of texts using the named provider."""
    if provider == "sentence_transformers":
        return _embed_sentence_transformers(texts, model)
    if provider == "gemini":
        return _embed_gemini(texts, model)
    if provider == "openai":
        return _embed_openai(texts, model)
    raise ValueError(f"Unknown embedding provider: {provider!r}")


# ── Cosine similarity (pure Python, no numpy) ───────────────────────────


def cosine_similarity(a: list[float], b: list[float]) -> float:
    """Compute cosine similarity between two vectors."""
    dot = sum(x * y for x, y in zip(a, b))
    norm_a = math.sqrt(sum(x * x for x in a))
    norm_b = math.sqrt(sum(x * x for x in b))
    if norm_a == 0.0 or norm_b == 0.0:
        return 0.0
    return dot / (norm_a * norm_b)
