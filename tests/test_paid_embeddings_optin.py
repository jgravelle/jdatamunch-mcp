"""A bare cloud API key must never select a paid embedding provider.

Suite-parity check for a defect found in jdocmunch, NOT a port of its fix.

jdoc's embedding auto-detect selected OpenAI from an ambient `OPENAI_API_KEY`
alone, so its default `use_embeddings="auto"` began billing a remote account and
sending the indexed corpus off the machine. It needed a new opt-in gate.

⚠ jdatamunch does NOT have that defect. `detect_provider` already requires TWO
signals for a cloud provider: the API key AND an explicit `*_EMBED_MODEL`.
Naming the model is the opt-in. Adding jdoc's gate on top would be an
unreachable guard, which reads as protection and is not.

The stakes here are higher than in the sibling repos and that is why the check
is worth pinning: jdata indexes CSV/Excel and database content, so an
auto-enabled cloud embedder would ship customer rows to a third party, not just
source code or prose.

So what is ported is the DEFECT CHECK, not the remedy. If `detect_provider` is
ever "simplified" to key off the API key alone, this fails loudly.
"""

from __future__ import annotations

import pytest

from jdatamunch_mcp.embeddings import detect_provider


_ENV = (
    "GOOGLE_API_KEY", "GOOGLE_EMBED_MODEL",
    "OPENAI_API_KEY", "OPENAI_EMBED_MODEL",
    "JDATAMUNCH_EMBED_MODEL",
)


@pytest.fixture
def clean_env(monkeypatch):
    for var in _ENV:
        monkeypatch.delenv(var, raising=False)
    return monkeypatch


@pytest.mark.parametrize(
    "key_var,model_var,name",
    [
        ("OPENAI_API_KEY", "OPENAI_EMBED_MODEL", "openai"),
        ("GOOGLE_API_KEY", "GOOGLE_EMBED_MODEL", "gemini"),
    ],
)
def test_bare_cloud_key_alone_selects_nothing(clean_env, key_var, model_var, name):
    """The property jdoc lost. A key with no model must not reach a cloud provider."""
    clean_env.setenv(key_var, "not-a-real-key")
    assert detect_provider() is None, (
        f"a bare {key_var} selected {name}: for jdata that means customer rows "
        f"leaving the machine. {model_var} is the opt-in and must stay required."
    )


@pytest.mark.parametrize(
    "key_var,model_var,name,model",
    [
        ("OPENAI_API_KEY", "OPENAI_EMBED_MODEL", "openai", "text-embedding-3-small"),
        ("GOOGLE_API_KEY", "GOOGLE_EMBED_MODEL", "gemini", "text-embedding-004"),
    ],
)
def test_naming_the_model_opts_in(clean_env, key_var, model_var, name, model):
    """Non-vacuity for the test above: the provider is gated, not broken."""
    clean_env.setenv(key_var, "not-a-real-key")
    clean_env.setenv(model_var, model)
    assert detect_provider() == (name, model)


def test_local_sentence_transformers_needs_no_key(clean_env):
    """The offline option is selected by naming a model and never touches a
    network, so it is deliberately not gated."""
    clean_env.setenv("JDATAMUNCH_EMBED_MODEL", "all-MiniLM-L6-v2")
    assert detect_provider() == ("sentence_transformers", "all-MiniLM-L6-v2")
