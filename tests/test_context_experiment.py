import json

import pytest

pytest.importorskip("jsonschema")

from adapters.anthropic_runtime import AnthropicDecider
from adapters.openai_runtime import OpenAIChatDecider
from adapters.runtime import DEFAULT_ENDPOINTS, ModelConfig
from agent_chassis.contracts import Task
from payloads.patch_showcase import fixture_solution
from tools import run_context_experiment as experiment
from tools.run_roadmap_showcase import assemble, execute_scenario, write_json
from tools.verify_context_experiment import verify_experiment


def decider_for(config, protocol, scenario, requests, invalid=False):
    def transport(url, headers, body, timeout):
        snapshot = json.loads(body["messages"][-1]["content"])
        requests.append(snapshot)
        args = {"content": "invalid" if invalid else fixture_solution(Task("fixture", scenario, snapshot["task"]))}
        if protocol == "anthropic":
            return {"stop_reason": "tool_use", "content": [{"type": "tool_use", "name": "submit_source", "input": args}],
                    "usage": {"input_tokens": 20, "output_tokens": 5}}
        return {"choices": [{"finish_reason": "tool_calls", "message": {"role": "assistant", "tool_calls": [{
            "type": "function", "function": {"name": "submit_source", "arguments": json.dumps(args)}}]}}],
            "usage": {"prompt_tokens": 20, "completion_tokens": 5}}

    adapter = AnthropicDecider if protocol == "anthropic" else OpenAIChatDecider
    return adapter(config, tool_names=["submit_source"], transport=transport)


@pytest.mark.parametrize("protocol", ["anthropic", "openai"])
@pytest.mark.parametrize("scenario", ["python_quality", "header_build"])
def test_only_context_changes_in_actual_request(monkeypatch, protocol, scenario):
    monkeypatch.setenv("TEST_MODEL_KEY", "test-only-canary")
    config = ModelConfig("test", base_url=DEFAULT_ENDPOINTS[protocol], api_key_env="TEST_MODEL_KEY")
    snapshots, chars = {}, {}
    for policy in experiment.CONTEXT_POLICIES:
        requests = []
        decider = decider_for(config, protocol, scenario, requests)
        chassis, _, evidence, manifest = assemble(scenario, config=config, decider=decider,
                                                 protocol=protocol, context_policy=policy)
        try:
            assert chassis.run_once().outcome.value == "succeeded"
            assert len(requests) == 1
            snapshots[policy] = requests[0]
            chars[policy] = evidence.runs[0]["context_receipts"][0]["chars"]
            assert not any(i["point"] == "agent_boot" for i in evidence.runs[0]["injections"])
        finally:
            chassis.close()
    assert snapshots["routed"]["task"] == snapshots["full"]["task"] == snapshots["none"]["task"]
    assert snapshots["routed"]["observations"] == snapshots["full"]["observations"] == snapshots["none"]["observations"]
    assert snapshots["routed"]["context"] in snapshots["full"]["context"]
    assert snapshots["none"]["context"] == ""
    assert chars["full"] > chars["routed"] > chars["none"] == 0


def test_offline_experiment_preserves_plan_and_unknown_usage(tmp_path):
    output = tmp_path / "experiment"
    assert experiment.main(["--output-dir", str(output)]) == 0
    summary = verify_experiment(output)
    assert summary["coverage_complete"] and summary["all_passed"]
    assert summary["execution_modes"] == ["offline-contract"]
    assert summary["model_calls"] == 0
    assert len(summary["trials"]) == 6
    assert all(g["usage"]["input_tokens"] is None for g in summary["groups"])
    groups = {g["context_policy"]: g for g in summary["groups"]}
    assert groups["full"]["context_chars"] > groups["routed"]["context_chars"] > groups["none"]["context_chars"] == 0
    with pytest.raises(ValueError, match="Not a live"):
        verify_experiment(output, require_live=True)
    assert experiment.trial_order(2, 42) == experiment.trial_order(2, 42)
    assert experiment.trial_order(2, 42) != experiment.trial_order(2, 43)
    for repeat in (1, 2):
        assert len({(t["scenario"], t["context_policy"]) for t in experiment.trial_order(2, 42)
                    if t["repeat"] == repeat}) == 6


def test_failures_and_unrun_trials_stay_in_denominator(monkeypatch, tmp_path):
    monkeypatch.setenv("TEST_MODEL_KEY", "test-only-canary")
    requests = []

    def execute(scenario, output, **kwargs):
        kwargs["decider"] = decider_for(kwargs["config"], kwargs["protocol"], scenario, requests, invalid=True)
        return execute_scenario(scenario, output, **kwargs)

    monkeypatch.setattr(experiment, "execute_scenario", execute)
    output = tmp_path / "budget"
    assert experiment.main(["--mode", "live", "--api-key-env", "TEST_MODEL_KEY", "--max-calls", "1",
                            "--max-total-model-calls", "2", "--output-dir", str(output)]) == 1
    summary = verify_experiment(output)
    assert len(requests) == summary["model_calls"] == 2
    assert not summary["coverage_complete"] and not summary["all_passed"]
    assert sum(g["planned"] for g in summary["groups"]) == 6
    assert sum(g["not_run"] for g in summary["groups"]) == 4
    assert sum(g["accepted"] for g in summary["groups"]) == 0
    assert "不能声明真实模型实测" in experiment.markdown(summary)
    with pytest.raises(ValueError, match="Not live"):
        verify_experiment(output, require_live=True)


@pytest.mark.parametrize("change", ["aggregate", "trial", "input", "budget", "patch"])
def test_report_verifier_detects_inconsistent_evidence(tmp_path, change):
    output = tmp_path / "report"
    experiment.main(["--output-dir", str(output)])
    summary = json.loads((output / "summary.json").read_text())
    if change == "patch":
        (output / "trial-001.patch").write_text("changed")
    elif change == "aggregate":
        summary["groups"][0]["model_calls"] = 123
    elif change == "trial":
        summary["trials"][0]["context_chars"] += 1
    else:
        plan = json.loads((output / "experiment-plan.json").read_text())
        if change == "input":
            plan["inputs"]["header_build"] = "changed"
        else:
            plan["model_config"]["context_max_chars"] = 1
        plan["content_id"] = experiment.content_id({k: v for k, v in plan.items() if k != "content_id"})
        write_json(output / "experiment-plan.json", plan)
    write_json(output / "summary.json", summary)
    with pytest.raises(ValueError):
        verify_experiment(output)


def test_missing_key_and_invalid_config_do_not_create_output(monkeypatch, tmp_path):
    monkeypatch.delenv("MISSING_TEST_KEY", raising=False)
    output = tmp_path / "missing"
    with pytest.raises(SystemExit):
        experiment.main(["--mode", "live", "--api-key-env", "MISSING_TEST_KEY", "--output-dir", str(output)])
    assert not output.exists()
    with pytest.raises(SystemExit):
        experiment.main(["--repeats", "0", "--output-dir", str(output)])
    assert not output.exists()


def test_interruption_keeps_completed_evidence_and_pending_denominator(monkeypatch, tmp_path):
    calls = []

    def execute(scenario, output, **kwargs):
        if calls:
            raise RuntimeError("simulated interruption")
        calls.append(scenario)
        return execute_scenario(scenario, output, **kwargs)

    monkeypatch.setattr(experiment, "execute_scenario", execute)
    output = tmp_path / "partial"
    with pytest.raises(RuntimeError, match="simulated interruption"):
        experiment.main(["--output-dir", str(output)])
    summary = json.loads((output / "summary.json").read_text())
    assert len(summary["trials"]) == 1
    assert sum(g["pending"] for g in summary["groups"]) == 5
    assert not summary["all_passed"] and not summary["coverage_complete"]
    assert (output / summary["trials"][0]["evidence"]).exists()
    with pytest.raises(ValueError, match="all planned trials"):
        verify_experiment(output)
