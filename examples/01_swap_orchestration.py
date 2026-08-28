"""
演示 1 —— 换编排方式，载荷代码一行不动。

同一个「代码质量治理」载荷，依次用四种编排形态跑一遍：

    state_machine   线性状态机，AI 只在一个阶段里
    react           单层 ReAct，全程模型驱动
    nested          外层状态机 + 内层 ReAct（生产实际形态）
    subgraph        分层子图 + 路由 + 人工介入（下一代设计）

任务源、完成判据、领域工具集始终是同一份。变的只有编排器。

    python examples/01_swap_orchestration.py
"""
from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from agent_chassis import Chassis, ConsoleObserver, InjectionPoint, borrowed_executor
from agent_chassis.knowledge import SkillLibrary, SkillProvider, by_extension, by_filename_markers
from agent_chassis.orchestration import (
    AgentStep,
    FnStep,
    NestedOrchestrator,
    ReActOrchestrator,
    StateMachineOrchestrator,
    Subgraph,
    SubgraphOrchestrator,
)
from payloads.code_quality import (
    FakeRepo,
    ScannerTaskSource,
    WorkspaceChangedCriteria,
    build_toolbox,
    make_decider,
)

BAR = "─" * 74

SKILLS = SkillLibrary(
    root="",
    rules=[
        by_extension({".cs": "abp-net-backend"}),
        by_filename_markers(
            extensions=[".ts", ".html", ".scss"],
            markers=["component", "service", "module", "directive", "pipe", "guard"],
            hit="angular-frontend",
            miss="typescript-common",
        ),
    ],
    inline={
        "abp-net-backend": "ABP 应用服务需继承 ApplicationService，仓储用 IRepository<T,TKey>，异步方法优先。",
        "angular-frontend": "组件用 OnPush 变更检测，订阅在 ngOnDestroy 中释放，模板不写业务逻辑。",
        "typescript-common": "禁止 any，公共函数必须有显式返回类型，字符串字面量重复三次以上抽常量。",
    },
)


def deterministic_steps(repo: FakeRepo, agent_runner=None):
    """外层五阶段。只有 agent_fix 一个阶段把决策权交出去。"""
    def pull(task, ctx):
        ctx.facts["smell"] = dict(task.payload)

    def prepare(task, ctx):
        repo.reset_hard()
        repo.checkout_new(f"fix/{task.key}")
        ctx.facts["branch"] = repo.branch

    def make_pr(task, ctx):
        if not repo.diff():
            raise RuntimeError("没有变更，不建 PR")
        ctx.facts["commit"] = repo.commit(f"fix: {task.key}")
        ctx.facts["pr"] = f"PR-{task.key[-4:]}"

    def record(task, ctx):
        ctx.facts["recorded"] = True

    steps = [
        FnStep("issue_analysis", pull),
        FnStep("workspace_setup", prepare),
    ]
    if agent_runner is not None:
        steps.append(AgentStep("agent_fix", agent_runner))
    steps += [FnStep("pr_creation", make_pr), FnStep("record_keeping", record)]
    return steps


def base_chassis(label: str) -> tuple:
    repo = FakeRepo()
    boundary = borrowed_executor("claude-code-cli")
    box = build_toolbox(repo, boundary, seed=7)
    source = ScannerTaskSource()
    criteria = WorkspaceChangedCriteria(repo)
    chassis = (
        Chassis(label)
        .with_knowledge(SkillProvider(SKILLS, points=[InjectionPoint.BEFORE_EXECUTOR]))
        .with_boundary(boundary)
        .observe(ConsoleObserver())
        .with_payload(source, criteria)
    )
    return chassis, repo, box, criteria


def run(label: str, make_orchestrator) -> None:
    print(f"\n{BAR}\n▌ {label}\n{BAR}")
    chassis, repo, box, criteria = base_chassis(label)
    chassis.with_orchestrator(make_orchestrator(chassis, repo, box, criteria)).build()
    print(f"  下放点：{', '.join(chassis._orchestrator.delegation_points) or '无'}")
    for _ in range(2):
        chassis.run_once()


# ── 形态一：线性状态机 ─────────────────────────────────
def as_state_machine(chassis, repo, box, criteria):
    def fix(task, ctx):
        chassis.inject(InjectionPoint.BEFORE_EXECUTOR, task, ctx)
        box.call("apply_fix", path=task.payload["path"], note="固定策略")
        ctx.iterations = 1
    return StateMachineOrchestrator(deterministic_steps(repo, fix), criteria)


# ── 形态二：单层 ReAct ────────────────────────────────
def as_react(chassis, repo, box, criteria):
    repo.checkout_new("fix/react")
    return ReActOrchestrator(box, make_decider(), criteria,
                             max_iterations=8, executor_tools=["apply_fix"])


# ── 形态三：嵌套（生产实际形态）────────────────────
def as_nested(chassis, repo, box, criteria):
    inner = ReActOrchestrator(box, make_decider(), criteria=None,
                              max_iterations=8, executor_tools=["apply_fix"])
    return NestedOrchestrator(
        outer_steps=deterministic_steps(repo, lambda t, c: None),
        inner=inner,
        delegate_at="agent_fix",
        criteria=criteria,
    )


# ── 形态四：分层子图（下一代设计）──────────────────────
def as_subgraph(chassis, repo, box, criteria):
    def pull(task, ctx):
        ctx.facts["smell"] = dict(task.payload)

    def classify(task, ctx):
        r = task.payload["rule"]
        ctx.facts["difficulty"] = "high" if "S3776" in r else "low"

    def prepare(task, ctx):
        repo.reset_hard()
        repo.checkout_new(f"fix/{task.key}")

    def light_fix(task, ctx):
        chassis.inject(InjectionPoint.BEFORE_EXECUTOR, task, ctx)
        box.call("apply_fix", path=task.payload["path"], note="轻量支路")
        ctx.iterations = 1

    def deep_fix(task, ctx):
        chassis.inject(InjectionPoint.BEFORE_EXECUTOR, task, ctx)
        box.call("read_full_file", path=task.payload["path"])
        box.call("analyze_complexity", path=task.payload["path"], line=task.payload["line"])
        box.call("apply_fix", path=task.payload["path"], note="深度支路")
        ctx.iterations = 3

    def deliver(task, ctx):
        ctx.facts["commit"] = repo.commit(f"fix: {task.key}")
        ctx.facts["pr"] = f"PR-{task.key[-4:]}"

    return SubgraphOrchestrator(
        analysis=Subgraph("分析", [FnStep("pull", pull), FnStep("classify", classify)]),
        router=lambda t, c: "deep" if c.facts["difficulty"] == "high" else "light",
        branches={
            "light": Subgraph("轻量修复", [FnStep("prepare", prepare),
                                       AgentStep("light_fix", light_fix)]),
            "deep": Subgraph("深度修复", [FnStep("prepare", prepare),
                                      AgentStep("deep_fix", deep_fix)]),
        },
        delivery=Subgraph("交付", [FnStep("deliver", deliver)]),
        criteria=criteria,
        interrupt_if=lambda t, c: c.facts.get("difficulty") == "high",
        on_interrupt=lambda t, c: True,   # 演示里人工直接放行
    )


if __name__ == "__main__":
    print("同一个载荷，四种编排形态。任务源、完成判据、工具集始终不变。")
    run("① 线性状态机", as_state_machine)
    run("② 单层 ReAct 循环", as_react)
    run("③ 嵌套：外状态机 + 内 ReAct（生产形态）", as_nested)
    run("④ 分层子图 + 路由 + 人工介入（下一代）", as_subgraph)
    print(f"\n{BAR}")
    print("载荷代码一行没动。换的只是 with_orchestrator() 的那个参数。")
    print(BAR)
