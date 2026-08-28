"""
① 编排契约 —— 可插拔的编排形态。

底盘不规定 Agent 该怎么被编排。它规定的是编排器必须回答的问题：
**在哪一个点上，只在那一个点上，把决策权交给模型。**

每个编排器通过 `delegation_points` 声明自己的下放点，底盘据此生成
信任边界报告。四种内置形态可以按名字互换，载荷代码一行不用动：

    state_machine   线性状态机。当前生产使用，二十行代码
    react           单层 ReAct 循环。全程由模型驱动
    nested          外层状态机 + 内层 ReAct。生产实际形态
    subgraph        分层子图 + 路由 + 人工介入。下一代设计
"""
from __future__ import annotations

import time
from typing import Any, Callable, Dict, List, Optional, Sequence

from ..contracts import (
    DoneCriteria,
    InjectionPoint,
    Orchestrator,
    Outcome,
    Registry,
    RunContext,
    Step,
    Task,
    TaskResult,
    Verdict,
)

orchestrator_registry = Registry("orchestrator")


# ═══════════════════════════════════════════════════════════
#  通用阶段
# ═══════════════════════════════════════════════════════════

class FnStep(Step):
    """用普通函数包一个阶段。绝大多数确定性阶段用这个就够。"""

    def __init__(
        self,
        name: str,
        fn: Callable[[Task, RunContext], None],
        delegates_to_model: bool = False,
    ) -> None:
        self.name = name
        self.fn = fn
        self.delegates_to_model = delegates_to_model

    def execute(self, task: Task, ctx: RunContext) -> None:
        self.fn(task, ctx)


class AgentStep(Step):
    """把决策权交给模型的那个阶段。

    它是编排器里唯一 `delegates_to_model = True` 的阶段类型。
    内部跑什么由 `runner` 决定，可以是 ReAct 循环，也可以是一次性调用。
    """

    delegates_to_model = True

    def __init__(self, name: str, runner: Callable[[Task, RunContext], None]) -> None:
        self.name = name
        self.runner = runner

    def execute(self, task: Task, ctx: RunContext) -> None:
        self.runner(task, ctx)


def _finish(
    task: Task,
    ctx: RunContext,
    criteria: Optional[DoneCriteria],
    started: float,
) -> TaskResult:
    """统一收尾：跑完成判据，组装结果。

    判据只读 ctx.facts，拿不到 ctx.model_notes。这不是约定俗成，
    是把「Agent 不能自己给自己判卷」写进了调用签名。
    """
    verdict: Optional[Verdict] = None
    if criteria is not None:
        if ctx.chassis is not None:
            ctx.chassis.inject(InjectionPoint.BEFORE_VERDICT, task, ctx)
        verdict = criteria.judge(task, ctx)
    outcome = Outcome.SUCCEEDED if (verdict is None or verdict.done) else Outcome.FAILED
    return TaskResult(
        task=task,
        outcome=outcome,
        verdict=verdict,
        steps=list(ctx.steps),
        iterations=ctx.iterations,
        elapsed_ms=int((time.time() - started) * 1000),
        error="" if outcome is Outcome.SUCCEEDED else (verdict.reason if verdict else ""),
    )


# ═══════════════════════════════════════════════════════════
#  形态一：线性状态机
# ═══════════════════════════════════════════════════════════

@orchestrator_registry.register("state_machine")
class StateMachineOrchestrator(Orchestrator):
    """按顺序推进阶段，任一阶段抛异常即短路。

    这是当前生产使用的形态。五个阶段只有一条主路径和一条失败短路，
    没有分支、并发、循环。这种形状引入工作流引擎不产生收益，
    只多一层需要理解和调试的抽象。所以它就是一个循环加一个异常判断。
    """

    name = "state_machine"

    def __init__(self, steps: Sequence[Step], criteria: Optional[DoneCriteria] = None) -> None:
        self.steps = list(steps)
        self.criteria = criteria
        self.delegation_points = [s.name for s in self.steps if s.delegates_to_model]

    def run(self, task: Task, ctx: RunContext) -> TaskResult:
        started = time.time()
        for step in self.steps:
            ctx.record_step(step.name)
            if ctx.chassis is not None:
                ctx.chassis.notify_step(step.name, task, ctx)
            step.execute(task, ctx)
        return _finish(task, ctx, self.criteria, started)


# ═══════════════════════════════════════════════════════════
#  形态二：单层 ReAct 循环
# ═══════════════════════════════════════════════════════════

class ToolBox:
    """暴露给模型的工具集合。

    与 ConnectorManager 的区别：连接器是给确定性代码直接调的，
    ToolBox 是给模型选的。同一个能力可以两边都出现，但走的是两条路。
    """

    def __init__(self) -> None:
        self._tools: Dict[str, Callable[..., Any]] = {}
        self._desc: Dict[str, str] = {}

    def add(self, name: str, fn: Callable[..., Any], description: str = "") -> "ToolBox":
        self._tools[name] = fn
        self._desc[name] = description or (fn.__doc__ or "").strip().splitlines()[0] if fn.__doc__ else description
        return self

    def names(self) -> List[str]:
        return list(self._tools)

    def schema(self) -> List[Dict[str, str]]:
        return [{"name": n, "description": self._desc.get(n, "")} for n in self._tools]

    def call(self, name: str, **kwargs: Any) -> Any:
        if name not in self._tools:
            raise KeyError(f"未注册工具 {name!r}，可用：{self.names()}")
        return self._tools[name](**kwargs)


#: 决策函数签名：给定任务、上下文、可用工具，返回下一步动作
#: 返回 ("call", tool_name, kwargs) 或 ("stop", reason, None)
Decide = Callable[[Task, RunContext, ToolBox], tuple]


@orchestrator_registry.register("react")
class ReActOrchestrator(Orchestrator):
    """单层 ReAct：模型全程驱动，自己决定调什么、调几轮、什么时候停。

    底盘只强制两件事：迭代上限，以及每次工具调用前触发 BEFORE_TOOL 注入。
    收敛点由模型判断 —— 这正是 Agent 与写死步数的 pipeline 的分界线。
    """

    name = "react"
    delegation_points = ["整个循环"]

    def __init__(
        self,
        toolbox: ToolBox,
        decide: Decide,
        criteria: Optional[DoneCriteria] = None,
        max_iterations: int = 8,
        executor_tools: Sequence[str] = (),
    ) -> None:
        self.toolbox = toolbox
        self.decide = decide
        self.criteria = criteria
        self.max_iterations = max_iterations
        #: 这些工具会把活交给外部执行器，调用前触发 BEFORE_EXECUTOR
        #: 而不是 BEFORE_TOOL。规范就是在这一刻拼进去的。
        self.executor_tools = set(executor_tools)

    def run(self, task: Task, ctx: RunContext) -> TaskResult:
        started = time.time()
        ctx.record_step("react_loop")
        if ctx.chassis is not None:
            ctx.chassis.notify_step("react_loop", task, ctx)

        for _ in range(self.max_iterations):
            ctx.iterations += 1
            action, target, kwargs = self.decide(task, ctx, self.toolbox)
            if action == "stop":
                ctx.note(f"模型判断收敛：{target}")
                break
            if ctx.chassis is not None:
                point = (
                    InjectionPoint.BEFORE_EXECUTOR
                    if target in self.executor_tools
                    else InjectionPoint.BEFORE_TOOL
                )
                ctx.chassis.inject(point, task, ctx)
            result = self._invoke(target, kwargs or {}, task, ctx)
            ctx.facts.setdefault("tool_results", {})[target] = result
        else:
            ctx.note(f"达到迭代上限 {self.max_iterations}，强制收敛")

        return _finish(task, ctx, self.criteria, started)

    def _invoke(self, tool: str, kwargs: Dict[str, Any], task: Task, ctx: RunContext) -> Any:
        from ..contracts import ToolCall
        t0 = time.time()
        try:
            out = self.toolbox.call(tool, **kwargs)
            rec = ToolCall(name=tool, args=kwargs, result=out, ok=True,
                           elapsed_ms=int((time.time() - t0) * 1000))
        except Exception as exc:
            rec = ToolCall(name=tool, args=kwargs, ok=False,
                           error=f"{type(exc).__name__}: {exc}",
                           elapsed_ms=int((time.time() - t0) * 1000))
            ctx.tool_calls.append(rec)
            if ctx.chassis is not None:
                ctx.chassis.notify_tool_call(rec, task, ctx)
            raise
        ctx.tool_calls.append(rec)
        if ctx.chassis is not None:
            ctx.chassis.notify_tool_call(rec, task, ctx)
        return out


# ═══════════════════════════════════════════════════════════
#  形态三：嵌套（生产实际形态）
# ═══════════════════════════════════════════════════════════

@orchestrator_registry.register("nested")
class NestedOrchestrator(Orchestrator):
    """外层确定性状态机，内层 ReAct 循环。

    外层的每个阶段都是确定性代码，只有被标记为下放点的那个阶段
    把控制权交给内层循环。这是「在哪一个点上把决策权交出去」这个
    问题最直接的结构化回答。
    """

    name = "nested"

    def __init__(
        self,
        outer_steps: Sequence[Step],
        inner: ReActOrchestrator,
        delegate_at: str,
        criteria: Optional[DoneCriteria] = None,
    ) -> None:
        self.outer_steps = list(outer_steps)
        self.inner = inner
        self.delegate_at = delegate_at
        self.criteria = criteria
        self.delegation_points = [delegate_at]
        if delegate_at not in {s.name for s in self.outer_steps}:
            raise ValueError(
                f"下放点 {delegate_at!r} 不在外层阶段中："
                f"{[s.name for s in self.outer_steps]}"
            )

    def run(self, task: Task, ctx: RunContext) -> TaskResult:
        started = time.time()
        for step in self.outer_steps:
            ctx.record_step(step.name)
            if ctx.chassis is not None:
                ctx.chassis.notify_step(step.name, task, ctx)
            if step.name == self.delegate_at:
                # 唯一的下放点：内层循环接管，跑完把控制权还回来
                inner_ctx_steps = len(ctx.steps)
                self.inner.run(task, ctx)
                del ctx.steps[inner_ctx_steps:]
                ctx.record_step(f"{step.name}(内层已收敛)")
            else:
                step.execute(task, ctx)
        return _finish(task, ctx, self.criteria, started)


# ═══════════════════════════════════════════════════════════
#  形态四：分层子图（下一代设计）
# ═══════════════════════════════════════════════════════════

#: 路由函数：给定任务返回下一个子图名
Router = Callable[[Task, RunContext], str]


class Subgraph:
    """一组阶段构成的独立子图，有自己的入口与出口。"""

    def __init__(self, name: str, steps: Sequence[Step]) -> None:
        self.name = name
        self.steps = list(steps)

    def run(self, task: Task, ctx: RunContext) -> None:
        for step in self.steps:
            label = f"{self.name}/{step.name}"
            ctx.record_step(label)
            if ctx.chassis is not None:
                ctx.chassis.notify_step(label, task, ctx)
            step.execute(task, ctx)


@orchestrator_registry.register("subgraph")
class SubgraphOrchestrator(Orchestrator):
    """分析子图 → 路由 → 修复子图（多支路）→ 人工介入 → 交付子图。

    这是当前平铺式状态机的下一代形态。命名类走轻量支路，认知复杂度类
    走深度支路，高风险任务在子图之间停下来等人决策。

    到这个形状才真正需要工作流引擎。当前形态不需要，所以当前不用。
    """

    name = "subgraph"

    def __init__(
        self,
        analysis: Subgraph,
        router: Router,
        branches: Dict[str, Subgraph],
        delivery: Subgraph,
        criteria: Optional[DoneCriteria] = None,
        interrupt_if: Optional[Callable[[Task, RunContext], bool]] = None,
        on_interrupt: Optional[Callable[[Task, RunContext], bool]] = None,
    ) -> None:
        self.analysis = analysis
        self.router = router
        self.branches = dict(branches)
        self.delivery = delivery
        self.criteria = criteria
        self.interrupt_if = interrupt_if
        self.on_interrupt = on_interrupt
        self.delegation_points = [
            f"{g.name}/{s.name}"
            for g in [analysis, delivery, *branches.values()]
            for s in g.steps
            if s.delegates_to_model
        ]

    def run(self, task: Task, ctx: RunContext) -> TaskResult:
        started = time.time()

        self.analysis.run(task, ctx)

        branch_name = self.router(task, ctx)
        ctx.facts["route"] = branch_name
        ctx.record_step(f"route → {branch_name}")
        if ctx.chassis is not None:
            ctx.chassis.notify_step(f"route → {branch_name}", task, ctx)
        if branch_name not in self.branches:
            raise KeyError(
                f"路由到未知支路 {branch_name!r}，可用：{sorted(self.branches)}"
            )
        self.branches[branch_name].run(task, ctx)

        if self.interrupt_if and self.interrupt_if(task, ctx):
            ctx.record_step("human_in_the_loop")
            if ctx.chassis is not None:
                ctx.chassis.notify_step("human_in_the_loop", task, ctx)
            approved = self.on_interrupt(task, ctx) if self.on_interrupt else False
            ctx.facts["human_approved"] = approved
            if not approved:
                return TaskResult(
                    task=task, outcome=Outcome.SKIPPED,
                    verdict=Verdict(False, "等待人工决策，本轮不交付"),
                    steps=list(ctx.steps), iterations=ctx.iterations,
                    elapsed_ms=int((time.time() - started) * 1000),
                )

        self.delivery.run(task, ctx)
        return _finish(task, ctx, self.criteria, started)


__all__ = [
    "AgentStep",
    "Decide",
    "FnStep",
    "NestedOrchestrator",
    "ReActOrchestrator",
    "Router",
    "StateMachineOrchestrator",
    "Subgraph",
    "SubgraphOrchestrator",
    "ToolBox",
    "orchestrator_registry",
]
