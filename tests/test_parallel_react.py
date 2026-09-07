"""Concurrency is proved with rendezvous, not fragile elapsed-time assertions."""
from threading import Barrier, Event, Lock, get_ident

import pytest

from agent_chassis import Chassis, Outcome, borrowed_executor
from agent_chassis.contracts import DoneCriteria, Observer, RunContext, Task, TaskSource, Verdict
from agent_chassis.evidence import EvidenceObserver
from agent_chassis.failure import RetryThenGiveUpPolicy, ZeroSideEffectPolicy
from agent_chassis.observability import RecordingObserver
from agent_chassis.orchestration import ReActPattern, SingleAgentOrchestrator, ToolBox, ToolRequest


def requests(count=2, name="read"):
    return [ToolRequest(f"call-{i}", name, {"value": i}) for i in range(count)]


def pattern_for(calls, **options):
    return ReActPattern(lambda t, c, b: ("batch", calls, None),
                        max_iterations=1, max_parallel_tools=2, **options)


class Source(TaskSource):
    def fetch(self, limit=1):
        return [Task("parallel", "test")]


class Criteria(DoneCriteria):
    def __init__(self, accept=True):
        self.accept = accept

    def judge(self, task, ctx):
        return Verdict(self.accept and bool(ctx.facts.get("tool_results")), "objective check")


def test_calls_overlap_but_traces_keep_request_order_and_stop_waits_for_all():
    rendezvous = Barrier(2, timeout=5)
    second_done = Event()
    owner = get_ident()
    workers = set()
    lock = Lock()

    def read(value):
        with lock:
            workers.add(get_ident())
        rendezvous.wait()
        if value == 0:
            assert second_done.wait(5)
        else:
            second_done.set()
        return value * 10

    def stop(task, ctx):
        assert get_ident() == owner and second_done.is_set()
        assert [c.result for c in ctx.tool_calls] == [0, 10]
        return "all evidence collected"

    box = ToolBox().add("read", read, parallel_safe=True)
    ctx = RunContext()
    pattern_for(requests(), stop_when=stop).reason(Task("t", "test"), ctx, box)
    assert len(workers) == 2 and owner not in workers
    assert [c.call_id for c in ctx.tool_calls] == ["call-0", "call-1"]
    assert len({c.batch_id for c in ctx.tool_calls}) == 1
    assert ctx.facts["stop_reason"] == "objective_stop"
    assert ctx.tool_actions_started == 2 and ctx.iterations == 1


def test_worker_count_is_bounded_for_a_larger_batch():
    rendezvous = Barrier(2, timeout=5)
    lock = Lock()
    active = peak = 0

    def read(value):
        nonlocal active, peak
        with lock:
            active += 1
            peak = max(peak, active)
        rendezvous.wait()
        with lock:
            active -= 1
        return value

    ctx = RunContext()
    pattern_for(requests(6)).reason(Task("t", "test"), ctx, ToolBox().add("read", read, parallel_safe=True))
    assert peak == 2 and len(ctx.tool_calls) == 6


@pytest.mark.parametrize("bad", ["unknown", "unsafe", "contextual", "args", "id", "missing-id", "size"])
def test_entire_batch_preflight_prevents_partial_execution(bad):
    executed = []
    box = (ToolBox().add("read", lambda value: executed.append(value), parallel_safe=True)
           .add("unsafe", lambda value: executed.append(value))
           .add_contextual("contextual", lambda task, ctx, value: executed.append(value)))
    calls = requests(3 if bad == "size" else 2)
    if bad in {"unknown", "unsafe", "contextual"}:
        calls[-1] = ToolRequest("bad", bad, {"value": 2})
    elif bad == "args":
        calls[-1] = ToolRequest("bad", "read", {"wrong": 2})
    elif bad in {"id", "missing-id"}:
        calls[-1] = ToolRequest("call-0" if bad == "id" else "", "read", {"value": 2})
    ctx = RunContext()
    with pytest.raises((ValueError, TypeError)):
        pattern_for(calls, max_batch_calls=2).reason(Task("t", "test"), ctx, box)
    assert executed == [] and ctx.tool_calls == [] and ctx.tool_actions_started == 0


def test_safe_flag_is_removed_when_a_tool_is_replaced():
    box = ToolBox().add("read", lambda: None, parallel_safe=True)
    box.add("read", lambda: None)
    assert not box.is_parallel_safe("read")
    box.add("read", lambda: None, parallel_safe=True)
    box.add_contextual("read", lambda task, ctx: None)
    assert not box.is_parallel_safe("read")


def test_default_react_rejects_batches_and_task_budget_survives_reason_calls():
    box = ToolBox().add("read", lambda value: value, parallel_safe=True)
    task, ctx = Task("t", "test"), RunContext()
    with pytest.raises(ValueError, match="disabled"):
        ReActPattern(lambda t, c, b: ("batch", requests(), None)).reason(task, ctx, box)
    pattern = pattern_for(requests(), max_tool_calls=3)
    pattern.reason(task, ctx, box)
    with pytest.raises(ValueError, match="budget"):
        pattern.reason(task, ctx, box)
    assert ctx.tool_actions_started == 2 and len(ctx.tool_calls) == 2
    assert ctx.facts["stop_reason"] == "tool_call_limit"


def test_failure_is_fully_recorded_before_cleanup_and_does_not_retry_tools():
    rendezvous = Barrier(2, timeout=5)
    failed = Event()
    completed = Event()
    owner = get_ident()
    seen = []

    def read(value):
        rendezvous.wait()
        if value == 0:
            failed.set()
            raise RuntimeError("synthetic failure")
        assert failed.wait(5)
        completed.set()
        return "sibling result"

    class Policy(ZeroSideEffectPolicy):
        def on_failure(self, task, error, ctx):
            assert get_ident() == owner and completed.is_set()
            assert [c.ok for c in ctx.tool_calls] == [False, True]
            assert ctx.facts["tool_results"]["read"] == "sibling result"
            seen.append(ctx.tool_actions_started)
            return Outcome.FAILED

    chassis = (Chassis().with_payload(Source(), Criteria())
               .with_orchestrator(SingleAgentOrchestrator(ToolBox().add("read", read, parallel_safe=True),
                                                          pattern_for(requests())))
               .with_failure_policy(Policy()).build())
    try:
        result = chassis.run_once()
        assert result.outcome is Outcome.FAILED
        assert "all calls settled" in result.error and seen == [2]
    finally:
        chassis.close()


def test_connector_records_and_observers_stay_on_coordinator_and_verdict_is_independent():
    rendezvous = Barrier(2, timeout=5)
    owner = get_ident()
    recorder, evidence = RecordingObserver(), EvidenceObserver()

    class ThreadObserver(Observer):
        def on_tool_call(self, call, task, ctx):
            assert get_ident() == owner
            assert len(ctx.tool_calls) == 4

    def handler(args):
        rendezvous.wait()
        return args["value"]

    chassis = Chassis().mount("svc", "mock", handlers={"read": handler})
    box = ToolBox().add("read", lambda value: chassis.connectors.call("svc", preferred=["read"],
                                  args={"value": value}), parallel_safe=True)
    chassis.with_payload(Source(), Criteria(False)).with_orchestrator(SingleAgentOrchestrator(
        box, pattern_for(requests(), stop_when=lambda t, c: "stop")))
    chassis.observe(ThreadObserver()).observe(recorder).observe(evidence).build()
    try:
        assert chassis.run_once().outcome is Outcome.FAILED
        calls = recorder.tool_calls
        assert [c.name for c in calls] == ["svc.read", "read", "svc.read", "read"]
        assert [c.call_id for c in calls] == ["call-0/connector-1", "call-0", "call-1/connector-1", "call-1"]
        assert len({c.run_id for c in calls}) == 1
        assert len(chassis.connectors.calls) == 2
        assert evidence.runs[0]["stop_reason"] == "objective_stop"
        assert len(evidence.runs[0]["tool_calls"]) == 4
    finally:
        chassis.close()


def test_arguments_are_isolated_between_workers():
    rendezvous = Barrier(2, timeout=5)
    shared = []

    def mutate(values):
        values.append(get_ident())
        rendezvous.wait()
        return len(values)

    calls = [ToolRequest(str(i), "mutate", {"values": shared}) for i in range(2)]
    ctx = RunContext()
    pattern_for(calls).reason(Task("t", "test"), ctx, ToolBox().add("mutate", mutate, parallel_safe=True))
    assert [c.result for c in ctx.tool_calls] == [1, 1] and shared == []
    assert all(c.args == {"values": []} for c in ctx.tool_calls)


def test_parallel_declaration_does_not_grant_executor_permissions():
    from agent_chassis.orchestration.reasoning import BatchToolError

    boundary = borrowed_executor("test")
    effects = []

    def commit(value):
        boundary.check("vcs.commit")
        effects.append(value)

    box = ToolBox().add("commit", commit, parallel_safe=True)
    ctx = RunContext()
    with pytest.raises(BatchToolError):
        pattern_for(requests(name="commit")).reason(Task("t", "test"), ctx, box)
    assert effects == [] and [c.ok for c in ctx.tool_calls] == [False, False]


@pytest.mark.parametrize("queued", [False, True])
def test_partial_thread_submission_failure_still_drains_and_records_started_work(monkeypatch, queued):
    from concurrent.futures import ThreadPoolExecutor
    from agent_chassis.orchestration import reasoning

    finished = Event()

    class LimitedPool(ThreadPoolExecutor):
        submitted = 0

        def submit(self, fn, *args, **kwargs):
            self.submitted += 1
            if self.submitted == 2:
                if queued:
                    super().submit(fn, *args, **kwargs)
                raise RuntimeError("synthetic resource exhaustion")
            return super().submit(fn, *args, **kwargs)

    monkeypatch.setattr(reasoning, "ThreadPoolExecutor", LimitedPool)
    box = ToolBox().add("read", lambda value: finished.set(), parallel_safe=True)
    ctx = RunContext()
    with pytest.raises(reasoning.BatchToolError, match="submission failed"):
        pattern_for(requests()).reason(Task("t", "test"), ctx, box)
    assert finished.is_set() and len(ctx.tool_calls) == (2 if queued else 1)
    assert all(call.ok for call in ctx.tool_calls)


def test_task_retry_cannot_reset_tool_action_budget():
    ran = []
    lock = Lock()

    def read(value):
        with lock:
            ran.append(value)
        raise RuntimeError("synthetic failure")

    box = ToolBox().add("read", read, parallel_safe=True)
    recorder = RecordingObserver()
    chassis = (Chassis().with_payload(Source(), Criteria()).observe(recorder)
               .with_failure_policy(RetryThenGiveUpPolicy(max_retries=1))
               .with_orchestrator(SingleAgentOrchestrator(box, pattern_for(requests(), max_tool_calls=2))).build())
    try:
        result = chassis.run_once()
        assert result.outcome is Outcome.FAILED and "budget exhausted" in result.error
        assert sorted(ran) == [0, 1] and len(recorder.tool_calls) == 2
    finally:
        chassis.close()
