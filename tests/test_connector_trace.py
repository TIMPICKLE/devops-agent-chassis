from __future__ import annotations

from agent_chassis import Chassis, Outcome
from agent_chassis.contracts import DoneCriteria, RunContext, Task, TaskSource, Verdict
from agent_chassis.observability import RecordingObserver
from agent_chassis.orchestration import FnStep, StateMachineOrchestrator


class StaticSource(TaskSource):
    name = "static"

    def __init__(self, count: int = 1) -> None:
        self._tasks = [Task(key=f"connector-{i}", kind="test") for i in range(count)]

    def fetch(self, limit: int = 1):
        out, self._tasks = self._tasks[:limit], self._tasks[limit:]
        return out


class DoneFactCriteria(DoneCriteria):
    name = "done fact"

    def judge(self, task: Task, ctx: RunContext) -> Verdict:
        return Verdict(bool(ctx.facts.get("done")), "done fact")


def _build(step, handler, count: int = 1):
    criteria = DoneFactCriteria()
    rec = RecordingObserver()
    chassis = (
        Chassis("connector-trace")
        .mount("svc", "mock", handlers={"ping": handler})
        .with_orchestrator(StateMachineOrchestrator([FnStep("call_connector", step)], criteria))
        .observe(rec)
        .with_payload(StaticSource(count), criteria)
        .build()
    )
    return chassis, rec


def test_connector_call_joins_active_run_trace_and_context():
    seen_context_calls = []

    def step(task, ctx):
        out = ctx.chassis.connectors.call("svc", preferred=["ping"], args={"value": 7})
        seen_context_calls.append(list(ctx.tool_calls))
        ctx.facts["done"] = out == "pong"

    chassis, rec = _build(step, lambda args: "pong")
    result = chassis.run_once()

    assert result is not None
    assert result.outcome is Outcome.SUCCEEDED
    assert len(seen_context_calls) == 1
    assert [call.name for call in seen_context_calls[0]] == ["svc.ping"]

    assert len(rec.tasks) == 1
    run_id = rec.tasks[0].run_id
    assert len(rec.tool_calls) == 1
    assert rec.tool_calls[0].run_id == run_id
    assert rec.tool_calls[0].name == "svc.ping"
    assert rec.tool_calls[0].args == {"value": 7}

    tool_traces = [trace for trace in rec.traces if trace.kind == "tool_call"]
    assert len(tool_traces) == 1
    assert tool_traces[0].run_id == run_id
    assert tool_traces[0].label == "svc.ping"


def test_failed_connector_call_is_traced_before_exception_propagates():
    def boom(args):
        raise RuntimeError("connector down")

    def step(task, ctx):
        ctx.chassis.connectors.call("svc", preferred=["ping"])

    chassis, rec = _build(step, boom)
    result = chassis.run_once()

    assert result is not None
    assert result.outcome is Outcome.FAILED
    assert len(rec.tool_calls) == 1
    assert rec.tool_calls[0].name == "svc.ping"
    assert not rec.tool_calls[0].ok
    assert "connector down" in rec.tool_calls[0].error
    assert rec.tool_calls[0].run_id == rec.tasks[0].run_id


def test_connector_calls_outside_task_run_do_not_get_fake_run_id():
    def step(task, ctx):
        ctx.chassis.connectors.call("svc", preferred=["ping"])
        ctx.facts["done"] = True

    chassis, rec = _build(step, lambda args: "pong")

    chassis.connectors.call("svc", preferred=["ping"])
    assert len(chassis.connectors.calls) == 1
    assert rec.tool_calls == []

    result = chassis.run_once()
    assert result is not None and result.outcome is Outcome.SUCCEEDED
    assert len(chassis.connectors.calls) == 2
    assert len(rec.tool_calls) == 1

    chassis.connectors.call("svc", preferred=["ping"])
    assert len(chassis.connectors.calls) == 3
    assert len(rec.tool_calls) == 1, "run 结束后不能沿用已经失效的任务上下文"


def test_sequential_tasks_keep_connector_calls_on_their_own_run_ids():
    def step(task, ctx):
        ctx.chassis.connectors.call("svc", preferred=["ping"], args={"task": task.key})
        ctx.facts["done"] = True

    chassis, rec = _build(step, lambda args: "pong", count=2)
    results = chassis.run(limit=2)

    assert [result.outcome for result in results] == [Outcome.SUCCEEDED, Outcome.SUCCEEDED]
    task_run_ids = [task.run_id for task in rec.tasks]
    call_run_ids = [call.run_id for call in rec.tool_calls]
    assert len(set(task_run_ids)) == 2
    assert call_run_ids == task_run_ids
    assert [call.args["task"] for call in rec.tool_calls] == ["connector-0", "connector-1"]
