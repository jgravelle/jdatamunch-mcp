"""check_embedding_drift (v1.15.0) -- canary-based embedding drift detection.

Parity: jcm and jdoc both ship check_embedding_drift over their embeddings;
jData has embed_dataset + an embedding store but had no drift guard. The
provider/embedder are monkeypatched so these tests need no real model.
"""

import hashlib

import pytest

from jdatamunch_mcp import embed_drift
from jdatamunch_mcp.tools.check_embedding_drift import check_embedding_drift


def _vec(text: str) -> list[float]:
    h = hashlib.sha256(text.encode("utf-8")).digest()
    return [b / 255.0 for b in h[:8]]


def _make_embed(salt: str = ""):
    def _embed(strings, provider, model):
        return [_vec(s + salt) for s in strings]
    return _embed


@pytest.fixture
def fake_provider(monkeypatch):
    monkeypatch.setattr(embed_drift, "_resolve_provider", lambda: ("sentence_transformers", "fake-model"))
    monkeypatch.setattr(embed_drift, "_embed", _make_embed())


def test_canary_contract_stable():
    assert len(embed_drift.CANARY_STRINGS) == 16
    # Order is part of the contract.
    assert embed_drift.CANARY_STRINGS[0] == "customer email address"
    assert len(set(embed_drift.CANARY_STRINGS)) == 16


def test_no_canary_returns_hint(tmp_path, fake_provider):
    out = check_embedding_drift(storage_path=str(tmp_path))
    assert out["has_canary"] is False
    assert "hint" in out


def test_force_captures_baseline(tmp_path, fake_provider):
    out = check_embedding_drift(force=True, storage_path=str(tmp_path))
    assert out["captured"] is True
    assert out["n_canaries"] == 16
    assert out["provider"] == "sentence_transformers"
    assert (tmp_path / "embed_canary.json").exists()


def test_recapture_is_idempotent_without_force(tmp_path, fake_provider):
    check_embedding_drift(force=True, storage_path=str(tmp_path))
    from jdatamunch_mcp.embed_drift import capture_canary
    again = capture_canary(str(tmp_path))
    assert again["captured"] is False
    assert again["reason"] == "canary_already_exists"


def test_stable_provider_no_drift(tmp_path, fake_provider):
    check_embedding_drift(force=True, storage_path=str(tmp_path))
    res = check_embedding_drift(storage_path=str(tmp_path))
    assert res["has_canary"] is True
    assert res["alarm"] is False
    assert res["max_drift"] < 1e-6
    assert len(res["per_canary"]) == 16


def test_provider_change_alarms(tmp_path, monkeypatch):
    monkeypatch.setattr(embed_drift, "_resolve_provider", lambda: ("sentence_transformers", "v1"))
    monkeypatch.setattr(embed_drift, "_embed", _make_embed())
    check_embedding_drift(force=True, storage_path=str(tmp_path))
    # Provider "changes" -- the encoder now produces different vectors.
    monkeypatch.setattr(embed_drift, "_embed", _make_embed(salt="__v2__"))
    res = check_embedding_drift(storage_path=str(tmp_path))
    assert res["alarm"] is True
    assert res["max_drift"] > 0.05


def test_no_provider_configured(tmp_path, monkeypatch):
    monkeypatch.setattr(embed_drift, "_resolve_provider", lambda: None)
    cap = check_embedding_drift(force=True, storage_path=str(tmp_path))
    assert cap["captured"] is False
    assert "error" in cap
    chk = check_embedding_drift(storage_path=str(tmp_path))
    assert chk["has_canary"] is False


def test_meta_timing_present(tmp_path, monkeypatch):
    monkeypatch.setattr(embed_drift, "_resolve_provider", lambda: None)
    out = check_embedding_drift(storage_path=str(tmp_path))
    assert "_meta" in out and "timing_ms" in out["_meta"]


@pytest.mark.asyncio
async def test_registered_and_dispatches(tmp_path, monkeypatch):
    from jdatamunch_mcp.server import call_tool, list_tools
    import json

    names = [t.name for t in await list_tools()]
    assert "check_embedding_drift" in names

    # call_tool takes the storage root from DATA_INDEX_PATH, not from the
    # arguments dict -- passing storage_path here reads the real home index.
    monkeypatch.setenv("DATA_INDEX_PATH", str(tmp_path))
    monkeypatch.setattr(embed_drift, "_resolve_provider", lambda: None)
    res = await call_tool("check_embedding_drift", {})
    payload = json.loads(res[0].text)
    assert payload["has_canary"] is False
