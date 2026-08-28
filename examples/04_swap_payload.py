"""
演示 4 —— 换载荷：同一套底盘，跑一个完全不相关的场景。

载荷 ① 代码质量治理：任务来自扫描器，做完 = 工作区有变更
载荷 ② PR 评论区 @Agent：任务来自人打的字，做完 = 回帖发出去了

两者共用同一套底盘。这个脚本把两台数字员工并排跑一遍，
并打印各自的装配报告，让「换的是什么、没换的是什么」一目了然。

    python examples/04_swap_payload.py
"""
from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from agent_chassis import (
    Chassis,
    ConsoleObserver,
    InjectionPoint,
    RecordingObserver,
    borrowed_executor,
)
from agent_chassis.failure import Ledger, ZeroSideEffectPolicy
from agent_chassis.knowledge import SkillLibrary, SkillProvider, by_extension, by_filename_markers
from agent_chassis.orchestration import AgentStep, FnStep, NestedOrchestrator, ReActOrchestrator

from payloads import code_quality as p1
from payloads import pr_mention as p2

BAR = "═" * 74

SKILLS = SkillLibrary(
    root="",
    rules=[
        by_extension({".cs": "abp-net-backend"}),
        by_filename_markers([".ts", ".html"], ["component", "service", "module"],
                            hit="angular-frontend", miss="typescript-common"),
    ],
    inline={
        "abp-net-backend": "ABP 应用服务需继承 ApplicationService；异步方法优先。",
        "angular-frontend": "组件用 OnPush；订阅在 ngOnDestroy 释放。",
        "typescript-common": "禁止 any；公共函数必须有显式返回类型。",
    },
)


def shared_chassis(name: str, source, criteria, box, outer_steps, recorder):
    """底盘装配代码。两个载荷共用这一段，一字不差。"""
    boundary = borrowed_executor(f"executor@{name}")
    inner = ReActOrchestrator(box, DECIDERS[name], criteria=None, max_iterations=8,
                              executor_tools=["apply_fix", "draft_change"])
    return (
        Chassis(name)
        .with_orchestrator(NestedOrchestrator(outer_steps, inner, "agent_work", criteria))
        .with_knowledge(SkillProvider(SKILLS, points=[InjectionPoint.BEFORE_EXECUTOR]))
        .with_failure_policy(ZeroSideEffectPolicy(Ledger()))
        .with_boundary(boundary)
        .observe(ConsoleObserver(), recorder)
        .with_payload(source, criteria)
        .build()
    )


DECIDERS = {}


def build_payload_1(recorder):
    repo = p1.FakeRepo()
    boundary = borrowed_executor("executor@代码质量治理")
    box = p1.build_toolbox(repo, boundary, seed=11)
    criteria = p1.WorkspaceChangedCriteria(repo)
    DECIDERS["代码质量治理"] = p1.make_decider()

    def prepare(task, ctx):
        repo.reset_hard()
        repo.checkout_new(f"fix/{task.key}")

    def deliver(task, ctx):
        if repo.diff():
            ctx.facts["commit"] = repo.commit(f"fix: {task.key}")
            ctx.facts["pr"] = f"PR-{task.key[-4:]}"

    steps = [
        FnStep("workspace_setup", prepare),
        AgentStep("agent_work", lambda t, c: None),
        FnStep("delivery", deliver),
    ]
    return shared_chassis("代码质量治理", p1.ScannerTaskSource(), criteria, box, steps, recorder)


def build_payload_2(recorder):
    thread = p2.Thread()
    boundary = borrowed_executor("executor@PR评论区")
    box = p2.build_toolbox(thread, boundary)
    criteria = p2.RepliedCriteria(thread)
    DECIDERS["PR 评论区 @Agent"] = p2.decide

    def ack(task, ctx):
        ctx.facts["acked"] = True

    def close_loop(task, ctx):
        ctx.facts["replies"] = len(thread.replies_for(task.key))

    steps = [
        FnStep("workspace_setup", ack),
        AgentStep("agent_work", lambda t, c: None),
        FnStep("delivery", close_loop),
    ]
    return shared_chassis("PR 评论区 @Agent", p2.MentionTaskSource(), criteria, box, steps, recorder)


if __name__ == "__main__":
    rec1 = RecordingObserver(subject="载荷①", mode="cron")
    rec2 = RecordingObserver(subject="载荷②", mode="webhook")

    for label, factory, recorder, rounds in [
        ("载荷 ① 代码质量治理数字员工", build_payload_1, rec1, 2),
        ("载荷 ② PR 评论区 @Agent 编码助手", build_payload_2, rec2, 2),
    ]:
        print(f"\n{BAR}\n  {label}\n{BAR}")
        chassis = factory(recorder)
        print(chassis.report().render())
        for _ in range(rounds):
            chassis.run_once()

    print(f"\n{BAR}\n  两台数字员工的通用可观测数据\n{BAR}")
    for rec in (rec1, rec2):
        h = rec.health()
        print(f"  {h.subject:<8} mode={h.mode:<8} runs={h.total_runs} "
              f"成功={h.succeeded} 失败={h.failed} 成功率={h.success_rate}%")

    print(f"""
{BAR}
  换的：TaskSource（任务从哪来）、DoneCriteria（怎么算做完）、领域工具集
  没换：编排器、连接器管理、知识注入时机、失败契约、可观测数据模型
        以及 shared_chassis() 里那段装配代码，一字不差
{BAR}""")
