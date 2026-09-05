from __future__ import annotations

import json
from copy import deepcopy

import pytest

pytest.importorskip("jsonschema")

from agent_chassis.evidence import content_id
from tools.run_roadmap_showcase import assemble, main as showcase_main
from tools.verify_roadmap_evidence import main, verify_documents


@pytest.fixture
def documents():
    chassis, _, evidence, manifest = assemble("python_quality", mode="offline", code_ref="test-ref")
    try:
        chassis.run_once()
        return manifest, json.loads(json.dumps(evidence.snapshot()))
    finally:
        chassis.close()


def rehash(document):
    document["content_id"] = content_id({k: v for k, v in document.items() if k != "content_id"})


def test_versioned_evidence_matches_public_schema_and_has_no_prompt_body(documents):
    manifest, evidence = documents
    assert verify_documents(manifest, evidence) == 1
    assert "identity comparisons" not in json.dumps(evidence)
    assert "def is_missing" not in json.dumps(evidence)
    assert evidence["runs"][0]["code_ref"] == "test-ref"


def test_offline_evidence_cannot_be_claimed_as_live(documents):
    with pytest.raises(ValueError, match="Not live"):
        verify_documents(*documents, require_live=True)


def test_evidence_detects_content_id_mismatch(documents):
    manifest, evidence = documents
    evidence["runs"][0]["code_ref"] = "changed"
    with pytest.raises(ValueError, match="Content ID"):
        verify_documents(manifest, evidence)


@pytest.mark.parametrize("change,reason", [
    ({"assembly_id": "0" * 64}, "different assembly"),
    ({"verdict": None}, "accepting verdict"),
    ({"mode": "live"}, "modes disagree"),
    ({"usage": {"complete": True, "input_tokens": 0, "output_tokens": 0}}, "completeness"),
])
def test_semantic_contract_rejects_rehashed_inconsistencies(documents, change, reason):
    manifest, evidence = documents
    evidence["runs"][0].update(change)
    rehash(evidence["runs"][0])
    with pytest.raises(ValueError, match=reason):
        verify_documents(manifest, evidence)


def test_context_receipts_must_reference_recorded_content(documents):
    manifest, evidence = documents
    evidence["runs"][0]["context_receipts"][0]["included"] = ["0" * 64]
    rehash(evidence["runs"][0])
    with pytest.raises(ValueError, match="unrecorded injection"):
        verify_documents(manifest, evidence)


def test_unknown_schema_version_is_rejected_without_echoing_document(documents):
    manifest, evidence = documents
    evidence["schema_version"] = "test-private-marker"
    with pytest.raises(ValueError, match="v1 schema") as exc:
        verify_documents(manifest, evidence)
    assert "test-private-marker" not in str(exc.value)


def test_snapshot_is_not_a_mutable_reference():
    chassis, _, evidence, _ = assemble("python_quality", mode="offline")
    try:
        chassis.run_once()
        saved = deepcopy(evidence.runs)
        evidence.snapshot()["runs"][0]["steps"].append("changed")
        assert evidence.runs == saved
    finally:
        chassis.close()


def test_export_and_offline_verifier_cli(tmp_path):
    folder = tmp_path / "evidence"
    assert showcase_main(["--mode", "offline", "--scenario", "python_quality", "--output-dir", str(folder)]) == 0
    assert main([str(folder)]) == 0
    with pytest.raises(ValueError, match="Not live"):
        main([str(folder), "--require-live"])
