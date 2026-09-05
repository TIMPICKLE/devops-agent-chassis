from __future__ import annotations

import pytest

from agent_chassis import Chassis, ChassisError, Outcome
from agent_chassis.contracts import DoneCriteria, Orchestrator, RunContext, Task, TaskResult, TaskSource, Verdict
from agent_chassis.failure import Ledger, RetryThenGiveUpPolicy
from agent_chassis.orchestration import (
    FnStep, NestedOrchestrator, ReActPattern, SingleAgentOrchestrator,
    StateMachineOrchestrator, Subgraph, SubgraphOrchestrator, ToolBox,
)


class Source(TaskSource):
    def __init__(self, count=1):
        self.tasks = [Task(str(i), "test") for i in range(count)]

    def fetch(self, limit=1):
        out, self.tasks = self.tasks[:limit], self.tasks[limit:]
        return out


class Criteria(DoneCriteria):
    def __init__(self, done=False):
        self.done, self.calls = done, 0

    def judge(self, task, ctx):
        self.calls += 1
        return Verdict(self.done, "objective verdict", {"checked": True})


def build(orchestrator, criteria, policy=None, count=1):
    c = Chassis().with_orchestrator(orchestrator).with_payload(Source(count), criteria)
    if policy is not None:
        c.with_failure_policy(policy)
    return c.build()


def orchestrators():
    stop = ReActPattern(lambda t, c, b: ("stop", "claimed success", None))
    return [
        StateMachineOrchestrator([]),
        SingleAgentOrchestrator(ToolBox(), stop),
        NestedOrchestrator([FnStep("work", lambda t, c: None)], ToolBox(), stop, "work"),
        SubgraphOrchestrator(Subgraph("read", []), lambda t, c: "one",
                             {"one": Subgraph("one", [])}, Subgraph("out", [])),
    ]


@pytest.mark.parametrize("orchestrator", orchestrators(), ids=lambda o: o.name)
@pytest.mark.parametrize("done", [False, True])
def test_payload_is_authoritative_and_runs_once(orchestrator, done):
    criteria = Criteria(done)
    c = build(orchestrator, criteria)
    result = c.run_once()
    assert criteria.calls == 1
    assert result.verdict.done is done
    assert result.outcome is (Outcome.SUCCEEDED if done else Outcome.FAILED)
    c.close()


def test_matching_legacy_criteria_is_not_called_twice():
    criteria = Criteria(True)
    result = build(StateMachineOrchestrator([], criteria), criteria).run_once()
    assert result.outcome is Outcome.SUCCEEDED
    assert criteria.calls == 1


def test_conflicting_legacy_criteria_is_a_build_error():
    with pytest.raises(ChassisError, match="不同的完成判据"):
        build(StateMachineOrchestrator([], Criteria(True)), Criteria(False))


def test_custom_orchestrator_cannot_bypass_payload_with_success_or_own_verdict():
    class Custom(Orchestrator):
        def run(self, task, ctx):
            return TaskResult(task, Outcome.SUCCEEDED, Verdict(True, "self report"))
    criteria = Criteria(False)
    result = build(Custom(), criteria).run_once()
    assert result.outcome is Outcome.FAILED
    assert result.verdict.reason == "objective verdict"
    assert criteria.calls == 1


def test_skip_does_not_run_final_acceptance():
    class Skip(Orchestrator):
        def run(self, task, ctx):
            return TaskResult(task, Outcome.SKIPPED)
    criteria = Criteria(False)
    result = build(Skip(), criteria).run_once()
    assert result.outcome is Outcome.SKIPPED
    assert criteria.calls == 0


def test_standalone_orchestrator_retains_optional_criteria_behavior():
    t, c = Task("one", "test"), RunContext()
    assert StateMachineOrchestrator([]).run(t, c).outcome is Outcome.SUCCEEDED
    assert StateMachineOrchestrator([], Criteria()).run(t, c).outcome is Outcome.FAILED


def test_verdict_cache_is_task_scoped_and_not_callable_outside_run():
    criteria = Criteria(True)
    c = build(StateMachineOrchestrator([]), criteria, count=2)
    assert len(c.run(2)) == 2
    assert criteria.calls == 2
    with pytest.raises(ChassisError):
        c.judge(Task("other", "test"), RunContext(chassis=c))


@pytest.mark.parametrize("done", ["true", 1, None])
def test_invalid_verdict_cannot_succeed(done):
    result = build(StateMachineOrchestrator([]), Criteria(done)).run_once()
    assert result.outcome is Outcome.FAILED
    assert "布尔" in result.error


def test_rejected_mutation_cleans_once_records_once_and_never_retries():
    class CountingLedger(Ledger):
        writes = 0

        def remember(self, *args, **kwargs):
            self.writes += 1
            super().remember(*args, **kwargs)

    ledger = CountingLedger()
    policy = RetryThenGiveUpPolicy(ledger, max_retries=3)
    state = {"user_file": "keep", "candidate": None, "attempts": 0, "cleanups": 0}

    def work(task, ctx):
        state["attempts"] += 1
        state["candidate"] = "wrong change"

    def cleanup(task, ctx):
        state["cleanups"] += 1
        state["candidate"] = None

    policy.register_cleanup("candidate", cleanup)
    result = build(StateMachineOrchestrator([FnStep("work", work)]), Criteria(), policy).run_once()
    assert result.outcome is Outcome.FAILED
    assert result.verdict.reason == "objective verdict"
    assert state == {"user_file": "keep", "candidate": None, "attempts": 1, "cleanups": 1}
    assert ledger.writes == 1
    assert result.artifacts["cleanups"] == ["candidate"]


def test_cleanup_failure_preserves_original_verdict_and_residue_detail():
    def bad_cleanup(task, ctx):
        raise RuntimeError("resource still exists")
    policy = RetryThenGiveUpPolicy()
    policy.register_cleanup("candidate", bad_cleanup)
    result = build(StateMachineOrchestrator([]), Criteria(), policy).run_once()
    assert result.error == "objective verdict"
    assert result.verdict.done is False
    assert "resource still exists" in result.artifacts["cleanups"][0]


@pytest.mark.parametrize("exceptional", [False, True])
def test_failing_ledger_does_not_repeat_cleanup_or_hide_original_failure(exceptional):
    class BrokenLedger(Ledger):
        writes = 0

        def remember(self, *args, **kwargs):
            self.writes += 1
            raise OSError("ledger unavailable")

    ledger = BrokenLedger()
    policy = RetryThenGiveUpPolicy(ledger, max_retries=0)
    cleaned = []
    policy.register_cleanup("candidate", lambda t, c: cleaned.append(t.key))

    def work(task, ctx):
        if exceptional:
            raise RuntimeError("original step failure")

    result = build(StateMachineOrchestrator([FnStep("work", work)]), Criteria(), policy).run_once()
    assert result.outcome is Outcome.FAILED
    assert result.error == ("RuntimeError: original step failure" if exceptional else "objective verdict")
    assert exceptional or result.verdict.done is False
    assert result.artifacts["failure_policy_error"] == "OSError: ledger unavailable"
    assert result.artifacts["cleanups"] == ["candidate"]
    assert len(cleaned) == ledger.writes == 1


def test_failure_policy_cannot_turn_an_unverified_exception_into_success():
    class InvalidPolicy(RetryThenGiveUpPolicy):
        def on_failure(self, task, error, ctx):
            return Outcome.SUCCEEDED

    def fail(task, ctx):
        raise RuntimeError("original failure")

    result = build(StateMachineOrchestrator([FnStep("work", fail)]), Criteria(),
                   InvalidPolicy(max_retries=0)).run_once()
    assert result.outcome is Outcome.FAILED
    assert result.error == "RuntimeError: original failure"
    assert "未验收" in result.artifacts["failure_policy_error"]
