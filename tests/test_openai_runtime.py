import argparse
import json
from copy import deepcopy
from dataclasses import asdict

import pytest

pytest.importorskip("jsonschema")

from adapters.anthropic_runtime import AnthropicDecider
from adapters.openai_runtime import OpenAIChatDecider
from adapters.runtime import DEFAULT_ENDPOINTS, ModelConfig, ModelError
from agent_chassis import Outcome
from agent_chassis.contracts import RunContext, Task
from agent_chassis.orchestration import ToolBox
from payloads.patch_showcase import fixture_solution
from tools.run_roadmap_showcase import add_model_arguments, assemble, model_config
from tools.verify_roadmap_evidence import verify_documents


def reply(name="echo", arguments='{"value": 3}'):
    return {"id": "chat-test", "model": "resolved-test", "choices": [{
        "finish_reason": "tool_calls", "message": {"role": "assistant", "tool_calls": [{
            "id": "call-1", "type": "function", "function": {"name": name, "arguments": arguments},
        }]}}], "usage": {"prompt_tokens": 20, "completion_tokens": 5}}


def setup(monkeypatch, response, max_calls=8, **config_options):
    monkeypatch.setenv("TEST_MODEL_KEY", "test-only-canary")
    requests = []

    def transport(url, headers, body, timeout):
        requests.append((url, headers, body))
        if isinstance(response, Exception):
            raise response
        return deepcopy(response)

    config = ModelConfig("test", base_url=DEFAULT_ENDPOINTS["openai"], api_key_env="TEST_MODEL_KEY",
                         max_calls=max_calls, **config_options)
    decider = OpenAIChatDecider(config, tool_names=["echo"], transport=transport)
    box = ToolBox().add("echo", lambda value: value, input_schema={
        "type": "object", "properties": {"value": {"type": "integer"}},
        "required": ["value"], "additionalProperties": False,
    })
    return decider, box, requests


def test_protocol_normalizes_tool_usage_and_request(monkeypatch):
    decider, box, requests = setup(monkeypatch, reply())
    ctx = RunContext()
    assert decider(Task("t", "test"), ctx, box) == ("call", "echo", {"value": 3})
    url, headers, body = requests[0]
    assert url == DEFAULT_ENDPOINTS["openai"] + "/chat/completions"
    assert headers["Authorization"] == "Bearer test-only-canary"
    assert body["stream"] is False
    assert body["parallel_tool_calls"] is False
    assert body["tool_choice"] == "auto"
    assert body["tools"][0]["function"]["parameters"]["required"] == ["value"]
    record = ctx.model_calls[0]
    assert (record["input_tokens"], record["output_tokens"], record["mode"]) == (20, 5, "test-transport")
    assert record["adapter"] == "openai-chat-completions"
    assert "test-only-canary" not in json.dumps(ctx.model_calls)


@pytest.mark.parametrize("name,arguments", [("unknown", "{}"), ("echo", "[]"), ("echo", "broken"),
    ("echo", '{}'), ("echo", '{"value":"3"}'), ("echo", '{"value":3,"extra":1}')])
def test_invalid_function_never_runs(monkeypatch, name, arguments):
    decider, box, _ = setup(monkeypatch, reply(name, arguments))
    ctx = RunContext()
    with pytest.raises(ModelError):
        decider(Task("t", "test"), ctx, box)
    assert ctx.model_calls[-1]["ok"] is False


@pytest.mark.parametrize("change", ["length", "inconsistent", "multiple", "empty", "encoded_object", "filtered"])
def test_invalid_completion_never_runs(monkeypatch, change):
    response = reply()
    choice = response["choices"][0]
    if change in {"length", "inconsistent", "filtered"}:
        choice["finish_reason"] = {"length": "length", "inconsistent": "stop", "filtered": "content_filter"}[change]
    elif change == "multiple":
        choice["message"]["tool_calls"] *= 2
    elif change == "empty":
        response["choices"] = []
    else:
        choice["message"]["tool_calls"][0]["function"]["arguments"] = {"value": 3}
    decider, box, _ = setup(monkeypatch, response)
    with pytest.raises(ModelError):
        decider(Task("t", "test"), RunContext(), box)


@pytest.mark.parametrize("control", [False, None])
def test_stop_is_not_success_and_missing_usage_not_zero(monkeypatch, control):
    response = reply()
    response["choices"][0] = {"finish_reason": "stop", "message": {"role": "assistant", "content": "done"}}
    response.pop("usage")
    decider, box, _ = setup(monkeypatch, response, openai_parallel_tool_calls=control)
    ctx = RunContext()
    assert decider(Task("t", "test"), ctx, box)[0] == "stop"
    assert ctx.facts == {}
    assert ctx.model_calls[0]["input_tokens"] is None


def test_budget_missing_key_and_transport_error(monkeypatch):
    decider, box, requests = setup(monkeypatch, reply(), max_calls=1)
    task, ctx = Task("t", "test"), RunContext()
    decider(task, ctx, box)
    with pytest.raises(ModelError, match="budget"):
        decider(task, ctx, box)
    monkeypatch.delenv("TEST_MODEL_KEY")
    with pytest.raises(ModelError, match="Missing environment variable"):
        decider(task, RunContext(), box)
    assert len(requests) == 1
    decider, box, _ = setup(monkeypatch, RuntimeError("test-only-canary"))
    with pytest.raises(ModelError) as caught:
        decider(task, RunContext(), box)
    assert "test-only-canary" not in str(caught.value)


@pytest.mark.parametrize("scenario", ["python_quality", "header_build"])
@pytest.mark.parametrize("flow", ["nested", "state_machine", "single_agent"])
def test_same_payload_verifier_and_evidence_across_protocols(monkeypatch, scenario, flow):
    monkeypatch.setenv("TEST_MODEL_KEY", "test-only-canary")

    def transport(url, headers, body, timeout):
        snapshot = json.loads(body["messages"][-1]["content"])
        task = Task("fixture", scenario, snapshot["task"])
        return reply("submit_source", json.dumps({"content": fixture_solution(task)}))

    config = ModelConfig("test", base_url=DEFAULT_ENDPOINTS["openai"], api_key_env="TEST_MODEL_KEY")
    decider = OpenAIChatDecider(config, tool_names=["submit_source"], transport=transport)
    chassis, candidate, evidence, manifest = assemble(scenario, flow=flow, config=config,
                                                      decider=decider, protocol="openai")
    try:
        assert chassis.run_once().outcome is Outcome.SUCCEEDED
        assert len(evidence.runs[0]["model_calls"]) == 1
        assert evidence.runs[0]["stop_reason"] == "objective_stop"
        assert manifest["runtime"]["adapter"] == "openai-chat-completions"
        verify_documents(manifest, json.loads(json.dumps(evidence.snapshot())))
    finally:
        chassis.close()


def test_cli_uses_protocol_specific_environment_and_plan_endpoint(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_BASE_URL", "https://anthropic.example.test")
    monkeypatch.delenv("OPENAI_BASE_URL", raising=False)
    parser = argparse.ArgumentParser()
    add_model_arguments(parser)
    assert model_config(parser.parse_args(["--protocol", "openai"])).base_url == DEFAULT_ENDPOINTS["openai"]
    assert model_config(parser.parse_args([])).base_url == "https://anthropic.example.test"


@pytest.mark.parametrize("control", [False, None])
def test_explicit_wire_control_is_serializable_and_keeps_single_call_contract(monkeypatch, control):
    decider, box, requests = setup(monkeypatch, reply(), openai_parallel_tool_calls=control)
    assert decider(Task("t", "test"), RunContext(), box) == ("call", "echo", {"value": 3})
    body = requests[0][2]
    if control is None:
        assert "parallel_tool_calls" not in body
    else:
        assert body["parallel_tool_calls"] is False
    assert body["tool_choice"] == "auto"
    assert json.loads(json.dumps(asdict(decider.config)))["openai_parallel_tool_calls"] is control


@pytest.mark.parametrize("control", [True, 0, 1, "false", "omit", [], {}])
def test_wire_control_rejects_parallel_execution_and_ambiguous_values(control):
    with pytest.raises(ValueError, match="openai_parallel_tool_calls"):
        ModelConfig("test", openai_parallel_tool_calls=control)


def test_cli_omission_is_explicit_and_anthropic_wire_is_unchanged():
    parser = argparse.ArgumentParser()
    add_model_arguments(parser)
    default = model_config(parser.parse_args(["--protocol", "openai"]))
    omitted = model_config(parser.parse_args(["--protocol", "openai", "--openai-omit-parallel-tool-calls"]))
    assert default.openai_parallel_tool_calls is False
    assert omitted.openai_parallel_tool_calls is None
    for config in (default, omitted):
        body = AnthropicDecider(config, tool_names=["echo"]).request_body("test", [])
        assert "parallel_tool_calls" not in body
        assert body["tool_choice"] == {"type": "auto", "disable_parallel_tool_use": True}


@pytest.mark.parametrize("control", [False, None])
@pytest.mark.parametrize("second_valid", [True, False])
def test_multiple_calls_execute_nothing_and_preserve_failure_usage_and_budget(monkeypatch, control, second_valid):
    # Even a valid first call must never execute when followed by another call.
    response = reply("submit_source", '{"content":"private-argument-canary"}')
    calls = response["choices"][0]["message"]["tool_calls"]
    second = deepcopy(calls[0]) if second_valid else {"type": "function", "function": {
        "name": "private-tool-canary", "arguments": "private-argument-canary"}}
    second["id"] = "call-2"
    calls.append(second)
    decider, _, requests = setup(monkeypatch, response, max_calls=1,
                                  openai_parallel_tool_calls=control)
    decider.tool_names = ("submit_source",)
    chassis, candidate, evidence, manifest = assemble("python_quality", flow="nested",
        config=decider.config, decider=decider, protocol="openai")
    executed = []
    chassis._orchestrator.toolbox.add("submit_source", lambda content: executed.append(content), input_schema={
        "type": "object", "properties": {"content": {"type": "string"}}, "required": ["content"],
        "additionalProperties": False,
    })
    try:
        result = chassis.run_once()
        assert result.outcome is Outcome.FAILED
        assert "Expected at most one function call, received 2; no tools executed" in result.error
        assert not executed and candidate.content is None
        run = evidence.runs[0]
        assert run["tool_calls"] == []
        assert len(requests) == len(run["model_calls"]) == 1  # No implicit retry.
        assert run["model_calls"][0]["ok"] is False
        assert run["model_calls"][0]["error_type"] == "ModelError"
        assert run["model_calls"][0]["request_id"] == "chat-test"
        assert run["usage"] == {"complete": True, "input_tokens": 20, "output_tokens": 5}
        assert manifest["runtime"]["config"]["openai_parallel_tool_calls"] is control
        verify_documents(manifest, json.loads(json.dumps(evidence.snapshot())))
        serialized = json.dumps(evidence.snapshot())
        for secret in ("private-argument-canary", "private-tool-canary", "test-only-canary"):
            assert secret not in serialized
        ctx = RunContext(model_calls=deepcopy(run["model_calls"]))
        with pytest.raises(ModelError, match="budget"):
            decider(Task("again", "test"), ctx, chassis._orchestrator.toolbox)
        assert len(requests) == 1
    finally:
        chassis.close()


@pytest.mark.parametrize("control", [False, None])
def test_gateway_error_does_not_strip_parameter_or_retry(monkeypatch, control):
    decider, box, requests = setup(monkeypatch, ModelError("Model HTTP status 400"),
                                  openai_parallel_tool_calls=control)
    ctx = RunContext()
    with pytest.raises(ModelError, match="HTTP status 400"):
        decider(Task("t", "test"), ctx, box)
    assert len(requests) == len(ctx.model_calls) == 1
    assert ("parallel_tool_calls" in requests[0][2]) is (control is False)
    assert ctx.model_calls[0]["ok"] is False
