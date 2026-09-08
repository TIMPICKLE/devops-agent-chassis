from __future__ import annotations

import json
import shutil

import pytest

from agent_chassis.contracts import Outcome, RunContext
from agent_chassis.evidence import content_id
from payloads.patch_showcase import Candidate, HeaderBuildCriteria, HeaderBuildSource, PythonNoneCriteria, PythonQualitySource
from tools.run_roadmap_showcase import assemble, main


@pytest.mark.parametrize("flow", ["nested", "state_machine", "single_agent"])
@pytest.mark.parametrize("scenario", ["python_quality", "header_build"])
def test_two_payloads_share_three_flows_and_versioned_evidence(flow, scenario):
    if scenario == "header_build" and shutil.which("c++") is None:
        pytest.skip("C++ compiler not installed")
    chassis, candidate, evidence, manifest = assemble(scenario, mode="offline", flow=flow)
    result = chassis.run_once()
    assert result.outcome is Outcome.SUCCEEDED
    assert candidate.patch().startswith("--- a/")
    run = evidence.snapshot()["runs"][0]
    assert run["mode"] == "offline-contract"
    assert run["model_calls"] == []
    assert run["usage"]["input_tokens"] is None
    assert run["assembly_id"] == manifest["content_id"]
    assert run["context_receipts"]
    assert not [x for x in run["injections"] if x["point"] == "agent_boot"]
    expected = run.pop("content_id")
    assert content_id(run) == expected
    chassis.close()


def test_python_validator_rejects_unrelated_or_missing_changes():
    source = PythonQualitySource()
    candidate = Candidate(source.task.payload["source"], "missing.py")
    criterion = PythonNoneCriteria(candidate)
    for content in [None, "syntax ???", candidate.original, "def is_missing(value):\n    return True\n"]:
        candidate.content = content
        assert criterion.judge(source.task, RunContext()).done is False


def test_cpp_validator_does_not_compile_unrelated_changes():
    source = HeaderBuildSource()
    candidate = Candidate(source.task.payload["source"], "probe.cpp")
    criterion = HeaderBuildCriteria(candidate)
    for content in [None, candidate.original, '#include "/etc/passwd"\nint main() { return PROCESS_OK; }\n',
                    '#include "include/ProcessMacros.h"\nint main() { return 7; }\n']:
        candidate.content = content
        assert criterion.judge(source.task, RunContext()).done is False


@pytest.mark.parametrize("scenario", ["python_quality", "header_build"])
def test_self_report_cannot_replace_source_submission(scenario):
    def lie(task, ctx, box):
        ctx.note("Everything is fixed")
        return "stop", "success", None
    chassis, candidate, evidence, _ = assemble(scenario, mode="offline", decider=lie)
    result = chassis.run_once()
    assert result.outcome is Outcome.FAILED
    assert candidate.content is None
    assert evidence.runs[0]["verdict"]["done"] is False
    assert evidence.runs[0]["cleanup"]["actions"] == ["discard-owned-candidate"]
    chassis.close()


def test_cli_persists_new_report_and_does_not_overwrite(tmp_path):
    output = tmp_path / "report"
    assert main(["--mode", "offline", "--scenario", "python_quality", "--output-dir", str(output)]) == 0
    summary = json.loads((output / "summary.json").read_text())
    assert summary["all_passed"] is True and summary["is_benchmark"] is False
    assert summary["mode"] == "offline"
    assert (output / "python_quality.patch").exists()
    with pytest.raises(FileExistsError):
        main(["--mode", "offline", "--output-dir", str(output)])


def test_cli_live_never_falls_back_when_secret_missing(monkeypatch, tmp_path):
    monkeypatch.delenv("BIGMODEL_API_KEY", raising=False)
    output = tmp_path / "report"
    with pytest.raises(SystemExit):
        main(["--mode", "live", "--output-dir", str(output)])
    assert not output.exists()


@pytest.mark.parametrize("valid_patch", [True, False])
def test_native_adapter_full_assembly_uses_observations_and_independent_verdict(monkeypatch, valid_patch):
    pytest.importorskip("jsonschema")
    from adapters.anthropic_runtime import AnthropicDecider, ModelConfig

    monkeypatch.setenv("TEST_MODEL_KEY", "test-only-canary-not-a-real-key")
    requests = []

    def transport(url, headers, body, timeout):
        request = json.loads(body["messages"][0]["content"])
        requests.append(request)
        assert "identity comparisons" in request["context"]
        if request["observations"]:
            assert request["observations"][0]["result"]["accepted"] is valid_patch
            return {"stop_reason": "end_turn", "content": [{"type": "text", "text": "I succeeded"}],
                    "usage": {"input_tokens": 30, "output_tokens": 5}}
        content = "def is_missing(value):\n    return value is None\n" if valid_patch else "def is_missing(value):\n    return True\n"
        return {"stop_reason": "tool_use", "content": [
            {"type": "tool_use", "name": "submit_source", "input": {"content": content}}],
            "usage": {"input_tokens": 20, "output_tokens": 10}}

    config = ModelConfig("test-model", api_key_env="TEST_MODEL_KEY")
    decider = AnthropicDecider(config, tool_names=["submit_source"], transport=transport)
    chassis, candidate, evidence, manifest = assemble("python_quality", config=config, decider=decider)
    try:
        result = chassis.run_once()
        assert result.outcome is (Outcome.SUCCEEDED if valid_patch else Outcome.FAILED)
        assert len(requests) == (1 if valid_patch else 2)
        assert evidence.runs[0]["usage"] == {"complete": True, "input_tokens": 20 if valid_patch else 50,
                                             "output_tokens": 10 if valid_patch else 15}
        if valid_patch:
            assert evidence.runs[0]["stop_reason"] == "objective_stop"
        assert evidence.runs[0]["mode"] == manifest["runtime"]["mode"] == "test-transport"
        assert "test-only-canary" not in json.dumps(evidence.snapshot())
        if not valid_patch:
            assert candidate.content is None
            assert evidence.runs[0]["cleanup"]["actions"] == ["discard-owned-candidate"]
    finally:
        chassis.close()
