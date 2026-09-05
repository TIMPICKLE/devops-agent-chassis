import argparse
import json
from copy import deepcopy

import pytest

pytest.importorskip("jsonschema")

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


def setup(monkeypatch, response, max_calls=8):
    monkeypatch.setenv("TEST_MODEL_KEY", "test-only-canary")
    requests = []

    def transport(url, headers, body, timeout):
        requests.append((url, headers, body))
        if isinstance(response, Exception):
            raise response
        return deepcopy(response)

    config = ModelConfig("test", base_url=DEFAULT_ENDPOINTS["openai"], api_key_env="TEST_MODEL_KEY", max_calls=max_calls)
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


def test_stop_is_not_success_and_missing_usage_not_zero(monkeypatch):
    response = reply()
    response["choices"][0] = {"finish_reason": "stop", "message": {"role": "assistant", "content": "done"}}
    response.pop("usage")
    decider, box, _ = setup(monkeypatch, response)
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
