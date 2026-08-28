"""
演示 3 —— 知识注入时机可视化。

底盘对知识层的主张是：**时机比内容更重要**。

同一份 ABP 规范，注入在决策层的 system prompt 里，Agent 就变成了
「懂 ABP 的 Agent」，换技术栈要改 Agent；注入在调用执行器之前的
最后一步，Agent 始终是「不知道 ABP 是什么的 Agent」，换技术栈只改
markdown 文件。

本演示打印三样东西：
  1. 六个注入时机，各自挂了哪些 provider
  2. 一次真实执行里，注入实际发生的顺序与位置
  3. 把同一个 provider 挪到 AGENT_BOOT 之后，耦合是怎么发生的

    python examples/03_injection_timing.py
"""
from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from agent_chassis import Chassis, ConsoleObserver, InjectionPoint, borrowed_executor
from agent_chassis.knowledge import (
    RetryFeedback,
    SkillLibrary,
    SkillProvider,
    StaticKnowledge,
    by_extension,
    by_filename_markers,
)
from agent_chassis.orchestration import AgentStep, FnStep, NestedOrchestrator, ReActOrchestrator
from payloads.code_quality import (
    FakeRepo,
    ScannerTaskSource,
    WorkspaceChangedCriteria,
    build_toolbox,
    make_decider,
)

BAR = "─" * 74


def section(title: str) -> None:
    print(f"\n{BAR}\n▌ {title}\n{BAR}")


SKILLS = SkillLibrary(
    root="",
    rules=[
        by_extension({".cs": "abp-net-backend"}),
        by_filename_markers(
            extensions=[".ts", ".html"],
            markers=["component", "service", "module", "directive", "pipe", "guard"],
            hit="angular-frontend",
            miss="typescript-common",
        ),
    ],
    inline={
        "abp-net-backend": "ABP 应用服务需继承 ApplicationService；仓储用 IRepository<T,TKey>；异步方法优先。",
        "angular-frontend": "组件用 OnPush；订阅在 ngOnDestroy 释放；模板不写业务逻辑。",
        "typescript-common": "禁止 any；公共函数必须有显式返回类型；字面量重复三次以上抽常量。",
    },
)


def build(skill_points, extra=()):
    repo = FakeRepo()
    boundary = borrowed_executor("claude-code-cli")
    box = build_toolbox(repo, boundary, seed=3)
    criteria = WorkspaceChangedCriteria(repo)

    def pull(task, ctx):
        ctx.facts["smell"] = dict(task.payload)

    def prepare(task, ctx):
        repo.reset_hard()
        repo.checkout_new(f"fix/{task.key}")

    def deliver(task, ctx):
        if repo.diff():
            ctx.facts["commit"] = repo.commit(f"fix: {task.key}")

    chassis = Chassis("知识注入时机演示")
    inner = ReActOrchestrator(box, make_decider(), criteria=None,
                              max_iterations=8, executor_tools=["apply_fix"])
    orch = NestedOrchestrator(
        outer_steps=[
            FnStep("issue_analysis", pull),
            FnStep("workspace_setup", prepare),
            AgentStep("agent_fix", lambda t, c: None),
            FnStep("pr_creation", deliver),
        ],
        inner=inner,
        delegate_at="agent_fix",
        criteria=criteria,
    )
    return (
        chassis
        .with_orchestrator(orch)
        .with_knowledge(
            SkillProvider(SKILLS, points=list(skill_points)),
            StaticKnowledge(
                "本轮只处理 CRITICAL 级别，不处理 Bug 与安全漏洞。",
                points=[InjectionPoint.TASK_ADMITTED],
                name="scope-note",
            ),
            RetryFeedback(),
            *extra,
        )
        .with_boundary(boundary)
        .observe(ConsoleObserver())
        .with_payload(ScannerTaskSource(), criteria)
        .build()
    )


# ═══════════════════════════════════════════════════════════
#  1. 六个时机的静态视图
# ═══════════════════════════════════════════════════════════
section("1. 六个注入时机，各自挂了谁")

good = build(skill_points=[InjectionPoint.BEFORE_EXECUTOR])
for point, providers in good._scheduler.timeline():
    mark = ", ".join(providers) if providers else "—"
    note = ""
    if point is InjectionPoint.AGENT_BOOT:
        note = "  ← 决策层。刻意留空，Agent 不该知道 ABP 是什么"
    if point is InjectionPoint.BEFORE_EXECUTOR:
        note = "  ← 生产实际用的点。最后一公里才注入"
    print(f"  {point.value:<18} {mark}{note}")


# ═══════════════════════════════════════════════════════════
#  2. 一次真实执行里，注入发生在哪
# ═══════════════════════════════════════════════════════════
section("2. 跑两条任务，看注入实际落在哪一步")
print("  第一条是 .cs 文件，第二条也是；第三条起是前端文件，路由会换 skill\n")

for _ in range(3):
    good.run_once()


# ═══════════════════════════════════════════════════════════
#  3. 两级路由的效果
# ═══════════════════════════════════════════════════════════
section("3. 两级路由：同是 .ts，拿到的规范不同")
for path in [
    "src/Application/Charge/ChargeAppService.cs",
    "src/app/patient/user-list.component.ts",
    "src/app/shared/date-utils.ts",
]:
    skill = SKILLS.route({"path": path})
    print(f"  {os.path.basename(path):<32} → {skill}")


# ═══════════════════════════════════════════════════════════
#  4. 把 skill 挪到 AGENT_BOOT，耦合就发生了
# ═══════════════════════════════════════════════════════════
section("4. 反例：把同一个 provider 挪到 AGENT_BOOT")

bad = build(skill_points=[InjectionPoint.AGENT_BOOT])
for point, providers in bad._scheduler.timeline():
    if point in (InjectionPoint.AGENT_BOOT, InjectionPoint.BEFORE_EXECUTOR):
        mark = ", ".join(providers) if providers else "—"
        flag = "  ← 决策层现在知道 ABP 了" if providers and point is InjectionPoint.AGENT_BOOT else ""
        print(f"  {point.value:<18} {mark}{flag}")

print("""
  一个字符的改动，后果是：
    · Agent 的 system prompt 里出现了具体技术栈
    · 换技术栈要改 Agent，不是改 markdown
    · 决策层上下文被规范文档挤占
  底盘不禁止你这么做，但它把这个选择变成了一行显式代码，而不是隐式约定。""")

print(f"\n{BAR}")
print("时机是可配置的一等公民，不是藏在 prompt 拼接代码里的实现细节。")
print(BAR)
