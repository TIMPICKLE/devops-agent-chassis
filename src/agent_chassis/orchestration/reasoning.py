"""
内层推理模式 —— Agent 设计模式的可插拔实现。

底盘把编排拆成两个正交的轴：

    外层 · 流程编排 Flow          内层 · 推理模式 Reasoning
    ├─ 线性状态机                  ├─ ReAct           边想边做
    ├─ 分层子图 + 路由              ├─ Plan-and-Execute 先想清楚再做
    └─ （可扩展）DAG / 并行         ├─ ReWOO            一次规划，不看中间观察
                                  └─ Reflexion        做完自省，不满意重来

外层决定「任务被推进的骨架」，内层决定「在下放点内部，模型怎么想」。
两者独立替换：同一个流程可以换推理模式，同一个推理模式可以放进不同流程。

`NestedOrchestrator` 不是第三种形态，它是把两个轴接起来的组合算子。
"""
from __future__ import annotations

import time
from typing import Any, Callable, Dict, List, Optional, Sequence, Tuple

from ..contracts import (
    InjectionPoint,
    ReasoningPattern,
    Registry,
    RunContext,
    Task,
    ToolCall,
)

reasoning_registry = Registry("reasoning-pattern")


# ═══════════════════════════════════════════════════════════
#  决策函数签名
# ═══════════════════════════════════════════════════════════

#: ReAct 用：给定当前状态，决定下一步
#: 返回 ("call", tool_name, kwargs) 或 ("stop", reason, None)
Decide = Callable[[Task, RunContext, Any], tuple]

#: Plan-and-Execute / ReWOO 用：一次性给出完整计划
#: 返回 [(tool_name, kwargs), ...]
Planner = Callable[[Task, RunContext, Any], List[Tuple[str, Dict[str, Any]]]]

#: Reflexion 用：检查这一轮做得行不行，不行就返回原因
Critic = Callable[[Task, RunContext], Optional[str]]


# ═══════════════════════════════════════════════════════════
#  共用的工具调用逻辑
# ═══════════════════════════════════════════════════════════

def invoke_tool(
    toolbox: Any,
    tool: str,
    kwargs: Dict[str, Any],
    task: Task,
    ctx: RunContext,
    executor_tools: Sequence[str] = (),
) -> Any:
    """所有推理模式共用的工具调用路径。

    这里统一处理两件底盘级的事：
      1. 调用前按工具类型触发对应的知识注入时机
      2. 调用记账，无论成功失败都进 tool_call 表
    """
    if ctx.chassis is not None:
        point = (
            InjectionPoint.BEFORE_EXECUTOR
            if tool in set(executor_tools)
            else InjectionPoint.BEFORE_TOOL
        )
        ctx.chassis.inject(point, task, ctx)

    t0 = time.time()
    try:
        out = toolbox.call(tool, **kwargs)
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
    ctx.facts.setdefault("tool_results", {})[tool] = out
    return out


# ═══════════════════════════════════════════════════════════
#  ReAct：边想边做
# ═══════════════════════════════════════════════════════════

@reasoning_registry.register("react")
class ReActPattern(ReasoningPattern):
    """Reason + Act 交替进行，每次观察结果后再决定下一步。

    特征：**没有预先计划**。收敛点由模型在循环里自己判断。
    适合上下文不完整、需要边取证边调整的任务。
    代价是轮次不可预测，token 消耗随难度上升。
    """

    name = "ReAct"
    description = "边想边做，每步观察后重新决策，收敛点由模型判断"

    def __init__(
        self,
        decide: Decide,
        max_iterations: int = 8,
        executor_tools: Sequence[str] = (),
    ) -> None:
        self.decide = decide
        self.max_iterations = max_iterations
        self.executor_tools = list(executor_tools)

    def reason(self, task: Task, ctx: RunContext, toolbox: Any) -> None:
        for _ in range(self.max_iterations):
            ctx.iterations += 1
            action, target, kwargs = self.decide(task, ctx, toolbox)
            if action == "stop":
                ctx.note(f"[ReAct] 模型判断收敛：{target}")
                return
            invoke_tool(toolbox, target, kwargs or {}, task, ctx, self.executor_tools)
        ctx.note(f"[ReAct] 达到迭代上限 {self.max_iterations}，强制收敛")


# ═══════════════════════════════════════════════════════════
#  Plan-and-Execute：先想清楚再做
# ═══════════════════════════════════════════════════════════

@reasoning_registry.register("plan_execute")
class PlanExecutePattern(ReasoningPattern):
    """先出一份完整计划，再逐步执行；每步执行后允许重新规划。

    特征：**计划是显式产物**，可以在执行前被人审查或被规则校验。
    适合步骤相对确定、希望执行过程可预测的任务。
    与 ReAct 的区别是决策集中在前期，而不是散布在每一步。
    """

    name = "Plan-and-Execute"
    description = "先产出完整计划再执行，计划可被审查；执行中可触发重规划"

    def __init__(
        self,
        planner: Planner,
        executor_tools: Sequence[str] = (),
        max_steps: int = 12,
        replan_on_failure: bool = True,
    ) -> None:
        self.planner = planner
        self.executor_tools = list(executor_tools)
        self.max_steps = max_steps
        self.replan_on_failure = replan_on_failure

    def reason(self, task: Task, ctx: RunContext, toolbox: Any) -> None:
        plan = list(self.planner(task, ctx, toolbox))[: self.max_steps]
        ctx.facts["plan"] = [step[0] for step in plan]
        ctx.note(f"[Plan-and-Execute] 计划 {len(plan)} 步：{' → '.join(ctx.facts['plan'])}")

        executed = 0
        while plan and executed < self.max_steps:
            tool, kwargs = plan.pop(0)
            ctx.iterations += 1
            executed += 1
            try:
                invoke_tool(toolbox, tool, kwargs or {}, task, ctx, self.executor_tools)
            except Exception as exc:
                if not self.replan_on_failure:
                    raise
                ctx.facts["last_error"] = str(exc)
                ctx.note(f"[Plan-and-Execute] 第 {executed} 步失败，重新规划")
                if ctx.chassis is not None:
                    ctx.chassis.inject(InjectionPoint.ON_RETRY, task, ctx)
                plan = list(self.planner(task, ctx, toolbox))[: self.max_steps - executed]
                ctx.facts["replanned"] = ctx.facts.get("replanned", 0) + 1


# ═══════════════════════════════════════════════════════════
#  ReWOO：一次规划，不看中间观察
# ═══════════════════════════════════════════════════════════

@reasoning_registry.register("rewoo")
class ReWOOPattern(ReasoningPattern):
    """Reasoning WithOut Observation：一次规划出全部调用，中途不回喂观察结果。

    特征：**模型只被调用一次**（规划那次），工具调用全部并列执行，
    最后由 solver 汇总。token 消耗最低、延迟最可控。
    代价是无法根据中间结果调整，只适合上下文已经充分的任务。
    """

    name = "ReWOO"
    description = "一次规划出全部调用，中途不回喂观察，token 消耗最低"

    def __init__(
        self,
        planner: Planner,
        solver: Optional[Callable[[Task, RunContext], None]] = None,
        executor_tools: Sequence[str] = (),
    ) -> None:
        self.planner = planner
        self.solver = solver
        self.executor_tools = list(executor_tools)

    def reason(self, task: Task, ctx: RunContext, toolbox: Any) -> None:
        plan = list(self.planner(task, ctx, toolbox))
        ctx.iterations += 1                       # 规划本身算一次模型调用
        ctx.facts["plan"] = [s[0] for s in plan]
        ctx.note(f"[ReWOO] 一次规划 {len(plan)} 步，执行期间不再询问模型")

        for tool, kwargs in plan:
            invoke_tool(toolbox, tool, kwargs or {}, task, ctx, self.executor_tools)

        if self.solver is not None:
            ctx.iterations += 1                   # 汇总是第二次也是最后一次模型调用
            self.solver(task, ctx)


# ═══════════════════════════════════════════════════════════
#  Reflexion：做完自省，不满意重来
# ═══════════════════════════════════════════════════════════

@reasoning_registry.register("reflexion")
class ReflexionPattern(ReasoningPattern):
    """包装另一个推理模式，跑完后自省；不满意就带着反思重来。

    特征：**它是一个装饰器而不是独立模式**，内层可以是 ReAct、
    Plan-and-Execute 或任何其他模式。反思结果通过 ON_RETRY 时机
    回灌，所以下一轮不是简单重跑，而是带着上一轮的教训重跑。
    """

    name = "Reflexion"
    description = "包装其他模式，跑完自省，带着反思重试"

    def __init__(
        self,
        inner: ReasoningPattern,
        critic: Critic,
        max_attempts: int = 2,
    ) -> None:
        self.inner = inner
        self.critic = critic
        self.max_attempts = max_attempts

    def describe(self) -> str:
        return f"Reflexion({self.inner.name})：{self.description}"

    def reason(self, task: Task, ctx: RunContext, toolbox: Any) -> None:
        for attempt in range(1, self.max_attempts + 1):
            ctx.facts["attempt"] = attempt
            self.inner.reason(task, ctx, toolbox)

            complaint = self.critic(task, ctx)
            if complaint is None:
                if attempt > 1:
                    ctx.note(f"[Reflexion] 第 {attempt} 次尝试通过自省")
                return

            ctx.facts["last_error"] = complaint
            ctx.note(f"[Reflexion] 第 {attempt} 次自省不通过：{complaint}")
            if attempt < self.max_attempts:
                if ctx.chassis is not None:
                    ctx.chassis.inject(InjectionPoint.ON_RETRY, task, ctx)
                # 清掉上一轮的工具结果，否则决策函数会以为已经做过
                ctx.facts.pop("tool_results", None)

        ctx.note(f"[Reflexion] {self.max_attempts} 次尝试后仍未通过自省")


__all__ = [
    "Critic",
    "Decide",
    "Planner",
    "PlanExecutePattern",
    "ReActPattern",
    "ReWOOPattern",
    "ReflexionPattern",
    "invoke_tool",
    "reasoning_registry",
]
