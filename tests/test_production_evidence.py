import json
from pathlib import Path
import runpy
import subprocess

import pytest

pytest.importorskip("jsonschema")

from agent_chassis.contracts import RunContext
from agent_chassis.evidence import EvidenceObserver, content_id
from agent_chassis.production import source_revision
from tools.verify_production_evidence import main
from tools.verify_roadmap_evidence import verify_documents


def rehash(document):
    document["content_id"] = content_id({k: v for k, v in document.items() if k != "content_id"})


@pytest.fixture
def reports():
    example = Path(__file__).resolve().parents[1] / "examples/07_verified_assembly.py"
    run = runpy.run_path(str(example))["run_example"]
    _, manifest, evidence = run(source={"commit": "b" * 40, "dirty": False})
    return manifest, json.loads(json.dumps(evidence))


def test_actual_parallelism_counts_match_actions_not_nested_events(reports):
    manifest, evidence = reports
    run = evidence["runs"][0]
    batch = run["execution"]["batches"][0]
    assert {k: batch[k] for k in ("requested", "started", "succeeded", "failed", "peak_in_flight")} == {
        "requested": 2, "started": 2, "succeeded": 2, "failed": 0, "peak_in_flight": 2}
    assert run["source"] == manifest["runtime"]["source"]
    assert verify_documents(manifest, evidence, require_production=True) == 1


@pytest.mark.parametrize("field", ["source", "executors", "required_checks"])
def test_production_binding_rejects_missing_metadata_before_run(reports, field):
    manifest, _ = reports
    manifest["runtime"].pop(field)
    rehash(manifest)
    with pytest.raises(ValueError):
        EvidenceObserver().bind_manifest(manifest, production=True)


@pytest.mark.parametrize("change", ["missing-assembly", "wrong-revision", "dirty-mismatch", "limits", "missing-check", "stale-check", "empty-proof"])
def test_production_rejects_incomplete_or_inconsistent_run_even_after_rehash(reports, change):
    manifest, evidence = reports
    run = evidence["runs"][0]
    if change == "missing-assembly":
        run["assembly_id"] = ""
    elif change == "wrong-revision":
        run["code_ref"] = "unverifiable-label"
    elif change == "dirty-mismatch":
        run["source"]["dirty"] = True
    elif change == "limits":
        run["execution"]["runs"][0]["limits"]["max_tool_calls"] += 1
    elif change == "missing-check":
        run.pop("checks")
    elif change == "stale-check":
        run["attempt"] = 2
    else:
        run["checks"][-1]["evidence_refs"] = {}
    rehash(run)
    with pytest.raises(ValueError):
        verify_documents(manifest, evidence, require_production=True)


@pytest.mark.parametrize("status", ["failed", "not_run"])
def test_success_requires_current_attempt_required_checks_passed(reports, status):
    manifest, evidence = reports
    run = evidence["runs"][0]
    run["checks"][-1]["status"] = status
    rehash(run)
    with pytest.raises(ValueError, match="failed or not_run"):
        verify_documents(manifest, evidence, require_production=True)
    # A failed task can truthfully report a failed or unexecuted check.
    run["outcome"] = "failed"
    run["verdict"]["done"] = False
    rehash(run)
    assert verify_documents(manifest, evidence, require_production=True) == 1


@pytest.mark.parametrize("field,value", [("started", 1), ("failed", 1), ("succeeded", 1),
    ("requested", 3), ("peak_in_flight", 3), ("peak_in_flight", 0)])
def test_rehashed_batch_statistics_must_match_trace_and_limits(reports, field, value):
    manifest, evidence = reports
    run = evidence["runs"][0]
    run["execution"]["batches"][0][field] = value
    rehash(run)
    with pytest.raises(ValueError):
        verify_documents(manifest, evidence)


def test_parallel_flag_cannot_be_inferred_from_config_alone(reports):
    manifest, evidence = reports
    run = evidence["runs"][0]
    run["execution"]["parallel_observed"] = False
    rehash(run)
    with pytest.raises(ValueError, match="parallelism"):
        verify_documents(manifest, evidence)


def test_basic_v1_compatibility_and_strict_opt_in(reports):
    manifest, evidence = reports
    run = evidence["runs"][0]
    for field in ("execution", "checks", "source", "attempt"):
        run.pop(field)
    rehash(run)
    assert verify_documents(manifest, evidence) == 1
    with pytest.raises(ValueError):
        verify_documents(manifest, evidence, require_production=True)


def test_production_cli_is_payload_independent(tmp_path, reports):
    manifest, evidence = reports
    m, e = tmp_path / "assembly.json", tmp_path / "evidence.json"
    m.write_text(json.dumps(manifest), encoding="utf-8")
    e.write_text(json.dumps(evidence), encoding="utf-8")
    assert main([str(m), str(e)]) == 0  # No C++ source or patch requirement.
    with pytest.raises(ValueError, match="Not live"):
        main([str(m), str(e), "--require-live"])


def test_check_receipts_keep_attempt_and_snapshot_isolation():
    ctx = RunContext()
    refs = {"digest": "a" * 64}
    ctx.record_check("check", "passed", evidence_refs=refs)
    refs["digest"] = "changed"
    ctx.attempt = 2
    ctx.record_check("check", "not_run")
    assert [c["attempt"] for c in ctx.verification_checks] == [1, 2]
    assert ctx.verification_checks[0]["evidence_refs"]["digest"] == "a" * 64
    with pytest.raises(ValueError):
        ctx.record_check("check", "maybe")


@pytest.mark.parametrize("invalid", ["missing-attempt", "future-check"])
def test_attempt_metadata_must_be_consistent(reports, invalid):
    manifest, evidence = reports
    run = evidence["runs"][0]
    if invalid == "missing-attempt":
        run.pop("attempt")
    else:
        run["checks"].append({"name": "future", "status": "not_run", "attempt": 2, "evidence_refs": {}})
    rehash(run)
    with pytest.raises(ValueError, match="attempt"):
        verify_documents(manifest, evidence, require_production=True)


@pytest.mark.parametrize("recheck", [False, True])
def test_real_chassis_retry_requires_fresh_check_receipt(recheck):
    from agent_chassis import Chassis, Outcome
    from agent_chassis.evidence import assembly_manifest
    from agent_chassis.failure import RetryThenGiveUpPolicy
    from agent_chassis.orchestration import ReActPattern, SingleAgentOrchestrator, ToolBox

    example = runpy.run_path(str(Path(__file__).resolve().parents[1] / "examples/07_verified_assembly.py"))

    def decide(task, ctx, box):
        ctx.record_check("values_match", "not_run")
        if ctx.attempt == 1 or recheck:
            ctx.record_check("values_match", "passed", evidence_refs={"digest": "d" * 64})
        if ctx.attempt == 1:
            raise RuntimeError("synthetic failure after first check")
        ctx.facts["independently_verified"] = True
        return "stop", "done", None

    pattern = ReActPattern(decide)
    observer = EvidenceObserver(mode="test-decider")
    chassis = (Chassis().with_payload(example["Source"](), example["Criteria"]()).observe(observer)
               .with_failure_policy(RetryThenGiveUpPolicy(max_retries=1))
               .with_orchestrator(SingleAgentOrchestrator(ToolBox(), pattern)).build())
    manifest = assembly_manifest(chassis.report(), runtime={"mode": "test-decider",
        "source": {"commit": "b" * 40, "dirty": False}, "executors": {"react": pattern.execution_limits()},
        "required_checks": ["values_match"]})
    observer.bind_manifest(manifest, production=True)
    try:
        assert chassis.run_once().outcome is Outcome.SUCCEEDED
        evidence = json.loads(json.dumps(observer.snapshot()))
        run = evidence["runs"][0]
        assert run["attempt"] == 2
        assert [entry["attempt"] for entry in run["execution"]["runs"]] == [1, 2]
        if recheck:
            assert verify_documents(manifest, evidence, require_production=True) == 1
        else:
            with pytest.raises(ValueError, match="failed or not_run"):
                verify_documents(manifest, evidence, require_production=True)
    finally:
        chassis.close()


def test_source_snapshot_tracks_dirty_contents_and_is_root_relative(tmp_path):
    def git(*args):
        return subprocess.check_output(["git", "-C", str(tmp_path), *args], stderr=subprocess.DEVNULL)
    git("init")
    (tmp_path / "source.txt").write_text("baseline", encoding="utf-8")
    git("add", "source.txt")
    git("-c", "user.name=Test", "-c", "user.email=test@example.invalid", "commit", "-m", "fixture")
    clean = source_revision(tmp_path)
    assert clean["dirty"] is False and clean["commit"] == git("rev-parse", "HEAD").decode().strip()
    (tmp_path / "source.txt").write_text("change-one", encoding="utf-8")
    dirty = source_revision(tmp_path)
    assert dirty["dirty"] is True and len(dirty["changes_id"]) == 64
    (tmp_path / "source.txt").write_text("change-two", encoding="utf-8")
    assert dirty["changes_id"] != source_revision(tmp_path)["changes_id"]
    sub = tmp_path / "nested"
    sub.mkdir()
    (sub / "untracked.txt").write_text("private-canary", encoding="utf-8")
    current = source_revision(tmp_path)
    assert source_revision(sub) == current
    assert "private-canary" not in json.dumps(current)
    (sub / "untracked.txt").write_text("new-canary", encoding="utf-8")
    assert source_revision(tmp_path)["changes_id"] != current["changes_id"]


def test_dirty_source_without_fingerprint_rejected(reports):
    manifest, _ = reports
    manifest["runtime"]["source"]["dirty"] = True
    rehash(manifest)
    with pytest.raises(ValueError, match="fingerprint"):
        EvidenceObserver().bind_manifest(manifest, production=True)
