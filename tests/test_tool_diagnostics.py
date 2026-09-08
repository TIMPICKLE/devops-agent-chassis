import json

import pytest

pytest.importorskip("jsonschema")

from adapters.openai_runtime import OpenAIChatDecider
from adapters.runtime import ModelConfig, ModelError
from agent_chassis.contracts import RunContext, Task
from agent_chassis.diagnostics import ToolDiagnostic
from agent_chassis.orchestration import ReActPattern, ToolBox, ToolRequest


def test_core_diagnostics_are_distinct_and_preflight_executes_nothing():
    ran = []
    box = ToolBox().add("read", lambda value: ran.append(value), parallel_safe=True)
    box.add("write", lambda value: ran.append(value))
    first = ToolRequest("a", "read", {"value": 1})
    cases = [
        (ToolRequest("b", "write", {"value": 2}), "TOOL_NOT_PARALLEL_SAFE"),
        (ToolRequest("b", "secret-tool-name", {}), "UNKNOWN_TOOL"),
        (ToolRequest("a", "read", {"value": 2}), "DUPLICATE_CALL_ID"),
        (ToolRequest("", "read", {}), "INVALID_CALL_ID"),
        (ToolRequest("b", "read", {"secret-key": "secret-value"}), "INVALID_TOOL_ARGUMENTS"),
    ]
    for second, code in cases:
        ctx = RunContext()
        with pytest.raises(ToolDiagnostic) as exc:
            ReActPattern(lambda t, c, b: ("batch", [first, second], None), max_parallel_tools=2).reason(
                Task("t", "test"), ctx, box)
        assert exc.value.code == code and exc.value.details["action_index"] == 2
        assert ctx.diagnostics == [exc.value.as_dict()]
        assert "secret-" not in str(exc.value) + json.dumps(ctx.diagnostics)
    assert ran == []


@pytest.mark.parametrize("limit,code", [({"max_parallel_tools": 1}, "PARALLEL_DISABLED"),
    ({"max_parallel_tools": 2, "max_batch_calls": 1}, "BATCH_LIMIT_EXCEEDED"),
    ({"max_parallel_tools": 2, "max_tool_calls": 1}, "TOOL_BUDGET_EXHAUSTED")])
def test_limits_have_stable_codes(limit, code):
    box = ToolBox().add("read", lambda: None, parallel_safe=True)
    calls = [ToolRequest(str(i), "read", {}) for i in range(2)]
    with pytest.raises(ToolDiagnostic) as exc:
        ReActPattern(lambda t, c, b: ("batch", calls, None), **limit).reason(Task("t", "test"), RunContext(), box)
    assert exc.value.code == code


@pytest.mark.parametrize("args,path,constraint", [
    ({"item": {"count": "secret-value"}}, "$.item.count", "type"),
    ({"item": {"count": 1, "secret-key": "secret-value"}}, "$.item", "additionalProperties"),
    ({"item": {}}, "$.item", "required"),
])
def test_adapter_reports_schema_path_without_values_and_keeps_usage(monkeypatch, args, path, constraint):
    monkeypatch.setenv("TEST_KEY", "secret-key-value")
    response = {"choices": [{"finish_reason": "tool_calls", "message": {"role": "assistant", "tool_calls": [
        {"id": "a", "type": "function", "function": {"name": "read", "arguments": json.dumps(args)}}]}}],
        "usage": {"prompt_tokens": 12, "completion_tokens": 3}}
    box = ToolBox().add("read", lambda item: item, input_schema={"type": "object", "properties": {
        "item": {"type": "object", "properties": {"count": {"type": "integer"}},
                 "required": ["count"], "additionalProperties": False}}})
    decider = OpenAIChatDecider(ModelConfig("test", api_key_env="TEST_KEY"), tool_names=["read"],
                                transport=lambda *a: response)
    ctx = RunContext()
    with pytest.raises(ModelError) as exc:
        decider(Task("t", "test"), ctx, box)
    diagnostic = ctx.model_calls[0]["diagnostic"]
    assert diagnostic == {"code": "INVALID_TOOL_ARGUMENTS", "action_index": 1, "tool": "read",
                          "argument_path": path, "constraint": constraint}
    assert ctx.diagnostics == [diagnostic]
    assert ctx.model_calls[0]["input_tokens"] == 12 and ctx.model_calls[0]["ok"] is False
    assert "secret-" not in str(exc.value) + json.dumps(ctx.model_calls)
