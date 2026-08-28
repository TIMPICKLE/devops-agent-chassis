from __future__ import annotations

from agent_chassis import Chassis, Outcome
from agent_chassis.contracts import DoneCriteria, RunContext, Task, TaskSource, Verdict
from agent_chassis.failure import Ledger, RetryThenGiveUpPolicy, WorkspaceGuard
from agent_chassis.knowledge import RetryFeedback
from agent_chassis.observability import RecordingObserver
from agent_chassis.orchestration import FnStep, StateMachineOrchestrator


class OneTaskSource(TaskSource):
    name = "one-task"

    def __init__(self) -> None:
        self._done = False

    def fetch(self, limit: int = 1):
        if self._done or limit <= 0:
            return []
        self._done = True
        return [Task(key="retry-1", kind="test")]


class DoneFactCriteria(DoneCriteria):
    name = "done fact"

    def judge(self, task: Task, ctx: RunContext) -> Verdict:
        return Verdict(bool(ctx.facts.get("done")), "done fact")


def _build(step, policy, guard, rec):
    criteria = DoneFactCriteria()
    return (
        Chassis("retry-test")
        .with_orchestrator(StateMachineOrchestrator([FnStep("work", step)], criteria))
        .with_failure_policy(policy)
        .with_workspace_guard(guard)
        .with_knowledge(RetryFeedback())
        .observe(rec)
        .with_payload(OneTaskSource(), criteria)
        .build()
    )


def test_retry_policy_retries_exception_then_succeeds_with_clean_attempt_state():
    attempts = 0
    guard_runs = 0
    cleanup_runs = 0

    ledger = Ledger()
    policy = RetryThenGiveUpPolicy(ledger, max_retries=1)

    def cleanup(task, ctx):
        nonlocal cleanup_runs
        cleanup_runs += 1

    policy.register_cleanup("cleanup", cleanup)

    def guard_action(ctx):
        nonlocal guard_runs
        guard_runs += 1

    guard = WorkspaceGuard().add("prepare", guard_action)
    rec = RecordingObserver()

    def flaky(task, ctx):
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            ctx.facts["tool_results"] = {"stale": {"ok": False}}
            raise RuntimeError("transient")

        assert ctx.facts["retries"] == 1
        assert ctx.facts["last_error"] == "RuntimeError: transient"
        assert "tool_results" not in ctx.facts
        ctx.facts["done"] = True

    result = _build(flaky, policy, guard, rec).run_once()

    assert result is not None
    assert result.outcome is Outcome.SUCCEEDED
    assert attempts == 2
    assert guard_runs == 2, "初次尝试和重试前都应准备工作区"
    assert cleanup_runs == 1, "失败尝试应先补偿，再开始重试"
    assert ledger.stats() == {"succeeded": 1, "total": 1}

    retry_injections = [
        t for t in rec.traces
        if t.kind == "injection" and "on_retry" in t.label
    ]
    assert len(retry_injections) == 1


def test_retry_policy_gives_up_after_limit_and_records_failure_once():
    attempts = 0
    guard_runs = 0
    cleanup_runs = 0

    ledger = Ledger()
    policy = RetryThenGiveUpPolicy(ledger, max_retries=2)

    def cleanup(task, ctx):
        nonlocal cleanup_runs
        cleanup_runs += 1

    policy.register_cleanup("cleanup", cleanup)

    def guard_action(ctx):
        nonlocal guard_runs
        guard_runs += 1

    guard = WorkspaceGuard().add("prepare", guard_action)
    rec = RecordingObserver()

    def always_fails(task, ctx):
        nonlocal attempts
        attempts += 1
        raise RuntimeError(f"boom-{attempts}")

    result = _build(always_fails, policy, guard, rec).run_once()

    assert result is not None
    assert result.outcome is Outcome.FAILED
    assert attempts == 3, "max_retries=2 应为首次尝试 + 2 次重试"
    assert guard_runs == 3
    assert cleanup_runs == 3, "每个失败尝试都应补偿，包括最终失败"
    assert ledger.stats() == {"failed": 1, "total": 1}

    retry_injections = [
        t for t in rec.traces
        if t.kind == "injection" and "on_retry" in t.label
    ]
    assert len(retry_injections) == 2


def test_retry_policy_does_not_retry_deterministic_verdict_failure():
    attempts = 0
    policy = RetryThenGiveUpPolicy(Ledger(), max_retries=3)
    rec = RecordingObserver()

    def completes_without_done_fact(task, ctx):
        nonlocal attempts
        attempts += 1

    result = _build(
        completes_without_done_fact,
        policy,
        WorkspaceGuard(),
        rec,
    ).run_once()

    assert result is not None
    assert result.outcome is Outcome.FAILED
    assert attempts == 1, "DoneCriteria 的确定性否决不应被当作 transient exception 重试"
    assert not [
        t for t in rec.traces
        if t.kind == "injection" and "on_retry" in t.label
    ]
