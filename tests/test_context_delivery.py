from __future__ import annotations
from concurrent.futures import ThreadPoolExecutor
from dataclasses import asdict

import pytest

from agent_chassis.contracts import InjectionPoint as P, RunContext, Task
from agent_chassis.knowledge import InjectionScheduler, SkillLibrary, SkillProvider, StaticKnowledge, by_extension
from agent_chassis.orchestration import ToolBox, invoke_tool


def test_runtime_parameters_do_not_shadow_legacy_tool_arguments():
    box = ToolBox().add("legacy", lambda task, ctx: (task, ctx))
    assert box.call_with_context("legacy", Task("t", "test"), RunContext(),
                                 task="user-task-value", ctx="user-ctx-value") == ("user-task-value", "user-ctx-value")


def test_contextual_executor_receives_injected_text_not_just_trace():
    scheduler = InjectionScheduler([StaticKnowledge("important convention", points=[P.BEFORE_EXECUTOR])])

    class Runtime:
        def inject(self, point, task, ctx):
            return scheduler.collect(point, task, ctx)

        def notify_injection(self, *args):
            pass

        def notify_tool_call(self, *args):
            pass

    received = []

    def executor(*, task, ctx, value):
        received.append(ctx.context_for("executor", [P.BEFORE_EXECUTOR]))
        return value + 1

    box = ToolBox().add_contextual("execute", executor)
    ctx, task = RunContext(chassis=Runtime()), Task("one", "test")
    assert invoke_tool(box, "execute", {"value": 3}, task, ctx, ["execute"]) == 4
    assert received == ["important convention"]
    assert ctx.context_receipts[0].chars == len(received[0])
    assert ctx.context_receipts[0].included == (ctx.injections[0].content_hash,)
    assert "important convention" not in repr(ctx.knowledge)
    assert "knowledge" not in ctx.facts


def test_empty_collection_replaces_previous_scope_and_tasks_do_not_share_context():
    provider = StaticKnowledge("first", points=[P.BEFORE_TOOL])
    scheduler = InjectionScheduler([provider])
    one, two = RunContext(), RunContext()
    task = Task("one", "test")
    scheduler.collect(P.BEFORE_TOOL, task, one)
    provider.text = ""
    scheduler.collect(P.BEFORE_TOOL, task, one)
    assert one.context_for("tool", [P.BEFORE_TOOL]) == ""
    assert two.knowledge == {}
    assert two.context_receipts == []


def test_budget_preserves_whole_chunks_and_records_omissions():
    scheduler = InjectionScheduler([
        StaticKnowledge("abc", points=[P.TASK_ADMITTED], name="first"),
        StaticKnowledge("too long", points=[P.TASK_ADMITTED], name="second"),
        StaticKnowledge("z", points=[P.TASK_ADMITTED], name="third"),
    ])
    ctx = RunContext()
    scheduler.collect(P.TASK_ADMITTED, Task("one", "test"), ctx)
    assert ctx.context_for("model", [P.TASK_ADMITTED], max_chars=6) == "abc\n\nz"
    receipt = asdict(ctx.context_receipts[-1])
    assert len(receipt["included"]) == 2 and len(receipt["omitted"]) == 1
    assert receipt["chars"] == 6
    assert ctx.context_for("model", [P.AGENT_BOOT]) == ""


def test_skill_metadata_has_no_shared_last_task_state():
    library = SkillLibrary("", rules=[by_extension({".py": "python", ".js": "javascript"})],
                           inline={"python": "python only", "javascript": "js only"})
    provider = SkillProvider(library)
    py = Task("py", "test", {"target": {"path": "one.py"}})
    js = Task("js", "test", {"target": {"path": "two.js"}})
    provider.provide(P.BEFORE_EXECUTOR, py, RunContext())
    provider.provide(P.BEFORE_EXECUTOR, js, RunContext())
    assert provider.label_for(P.BEFORE_EXECUTOR, py, RunContext()) == "skills:python"
    scheduler = InjectionScheduler([provider])

    def read(task):
        ctx = RunContext()
        scheduler.collect(P.BEFORE_EXECUTOR, task, ctx)
        return ctx.injections[0].label, ctx.context_for(task.key, [P.BEFORE_EXECUTOR])

    with ThreadPoolExecutor(2) as pool:
        results = list(pool.map(read, [py, js]))
    assert results[0][0] == "skills:python" and "js only" not in results[0][1]
    assert results[1][0] == "skills:javascript" and "python only" not in results[1][1]


def test_tool_schema_and_legacy_call_are_backwards_compatible():
    schema = {"type": "object", "properties": {"value": {"type": "integer"}}}
    box = ToolBox().add("echo", lambda value: value, input_schema=schema)
    schema["type"] = "string"
    assert box.call("echo", value=2) == 2
    assert box.schema()[0]["inputSchema"]["type"] == "object"
    copy = box.schema()
    copy[0]["inputSchema"]["type"] = "string"
    assert box.schema()[0]["inputSchema"]["type"] == "object"
    box.add("echo", lambda: 7)
    assert "inputSchema" not in box.schema()[0]
    assert box.call("echo") == 7


def test_contextual_tools_require_explicit_runtime():
    box = ToolBox().add_contextual("exec", lambda **kwargs: kwargs)
    with pytest.raises(ValueError, match="call_with_context"):
        box.call("exec")


@pytest.mark.parametrize("budget", [-1, -20])
def test_invalid_context_budget_is_rejected(budget):
    with pytest.raises(ValueError):
        RunContext().context_for("executor", [], max_chars=budget)
