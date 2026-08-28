"""
底盘装配器 —— 把五大系统组装成一台可运行的数字员工。

一台数字员工 = 底盘（五大系统）+ 载荷（任务源 + 完成判据 + 领域工具集）。

    chassis = (
        Chassis("代码质量治理")
        .with_orchestrator(...)        # ① 编排契约
        .mount("sonarqube", "mcp.stdio", command="...")   # ② 接入层
        .with_knowledge(...)           # ③ 知识注入
        .with_failure_policy(...)      # ④ 失败契约
        .observe(ConsoleObserver())    # ⑤ 可观测
        .with_payload(source, criteria)
        .build()
    )
    chassis.run_once()

换一台数字员工，只换最后那行 with_payload 和编排器里的领域工具集。
"""
from __future__ import annotations

import time
from contextvars import ContextVar
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Sequence, Tuple

from .contracts import (
    DoneCriteria,
    Injection,
    InjectionPoint,
    KnowledgeProvider,
    Observer,
    Orchestrator,
    Outcome,
    RunContext,
    Task,
    TaskResult,
    TaskSource,
    ToolCall,
)
from .failure import Ledger, WorkspaceGuard, ZeroSideEffectPolicy
from .integration import ConnectorManager
from .knowledge import InjectionScheduler
from .observability import FanOutObserver, RecordingObserver
from .permissions import PermissionBoundary


class ChassisError(RuntimeError):
    """装配期错误。故意在装配期就失败，而不是等到凌晨三点跑起来才失败。"""


@dataclass
class BuildReport:
    """装配自检报告。路演时直接打出来，说明这台机器由什么构成。"""
    name: str
    orchestrator: str
    reasoning: str
    delegation_points: List[str]
    connectors: Dict[str, List[str]]
    injection_timeline: List[tuple]
    boundaries: List[str]
    task_source: str
    done_criteria: str
    failure_policy: str

    def render(self) -> str:
        lines = [
            f"╔ 装配报告 · {self.name}",
            f"║ ① 编排契约",
            f"║   外层·流程   {self.orchestrator}",
            f"║   内层·模式   {self.reasoning}",
            f"║              决策下放点：{', '.join(self.delegation_points) or '无'}",
            "║ ② 接入层",
        ]
        for conn, tools in self.connectors.items():
            preview = ", ".join(tools[:4]) + (" …" if len(tools) > 4 else "")
            lines.append(f"║              {conn:<14} {len(tools)} 个工具  {preview}")
        if not self.connectors:
            lines.append("║              （未挂载）")
        lines.append("║ ③ 知识注入   时机 → provider")
        for point, providers in self.injection_timeline:
            mark = ", ".join(providers) if providers else "—"
            flag = "  ← 决策层刻意留空" if point is InjectionPoint.AGENT_BOOT and not providers else ""
            lines.append(f"║              {point.value:<16} {mark}{flag}")
        lines.append("║ ④ 失败契约   " + self.failure_policy)
        lines.append("║ ⑤ 权限边界")
        for b in self.boundaries or ["（未设置）"]:
            lines.append(f"║              {b}")
        lines.append(f"╟ 载荷 · 任务源     {self.task_source}")
        lines.append(f"╚ 载荷 · 完成判据   {self.done_criteria}")
        return "\n".join(lines)


class Chassis:
    """底盘装配器。链式配置，build() 后得到可运行实例。"""

    def __init__(self, name: str = "digital-employee") -> None:
        self.name = name
        self.connectors = ConnectorManager()
        self.observers = FanOutObserver()
        self.boundaries: Dict[str, PermissionBoundary] = {}
        self.workspace_guard = WorkspaceGuard()

        self._orchestrator: Optional[Orchestrator] = None
        self._providers: List[KnowledgeProvider] = []
        self._scheduler: Optional[InjectionScheduler] = None
        self._failure = None
        self._source: Optional[TaskSource] = None
        self._criteria: Optional[DoneCriteria] = None
        self._built = False
        self._active_run: ContextVar[Optional[Tuple[Task, RunContext]]] = ContextVar(
            f"agent_chassis_active_run_{id(self)}", default=None
        )

    # ── ① 编排契约 ──────────────────────────────────────
    def with_orchestrator(self, orchestrator: Orchestrator) -> "Chassis":
        self._orchestrator = orchestrator
        return self

    @property
    def orchestrator_name(self) -> str:
        return self._orchestrator.name if self._orchestrator else ""

    # ── ② 接入层 ────────────────────────────────────────
    def mount(self, name: str, scheme: str, **config: Any) -> "Chassis":
        self.connectors.mount(name, scheme, **config)
        return self

    # ── ③ 知识注入 ──────────────────────────────────────
    def with_knowledge(self, *providers: KnowledgeProvider) -> "Chassis":
        self._providers.extend(providers)
        return self

    # ── ④ 失败契约 ──────────────────────────────────────
    def with_failure_policy(self, policy) -> "Chassis":
        self._failure = policy
        return self

    def with_workspace_guard(self, guard: WorkspaceGuard) -> "Chassis":
        self.workspace_guard = guard
        return self

    # ── ⑤ 可观测 ────────────────────────────────────────
    def observe(self, *observers: Observer) -> "Chassis":
        for o in observers:
            self.observers.add(o)
        return self

    # ── 权限边界 ────────────────────────────────────────
    def with_boundary(self, boundary: PermissionBoundary) -> "Chassis":
        self.boundaries[boundary.subject] = boundary
        return self

    def boundary(self, subject: str) -> PermissionBoundary:
        if subject not in self.boundaries:
            raise ChassisError(f"未定义权限边界：{subject}")
        return self.boundaries[subject]

    # ── 载荷 ────────────────────────────────────────────
    def with_payload(self, source: TaskSource, criteria: DoneCriteria) -> "Chassis":
        self._source = source
        self._criteria = criteria
        return self

    # ── 装配 ────────────────────────────────────────────
    def build(self) -> "Chassis":
        missing = [
            label
            for label, value in [
                ("编排器 with_orchestrator", self._orchestrator),
                ("任务源 with_payload", self._source),
                ("完成判据 with_payload", self._criteria),
            ]
            if value is None
        ]
        if missing:
            raise ChassisError("装配不完整，缺少：" + "、".join(missing))

        if self._failure is None:
            self._failure = ZeroSideEffectPolicy(Ledger())
        self._scheduler = InjectionScheduler(self._providers)
        self.connectors.subscribe(self._on_connector_call)
        self._built = True
        return self

    def report(self) -> BuildReport:
        if not self._built:
            raise ChassisError("请先调用 build()")
        return BuildReport(
            name=self.name,
            orchestrator=self._orchestrator.describe(),
            reasoning=getattr(self._orchestrator, "reasoning_name", "（该编排器未声明推理模式）"),
            delegation_points=list(self._orchestrator.delegation_points),
            connectors=self.connectors.inventory(),
            injection_timeline=self._scheduler.timeline(),
            boundaries=[b.summary() for b in self.boundaries.values()],
            task_source=self._source.describe(),
            done_criteria=self._criteria.describe(),
            failure_policy=self._failure.name,
        )

    # ── 运行 ────────────────────────────────────────────
    def run_once(self) -> Optional[TaskResult]:
        """取一个任务并跑完。没有可处理任务时返回 None。"""
        results = self.run(limit=1)
        return results[0] if results else None

    def run(self, limit: int = 1) -> List[TaskResult]:
        if not self._built:
            raise ChassisError("请先调用 build()")

        results: List[TaskResult] = []
        for task in self._source.fetch(limit=limit):
            if self._failure.seen(task.key):
                continue
            results.append(self._run_task(task))
        return results

    def _run_task(self, task: Task) -> TaskResult:
        ctx = RunContext(chassis=self)
        token = self._active_run.set((task, ctx))
        try:
            started = time.time()
            self.observers.on_task_start(task, ctx)

            try:
                self.workspace_guard.prepare(ctx)
                self.inject(InjectionPoint.TASK_ADMITTED, task, ctx)

                while True:
                    try:
                        result = self._orchestrator.run(task, ctx)
                        break
                    except Exception as exc:
                        error = f"{type(exc).__name__}: {exc}"
                        if self._prepare_retry(task, error, ctx):
                            continue
                        raise

                if result.outcome is Outcome.SUCCEEDED:
                    self._failure.remember(task.key, Outcome.SUCCEEDED)
                elif result.outcome is Outcome.FAILED:
                    self._failure.remember(task.key, Outcome.FAILED, result.error)
            except Exception as exc:
                error = f"{type(exc).__name__}: {exc}"
                outcome = self._failure.on_failure(task, error, ctx)
                result = TaskResult(
                    task=task, outcome=outcome, error=error,
                    steps=list(ctx.steps), iterations=ctx.iterations,
                    elapsed_ms=int((time.time() - started) * 1000),
                )

            self.observers.on_task_end(result, ctx)
            return result
        finally:
            self._active_run.reset(token)

    def _prepare_retry(self, task: Task, error: str, ctx: RunContext) -> bool:
        """若失败策略支持有限重试，准备下一次干净尝试。"""
        should_retry = getattr(self._failure, "should_retry", None)
        consume_retry = getattr(self._failure, "consume_retry", None)
        if not callable(should_retry) or not callable(consume_retry):
            return False
        if not should_retry(ctx):
            return False

        on_retry = getattr(self._failure, "on_retry", None)
        if callable(on_retry):
            on_retry(task, error, ctx)
        else:
            ctx.facts["last_error"] = error
        consume_retry(ctx)

        # invoke_tool 的结果属于单次尝试；保留它会让 ReAct 在下一轮误判为
        # 某些工具已经执行过。其他 facts（失败原因、反思记忆等）有意保留。
        ctx.facts.pop("tool_results", None)

        # 新尝试先恢复工作区，再把失败原因注入给下一轮推理。
        self.workspace_guard.prepare(ctx)
        self.inject(InjectionPoint.ON_RETRY, task, ctx)
        return True

    # ── 供插件回调 ──────────────────────────────────────
    def inject(self, point: InjectionPoint, task: Task, ctx: RunContext) -> str:
        """在指定时机收集知识。返回拼好的文本，同时留下审计记录。"""
        if self._scheduler is None:
            return ""
        return self._scheduler.collect(point, task, ctx)

    def notify_step(self, step: str, task: Task, ctx: RunContext) -> None:
        self.observers.on_step(step, task, ctx)

    def notify_tool_call(self, call: ToolCall, task: Task, ctx: RunContext) -> None:
        self.observers.on_tool_call(call, task, ctx)

    def notify_injection(self, inj: Injection, task: Task, ctx: RunContext) -> None:
        self.observers.on_injection(inj, task, ctx)

    def _on_connector_call(self, call: ToolCall) -> None:
        active = self._active_run.get()
        if active is None:
            # 装配期 discovery、任务源拉取、健康检查等没有 task/run 上下文；
            # ConnectorManager 仍会自行记账，但不能伪造一个 run_id。
            return
        task, ctx = active
        ctx.tool_calls.append(call)
        self.notify_tool_call(call, task, ctx)

    def close(self) -> None:
        self.connectors.close()


__all__ = ["Chassis", "ChassisError", "BuildReport"]
