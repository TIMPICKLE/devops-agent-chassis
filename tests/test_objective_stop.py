from __future__ import annotations

import pytest

from agent_chassis import Chassis, Outcome
from agent_chassis.contracts import DoneCriteria, RunContext, Task, TaskSource, Verdict
from agent_chassis.orchestration import ReActPattern, SingleAgentOrchestrator, ToolBox
from payloads.patch_showcase import Candidate, submission_ready
from tools.run_roadmap_showcase import assemble


@pytest.mark.parametrize("flow", ["nested", "state_machine", "single_agent"])
def test_accepted_candidate_stops_without_asking_the_decider_again(flow):
    decisions = []

    def repeats(task, ctx, box):
        decisions.append(1)
        return "call", "submit_source", {"content": "def is_missing(value):\n    return value is None\n"}

    chassis, _, evidence, _ = assemble("python_quality", mode="offline", flow=flow, decider=repeats)
    try:
        assert chassis.run_once().outcome is Outcome.SUCCEEDED
        assert len(decisions) == 1
        assert evidence.runs[0]["stop_reason"] == "objective_stop"
    finally:
        chassis.close()


def test_rejected_candidate_gets_feedback_and_can_be_repaired():
    decisions = []

    def repair(task, ctx, box):
        previous = ctx.facts.get("tool_results", {}).get("submit_source")
        decisions.append(previous)
        assert previous is None or previous["accepted"] is False
        content = "def is_missing(value):\n    return True\n" if previous is None else "def is_missing(value):\n    return value is None\n"
        return "call", "submit_source", {"content": content}

    chassis, _, evidence, _ = assemble("python_quality", mode="offline", decider=repair)
    try:
        assert chassis.run_once().outcome is Outcome.SUCCEEDED
        assert len(decisions) == 2
        assert evidence.runs[0]["stop_reason"] == "objective_stop"
    finally:
        chassis.close()


def test_early_stop_does_not_replace_final_independent_verdict():
    class Source(TaskSource):
        def fetch(self, limit=1):
            return [Task("t", "test")]

    class Reject(DoneCriteria):
        calls = 0

        def judge(self, task, ctx):
            self.calls += 1
            return Verdict(False, "Final state is not acceptable")

    criteria = Reject()
    pattern = ReActPattern(lambda t, c, b: ("call", "work", {}), stop_when=lambda t, c: "stop now")
    chassis = (Chassis().with_orchestrator(SingleAgentOrchestrator(ToolBox().add("work", lambda: None), pattern))
               .with_payload(Source(), criteria).build())
    try:
        result = chassis.run_once()
        assert result.outcome is Outcome.FAILED
        assert result.error == "Final state is not acceptable" and criteria.calls == 1
    finally:
        chassis.close()


def test_stale_acceptance_does_not_stop_a_changed_candidate():
    candidate = Candidate("old", "file", "accepted")
    ctx = RunContext()
    ctx.facts["tool_results"] = {"submit_source": {"accepted": True, **candidate.digests()}}
    assert submission_ready(candidate, ctx)
    candidate.content = "changed afterwards"
    assert submission_ready(candidate, ctx) is None


def test_original_model_driven_stop_policy_remains_available():
    calls = []

    def decide(task, ctx, box):
        calls.append(1)
        return "call", "submit_source", {"content": "def is_missing(value):\n    return value is None\n"}

    chassis, _, evidence, _ = assemble("python_quality", mode="offline", decider=decide, stop_policy="model")
    try:
        assert chassis.run_once().outcome is Outcome.SUCCEEDED
        assert len(calls) == 8 and evidence.runs[0]["stop_reason"] == "iteration_limit"
    finally:
        chassis.close()
