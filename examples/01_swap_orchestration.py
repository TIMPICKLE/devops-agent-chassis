"""
演示 1 —— 编排是两个正交的轴，不是一个下拉框。

    外层 · 流程编排 Flow            内层 · Agent 设计模式 Reasoning
    ├─ 线性状态机                    ├─ ReAct             边想边做
    ├─ 单 Agent（无外层）             ├─ Plan-and-Execute  先想清楚再做
    └─ 分层子图 + 路由                ├─ ReWOO             一次规划，不看观察
                                    └─ Reflexion         做完自省，不满意重来

这两件事经常被混在一起说成「编排形态」。它们不是一回事：
外层管**任务被推进的骨架**，内层管**在下放点内部模型怎么想**。

第一部分：固定内层 ReAct，换三种外层 → 骨架变了，思考方式没变。
第二部分：固定外层状态机，换四种内层 → 思考方式变了，骨架没变。
第三部分：把矩阵打出来，说明两轴可以任意组合。

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
    PlanExecutePattern,
    ReActPattern,
    ReWOOPattern,
    ReflexionPattern,
    SingleAgentOrchestrator,
    StateMachineOrchestrator,
    Subgraph,
    SubgraphOrchestrator,
)
from payloads.code_quality import (
    FakeRepo,
    ScannerTaskSource,
    WorkspaceChangedCriteria,
    build_toolbox,
    make_critic,
    make_decider,
    make_planner,
)

BAR = "─" * 74
EXECUTOR_TOOLS = ["apply_fix"]

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


# ═══════════════════════════════════════════════════════════
#  公共装配：载荷始终是同一份
# ═══════════════════════════════════════════════════════════

def base_chassis(label: str):
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


def outer_steps(repo: FakeRepo):
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

    return [
        FnStep("issue_analysis", pull),
        FnStep("workspace_setup", prepare),
        AgentStep("agent_fix", runner=lambda t, c: None),   # 占位，由 pattern 接管
        FnStep("pr_creation", make_pr),
        FnStep("record_keeping", record),
    ]


def run(label: str, build) -> dict:
    print(f"\n{BAR}\n▌ {label}\n{BAR}")
    chassis, repo, box, criteria = base_chassis(label)
    orch = build(chassis, repo, box, criteria)
    chassis.with_orchestrator(orch).build()
    print(f"  外层·流程   {orch.describe()}")
    print(f"  内层·模式   {getattr(orch, 'reasoning_name', '—')}")
    total = 0
    for _ in range(2):
        r = chassis.run_once()
        if r:
            total += r.iterations
    return {"label": label, "iterations": total}


# ═══════════════════════════════════════════════════════════
#  第一部分：固定内层 ReAct，换外层流程
# ═══════════════════════════════════════════════════════════

def flow_state_machine(chassis, repo, box, criteria, make_pattern=None):
    """外层线性状态机，agent_fix 那一步跑内层模式。"""
    make_pattern = make_pattern or p_react
    return NestedOrchestrator(
        outer_steps=outer_steps(repo),
        toolbox=box,
        pattern=make_pattern(repo, box),
        delegate_at="agent_fix",
        criteria=criteria,
    )


def flow_single_agent(chassis, repo, box, criteria, make_pattern=None):
    """没有外层骨架，整个任务交给内层模式。"""
    make_pattern = make_pattern or p_react
    repo.checkout_new("fix/single-agent")
    return SingleAgentOrchestrator(
        toolbox=box,
        pattern=make_pattern(repo, box),
        criteria=criteria,
    )


def flow_subgraph(chassis, repo, box, criteria, make_pattern=None):
    """分层子图：两条支路可以挂不同的推理模式。

    这是两轴分离最直接的好处 —— 简单支路用 ReWOO 省 token，
    复杂支路用 Reflexion 包 ReAct 换准确率。同一个外层骨架，
    不同支路的思考方式可以不一样。
    """
    def pull(task, ctx):
        ctx.facts["smell"] = dict(task.payload)

    def classify(task, ctx):
        ctx.facts["difficulty"] = "high" if "S3776" in task.payload["rule"] else "low"

    def prepare(task, ctx):
        repo.reset_hard()
        repo.checkout_new(f"fix/{task.key}")

    def deliver(task, ctx):
        ctx.facts["commit"] = repo.commit(f"fix: {task.key}")
        ctx.facts["pr"] = f"PR-{task.key[-4:]}"

    if make_pattern is None:
        light = ReWOOPattern(make_planner(), executor_tools=EXECUTOR_TOOLS)
        deep = ReflexionPattern(
            inner=ReActPattern(make_decider(), max_iterations=8,
                               executor_tools=EXECUTOR_TOOLS),
            critic=make_critic(repo),
            max_attempts=2,
        )
    else:
        light, deep = make_pattern(repo, box), make_pattern(repo, box)

    return SubgraphOrchestrator(
        analysis=Subgraph("分析", [FnStep("pull", pull), FnStep("classify", classify)]),
        router=lambda t, c: "deep" if c.facts["difficulty"] == "high" else "light",
        branches={
            "light": Subgraph("轻量修复", [
                FnStep("prepare", prepare),
                AgentStep("fix", pattern=light, toolbox=box),
            ]),
            "deep": Subgraph("深度修复", [
                FnStep("prepare", prepare),
                AgentStep("fix", pattern=deep, toolbox=box),
            ]),
        },
        delivery=Subgraph("交付", [FnStep("deliver", deliver)]),
        criteria=criteria,
        interrupt_if=lambda t, c: c.facts.get("difficulty") == "high",
        on_interrupt=lambda t, c: True,   # 演示里人工直接放行
    )


# ═══════════════════════════════════════════════════════════
#  第二部分：固定外层状态机，换内层 Agent 设计模式
# ═══════════════════════════════════════════════════════════

def with_pattern(make_pattern):
    """外层永远是同一个五阶段状态机，只换内层模式。"""
    def build(chassis, repo, box, criteria):
        return flow_state_machine(chassis, repo, box, criteria, make_pattern)
    return build


def p_react(repo, box):
    return ReActPattern(make_decider(), max_iterations=8, executor_tools=EXECUTOR_TOOLS)


def p_plan_execute(repo, box):
    return PlanExecutePattern(make_planner(), executor_tools=EXECUTOR_TOOLS)


def p_rewoo(repo, box):
    return ReWOOPattern(make_planner(), executor_tools=EXECUTOR_TOOLS)


def p_reflexion(repo, box):
    return ReflexionPattern(
        inner=ReActPattern(make_decider(), max_iterations=8,
                           executor_tools=EXECUTOR_TOOLS),
        critic=make_critic(repo),
        max_attempts=2,
    )


# ═══════════════════════════════════════════════════════════
#  第三部分：真的把矩阵跑一遍
# ═══════════════════════════════════════════════════════════

FLOWS = [
    ("线性状态机", flow_state_machine),
    ("单 Agent", flow_single_agent),
    ("分层子图", flow_subgraph),
]
PATTERNS = [
    ("ReAct", p_react),
    ("Plan-Exec", p_plan_execute),
    ("ReWOO", p_rewoo),
    ("Reflexion", p_reflexion),
]


def probe(flow_build, make_pattern) -> str:
    """静默跑一遍，返回模型调用次数。不是打勾，是真跑。"""
    repo = FakeRepo()
    boundary = borrowed_executor("claude-code-cli")
    box = build_toolbox(repo, boundary, seed=7)
    chassis = (
        Chassis("probe")
        .with_knowledge(SkillProvider(SKILLS, points=[InjectionPoint.BEFORE_EXECUTOR]))
        .with_boundary(boundary)
        .with_payload(ScannerTaskSource(), WorkspaceChangedCriteria(repo))
    )
    try:
        orch = flow_build(chassis, repo, box, WorkspaceChangedCriteria(repo), make_pattern)
        chassis.with_orchestrator(orch).build()
        return str(sum(r.iterations for r in chassis.run(limit=2)))
    except Exception as exc:                      # 跑不通就如实说跑不通
        return f"!{type(exc).__name__}"


# ═══════════════════════════════════════════════════════════

if __name__ == "__main__":
    print("编排不是一个下拉框，是两个正交的轴。")

    print(f"\n\n{'═' * 74}")
    print("第一部分 · 固定内层 ReAct，换外层流程 —— 骨架变了，思考方式没变")
    print("═" * 74)
    run("外层① 线性状态机 × 内层 ReAct（生产形态）", flow_state_machine)
    run("外层② 单 Agent（无外层骨架）× 内层 ReAct", flow_single_agent)
    run("外层③ 分层子图 + 路由 × 支路各挂各的模式（下一代）", flow_subgraph)

    print(f"\n\n{'═' * 74}")
    print("第二部分 · 固定外层状态机，换内层 Agent 设计模式 —— 骨架没变，思考方式变了")
    print("═" * 74)
    run("内层① ReAct —— 边想边做，收敛点由模型判断", with_pattern(p_react))
    run("内层② Plan-and-Execute —— 先出完整计划，计划可被审查", with_pattern(p_plan_execute))
    run("内层③ ReWOO —— 一次规划，执行期间不再问模型", with_pattern(p_rewoo))
    run("内层④ Reflexion(ReAct) —— 跑完自省，不满意带着教训重来", with_pattern(p_reflexion))

    print(f"\n\n{'═' * 74}")
    print("第三部分 · 3 × 4 全矩阵，每一格都真的跑了一遍")
    print("═" * 74)
    print("格子里是两个任务合计的模型调用次数，越少越省 token。")
    print()
    header = f"{'外层 \\ 内层':<16}" + "".join(f"{p:<13}" for p, _ in PATTERNS)
    print(header)
    print("─" * len(header))
    for flow_name, flow_build in FLOWS:
        cells = [probe(flow_build, mk) for _, mk in PATTERNS]
        print(f"{flow_name:<14}" + "".join(f"{c:<13}" for c in cells))

    print(f"\n{BAR}")
    print("载荷代码一行没动。变的只有 with_orchestrator() 里的两个参数：")
    print("  外层给谁做骨架，内层给谁做思考。底盘不替业务选，只保证能换。")
    print(BAR)
