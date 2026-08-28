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
from agent_chassis.orchestration import ToolBox
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


__all__ = [
    "FakeRepo",
    "ScannerTaskSource",
    "WorkspaceChangedCriteria",
    "SMELLS",
    "build_toolbox",
    "make_decider",
]
