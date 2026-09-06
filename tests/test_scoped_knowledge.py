import pytest

from agent_chassis.contracts import InjectionPoint as P, RunContext, Task
from agent_chassis.knowledge import InjectionScheduler, ScopedKnowledge


def test_scopes_versions_multiple_documents_and_stage_transition():
    task = Task("t", "config", {"project": "atlas", "environment": "prod"})
    ctx = RunContext()
    ctx.facts["phase"] = "repair"
    providers = [ScopedKnowledge("local convention", name="project", version="v2",
                                task_scope={"project": "atlas"}, fact_scope={"phase": "repair"}),
                 ScopedKnowledge("production defaults", name="environment", version="v3",
                                task_scope={"environment": "prod"}, fact_scope={"phase": "repair"}),
                 ScopedKnowledge("unrelated", name="other", version="v1", task_scope={"project": "other"})]
    scheduler = InjectionScheduler(providers)
    scheduler.collect(P.BEFORE_EXECUTOR, task, ctx)
    assert len(ctx.knowledge[P.BEFORE_EXECUTOR]) == 2
    assert {i.version for i in ctx.injections} == {"v2", "v3"}
    assert "unrelated" not in ctx.context_for("model", [P.BEFORE_EXECUTOR], max_chars=1000)
    ctx.facts["phase"] = "verify"
    assert scheduler.collect(P.BEFORE_EXECUTOR, task, ctx) == ""
    assert ctx.context_for("model", [P.BEFORE_EXECUTOR], max_chars=1000) == ""


def test_missing_scope_and_whole_document_budget():
    task, ctx = Task("t", "test", {}), RunContext()
    scoped = ScopedKnowledge("text", name="doc", version="v1", task_scope={"project": "atlas"})
    assert scoped.provide(P.BEFORE_EXECUTOR, task, ctx) is None
    scheduler = InjectionScheduler([ScopedKnowledge("x" * 100, name="doc", version="v1")])
    scheduler.collect(P.BEFORE_EXECUTOR, task, ctx)
    assert ctx.context_for("model", [P.BEFORE_EXECUTOR], max_chars=10) == ""
    assert len(ctx.context_receipts[-1].omitted) == 1


def test_scope_is_copied_and_boot_is_rejected():
    scope = {"project": "atlas"}
    provider = ScopedKnowledge("text", name="doc", version="v1", task_scope=scope)
    scope["project"] = "other"
    assert provider.provide(P.BEFORE_EXECUTOR, Task("t", "x", {"project": "atlas"}), RunContext())
    with pytest.raises(ValueError):
        ScopedKnowledge("text", name="doc", version="v1", points=[P.AGENT_BOOT])
