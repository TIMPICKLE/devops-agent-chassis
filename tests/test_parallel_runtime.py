import argparse
import json
from copy import deepcopy
from dataclasses import asdict
from threading import Barrier

import pytest

pytest.importorskip("jsonschema")

from adapters.anthropic_runtime import AnthropicDecider
from adapters.openai_runtime import OpenAIChatDecider
from adapters.runtime import ModelConfig, ModelError
from agent_chassis import Chassis, Outcome
from agent_chassis.contracts import DoneCriteria, RunContext, Task, TaskSource, Verdict
from agent_chassis.evidence import EvidenceObserver, assembly_manifest
from agent_chassis.orchestration import ReActPattern, SingleAgentOrchestrator, ToolBox
from tools.run_roadmap_showcase import add_model_arguments, model_config
from tools.verify_roadmap_evidence import verify_documents


def response(protocol):
    if protocol == "openai":
        return {"id": "test-request", "choices": [{"finish_reason": "tool_calls", "message": {
            "role": "assistant", "tool_calls": [{"id": f"call-{i}", "type": "function", "function": {
                "name": "read", "arguments": json.dumps({"value": i})}} for i in range(2)]}}],
            "usage": {"prompt_tokens": 20, "completion_tokens": 5}}
    return {"id": "test-request", "stop_reason": "tool_use", "content": [
        {"type": "tool_use", "id": f"call-{i}", "name": "read", "input": {"value": i}} for i in range(2)],
        "usage": {"input_tokens": 20, "output_tokens": 5}}


def setup(monkeypatch, protocol, payload, *, reader=lambda value: value, **limits):
    monkeypatch.setenv("TEST_PARALLEL_KEY", "test-only-canary")
    config = ModelConfig("test-model", api_key_env="TEST_PARALLEL_KEY", max_parallel_tools=2,
                         openai_parallel_tool_calls=True, **limits)
    sent = []

    def transport(url, headers, body, timeout):
        sent.append(body)
        return deepcopy(payload)

    decider = (OpenAIChatDecider if protocol == "openai" else AnthropicDecider)(
        config, tool_names=["read"], transport=transport)
    toolbox = ToolBox().add("read", reader, parallel_safe=True, input_schema={
        "type": "object", "properties": {"value": {"type": "integer"}},
        "required": ["value"], "additionalProperties": False})
    return config, decider, toolbox, sent


@pytest.mark.parametrize("protocol", ["openai", "anthropic"])
def test_model_batch_runs_concurrently_and_next_request_correlates_repeated_tool_names(monkeypatch, protocol):
    rendezvous = Barrier(2, timeout=5)

    def read(value):
        rendezvous.wait()
        return value + 10

    config, decider, box, sent = setup(monkeypatch, protocol, response(protocol), reader=read)
    task, ctx = Task("t", "test"), RunContext()
    ReActPattern(decider, **{**config.react_options(), "max_iterations": 1}).reason(task, ctx, box)
    assert [call.result for call in ctx.tool_calls] == [10, 11]
    assert len(ctx.model_calls) == 1 and ctx.model_calls[0]["ok"]
    decider(task, ctx, box)
    request = sent[-1]
    body = request["messages"][-1]["content"]
    observations = json.loads(body)["observations"]
    assert [(o["call_id"], o["args"], o["result"]) for o in observations] == [
        ("call-0", {"value": 0}, 10), ("call-1", {"value": 1}, 11)]
    assert len({o["batch_id"] for o in observations}) == 1
    if protocol == "openai":
        assert request["parallel_tool_calls"] is True
        assert "parallel_safe=true" in request["tools"][0]["function"]["description"]
    else:
        assert request["tool_choice"]["disable_parallel_tool_use"] is False
        assert "parallel_safe=true" in request["tools"][0]["description"]
    assert "test-only-canary" not in json.dumps(ctx.model_calls)


@pytest.mark.parametrize("protocol", ["openai", "anthropic"])
@pytest.mark.parametrize("bad", ["schema", "name", "id", "missing-id", "unsafe", "limit", "truncated"])
def test_invalid_later_action_executes_nothing_and_retains_usage_and_budget(monkeypatch, protocol, bad):
    payload = response(protocol)
    calls = payload["choices"][0]["message"]["tool_calls"] if protocol == "openai" else payload["content"]
    second = calls[1]
    if bad == "schema":
        if protocol == "openai":
            second["function"]["arguments"] = '{"value":"private-argument-canary"}'
        else:
            second["input"] = {"value": "private-argument-canary"}
    elif bad == "name":
        (second["function"] if protocol == "openai" else second)["name"] = "private-tool-canary"
    elif bad in {"id", "missing-id"}:
        second["id"] = "call-0" if bad == "id" else ""
    elif bad == "truncated":
        if protocol == "openai":
            payload["choices"][0]["finish_reason"] = "length"
        else:
            payload["stop_reason"] = "max_tokens"
    executed = []
    config, decider, box, sent = setup(monkeypatch, protocol, payload,
        reader=lambda value: executed.append(value), max_calls=1, max_batch_calls=1 if bad == "limit" else 8)
    if bad == "unsafe":
        box.add("read", lambda value: executed.append(value), input_schema=box.schema()[0]["inputSchema"])
    task, ctx = Task("t", "test"), RunContext()
    with pytest.raises(ModelError) as error:
        ReActPattern(decider, **config.react_options()).reason(task, ctx, box)
    assert executed == [] and ctx.tool_calls == [] and len(sent) == 1
    assert ctx.model_calls[0]["ok"] is False
    assert (ctx.model_calls[0]["input_tokens"], ctx.model_calls[0]["output_tokens"]) == (20, 5)
    assert "private-" not in str(error.value)
    with pytest.raises(ModelError, match="budget"):
        decider(task, ctx, box)
    assert len(sent) == 1


@pytest.mark.parametrize("protocol", ["openai", "anthropic"])
def test_parallel_evidence_passes_public_verifier_and_does_not_export_arguments(monkeypatch, protocol):
    config, decider, box, _ = setup(monkeypatch, protocol, response(protocol))

    class Source(TaskSource):
        def fetch(self, limit=1):
            return [Task("t", "test")]

    class Criteria(DoneCriteria):
        def judge(self, task, ctx):
            return Verdict(ctx.facts.get("tool_results", {}).get("read") == 1, "objective result")

    evidence = EvidenceObserver(mode="test-transport")
    chassis = (Chassis().with_payload(Source(), Criteria()).observe(evidence)
               .with_orchestrator(SingleAgentOrchestrator(box, ReActPattern(
                   decider, **config.react_options(), stop_when=lambda t, c: "observed both"))).build())
    manifest = assembly_manifest(chassis.report(), runtime={"mode": "test-transport", "config": asdict(config)})
    evidence.assembly_id = manifest["content_id"]
    try:
        assert chassis.run_once().outcome is Outcome.SUCCEEDED
        assert verify_documents(manifest, json.loads(json.dumps(evidence.snapshot()))) == 1
        calls = evidence.runs[0]["tool_calls"]
        assert [c["call_id"] for c in calls] == ["call-0", "call-1"]
        assert all("args" not in c and "result" not in c for c in calls)
        assert evidence.runs[0]["usage"] == {"complete": True, "input_tokens": 20, "output_tokens": 5}
    finally:
        chassis.close()


def test_cli_options_align_protocol_execution_and_omission():
    parser = argparse.ArgumentParser()
    add_model_arguments(parser)
    args = ["--protocol", "openai", "--max-parallel-tools", "4", "--max-batch-calls", "6", "--max-tool-calls", "12"]
    config = model_config(parser.parse_args(args))
    assert config.openai_parallel_tool_calls is True
    assert config.react_options() == {"max_iterations": 8, "max_parallel_tools": 4, "max_batch_calls": 6, "max_tool_calls": 12}
    omitted = model_config(parser.parse_args(args + ["--openai-omit-parallel-tool-calls"]))
    assert omitted.openai_parallel_tool_calls is None and omitted.max_parallel_tools == 4
    assert "parallel_tool_calls" not in OpenAIChatDecider(omitted, tool_names=["read"]).request_body("", [])


@pytest.mark.parametrize("name", ["max_parallel_tools", "max_batch_calls", "max_tool_calls"])
@pytest.mark.parametrize("value", [0, -1, True, 1.5, "2"])
def test_execution_limits_require_positive_integers(name, value):
    with pytest.raises(ValueError):
        ModelConfig("test", **{name: value})
    with pytest.raises(ValueError):
        ReActPattern(lambda t, c, b: ("stop", "", None), **{name: value})
