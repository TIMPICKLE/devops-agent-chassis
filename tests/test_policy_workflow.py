import json
from copy import deepcopy

import pytest

pytest.importorskip("jsonschema")

from adapters.anthropic_runtime import AnthropicDecider
from adapters.runtime import ModelConfig
from agent_chassis.contracts import RunContext
from agent_chassis.evidence import content_id
from payloads.config_policy import ConfigCriteria, ConfigSource, expected_config, strict_config
from payloads.patch_showcase import Candidate
from tools import run_policy_workflow as runner
from tools.verify_policy_workflow import verify


def snapshot():
    return json.loads(runner.DATA.read_text())


@pytest.mark.parametrize("index,replicas", [(0, 5), (1, 2), (2, 12), (3, 1)])
def test_policy_arithmetic_and_unchanged_fields(index, replicas):
    data = snapshot()
    task = ConfigSource(data["cases"][index]).task
    expected = expected_config(data, task)
    assert expected["replicas"] == replicas
    candidate = Candidate(task.payload["source"], "deployment.json", json.dumps(expected))
    criteria = ConfigCriteria(candidate, data)
    assert criteria.validate(task).done
    assert not criteria.judge(task, RunContext()).done  # Verification node cannot be skipped.
    for field, value in [("replicas", True), ("image", "changed"), ("port", 8080), ("extra", 1)]:
        modified = {**expected, field: value}
        candidate.content = json.dumps(modified)
        assert not criteria.validate(task).done
    with pytest.raises(ValueError):
        strict_config('{"replicas": 1, "replicas": 2}')
    ambiguous = deepcopy(data)
    ambiguous["documents"].append(ambiguous["documents"][index // 2])
    with pytest.raises(ValueError, match="Ambiguous"):
        expected_config(ambiguous, task)


def test_live_request_only_changes_context_and_does_not_leak_oracle(monkeypatch):
    monkeypatch.setenv("POLICY_TEST_KEY", "test-only")
    data, requests = snapshot(), {}
    config = ModelConfig("test", api_key_env="POLICY_TEST_KEY", max_calls=1)
    task = ConfigSource(data["cases"][0]).task
    for policy in runner.POLICIES:
        def transport(url, headers, body, timeout):
            requests[policy] = json.loads(body["messages"][-1]["content"])
            return {"stop_reason": "tool_use", "content": [{"type": "tool_use", "name": "submit_source",
                    "input": {"content": json.dumps(expected_config(data, task))}}],
                    "usage": {"input_tokens": 20, "output_tokens": 10}}
        decider = AnthropicDecider(config, tool_names=["submit_source"], transport=transport)
        chassis, _, evidence, _ = runner.assemble(data["cases"][0], data, mode="live", config=config,
                                                context_policy=policy, decider=decider)
        try:
            assert chassis.run_once().outcome.value == "succeeded"
            assert evidence.runs[0]["steps"] == runner.NODES
            assert len(evidence.runs[0]["model_calls"]) == 1
        finally:
            chassis.close()
    assert requests["none"]["context"] == ""
    assert "8127" not in json.dumps(requests["none"])
    assert "8127" in requests["routed"]["context"]
    assert "9136" not in requests["routed"]["context"]
    assert "9136" in requests["full"]["context"]
    for policy in ("full", "none"):
        assert requests[policy]["task"] == requests["routed"]["task"]
        assert requests[policy]["observations"] == requests["routed"]["observations"] == []


def test_rejected_candidate_can_be_corrected_without_answer_in_feedback(monkeypatch):
    monkeypatch.setenv("POLICY_TEST_KEY", "test-only")
    data, requests = snapshot(), []
    task = ConfigSource(data["cases"][0]).task
    config = ModelConfig("test", api_key_env="POLICY_TEST_KEY", max_calls=2)
    def transport(url, headers, body, timeout):
        requests.append(json.loads(body["messages"][-1]["content"]))
        candidate = task.payload["source"] if len(requests) == 1 else json.dumps(expected_config(data, task))
        return {"stop_reason": "tool_use", "content": [{"type": "tool_use", "name": "submit_source",
                "input": {"content": candidate}}], "usage": {"input_tokens": 20, "output_tokens": 10}}
    decider = AnthropicDecider(config, tool_names=["submit_source"], transport=transport)
    chassis, _, evidence, _ = runner.assemble(data["cases"][0], data, mode="live", config=config,
                                            context_policy="routed", decider=decider)
    try:
        assert chassis.run_once().outcome.value == "succeeded"
        assert len(requests) == 2
        assert "8127" not in json.dumps(requests[1]["observations"])
        assert evidence.runs[0]["stop_reason"] == "objective_stop"
    finally:
        chassis.close()


@pytest.mark.parametrize("tamper", [None, "summary", "candidate", "context", "pending"])
def test_frozen_report_and_independent_verification(tmp_path, tamper):
    output = tmp_path / "report"
    assert runner.main(["--output-dir", str(output), "--repeats", "1", "--max-calls", "2"]) == 0
    result = verify(output)
    assert result["completed"] == 12
    assert all(g["usage"]["input_tokens"] is None for g in result["groups"])
    with pytest.raises(ValueError, match="Not a live"):
        verify(output, require_live=True)
    if tamper is None:
        return
    if tamper == "candidate":
        (output / "trial-001.candidate.json").write_text('{}')
    elif tamper == "context":
        path = output / "trial-001.evidence.json"
        evidence = json.loads(path.read_text())
        run = evidence["runs"][0]
        run["context_receipts"][0]["chars"] += 1
        run["content_id"] = content_id({k: v for k, v in run.items() if k != "content_id"})
        path.write_text(json.dumps(evidence))
    else:
        if tamper == "summary":
            result["groups"][0]["accepted"] = 0
        else:
            result["results"].pop()
        (output / "summary.json").write_text(json.dumps(result))
    with pytest.raises(ValueError):
        verify(output)


def test_failed_baseline_remains_in_denominator():
    plan = {"content_id": "test", "mode": "live", "trials": runner.trial_order(snapshot(), 1, 42)}
    failed = {**plan["trials"][0], "outcome": "failed", "model_calls": 2, "context_chars": 100,
              "failure_kind": "not_accepted",
              "usage": {"complete": True, "input_tokens": 200, "output_tokens": 50}}
    result = runner.summarize(plan, [failed])
    assert result["pending"] == 11 and not result["coverage_complete"]
    assert sum(g["planned"] for g in result["groups"]) == 12
    assert sum(g["accepted"] for g in result["groups"]) == 0


def test_invalid_submission_is_retained_and_cannot_pass_final_judge():
    data = snapshot()
    def incorrect(task, ctx, toolbox):
        return "call", "submit_source", {"content": task.payload["source"]}
    chassis, candidate, evidence, _ = runner.assemble(data["cases"][0], data, mode="offline",
        context_policy="none", config=ModelConfig("test", max_calls=1), decider=incorrect)
    try:
        result = chassis.run_once()
        assert result.outcome.value == "failed"
        assert candidate.content is not None
        assert evidence.runs[0]["steps"] == runner.NODES
        assert runner.failure_kind(evidence.runs[0]) == "not_accepted"
        assert not result.verdict.done
    finally:
        chassis.close()


def test_interruption_preserves_completed_and_pending_trials(monkeypatch, tmp_path):
    original = runner.assemble
    calls = []
    def interrupted(*args, **kwargs):
        calls.append(1)
        if len(calls) == 2:
            raise RuntimeError("test interruption")
        return original(*args, **kwargs)
    monkeypatch.setattr(runner, "assemble", interrupted)
    output = tmp_path / "partial"
    with pytest.raises(RuntimeError, match="interruption"):
        runner.main(["--output-dir", str(output), "--repeats", "1"])
    result = json.loads((output / "summary.json").read_text())
    assert result["completed"] == 1 and result["pending"] == 11
    assert (output / "trial-001.evidence.json").exists()
    with pytest.raises(ValueError, match="Incomplete"):
        verify(output)
