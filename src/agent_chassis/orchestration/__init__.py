"""
① 编排契约 —— 外层流程编排。

底盘把编排拆成两个正交的轴：

    外层 · 流程编排 Flow（本模块）      内层 · 推理模式 Reasoning（reasoning.py）
    ├─ StateMachineOrchestrator        ├─ ReActPattern
    ├─ SubgraphOrchestrator            ├─ PlanExecutePattern
    └─ SingleAgentOrchestrator         ├─ PlanAndSolvePattern
                                       ├─ ReWOOPattern
                                       ├─ LLMCompilerPattern
                                       ├─ BasicReflectionPattern
                                       └─ ReflexionPattern
              ↘                    ↙
              NestedOrchestrator（组合算子）

外层决定任务被推进的骨架，内层决定模型在下放点内部怎么想。
换外层不影响内层，换内层不影响外层。

底盘不规定用哪一种，但要求每个编排器通过 `delegation_points` 说清楚
**在哪一个点、只在那一个点，把决策权交给了模型**。
"""
from __future__ import annotations

import time
from copy import deepcopy
from typing import Any, Callable, Dict, List, Optional, Sequence

from ..contracts import (
    DoneCriteria,
    InjectionPoint,
    Orchestrator,
    Outcome,
    ReasoningPattern,
    Registry,
    RunContext,
    Step,
    Task,
    TaskResult,
    Verdict,
)
from .reasoning import (
    BasicReflectionPattern,
    Critic,
    DagPlanner,
    Decide,
    Joiner,
    LLMCompilerPattern,
    PlanAndSolvePattern,
    PlanExecutePattern,
    PlanNode,
    Planner,
    ReActPattern,
    ReWOOPattern,
    Reflector,
    ReflexionPattern,
    invoke_tool,
    reasoning_registry,
)

orchestrator_registry = Registry("orchestrator")


# ═══════════════════════════════════════════════════════════
#  通用阶段与工具集
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

    它是流程里唯一 `delegates_to_model = True` 的阶段类型。
    内部跑哪种推理模式由 pattern 决定；也可以传 runner 写死一段固定逻辑，
    用于对照「不用 Agent 会怎样」。
    """

    delegates_to_model = True

    def __init__(
        self,
        name: str,
        runner: Optional[Callable[[Task, RunContext], None]] = None,
        pattern: Optional[ReasoningPattern] = None,
        toolbox: Any = None,
    ) -> None:
        if runner is None and pattern is None:
            raise ValueError("AgentStep 需要 runner 或 pattern 其一")
        self.name = name
        self.runner = runner
        self.pattern = pattern
        self.toolbox = toolbox

    def execute(self, task: Task, ctx: RunContext) -> None:
        if self.pattern is not None:
            self.pattern.reason(task, ctx, self.toolbox)
        else:
            self.runner(task, ctx)


class ToolBox:
    """暴露给模型的工具集合。

    与 ConnectorManager 的区别：连接器是给确定性代码直接调的，
    ToolBox 是给模型选的。同一个能力可以两边都出现，但走的是两条路。
    """

    def __init__(self) -> None:
        self._tools: Dict[str, Callable[..., Any]] = {}
        self._desc: Dict[str, str] = {}
        self._input_schemas: Dict[str, Dict[str, Any]] = {}
        self._contextual: set = set()

    def add(
        self, name: str, fn: Callable[..., Any], description: str = "",
        *, input_schema: Optional[Dict[str, Any]] = None,
    ) -> "ToolBox":
        self._tools[name] = fn
        self._contextual.discard(name)
        self._input_schemas.pop(name, None)
        if input_schema is not None:
            self._input_schemas[name] = deepcopy(input_schema)
        doc = (fn.__doc__ or "").strip().splitlines()
        self._desc[name] = description or (doc[0] if doc else "")
        return self

    def add_contextual(
        self, name: str, fn: Callable[..., Any], description: str = "",
        *, input_schema: Optional[Dict[str, Any]] = None,
    ) -> "ToolBox":
        """显式 opt-in：仅由运行时传入 task/ctx，不暴露为模型参数。"""
        self.add(name, fn, description, input_schema=input_schema)
        self._contextual.add(name)
        return self

    def names(self) -> List[str]:
        return list(self._tools)

    def schema(self) -> List[Dict[str, Any]]:
        return [dict(
            name=n, description=self._desc.get(n, ""),
            **({"inputSchema": deepcopy(self._input_schemas[n])}
               if n in self._input_schemas else {}),
        ) for n in self._tools]

    def call(self, name: str, **kwargs: Any) -> Any:
        if name not in self._tools:
            raise KeyError(f"未注册工具 {name!r}，可用：{self.names()}")
        if name in self._contextual:
            raise ValueError(f"工具 {name!r} 需要 call_with_context")
        return self._tools[name](**kwargs)

    def call_with_context(self, name: str, task: Task, ctx: RunContext, /, **kwargs: Any) -> Any:
        if name in self._contextual:
            if "task" in kwargs or "ctx" in kwargs:
                raise ValueError("模型参数不能覆盖运行时 task/ctx")
            return self._tools[name](task=task, ctx=ctx, **kwargs)
        return self.call(name, **kwargs)


def _finish(
    task: Task,
    ctx: RunContext,
    criteria: Optional[DoneCriteria],
    started: float,
) -> TaskResult:
    """统一收尾：跑完成判据，组装结果。

    通过 Chassis 运行时，以 with_payload 的判据为权威并缓存裁定；
    独立使用编排器时仍支持原有可选判据。判据只读 facts 是实现约定，
    不应把 RunContext 的 Python 签名描述成安全隔离。
    """
    verdict: Optional[Verdict] = None
    if ctx.chassis is not None:
        verdict = ctx.chassis.judge(task, ctx)
    elif criteria is not None:
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


def _mark(ctx: RunContext, task: Task, label: str) -> None:
    ctx.record_step(label)
    if ctx.chassis is not None:
        ctx.chassis.notify_step(label, task, ctx)


def _pattern_of(steps: Sequence[Step]) -> Optional[ReasoningPattern]:
    for s in steps:
        pattern = getattr(s, "pattern", None)
        if pattern is not None:
            return pattern
    return None


# ═══════════════════════════════════════════════════════════
#  外层形态一：线性状态机
# ═══════════════════════════════════════════════════════════

@orchestrator_registry.register("state_machine")
class StateMachineOrchestrator(Orchestrator):
    """按顺序推进阶段，任一阶段抛异常即短路。

    这是当前生产使用的外层形态。五个阶段只有一条主路径和一条失败短路，
    没有分支、并发、循环。这种形状引入工作流引擎不产生收益，
    只多一层需要理解和调试的抽象。所以它就是一个循环加一个异常判断。
    """

    name = "state_machine"

    def __init__(self, steps: Sequence[Step], criteria: Optional[DoneCriteria] = None) -> None:
        self.steps = list(steps)
        self.criteria = criteria
        self.delegation_points = [s.name for s in self.steps if s.delegates_to_model]

    @property
    def reasoning_name(self) -> str:
        pattern = _pattern_of(self.steps)
        return pattern.describe() if pattern else "（下放点内为固定逻辑，未用推理模式）"

    def run(self, task: Task, ctx: RunContext) -> TaskResult:
        started = time.time()
        for step in self.steps:
            _mark(ctx, task, step.name)
            step.execute(task, ctx)
        return _finish(task, ctx, self.criteria, started)


# ═══════════════════════════════════════════════════════════
#  外层形态二：单 Agent
# ═══════════════════════════════════════════════════════════

@orchestrator_registry.register("single_agent")
class SingleAgentOrchestrator(Orchestrator):
    """没有外层骨架，整个任务从头到尾交给一个推理模式。

    最激进的下放方式。适合任务本身就是一次开放式求解、
    前后没有必须由确定性代码把守的阶段的场景。
    """

    name = "single_agent"
    delegation_points = ["整个任务"]

    def __init__(
        self,
        toolbox: ToolBox,
        pattern: ReasoningPattern,
        criteria: Optional[DoneCriteria] = None,
    ) -> None:
        self.toolbox = toolbox
        self.pattern = pattern
        self.criteria = criteria

    @property
    def reasoning_name(self) -> str:
        return self.pattern.describe()

    def run(self, task: Task, ctx: RunContext) -> TaskResult:
        started = time.time()
        _mark(ctx, task, f"reasoning[{self.pattern.name}]")
        self.pattern.reason(task, ctx, self.toolbox)
        return _finish(task, ctx, self.criteria, started)


# ═══════════════════════════════════════════════════════════
#  组合算子：外层骨架 × 内层模式
# ═══════════════════════════════════════════════════════════

@orchestrator_registry.register("nested")
class NestedOrchestrator(Orchestrator):
    """把外层流程与内层推理模式接起来。

    这不是第三种编排形态，而是**两个轴的组合算子**。外层的每个阶段
    都是确定性代码，只有 `delegate_at` 指定的那个阶段把控制权交给内层。

        NestedOrchestrator(
            outer_steps=[...],                      # 外层骨架
            toolbox=box,
            pattern=PlanExecutePattern(planner),    # 内层模式，随时可换
            delegate_at="agent_fix",
        )

    换推理模式不影响外层，换外层不影响推理模式。
    """

    name = "nested"

    def __init__(
        self,
        outer_steps: Sequence[Step],
        toolbox: ToolBox,
        pattern: ReasoningPattern,
        delegate_at: str,
        criteria: Optional[DoneCriteria] = None,
    ) -> None:
        self.outer_steps = list(outer_steps)
        self.toolbox = toolbox
        self.pattern = pattern
        self.delegate_at = delegate_at
        self.criteria = criteria
        self.delegation_points = [delegate_at]
        if delegate_at not in {s.name for s in self.outer_steps}:
            raise ValueError(
                f"下放点 {delegate_at!r} 不在外层阶段中："
                f"{[s.name for s in self.outer_steps]}"
            )

    @property
    def reasoning_name(self) -> str:
        return self.pattern.describe()

    def run(self, task: Task, ctx: RunContext) -> TaskResult:
        started = time.time()
        for step in self.outer_steps:
            _mark(ctx, task, step.name)
            if step.name == self.delegate_at:
                _mark(ctx, task, f"reasoning[{self.pattern.name}]")
                self.pattern.reason(task, ctx, self.toolbox)
            else:
                step.execute(task, ctx)
        return _finish(task, ctx, self.criteria, started)


# ═══════════════════════════════════════════════════════════
#  外层形态三：分层子图（下一代设计）
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
            _mark(ctx, task, f"{self.name}/{step.name}")
            step.execute(task, ctx)


@orchestrator_registry.register("subgraph")
class SubgraphOrchestrator(Orchestrator):
    """分析子图 → 路由 → 修复子图（多支路）→ 人工介入 → 交付子图。

    这是当前平铺式状态机的下一代外层形态。不同支路可以挂**不同的推理模式**：
    命名类走 ReWOO（便宜、可预测），认知复杂度类走 Reflexion 包 ReAct（贵、能自省）。
    这正是两轴分离带来的直接好处。

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

    @property
    def reasoning_name(self) -> str:
        seen = []
        for g in [self.analysis, *self.branches.values(), self.delivery]:
            pattern = _pattern_of(g.steps)
            if pattern is not None:
                seen.append(f"{g.name}→{pattern.name}")
        return "；".join(seen) or "（各支路未使用推理模式）"

    def run(self, task: Task, ctx: RunContext) -> TaskResult:
        started = time.time()
        self.analysis.run(task, ctx)

        branch_name = self.router(task, ctx)
        ctx.facts["route"] = branch_name
        _mark(ctx, task, f"route → {branch_name}")
        if branch_name not in self.branches:
            raise KeyError(
                f"路由到未知支路 {branch_name!r}，可用：{sorted(self.branches)}"
            )
        self.branches[branch_name].run(task, ctx)

        if self.interrupt_if and self.interrupt_if(task, ctx):
            _mark(ctx, task, "human_in_the_loop")
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
    # 外层：流程编排
    "AgentStep",
    "FnStep",
    "NestedOrchestrator",
    "Router",
    "SingleAgentOrchestrator",
    "StateMachineOrchestrator",
    "Subgraph",
    "SubgraphOrchestrator",
    "ToolBox",
    "orchestrator_registry",
    # 内层：Agent 设计模式（转出，方便一处 import）
    "BasicReflectionPattern",
    "Critic",
    "DagPlanner",
    "Decide",
    "Joiner",
    "LLMCompilerPattern",
    "PlanAndSolvePattern",
    "PlanExecutePattern",
    "PlanNode",
    "Planner",
    "ReActPattern",
    "ReWOOPattern",
    "Reflector",
    "ReflexionPattern",
    "invoke_tool",
    "reasoning_registry",
]
