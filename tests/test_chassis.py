"""底盘契约的冒烟测试。跑得快，覆盖每个可插拔点的核心承诺。"""
from __future__ import annotations

import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from agent_chassis import (
    Chassis,
    ChassisError,
    InjectionPoint,
    Outcome,
    borrowed_executor,
)
from agent_chassis.failure import Ledger, ZeroSideEffectPolicy
from agent_chassis.integration import ConnectorManager
from agent_chassis.knowledge import SkillLibrary, SkillProvider, by_extension, by_filename_markers
from agent_chassis.observability import RecordingObserver
from agent_chassis.orchestration import (
    AgentStep,
    FnStep,
    NestedOrchestrator,
    ReActOrchestrator,
    StateMachineOrchestrator,
)
from agent_chassis.permissions import PermissionDenied

from payloads.code_quality import (
    FakeRepo,
    ScannerTaskSource,
    WorkspaceChangedCriteria,
    build_toolbox,
    make_decider,
)


# ═══════════════════════════════════════════════════════════
#  装配
# ═══════════════════════════════════════════════════════════

def test_build_fails_fast_when_incomplete():
    """装配期就该失败，而不是运行到一半才失败。"""
    with pytest.raises(ChassisError) as exc:
        Chassis("incomplete").build()
    assert "编排器" in str(exc.value)


def _make(orchestrator_factory, skill_points=(InjectionPoint.BEFORE_EXECUTOR,)):
    repo = FakeRepo()
    boundary = borrowed_executor("executor")
    box = build_toolbox(repo, boundary, seed=1)
    criteria = WorkspaceChangedCriteria(repo)
    skills = SkillLibrary(root="", rules=[
        by_extension({".cs": "backend"}),
        by_filename_markers([".ts"], ["component"], hit="ng", miss="ts"),
    ], inline={"backend": "后端规范", "ng": "组件规范", "ts": "通用规范"})
    rec = RecordingObserver()
    chassis = (
        Chassis("t")
        .with_orchestrator(orchestrator_factory(repo, box, criteria))
        .with_knowledge(SkillProvider(skills, points=list(skill_points)))
        .with_failure_policy(ZeroSideEffectPolicy(Ledger()))
        .with_boundary(boundary)
        .observe(rec)
        .with_payload(ScannerTaskSource(), criteria)
        .build()
    )
    return chassis, repo, rec, boundary


def _steps(repo, agent_fn):
    def pull(task, ctx):
        ctx.facts["smell"] = dict(task.payload)

    def prepare(task, ctx):
        repo.reset_hard()
        repo.checkout_new(f"fix/{task.key}")

    def deliver(task, ctx):
        if not repo.diff():
            raise RuntimeError("没有变更，不建 PR")
        ctx.facts["commit"] = repo.commit("fix")

    return [
        FnStep("issue_analysis", pull),
        FnStep("workspace_setup", prepare),
        AgentStep("agent_fix", agent_fn),
        FnStep("pr_creation", deliver),
    ]


# ═══════════════════════════════════════════════════════════
#  ① 编排可互换
# ═══════════════════════════════════════════════════════════

def test_orchestrators_are_interchangeable():
    """同一个载荷，三种编排形态都应跑成功。"""
    def sm(repo, box, criteria):
        def fix(task, ctx):
            box.call("apply_fix", path=task.payload["path"])
            ctx.iterations = 1
        return StateMachineOrchestrator(_steps(repo, fix), criteria)

    def react(repo, box, criteria):
        return ReActOrchestrator(box, make_decider(), criteria,
                                 executor_tools=["apply_fix"])

    def nested(repo, box, criteria):
        inner = ReActOrchestrator(box, make_decider(), None,
                                  executor_tools=["apply_fix"])
        return NestedOrchestrator(_steps(repo, lambda t, c: None), inner,
                                  "agent_fix", criteria)

    for factory in (sm, react, nested):
        chassis, *_ = _make(factory)
        result = chassis.run_once()
        assert result is not None
        assert result.outcome is Outcome.SUCCEEDED, factory.__name__


def test_delegation_points_are_declared():
    """编排器必须说清楚它在哪里把决策权交出去。"""
    def nested(repo, box, criteria):
        inner = ReActOrchestrator(box, make_decider(), None)
        return NestedOrchestrator(_steps(repo, lambda t, c: None), inner,
                                  "agent_fix", criteria)
    chassis, *_ = _make(nested)
    assert chassis.report().delegation_points == ["agent_fix"]


def test_nested_rejects_unknown_delegate_point():
    box_repo = FakeRepo()
    box = build_toolbox(box_repo, borrowed_executor("x"))
    inner = ReActOrchestrator(box, make_decider())
    with pytest.raises(ValueError):
        NestedOrchestrator(_steps(box_repo, lambda t, c: None), inner, "not_a_step")


# ═══════════════════════════════════════════════════════════
#  ② 接入层
# ═══════════════════════════════════════════════════════════

def test_connector_resolves_renamed_tool():
    """上游改了工具名，候选列表仍应命中。"""
    mgr = ConnectorManager()
    mgr.mount("scanner", "mock", handlers={"issues_search": lambda a: {"total": 7}})
    out = mgr.call("scanner", preferred=["issues", "issues.search", "issues_search"])
    assert out["total"] == 7


def test_connector_reports_available_tools_on_miss():
    """全部失配时，异常里要带可用清单，否则现场没法诊断。"""
    mgr = ConnectorManager()
    mgr.mount("scanner", "mock", handlers={"a": lambda x: 1, "b": lambda x: 2})
    with pytest.raises(LookupError) as exc:
        mgr.call("scanner", preferred=["nope"])
    assert "'a'" in str(exc.value) and "'b'" in str(exc.value)


def test_connector_calls_are_recorded():
    mgr = ConnectorManager()
    mgr.mount("s", "mock", handlers={"ping": lambda a: "pong"})
    mgr.call("s", preferred=["ping"])
    assert len(mgr.calls) == 1 and mgr.calls[0].ok


# ═══════════════════════════════════════════════════════════
#  ③ 知识注入时机
# ═══════════════════════════════════════════════════════════

def test_agent_boot_stays_empty_by_default():
    """默认配置下，决策层不该拿到任何技术栈规范。"""
    def nested(repo, box, criteria):
        inner = ReActOrchestrator(box, make_decider(), None,
                                  executor_tools=["apply_fix"])
        return NestedOrchestrator(_steps(repo, lambda t, c: None), inner,
                                  "agent_fix", criteria)
    chassis, *_ = _make(nested)
    timeline = dict(chassis.report().injection_timeline)
    assert timeline[InjectionPoint.AGENT_BOOT] == []
    assert timeline[InjectionPoint.BEFORE_EXECUTOR] == ["skills"]


def test_injection_actually_fires_before_executor():
    def nested(repo, box, criteria):
        inner = ReActOrchestrator(box, make_decider(), None,
                                  executor_tools=["apply_fix"])
        return NestedOrchestrator(_steps(repo, lambda t, c: None), inner,
                                  "agent_fix", criteria)
    chassis, _, rec, _ = _make(nested)
    chassis.run_once()
    injected = [t for t in rec.traces if t.kind == "injection"]
    assert injected, "应当至少发生一次注入"
    assert all("before_executor" in t.label for t in injected)


def test_two_level_skill_routing():
    lib = SkillLibrary(root="", rules=[
        by_extension({".cs": "backend"}),
        by_filename_markers([".ts"], ["component", "service"], hit="ng", miss="ts"),
    ], inline={"backend": "b", "ng": "n", "ts": "t"})
    assert lib.route({"path": "a/Foo.cs"}) == "backend"
    assert lib.route({"path": "a/user-list.component.ts"}) == "ng"
    assert lib.route({"path": "a/date-utils.ts"}) == "ts"


# ═══════════════════════════════════════════════════════════
#  ④ 失败契约
# ═══════════════════════════════════════════════════════════

def test_failure_leaves_nothing_behind():
    """模型说修好了但没动文件，判据不认，且不留残骸。"""
    def sm(repo, box, criteria):
        def liar(task, ctx):
            ctx.note("我已完成修复")
            ctx.iterations = 3
        return StateMachineOrchestrator(_steps(repo, liar), criteria)

    chassis, repo, _, _ = _make(sm)
    result = chassis.run_once()
    assert result.outcome is Outcome.FAILED
    assert repo.diff() == []


def test_ledger_dedupes_processed_tasks():
    def sm(repo, box, criteria):
        def fix(task, ctx):
            box.call("apply_fix", path=task.payload["path"])
        return StateMachineOrchestrator(_steps(repo, fix), criteria)

    chassis, *_ = _make(sm)
    chassis.run_once()
    chassis._source.reset()
    again = chassis.run(limit=4)
    assert len(again) == 3, "已处理的任务应被跳过"


def test_empty_ledger_is_truthy():
    """定义了 __len__ 的对象容易被 `x or default` 静默丢弃。"""
    assert bool(Ledger()) is True


# ═══════════════════════════════════════════════════════════
#  权限边界
# ═══════════════════════════════════════════════════════════

def test_borrowed_executor_cannot_commit():
    repo = FakeRepo()
    boundary = borrowed_executor("cli")
    box = build_toolbox(repo, boundary)
    box.call("apply_fix", path="a.cs")          # 有 repo.write，通过
    with pytest.raises(PermissionDenied):
        box.call("try_commit", message="x")     # 没有 vcs.commit
    assert boundary.denials == ["vcs.commit"]


# ═══════════════════════════════════════════════════════════
#  ⑤ 可观测
# ═══════════════════════════════════════════════════════════

def test_observability_model_has_no_business_concepts():
    """四张表的字段里不该出现场景专属名词。"""
    from dataclasses import fields
    from agent_chassis.observability import (
        HealthRecord, TaskRecord, ToolCallRecord, TraceRecord,
    )
    banned = {"smell", "sonarqube", "pr", "issue", "rule"}
    for model in (TaskRecord, TraceRecord, ToolCallRecord, HealthRecord):
        for f in fields(model):
            assert not any(b in f.name.lower() for b in banned), (model, f.name)
