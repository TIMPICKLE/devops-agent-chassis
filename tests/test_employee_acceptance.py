from __future__ import annotations

from agent_chassis import Chassis, InjectionPoint, Outcome, RecordingObserver, borrowed_executor
from agent_chassis.failure import Ledger, ZeroSideEffectPolicy
from agent_chassis.knowledge import StaticKnowledge
from agent_chassis.orchestration import AgentStep, FnStep, NestedOrchestrator, ReActPattern
from payloads import code_quality as quality
from payloads import pr_mention as mentions


def _assemble(name, source, criteria, toolbox, decider, steps, recorder, executor_tools):
    pattern = ReActPattern(
        decider,
        max_iterations=8,
        executor_tools=executor_tools,
    )
    return (
        Chassis(name)
        .with_orchestrator(
            NestedOrchestrator(
                steps,
                toolbox,
                pattern,
                "agent_work",
                criteria,
            )
        )
        .with_knowledge(
            StaticKnowledge(
                "acceptance baseline knowledge",
                points=[InjectionPoint.TASK_ADMITTED],
                name="acceptance-baseline",
            )
        )
        .with_failure_policy(ZeroSideEffectPolicy(Ledger()))
        .observe(recorder)
        .with_payload(source, criteria)
        .build()
    )


def _assert_observable_success(recorder: RecordingObserver) -> None:
    health = recorder.health()
    assert health.total_runs == 1
    assert health.succeeded == 1
    assert health.failed == 0
    assert health.success_rate == 100.0

    assert len(recorder.tasks) == 1
    run_id = recorder.tasks[0].run_id
    traces = recorder.replay(run_id)
    kinds = {trace.kind for trace in traces}
    assert {"task_start", "step", "tool_call", "injection", "task_end"}.issubset(kinds)
    assert recorder.tool_calls
    assert all(call.run_id == run_id for call in recorder.tool_calls)
    assert all(call.ok for call in recorder.tool_calls)


def test_code_quality_employee_can_be_assembled_run_and_deterministically_accepted():
    repo = quality.FakeRepo()
    boundary = borrowed_executor("acceptance@code-quality")
    toolbox = quality.build_toolbox(repo, boundary, seed=11)
    source = quality.ScannerTaskSource(smells=[quality.SMELLS[0]])
    criteria = quality.WorkspaceChangedCriteria(repo)
    recorder = RecordingObserver(subject="acceptance-code-quality", mode="ci")

    def prepare(task, ctx):
        repo.reset_hard()
        repo.checkout_new(f"fix/{task.key}")

    def deliver(task, ctx):
        if repo.diff():
            ctx.facts["commit"] = repo.commit(f"fix: {task.key}")
            ctx.facts["pr"] = f"PR-{task.key[-4:]}"

    chassis = _assemble(
        "acceptance-code-quality",
        source,
        criteria,
        toolbox,
        quality.make_decider(),
        [
            FnStep("workspace_setup", prepare),
            AgentStep("agent_work", lambda task, ctx: None),
            FnStep("delivery", deliver),
        ],
        recorder,
        ["apply_fix"],
    )

    try:
        report = chassis.report()
        assert report.task_source == source.describe()
        assert report.done_criteria == criteria.describe()
        assert report.delegation_points == ["agent_work"]

        result = chassis.run_once()
        assert result is not None
        assert result.outcome is Outcome.SUCCEEDED
        assert result.verdict is not None and result.verdict.done is True
        assert repo.diff(), "executor must produce an observable workspace change"
        assert repo.commits, "deterministic delivery stage must commit the accepted change"
        assert "apply_fix" in {call.name for call in recorder.tool_calls}
        _assert_observable_success(recorder)
    finally:
        chassis.close()


def test_pr_mention_employee_can_swap_payload_and_reach_external_done_criteria():
    thread = mentions.Thread()
    boundary = borrowed_executor("acceptance@pr-mention")
    toolbox = mentions.build_toolbox(thread, boundary)
    source = mentions.MentionTaskSource(mentions=[mentions.MENTIONS[0]])
    criteria = mentions.RepliedCriteria(thread)
    recorder = RecordingObserver(subject="acceptance-pr-mention", mode="ci")

    def acknowledge(task, ctx):
        ctx.facts["acked"] = True

    def close_loop(task, ctx):
        ctx.facts["replies"] = len(thread.replies_for(task.key))

    chassis = _assemble(
        "acceptance-pr-mention",
        source,
        criteria,
        toolbox,
        mentions.decide,
        [
            FnStep("workspace_setup", acknowledge),
            AgentStep("agent_work", lambda task, ctx: None),
            FnStep("delivery", close_loop),
        ],
        recorder,
        ["draft_change"],
    )

    try:
        result = chassis.run_once()
        assert result is not None
        assert result.outcome is Outcome.SUCCEEDED
        assert result.verdict is not None and result.verdict.done is True
        replies = thread.replies_for(result.task.key)
        assert len(replies) == 1, "external thread state, not model self-report, must prove completion"
        assert "post_reply" in {call.name for call in recorder.tool_calls}
        _assert_observable_success(recorder)
    finally:
        chassis.close()
