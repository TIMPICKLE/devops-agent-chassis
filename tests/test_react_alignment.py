"""Alignment checks fire at assembly time, before any model request or tool call."""
import warnings

import pytest

from adapters.runtime import ModelConfig, ModelError, react_pattern, validate_react_alignment
from agent_chassis.diagnostics import ToolDiagnostic
from agent_chassis.contracts import RunContext, Task
from agent_chassis.orchestration import ReActPattern, ToolBox, ToolRequest


def config(**limits):
    return ModelConfig("test-model", api_key_env="TEST_KEY", **limits)


def stopping_decider(log=None):
    def decider(task, ctx, box):
        if log is not None:
            log.append("decider-ran")
        return "stop", "done", None
    return decider


def batch_once_decider(requests):
    def decider(task, ctx, box):
        if ctx.tool_calls:
            return "stop", "batch executed", None
        return "batch", requests, None
    return decider


def test_unified_entry_applies_react_options_and_passes_overrides_through():
    stop = lambda t, c: None  # noqa: E731
    pattern = react_pattern(config(max_parallel_tools=4, max_batch_calls=6, max_tool_calls=12),
                            stopping_decider(), stop_when=stop, executor_tools=("read",))
    manual = ReActPattern(stopping_decider(), **config(max_parallel_tools=4, max_batch_calls=6,
                                                       max_tool_calls=12).react_options())
    assert (pattern.max_parallel_tools, pattern.max_batch_calls, pattern.max_tool_calls,
            pattern.max_iterations) == (manual.max_parallel_tools, manual.max_batch_calls,
                                        manual.max_tool_calls, manual.max_iterations)
    assert pattern.stop_when is stop and pattern.executor_tools == ["read"]


def test_conflict_is_reported_before_model_request_and_tool_execution():
    log = []
    # The exact footgun: model side allows batches, executor keeps its default 1.
    mismatched = ReActPattern(stopping_decider(log))
    with pytest.raises(ValueError) as error:
        validate_react_alignment(config(max_parallel_tools=4), mismatched)
    message = str(error.value)
    assert "max_parallel_tools" in message
    assert "model side allows batched calls (4)" in message
    assert "executor concurrency limit is 1" in message
    assert "config.react_options()" in message
    assert log == []  # no decider call, so no model request and no tool execution


@pytest.mark.parametrize("field", ["max_parallel_tools", "max_batch_calls", "max_tool_calls"])
@pytest.mark.parametrize("direction", ["model-higher", "executor-higher"])
def test_every_limit_field_is_checked_in_both_directions(field, direction):
    values = (6, 2) if direction == "model-higher" else (2, 6)
    model_config = config(**{field: values[0]})
    executor = ReActPattern(stopping_decider(), **{field: values[1]})
    with pytest.raises(ValueError, match=field):
        validate_react_alignment(model_config, executor)


def test_entry_refuses_overrides_that_conflict_with_config():
    with pytest.raises(ValueError, match="max_parallel_tools"):
        react_pattern(config(max_parallel_tools=4), stopping_decider(), max_parallel_tools=1)


def test_parallel_without_parallel_safe_tools_warns_but_single_tool_assembly_runs():
    calls = []
    box = ToolBox().add("read", lambda value: calls.append(value) or value)

    def single_call(task, ctx, box):
        return ("stop", "done", None) if ctx.tool_calls else ("call", "read", {"value": 7})

    with pytest.warns(UserWarning, match="parallel_safe=True"):
        pattern = react_pattern(config(max_parallel_tools=2), single_call, toolbox=box)
    ctx = RunContext()
    pattern.reason(Task("t", "test"), ctx, box)
    assert ctx.facts["stop_reason"] == "model_stop"
    assert calls == [7] and ctx.tool_calls[0].result == 7


@pytest.mark.parametrize("adapter", [False, True], ids=["custom-decider", "openai-adapter"])
def test_warned_assembly_rejects_batches_without_dropping_or_serializing_calls(adapter, monkeypatch):
    calls = []
    schema = {"type": "object", "properties": {"value": {"type": "integer"}},
              "required": ["value"], "additionalProperties": False}
    box = ToolBox().add("read", lambda value: calls.append(value), input_schema=schema)
    cfg = config(max_parallel_tools=2)
    requests = [ToolRequest("a", "read", {"value": 1}), ToolRequest("b", "read", {"value": 2})]
    decider = batch_once_decider(requests)
    if adapter:
        pytest.importorskip("jsonschema")
        import json
        from adapters.openai_runtime import OpenAIChatDecider

        monkeypatch.setenv("TEST_KEY", "synthetic-not-a-credential")

        def transport(url, headers, body, timeout):
            return {"choices": [{"finish_reason": "tool_calls", "message": {
                "role": "assistant", "tool_calls": [
                    {"id": r.call_id, "type": "function", "function": {
                        "name": r.name, "arguments": json.dumps(r.args)}} for r in requests]}}],
                "usage": {"prompt_tokens": 12, "completion_tokens": 6}}

        decider = OpenAIChatDecider(cfg, tool_names=["read"], transport=transport)
    with pytest.warns(UserWarning, match="batches will be rejected before any tool executes") as warning:
        pattern = react_pattern(cfg, decider, toolbox=box)
    message = str(warning[0].message)
    assert "no automatic fallback" in message
    assert "only for tools verified safe" in message
    assert "single-call contract still rejects batches" in message
    ctx = RunContext()
    with pytest.raises(ModelError if adapter else ToolDiagnostic) as error:
        pattern.reason(Task("t", "test"), ctx, box)
    diagnostic = error.value.diagnostic if adapter else error.value
    assert diagnostic.code == "TOOL_NOT_PARALLEL_SAFE"
    assert diagnostic.details["action_index"] == 1
    assert calls == [] and ctx.tool_calls == [] and ctx.tool_actions_started == 0
    if adapter:
        assert len(ctx.model_calls) == 1
        record = ctx.model_calls[0]
        assert record["ok"] is False
        assert (record["input_tokens"], record["output_tokens"]) == (12, 6)
        assert record["diagnostic"]["code"] == "TOOL_NOT_PARALLEL_SAFE"


def test_single_parallel_safe_tool_batches_without_warning():
    box = ToolBox().add("read", lambda value: value, parallel_safe=True)
    requests = [ToolRequest("a", "read", {"value": 1}), ToolRequest("b", "read", {"value": 2})]
    with warnings.catch_warnings():
        warnings.simplefilter("error")  # legal assembly must not warn
        pattern = react_pattern(config(max_parallel_tools=2), batch_once_decider(requests), toolbox=box)
    ctx = RunContext()
    pattern.reason(Task("t", "test"), ctx, box)
    assert [call.call_id for call in ctx.tool_calls] == ["a", "b"]


def test_legacy_single_call_and_custom_decider_assemblies_pass_silently():
    box = ToolBox().add("read", lambda value: value)
    with warnings.catch_warnings():
        warnings.simplefilter("error")
        pattern = react_pattern(config(), stopping_decider(), toolbox=box)
    assert pattern.max_parallel_tools == 1  # old single-call default, still valid


def test_alignment_check_rejects_non_react_executors():
    class NotAReActPattern:
        pass

    with pytest.raises(ValueError, match="ReActPattern-style"):
        validate_react_alignment(config(), NotAReActPattern())
