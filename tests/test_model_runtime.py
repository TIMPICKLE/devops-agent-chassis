from __future__ import annotations

import json
from dataclasses import asdict, replace

import pytest

pytest.importorskip("jsonschema")

from adapters.anthropic_runtime import AnthropicDecider, ModelConfig, ModelError
from agent_chassis.contracts import InjectionPoint as P, RunContext, Task
from agent_chassis.knowledge import InjectionScheduler, StaticKnowledge
from agent_chassis.orchestration import ToolBox


def setup(monkeypatch, response, **limits):
    monkeypatch.setenv("TEST_MODEL_KEY", "test-only-canary-not-a-real-key")
    requests = []

    def transport(url, headers, body, timeout):
        requests.append((url, headers, body, timeout))
        if isinstance(response, Exception):
            raise response
        return response

    config = ModelConfig("test-model", api_key_env="TEST_MODEL_KEY", **limits)
    decider = AnthropicDecider(config, tool_names=["echo"], transport=transport)
    box = ToolBox().add("echo", lambda value: value, input_schema={
        "type": "object", "properties": {"value": {"type": "integer"}},
        "required": ["value"], "additionalProperties": False,
    }).add("not_exposed", lambda: None)
    return decider, box, requests


def reply(**changes):
    value = {"id": "msg-test", "model": "resolved-test-model", "stop_reason": "tool_use",
             "content": [{"type": "tool_use", "name": "echo", "input": {"value": 3}}],
             "usage": {"input_tokens": 20, "output_tokens": 5}}
    value.update(changes)
    return value


def test_native_tool_schema_context_usage_and_key_reference(monkeypatch):
    decider, box, requests = setup(monkeypatch, reply())
    ctx, task = RunContext(), Task("one", "test", {"goal": "echo"})
    InjectionScheduler([StaticKnowledge("retry context", points=[P.ON_RETRY])]).collect(P.ON_RETRY, task, ctx)
    assert decider(task, ctx, box) == ("call", "echo", {"value": 3})
    url, headers, body, timeout = requests[0]
    assert url.endswith("/v1/messages")
    assert headers["x-api-key"] == "test-only-canary-not-a-real-key"
    assert [tool["name"] for tool in body["tools"]] == ["echo"]
    assert "retry context" in body["messages"][0]["content"]
    assert ctx.model_calls[0]["input_tokens"] == 20
    assert ctx.model_calls[0]["resolved_model"] == "resolved-test-model"
    assert ctx.model_calls[0]["mode"] == "test-transport"
    assert ctx.context_receipts[0].included
    evidence = json.dumps(ctx.model_calls) + json.dumps(asdict(decider.config))
    assert "test-only-canary-not-a-real-key" not in evidence
    assert "retry context" not in evidence


@pytest.mark.parametrize("content", [
    [{"type": "tool_use", "name": "not_exposed", "input": {}}],
    [{"type": "tool_use", "name": "echo", "input": {"value": "three"}}],
    [{"type": "tool_use", "name": "echo", "input": {"value": 3, "extra": 1}}],
    [{"type": "tool_use", "name": "echo", "input": []}],
    [{"type": "tool_use", "name": "echo", "input": {}}],
    [{"type": "tool_use", "name": "echo", "input": {"value": 3}}] * 2,
    "not a list", ["not a block"],
])
def test_bad_response_never_becomes_a_tool_action(monkeypatch, content):
    decider, box, _ = setup(monkeypatch, reply(content=content))
    ctx = RunContext()
    with pytest.raises(ModelError):
        decider(Task("one", "test"), ctx, box)
    assert ctx.model_calls[-1]["ok"] is False


def test_end_turn_does_not_create_a_verdict(monkeypatch):
    decider, box, _ = setup(monkeypatch, reply(stop_reason="end_turn", content=[{"type": "text", "text": "I succeeded"}]))
    ctx = RunContext()
    assert decider(Task("one", "test"), ctx, box)[0] == "stop"
    assert ctx.facts == {}


def test_truncated_output_is_not_accepted(monkeypatch):
    decider, box, _ = setup(monkeypatch, reply(stop_reason="max_tokens"))
    with pytest.raises(ModelError, match="truncated"):
        decider(Task("one", "test"), RunContext(), box)


def test_inconsistent_stop_reason_does_not_execute_embedded_tool(monkeypatch):
    decider, box, _ = setup(monkeypatch, reply(stop_reason="end_turn"))
    with pytest.raises(ModelError, match="inconsistent"):
        decider(Task("one", "test"), RunContext(), box)


def test_budget_and_missing_key_do_not_call_network(monkeypatch):
    decider, box, requests = setup(monkeypatch, reply(), max_calls=1)
    task, ctx = Task("one", "test"), RunContext()
    decider(task, ctx, box)
    with pytest.raises(ModelError, match="budget"):
        decider(task, ctx, box)
    assert len(requests) == 1
    monkeypatch.delenv("TEST_MODEL_KEY")
    with pytest.raises(ModelError, match="Missing environment variable"):
        decider(task, RunContext(), box)
    assert len(requests) == 1


def test_failure_does_not_echo_transport_exception_or_secret(monkeypatch):
    decider, box, _ = setup(monkeypatch, RuntimeError("test-only-canary-not-a-real-key"))
    ctx = RunContext()
    with pytest.raises(ModelError) as error:
        decider(Task("one", "test"), ctx, box)
    assert "test-only-canary" not in str(error.value)
    assert "test-only-canary" not in json.dumps(ctx.model_calls)


def test_unknown_usage_stays_unknown_instead_of_zero(monkeypatch):
    decider, box, _ = setup(monkeypatch, reply(usage={}))
    ctx = RunContext()
    decider(Task("one", "test"), ctx, box)
    assert ctx.model_calls[0]["input_tokens"] is None


def test_retry_does_not_present_compensated_artifacts_as_current_observations(monkeypatch):
    from agent_chassis.contracts import ToolCall

    decider, box, requests = setup(monkeypatch, reply())
    ctx = RunContext()
    ctx.tool_calls.append(ToolCall("echo", {"value": 1}, result="discarded artifact"))
    ctx.facts["attempt_tool_call_start"] = 1
    decider(Task("one", "test"), ctx, box)
    assert json.loads(requests[0][2]["messages"][0]["content"])["observations"] == []
    assert len(ctx.tool_calls) == 1  # Historical trace was retained.


@pytest.mark.parametrize("url", ["http://example.com", "https://user:secret@example.com", "https://example.com?key=value"])
def test_endpoint_configuration_contains_no_credentials(url):
    with pytest.raises(ValueError):
        ModelConfig("test", base_url=url)
