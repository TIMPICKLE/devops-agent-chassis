"""
演示 5 —— 权限边界与失败契约：能力借来，权限不借。

底盘允许集成任何第三方 Agent 作为执行器，但集成不等于全权委托。
这个脚本演示三件事：

  1. 权限清单：给了什么，没给什么
  2. 越界调用当场被拒，且留下审计记录
  3. 失败零副作用：判据不认可时，工作区被还原，任务进去重表

    python examples/05_permissions_and_failure.py
"""
from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from agent_chassis import Chassis, ConsoleObserver, Outcome, borrowed_executor
from agent_chassis.failure import Ledger, WorkspaceGuard, ZeroSideEffectPolicy
from agent_chassis.orchestration import AgentStep, FnStep, StateMachineOrchestrator
from agent_chassis.permissions import PermissionDenied
from payloads.code_quality import (
    FakeRepo,
    ScannerTaskSource,
    WorkspaceChangedCriteria,
    build_toolbox,
)

BAR = "─" * 74


def section(title: str) -> None:
    print(f"\n{BAR}\n▌ {title}\n{BAR}")


repo = FakeRepo()
boundary = borrowed_executor("claude-code-cli")
box = build_toolbox(repo, boundary, seed=5)


# ═══════════════════════════════════════════════════════════
section("1. 借来的执行器拿到了什么权限")
print(f"  {'能力':<16}{'说明':<24}{'授予':<8}不可逆")
for key, desc, granted, irreversible in boundary.as_table():
    print(f"  {key:<16}{desc:<24}{'是' if granted else '否':<8}{'是' if irreversible else ''}")
print(f"\n  {boundary.summary()}")


# ═══════════════════════════════════════════════════════════
section("2. 越界调用：执行器想自己提交")
print("  执行器完全有能力 commit。提示词里写了「不要 commit」，")
print("  但提示词是软约束。硬约束在这里：\n")
try:
    box.call("try_commit", message="fix: 我自己提交了")
except PermissionDenied as exc:
    print(f"  PermissionDenied → {exc}")
print(f"\n  被拒记录：{boundary.denials}")
print("  这条记录会进审计。边界生效过，是有证据的。")


# ═══════════════════════════════════════════════════════════
section("3. 失败零副作用：判据不认可时会发生什么")

ledger = Ledger()
policy = ZeroSideEffectPolicy(ledger)


def discard_branch(task, ctx):
    repo.reset_hard()


policy.register_cleanup("丢弃未推送的工作分支", discard_branch)

guard = WorkspaceGuard().add("reset --hard + clean -fd", lambda ctx: repo.reset_hard())


def pull(task, ctx):
    ctx.facts["smell"] = dict(task.payload)


def prepare(task, ctx):
    repo.checkout_new(f"fix/{task.key}")


def broken_fix(task, ctx):
    """模拟执行器跑完了、也说自己修好了，但一个文件都没动。"""
    ctx.note("我已完成修复，重构了该方法并抽出了两个私有方法。")
    ctx.iterations = 4


def make_pr(task, ctx):
    if not repo.diff():
        raise RuntimeError("没有变更，不建 PR")


criteria = WorkspaceChangedCriteria(repo)
chassis = (
    Chassis("失败零副作用演示")
    .with_orchestrator(StateMachineOrchestrator([
        FnStep("issue_analysis", pull),
        FnStep("workspace_setup", prepare),
        AgentStep("agent_fix", broken_fix),
        FnStep("pr_creation", make_pr),
    ], criteria))
    .with_failure_policy(policy)
    .with_workspace_guard(guard)
    .with_boundary(boundary)
    .observe(ConsoleObserver())
    .with_payload(ScannerTaskSource(), criteria)
    .build()
)

result = chassis.run_once()

print(f"\n  模型自述：{result.task.key} → 「我已完成修复…」")
print(f"  实际裁定：{result.outcome.value}")
print(f"  裁定依据：{result.error or (result.verdict.reason if result.verdict else '')}")
print(f"  工作区残留：{repo.diff() or '无'}")
print(f"  去重账本：{ledger.stats()}")

print("""
  这一轮的结果：
    · 模型说自己修好了，判据不认
    · 没有建分支残留，没有垃圾 PR
    · 任务进了去重表，下一轮不会重复尝试
    · 被消耗的只有算力，不是人力""")


# ═══════════════════════════════════════════════════════════
section("4. 去重生效：同一个任务不会被再次拾起")
source = chassis._source
source.reset()
again = chassis.run(limit=4)
print(f"  再次拉取 4 条，实际执行 {len(again)} 条（已处理的被跳过）")
for r in again:
    print(f"    {r.task.key:<18} {r.outcome.value}")

print(f"\n{BAR}")
print("能力借来，权限不借。失败之后，系统什么也不留下。")
print(BAR)
