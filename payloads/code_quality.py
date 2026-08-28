"""
载荷 ① —— 代码质量治理数字员工。

载荷只需要提供三样东西：
  1. TaskSource     任务从哪来  →  静态扫描器的未解决异味
  2. DoneCriteria   怎么算做完  →  工作区产生了实际文件变更
  3. 领域工具集      Agent 能调什么

底盘的五大系统一行不用改。

本文件用内存 fixture 模拟外部系统，因此可以完全离线运行。
把 MockConnector 换成 mcp.stdio 连接器就是生产形态。
"""
from __future__ import annotations

import random
from typing import Any, Dict, List, Optional

from agent_chassis import (
    DoneCriteria,
    RunContext,
    Task,
    TaskSource,
    Verdict,
)
from agent_chassis.orchestration import PlanNode, ToolBox
from agent_chassis.permissions import PermissionBoundary, PermissionDenied


# ═══════════════════════════════════════════════════════════
#  外部系统的离线替身
# ═══════════════════════════════════════════════════════════

SMELLS: List[Dict[str, Any]] = [
    {
        "key": "AZq-S3776-0041",
        "rule": "csharpsquid:S3776",
        "message": "方法认知复杂度 31，超过阈值 15",
        "path": "src/Domain/Appointment/TreatmentAppointmentManager.cs",
        "line": 214,
        "effort_min": 45,
        "severity": "CRITICAL",
    },
    {
        "key": "AZq-S4487-0112",
        "rule": "csharpsquid:S4487",
        "message": "私有字段 _cachedPolicy 从未被读取",
        "path": "src/Application/Charge/ChargeAppService.cs",
        "line": 58,
        "effort_min": 5,
        "severity": "MAJOR",
    },
    {
        "key": "AZq-NG-0233",
        "rule": "typescript:S1854",
        "message": "赋值后未被使用的局部变量",
        "path": "src/app/patient/user-list.component.ts",
        "line": 77,
        "effort_min": 5,
        "severity": "MAJOR",
    },
    {
        "key": "AZq-TS-0301",
        "rule": "typescript:S1192",
        "message": "字符串字面量重复 5 次",
        "path": "src/app/shared/date-utils.ts",
        "line": 12,
        "effort_min": 10,
        "severity": "MINOR",
    },
]


class FakeRepo:
    """代码仓的离线替身。它的唯一职责是让「完成判据」有真实事实可读。"""

    def __init__(self) -> None:
        self.branch = "master"
        self.modified: List[str] = []
        self.commits: List[str] = []
        self.pushed = False

    def reset_hard(self) -> None:
        self.modified.clear()
        self.branch = "master"
        self.pushed = False

    def checkout_new(self, name: str) -> None:
        self.branch = name

    def write(self, path: str) -> None:
        if path not in self.modified:
            self.modified.append(path)

    def diff(self) -> List[str]:
        return list(self.modified)

    def commit(self, message: str) -> str:
        if not self.modified:
            raise RuntimeError("没有变更可提交")
        self.commits.append(message)
        return f"sha-{len(self.commits):04d}"


# ═══════════════════════════════════════════════════════════
#  ① 任务源
# ═══════════════════════════════════════════════════════════

class ScannerTaskSource(TaskSource):
    """未解决的静态扫描异味。"""

    name = "静态扫描器 · 未解决异味"

    def __init__(self, smells: Optional[List[Dict[str, Any]]] = None) -> None:
        self.smells = list(smells if smells is not None else SMELLS)
        self._cursor = 0

    def fetch(self, limit: int = 1) -> List[Task]:
        out: List[Task] = []
        while self._cursor < len(self.smells) and len(out) < limit:
            s = self.smells[self._cursor]
            self._cursor += 1
            out.append(Task(
                key=s["key"],
                kind="code_smell",
                source=self.name,
                payload={
                    "rule": s["rule"],
                    "message": s["message"],
                    "path": s["path"],
                    "line": s["line"],
                    "effort_min": s["effort_min"],
                    "severity": s["severity"],
                    # SkillProvider 按这里的 path 做两级路由
                    "target": {"path": s["path"]},
                },
            ))
        return out

    def reset(self) -> None:
        self._cursor = 0


# ═══════════════════════════════════════════════════════════
#  ② 完成判据
# ═══════════════════════════════════════════════════════════

class WorkspaceChangedCriteria(DoneCriteria):
    """工作区必须产生实际文件变更，才算这一轮做完了。

    这是整套底盘里最不能让模型插手的地方。判据只读 ctx.facts 里由
    确定性代码采集的事实，不读 ctx.model_notes。模型可以幻觉出一段
    「我已完成修复」的总结，但它过不了这一关。
    """

    name = "工作区产生实际变更（读 VCS 状态）"

    def __init__(self, repo: FakeRepo) -> None:
        self.repo = repo

    def judge(self, task: Task, ctx: RunContext) -> Verdict:
        changed = self.repo.diff()
        if not changed:
            return Verdict(
                done=False,
                reason="未检测到文件变更",
                evidence={"diff": [], "model_said": ctx.model_notes[-1:] or ["（无）"]},
            )
        return Verdict(
            done=True,
            reason=f"检测到 {len(changed)} 个文件变更",
            evidence={"diff": changed},
        )


# ═══════════════════════════════════════════════════════════
#  ③ 领域工具集
# ═══════════════════════════════════════════════════════════

def build_toolbox(
    repo: FakeRepo,
    boundary: PermissionBoundary,
    fail_rate: float = 0.0,
    seed: Optional[int] = None,
) -> ToolBox:
    """构造给模型选的工具集。

    注意 `apply_fix` 里的权限检查：执行器有 repo.write，没有 vcs.commit。
    它想顺手提交会当场被拒。这不是提示词里的软约束，是会抛异常的硬约束。
    """
    rng = random.Random(seed)
    box = ToolBox()

    def analyze_smell_type(rule: str = "", message: str = "") -> Dict[str, Any]:
        """判断异味类型与难度，用于决定要不要继续取证。"""
        table = {
            "S3776": ("complexity", "high"),
            "S4487": ("unused", "low"),
            "S1854": ("unused", "low"),
            "S1192": ("duplication", "medium"),
        }
        for token, (kind, level) in table.items():
            if token in rule:
                return {"type": kind, "difficulty": level}
        return {"type": "unknown", "difficulty": "medium"}

    def read_source(path: str = "", line: int = 0, radius: int = 15) -> Dict[str, Any]:
        """读取指定行号附近的代码片段。"""
        return {"path": path, "from": max(1, line - radius), "to": line + radius, "lines": radius * 2}

    def read_full_file(path: str = "") -> Dict[str, Any]:
        """读取整个文件，用于需要理解全局的重构。"""
        return {"path": path, "total_lines": 420}

    def search(pattern: str = "", scope: str = "src") -> Dict[str, Any]:
        """在代码库中检索相似模式。"""
        return {"pattern": pattern, "matches": 3}

    def analyze_complexity(path: str = "", line: int = 0) -> Dict[str, Any]:
        """评估改动影响面。"""
        return {"nesting_depth": 5, "method_lines": 82, "suggestion": "deep_analysis"}

    def apply_fix(path: str = "", note: str = "") -> Dict[str, Any]:
        """调用外部执行器完成跨文件修复。执行器只有文件系统写权限。"""
        boundary.check("repo.read")
        boundary.check("repo.write")
        if rng.random() < fail_rate:
            return {"ok": False, "reason": "执行器未产生任何变更"}
        repo.write(path)
        if "component" in path:
            repo.write(path.replace(".ts", ".html"))
        return {"ok": True, "files": repo.diff()}

    def try_commit(message: str = "") -> Dict[str, Any]:
        """执行器尝试自行提交。这个工具存在只是为了演示边界会拦住它。"""
        boundary.check("vcs.commit")
        return {"sha": repo.commit(message)}

    for fn in (analyze_smell_type, read_source, read_full_file, search,
               analyze_complexity, apply_fix, try_commit):
        box.add(fn.__name__, fn)
    return box


# ═══════════════════════════════════════════════════════════
#  ④ 决策函数：模拟模型在 ReAct 循环里的判断
# ═══════════════════════════════════════════════════════════

def make_decider(deep_threshold: str = "high"):
    """返回一个 Decide 函数。

    真实系统里这一步是 LLM 的 function calling。这里用规则模拟，
    但保留了关键行为：**简单问题少调几轮，难问题自动追加取证**。
    收敛点由这个函数判断，不是外部写死的步数。
    """

    def decide(task: Task, ctx: RunContext, box: ToolBox) -> tuple:
        seen = ctx.facts.setdefault("tool_results", {})
        p = task.payload

        if "analyze_smell_type" not in seen:
            return ("call", "analyze_smell_type",
                    {"rule": p.get("rule", ""), "message": p.get("message", "")})

        kind = seen["analyze_smell_type"].get("difficulty", "medium")

        if "read_source" not in seen:
            return ("call", "read_source", {"path": p["path"], "line": p["line"]})

        # 难题才继续取证。这就是 3 轮与 8 轮的差别所在。
        if kind == deep_threshold:
            if "read_full_file" not in seen:
                return ("call", "read_full_file", {"path": p["path"]})
            if "analyze_complexity" not in seen:
                return ("call", "analyze_complexity",
                        {"path": p["path"], "line": p["line"]})

        if "apply_fix" not in seen:
            return ("call", "apply_fix",
                    {"path": p["path"], "note": p.get("message", "")})

        ctx.note("修复已应用，信息充分，收敛")
        return ("stop", "已完成修复", None)

    return decide


# ═══════════════════════════════════════════════════════════
#  ⑤ 规划函数：模拟模型在 Plan-and-Execute / ReWOO 里的判断
# ═══════════════════════════════════════════════════════════

def make_planner(deep_threshold: str = "high"):
    """返回一个 Planner 函数。

    与 decide 的关键差别：**它一次性给出完整计划**，不看中间观察结果。
    计划因此是显式产物，可以在执行前被人审查、被规则校验、被记账。

    代价也很明显：难度是靠规则表推的，而不是靠真的读了一遍代码。
    ReAct 是先看再判断，Plan 是先判断再看。哪个更合适取决于载荷，
    不取决于底盘 —— 所以底盘只提供两种模式，不替业务选。
    """
    table = {"S3776": "high", "S4487": "low", "S1854": "low", "S1192": "medium"}

    def planner(task: Task, ctx: RunContext, box: ToolBox):
        p = task.payload
        rule = p.get("rule", "")
        difficulty = next((v for k, v in table.items() if k in rule), "medium")
        ctx.facts["planned_difficulty"] = difficulty

        plan = [
            ("analyze_smell_type", {"rule": rule, "message": p.get("message", "")}),
            ("read_source", {"path": p["path"], "line": p["line"]}),
        ]
        if difficulty == deep_threshold:
            plan.append(("read_full_file", {"path": p["path"]}))
            plan.append(("analyze_complexity", {"path": p["path"], "line": p["line"]}))

        # 重规划时上一轮的错误已经通过 ON_RETRY 注入到 ctx，计划里加一次额外取证
        if ctx.facts.get("last_error") and "search" not in ctx.facts.get("tool_results", {}):
            plan.append(("search", {"pattern": p.get("message", "")[:20]}))

        plan.append(("apply_fix", {"path": p["path"], "note": p.get("message", "")}))
        return plan

    return planner


def make_critic(repo: FakeRepo, require_files: int = 1):
    """返回一个 Critic 函数，给 Reflexion 用。

    它检查的是**工作区里实际有没有变更**，不是模型说自己做完了没有。
    与 WorkspaceChangedCriteria 同源：自省也必须基于事实，
    否则 Reflexion 只是让模型多夸自己一遍。
    """

    def critic(task: Task, ctx: RunContext) -> Optional[str]:
        changed = repo.diff()
        if len(changed) >= require_files:
            return None
        return f"工作区只有 {len(changed)} 个文件变更，未达到 {require_files} 个，判定未生效"

    return critic


def make_reflector():
    """返回一个 Reflector 函数，给 Basic Reflection 用。

    关键在于它**只看得到模型自己产生的东西**（计划、调过的工具），
    看不到 repo.diff() 这种客观事实。所以它能挑出「难题取证不足」，
    却永远挑不出「根本没改成」—— 这就是它与 Reflexion 的天花板。
    """

    def reflector(task: Task, ctx: RunContext) -> Optional[str]:
        seen = ctx.facts.get("tool_results", {})
        hard = "S3776" in task.payload.get("rule", "")
        if hard and "read_full_file" not in seen:
            return "这是认知复杂度类问题，只读了片段就下手，应该先读完整文件"
        return None

    return reflector


def make_solver():
    """返回一个 Solver 函数，给 ReWOO 用。把证据汇总成一句结论。"""

    def solver(task: Task, ctx: RunContext) -> None:
        ev = ctx.facts.get("evidence", [])
        ctx.note(f"[Solver] 汇总 {len(ev)} 条证据，结论：{task.key} 已按规范修复")

    return solver


def make_joiner(repo: FakeRepo):
    """返回一个 Joiner 函数，给 LLMCompiler 用。

    Joiner 与 Critic 的职责很像，位置不同：Critic 在整个模式之外，
    Joiner 在模式内部，决定要不要重新编译一张新的 DAG。
    """

    def joiner(task: Task, ctx: RunContext) -> Optional[str]:
        if repo.diff():
            return None
        return "DAG 跑完但工作区无变更，需重新编译"

    return joiner


# ═════════════════════════════════════════════════════════
#  ⑥ 带证据变量与依赖图的规划器
# ═════════════════════════════════════════════════════════

def make_evidence_planner(deep_threshold: str = "high"):
    """返回一个带 `#E1` 证据变量的 Planner，给 ReWOO 用。

    与普通 Planner 的差别只有一处：步骤间的依赖写成了变量引用，
    由执行器在运行时解。这是 ReWOO 能在不回喂观察的前提下
    仍然表达「第三步要用第一步的结果」的原因。
    """
    table = {"S3776": "high", "S4487": "low", "S1854": "low", "S1192": "medium"}

    def planner(task: Task, ctx: RunContext, box: ToolBox):
        p = task.payload
        rule = p.get("rule", "")
        difficulty = next((v for k, v in table.items() if k in rule), "medium")

        plan = [
            ("analyze_smell_type", {"rule": rule, "message": p.get("message", "")}),
            ("read_source", {"path": p["path"], "line": p["line"]}),
        ]
        if difficulty == deep_threshold:
            plan.append(("read_full_file", {"path": p["path"]}))
        # note 引用第一步的产出，而不是把结果回喂给模型再让它写一遍
        plan.append(("apply_fix", {"path": p["path"], "note": "#E1"}))
        return plan

    return planner


def make_dag_planner(deep_threshold: str = "high"):
    """返回一个 DagPlanner，给 LLMCompiler 用。

    关键不在于步骤更少，而在于**把真实的依赖关系写出来**：
    判类型、读片段、读全文三件事互不相干，本来就能同时做。
    线性计划会把它们排成三步，只是因为列表这种数据结构只会排队。
    """
    table = {"S3776": "high", "S4487": "low", "S1854": "low", "S1192": "medium"}

    def planner(task: Task, ctx: RunContext, box: ToolBox):
        p = task.payload
        rule = p.get("rule", "")
        difficulty = next((v for k, v in table.items() if k in rule), "medium")

        nodes = [
            PlanNode("$1", "analyze_smell_type",
                     {"rule": rule, "message": p.get("message", "")}),
            PlanNode("$2", "read_source", {"path": p["path"], "line": p["line"]}),
        ]
        deps = ["$1", "$2"]
        if difficulty == deep_threshold:
            nodes.append(PlanNode("$3", "read_full_file", {"path": p["path"]}))
            nodes.append(PlanNode("$4", "analyze_complexity",
                                  {"path": p["path"], "line": p["line"]},
                                  deps=["$3"]))
            deps.append("$4")
        nodes.append(PlanNode("$9", "apply_fix",
                              {"path": p["path"], "note": "$1"}, deps=deps))
        return nodes

    return planner


__all__ = [
    "FakeRepo",
    "ScannerTaskSource",
    "WorkspaceChangedCriteria",
    "SMELLS",
    "build_toolbox",
    "make_critic",
    "make_dag_planner",
    "make_decider",
    "make_evidence_planner",
    "make_joiner",
    "make_planner",
    "make_reflector",
    "make_solver",
]
