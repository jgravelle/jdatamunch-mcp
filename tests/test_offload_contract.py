"""The offloadable-annotation SUITE CONTRACT. Identical in all three munches.

⚠ GENERATED FILE — do not hand-edit. Source of truth:
``C:\\MCPs\\_private\\offload_contract_test_template.py``, written by
``C:\\MCPs\\_private\\sync_offload_contract.py``. Edit the template, re-run the
sync, re-run all three suites.

Suite parity for this feature is a CONTRACT obligation: jCodeMunch, jDocMunch
and jDataMunch carry the identical annotation contract or none of them do. The
three servers share no package — each is an independent PyPI product importing
nothing from the others — so the module is COPIED, and copies drift. That is
not hypothetical: jcm v1.108.240 shipped a fix for three renderings of one
expression that had fallen out of step, two of which silently over-claimed.

`CONTRACT_DIGEST` is the mitigation available without a shared dependency. It
covers every part of the contract a consumer can observe, and it is pinned as
the same literal in all three repositories. A unilateral change to any one of
them fails THAT repository's copy of this test, which forces the change to be
made in all three deliberately.
"""

from __future__ import annotations

import pytest

from jdatamunch_mcp import offload


# --- the pinned contract --------------------------------------------------


def test_the_contract_digest_matches_the_pin():
    """⚠ If this fails you have changed the SUITE contract, not just this repo.

    Recompute, then update the literal in ALL THREE repositories in one go:
    jcodemunch-mcp, jdocmunch-mcp, jdatamunch-mcp. A digest that differs across
    the suite means a client cannot rely on one contract.
    """
    assert offload.contract_digest() == offload.CONTRACT_DIGEST


def test_the_pinned_digest_is_the_agreed_suite_value():
    """The literal itself, so a repo cannot silently re-pin to its own drift.

    Recomputing and re-pinning in one repo would make the test above pass while
    the suite disagrees. This asserts the AGREED value.
    """
    assert offload.CONTRACT_DIGEST == "3d4cf47e24fac358"


def test_the_three_verdict_states():
    assert offload.OFFLOADABLE == "offloadable"
    assert offload.NOT_OFFLOADABLE == "not_offloadable"
    assert offload.NOT_EVALUATED == "not_evaluated"
    assert len({offload.OFFLOADABLE, offload.NOT_OFFLOADABLE, offload.NOT_EVALUATED}) == 3


def test_the_reason_code_vocabulary_is_exact():
    surface = offload.contract_surface()
    assert surface["qualifiers"] == [
        "EXTRACTIVE",
        "IDENTITY_LOOKUP",
        "NO_ABSENCE_FLAGS",
        "SELF_CONTAINED",
        "SINGLE_CONTAINER",
        "WIDE_RANK_MARGIN",
    ]
    assert surface["disqualifiers"] == [
        "ABSENCE_CLAIM",
        "CROSS_CONTAINER_SYNTHESIS",
        "DEGRADED_VERDICT",
        "GRAPH_TRAVERSAL",
        "INCOMPLETE_COVERAGE",
        "NARROW_RANK_MARGIN",
        "NO_BODIES",
        "STALE_INDEX",
        "TRI_STATE_UNKNOWN",
        "TRUNCATED",
    ]


def test_the_vocabulary_is_domain_neutral():
    """The reason a section/column server can carry a code invented for symbols.

    A code naming one product's domain would have to be re-cut for the others,
    and under a 1.x no-removal contract we would live with both forever.
    """
    codes = [
        v
        for k, v in vars(offload).items()
        if (k.startswith("D_") or k.startswith("Q_")) and isinstance(v, str)
    ]
    assert codes
    for code in codes:
        for word in ("SYMBOL", "FILE", "SECTION", "DOCUMENT", "COLUMN", "DATASET", "CODE"):
            assert word not in code, f"{code} is domain-specific: contains {word}"


def test_the_shape_field_names_are_domain_neutral():
    fields = set(offload.EvidenceShape.__dataclass_fields__)
    assert "unit_count" in fields and "container_count" in fields
    for banned in ("symbol_count", "file_count", "section_count", "column_count"):
        assert banned not in fields


def test_the_thresholds_are_pinned():
    assert offload.MIN_RANK_MARGIN == 0.25
    assert offload.MAX_CONTAINERS == 2
    assert offload.CRITERION_VERSION == 2


# --- the gate -------------------------------------------------------------


def test_the_suite_gate_name_is_shared():
    """One switch expresses one intention across the suite.

    Modelmaxxing is a decision a client makes once, about a session. Requiring
    three variables for it would be a contract defect.
    """
    assert offload.ENV_GATE_SUITE == "JMUNCH_OFFLOADABLE"


def test_the_annotation_is_off_by_default():
    assert offload.is_enabled({}) is False


def test_the_suite_gate_switches_this_server_on():
    assert offload.is_enabled({"JMUNCH_OFFLOADABLE": "1"}) is True


def test_the_product_gate_can_switch_this_server_off_alone():
    """Narrower scope beats broader: the only precedence that lets an operator
    disable one server while the suite stays on."""
    env = {"JMUNCH_OFFLOADABLE": "1", offload.ENV_GATE: "0"}
    assert offload.is_enabled(env) is False


def test_the_product_gate_works_without_the_suite_gate():
    assert offload.is_enabled({offload.ENV_GATE: "1"}) is True


@pytest.mark.parametrize("val", ["", "maybe", "2", "on-ish"])
def test_an_unrecognised_gate_value_fails_closed(val):
    assert offload.is_enabled({offload.ENV_GATE: val}) is False


# --- the three properties of annotate() -----------------------------------


def _clean_payload():
    return {
        "unit": {"body": "text", "container": "c1"},
        "_meta": {"verdict": {"state": "ok", "channels": {"index": "fresh"}}},
    }


def test_annotate_adds_nothing_when_the_gate_is_off(monkeypatch):
    monkeypatch.delenv("JMUNCH_OFFLOADABLE", raising=False)
    monkeypatch.delenv(offload.ENV_GATE, raising=False)
    r = _clean_payload()
    offload.annotate(r, units=[r["unit"]], retrieval_mode=offload.MODE_IDENTITY,
                     body_field="body", container_field="container")
    assert "offloadable" not in r["_meta"]


def test_annotate_never_mutates_the_answer(monkeypatch):
    """It is an advisory note ON an answer, never a rewrite of one."""
    monkeypatch.setenv("JMUNCH_OFFLOADABLE", "1")
    r = _clean_payload()
    before_unit = dict(r["unit"])
    before_verdict = dict(r["_meta"]["verdict"])
    offload.annotate(r, units=[r["unit"]], retrieval_mode=offload.MODE_IDENTITY,
                     body_field="body", container_field="container")
    assert r["unit"] == before_unit
    assert r["_meta"]["verdict"] == before_verdict
    assert set(r) == {"unit", "_meta"}


def test_annotate_never_raises(monkeypatch):
    """A classifier defect must not fail a retrieval that otherwise worked."""
    monkeypatch.setenv("JMUNCH_OFFLOADABLE", "1")
    for bad in ({"_meta": "not-a-dict"}, {"unit": object()}, {}, {"_meta": {}}):
        offload.annotate(bad, retrieval_mode=offload.MODE_IDENTITY)


def test_not_evaluated_is_sayable(monkeypatch):
    """With the gate ON, silence is ambiguous with the gate being OFF."""
    monkeypatch.setenv("JMUNCH_OFFLOADABLE", "1")
    r = {"error": "x", "_meta": {}}
    offload.not_evaluated(r, reason="no evidence")
    assert r["_meta"]["offloadable"]["offloadable"] == offload.NOT_EVALUATED
    assert r["_meta"]["offloadable"]["reason"] == "no evidence"


# --- fail-closed behaviour, the reason the label is trustworthy -----------


def test_unknown_freshness_disqualifies():
    shape = offload.EvidenceShape(
        unit_count=1, container_count=1, bodies_present=True, truncated=False,
        retrieval_mode=offload.MODE_IDENTITY, freshness=None, verdict_state="ok",
    )
    v = offload.classify(shape)
    assert v["offloadable"] == offload.NOT_OFFLOADABLE
    assert offload.D_TRI_STATE_UNKNOWN in v["disqualifiers"]


def test_a_clean_identity_payload_is_offloadable():
    """The positive control. Without it the tests above pass on a classifier
    that refuses everything, which is as useless as one that accepts everything."""
    shape = offload.EvidenceShape(
        unit_count=1, container_count=1, bodies_present=True, truncated=False,
        retrieval_mode=offload.MODE_IDENTITY, freshness="fresh", verdict_state="ok",
    )
    assert offload.classify(shape)["offloadable"] == offload.OFFLOADABLE


def test_no_payload_is_not_evaluated_rather_than_negative():
    v = offload.classify(offload.EvidenceShape())
    assert v["offloadable"] == offload.NOT_EVALUATED
    assert v["disqualifiers"] == []
