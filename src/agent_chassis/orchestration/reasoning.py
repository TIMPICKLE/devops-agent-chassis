"""
内层推理模式 —— Agent 设计模式的可插拔实现。

底盘把编排拆成两个正交的轴。本模块是内层那一轴：

    无计划、边想边做
    └─ ReAct              每步观察后重新决策，收敛点由模型判断

    先规划再执行
    ├─ Plan-and-Execute   每步再问一次模型，失败可重规划
    ├─ Plan-and-Solve     一次调用出计划即答案，执行期不再问
    ├─ ReWOO              计划带 #E1 证据变量，执行后 Solver 汇总
    └─ LLMCompiler        规划出带依赖的 DAG，无依赖任务并行成波次

    做完再回头看（装饰器，包住上面任一种）
    ├─ Basic Reflection   纯自评，固定轮数，反思用完即弃
    └─ Reflexion          外部评估器判定，反思累积成情景记忆

外层决定「任务被推进的骨架」，内层决定「在下放点内部，模型怎么想」。
两者独立替换：同一个流程可以换推理模式，同一个推理模式可以放进不同流程。

`NestedOrchestrator` 不是第三种形态，它是把两个轴接起来的组合算子。
"""
from __future__ import annotations

import time
from dataclasses import dataclass, field
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

#: 线性规划器（Plan-and-Execute / Plan-and-Solve / ReWOO）：一次给出完整计划
#: 返回 [(tool_name, kwargs), ...]；ReWOO 的 kwargs 里可写 "#E1" 引用前面步骤的结果
Planner = Callable[[Task, RunContext, Any], List[Tuple[str, Dict[str, Any]]]]

#: 外部评估器（Reflexion 用）：基于事实判定这一轮行不行，不行就返回原因
Critic = Callable[[Task, RunContext], Optional[str]]

#: 自评器（Basic Reflection 用）：模型看自己刚才的输出，提一条意见
#: 与 Critic 的关键差别：它读不到客观事实，只能看模型自己产生的东西
Reflector = Callable[[Task, RunContext], Optional[str]]

#: LLMCompiler 的 Joiner：看一波 DAG 跑完的结果，决定收工还是重新规划
Joiner = Callable[[Task, RunContext], Optional[str]]


@dataclass
class PlanNode:
    """LLMCompiler 的 DAG 节点。

    `deps` 里写其他节点的 id；args 里出现 "$1" 这样的字符串会被替换成
    对应节点的实际结果。没有依赖关系的节点会被分到同一个波次。
    """
    id: str
    tool: str
    args: Dict[str, Any] = field(default_factory=dict)
    deps: Sequence[str] = ()


#: LLMCompiler 用：规划出一张带依赖的任务图
DagPlanner = Callable[[Task, RunContext, Any], List[PlanNode]]


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


def _substitute(args: Dict[str, Any], evidence: Dict[str, Any]) -> Dict[str, Any]:
    """把 "#E1" / "$1" 这样的占位符换成前面步骤的实际结果。

    这是 ReWOO 和 LLMCompiler 能在「不回喂观察给模型」的前提下
    仍然表达步骤依赖的原因：依赖在计划里是显式变量，由执行器解，不由模型解。
    """
    out = {}
    for k, v in args.items():
        out[k] = evidence[v] if isinstance(v, str) and v in evidence else v
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
#  Plan-and-Execute：计划显式，每步再问一次
# ═══════════════════════════════════════════════════════════

@reasoning_registry.register("plan_execute")
class PlanExecutePattern(ReasoningPattern):
    """先出一份完整计划，再逐步执行；每一步都是一次独立的模型调用。

    特征：**计划是显式产物**，可以在执行前被人审查或被规则校验；
    执行中失败会触发重规划，所以它能应对计划做错的情况。
    代价是每步一次模型调用，token 消耗接近 ReAct。
    """

    name = "Plan-and-Execute"
    description = "先产出完整计划再执行，每步一次模型调用，失败可重规划"

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
#  Plan-and-Solve：一次调用，计划即答案
# ═══════════════════════════════════════════════════════════

@reasoning_registry.register("plan_and_solve")
class PlanAndSolvePattern(ReasoningPattern):
    """一次模型调用里既定计划又给解法，执行期完全不再问模型。

    与 Plan-and-Execute 的差别不在于有没有计划，而在于**计划之后还问不问**：
    Plan-and-Execute 每步一次调用、失败能重规划；这里从头到尾只有一次调用。

    与 ReWOO 的差别只有一条：ReWOO 还要为 Solver 汇总再付一次 token。
    差别很小，但它决定了「要不要一段自然语言的结论」值不值那次调用。
    """

    name = "Plan-and-Solve"
    description = "一次调用出计划即答案，执行期不再问模型，无重规划"

    def __init__(
        self,
        planner: Planner,
        executor_tools: Sequence[str] = (),
        max_steps: int = 12,
    ) -> None:
        self.planner = planner
        self.executor_tools = list(executor_tools)
        self.max_steps = max_steps

    def reason(self, task: Task, ctx: RunContext, toolbox: Any) -> None:
        ctx.iterations += 1                       # 全程唯一一次模型调用
        plan = list(self.planner(task, ctx, toolbox))[: self.max_steps]
        ctx.facts["plan"] = [s[0] for s in plan]
        ctx.note(f"[Plan-and-Solve] 一次调用给出 {len(plan)} 步，之后不再询问模型")

        for tool, kwargs in plan:
            invoke_tool(toolbox, tool, kwargs or {}, task, ctx, self.executor_tools)


# ═══════════════════════════════════════════════════════════
#  ReWOO：证据变量 + Solver 汇总
# ═══════════════════════════════════════════════════════════

@reasoning_registry.register("rewoo")
class ReWOOPattern(ReasoningPattern):
    """Reasoning WithOut Observation：规划、执行、汇总三段分离。

    特征：计划里用 `#E1` 这样的**证据变量**表达步骤依赖，由执行器负责解，
    模型全程看不到中间观察。固定两次模型调用：规划一次、Solver 汇总一次。

    token 消耗接近下限，延迟可预测。代价是无法根据中间结果调整，
    只适合上下文已经充分、步骤依赖能在规划期就写清楚的任务。
    """

    name = "ReWOO"
    description = "计划带 #E1 证据变量，执行后 Solver 汇总，固定两次模型调用"

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
        ctx.iterations += 1                       # ① Planner
        plan = list(self.planner(task, ctx, toolbox))
        ctx.facts["plan"] = [s[0] for s in plan]
        ctx.note(f"[ReWOO] 一次规划 {len(plan)} 步，执行期间不再询问模型")

        evidence: Dict[str, Any] = {}
        for idx, (tool, kwargs) in enumerate(plan, start=1):
            resolved = _substitute(kwargs or {}, evidence)
            evidence[f"#E{idx}"] = invoke_tool(
                toolbox, tool, resolved, task, ctx, self.executor_tools)
        ctx.facts["evidence"] = list(evidence)

        if self.solver is not None:
            ctx.iterations += 1                   # ② Solver
            self.solver(task, ctx)


# ═══════════════════════════════════════════════════════════
#  LLMCompiler：DAG 规划 + 波次并行 + Joiner
# ═══════════════════════════════════════════════════════════

@reasoning_registry.register("llm_compiler")
class LLMCompilerPattern(ReasoningPattern):
    """把任务编译成一张带依赖的 DAG，无依赖的节点并行成波次执行。

    与 ReWOO 的关键差别：ReWOO 的计划是**线性**的，第 3 步必须等第 2 步；
    这里只要没有显式依赖就可以同波执行。串行长度从步数降到 DAG 深度，
    这在工具调用有真实延迟时是数量级的差别。

    执行完一轮后由 Joiner 判断收工还是重新编译。演示实现里波次是顺序跑的，
    因为底盘是同步的；`ctx.facts["waves"]` 记录了真正的并行结构。
    """

    name = "LLMCompiler"
    description = "规划成带依赖的 DAG，无依赖节点并行成波次，Joiner 决定收工或重编译"

    def __init__(
        self,
        planner: DagPlanner,
        joiner: Optional[Joiner] = None,
        executor_tools: Sequence[str] = (),
        max_rounds: int = 2,
    ) -> None:
        self.planner = planner
        self.joiner = joiner
        self.executor_tools = list(executor_tools)
        self.max_rounds = max_rounds

    @staticmethod
    def _waves(nodes: Sequence[PlanNode]) -> List[List[PlanNode]]:
        """按依赖分波。有环或依赖缺失就直接报错，不静默降级成串行。"""
        pending = {n.id: n for n in nodes}
        done: set = set()
        waves: List[List[PlanNode]] = []
        while pending:
            ready = [n for n in pending.values() if all(d in done for d in n.deps)]
            if not ready:
                raise ValueError(f"DAG 存在环或悬空依赖：{sorted(pending)}")
            waves.append(ready)
            for n in ready:
                done.add(n.id)
                pending.pop(n.id)
        return waves

    def reason(self, task: Task, ctx: RunContext, toolbox: Any) -> None:
        for round_no in range(1, self.max_rounds + 1):
            ctx.iterations += 1                   # ① Planner（每轮重编译一次）
            nodes = list(self.planner(task, ctx, toolbox))
            waves = self._waves(nodes)
            ctx.facts["waves"] = [[n.id for n in w] for w in waves]
            ctx.note(
                f"[LLMCompiler] {len(nodes)} 个节点编译成 {len(waves)} 波"
                f"（串行长度 {len(waves)}，非 {len(nodes)}）"
            )

            evidence: Dict[str, Any] = {}
            for wave in waves:
                for node in wave:                 # 同波之间无依赖，可并行
                    evidence[node.id] = invoke_tool(
                        toolbox, node.tool, _substitute(node.args, evidence),
                        task, ctx, self.executor_tools)

            if self.joiner is None:
                return
            ctx.iterations += 1                   # ② Joiner
            complaint = self.joiner(task, ctx)
            if complaint is None:
                return
            ctx.facts["last_error"] = complaint
            ctx.note(f"[LLMCompiler] 第 {round_no} 轮 Joiner 判定需重编译：{complaint}")
            if round_no < self.max_rounds and ctx.chassis is not None:
                ctx.chassis.inject(InjectionPoint.ON_RETRY, task, ctx)


# ═══════════════════════════════════════════════════════════
#  Basic Reflection：纯自评，反思用完即弃
# ═══════════════════════════════════════════════════════════

@reasoning_registry.register("basic_reflection")
class BasicReflectionPattern(ReasoningPattern):
    """生成 → 自评 → 重新生成，固定轮数跑完。

    它是最朴素的那种反思：**评价者就是模型自己**，看的也只是自己的输出，
    没有任何外部信号。反思用完即弃，下一轮只带最近一条意见。

    因此它能修的是「表达和完整性」，修不了「事实上没做对」——
    模型觉得自己做对了，它就会一直觉得自己做对了。要修后者得用 Reflexion。
    """

    name = "Basic Reflection"
    description = "生成→自评→重生成，固定轮数，评价者是模型自己，反思用完即弃"

    def __init__(
        self,
        inner: ReasoningPattern,
        reflector: Reflector,
        rounds: int = 2,
    ) -> None:
        self.inner = inner
        self.reflector = reflector
        self.rounds = rounds

    def describe(self) -> str:
        return f"Basic Reflection({self.inner.name})：{self.description}"

    def reason(self, task: Task, ctx: RunContext, toolbox: Any) -> None:
        for round_no in range(1, self.rounds + 1):
            ctx.facts["reflection_round"] = round_no
            self.inner.reason(task, ctx, toolbox)

            if round_no == self.rounds:
                break
            ctx.iterations += 1                   # 自评本身是一次模型调用
            note = self.reflector(task, ctx)
            if note is None:
                ctx.note(f"[Basic Reflection] 第 {round_no} 轮自评无意见，提前结束")
                return
            # 只保留最近一条：这就是它与 Reflexion 的分水岭
            ctx.facts["reflection"] = note
            ctx.note(f"[Basic Reflection] 第 {round_no} 轮自评：{note}")


# ═══════════════════════════════════════════════════════════
#  Reflexion：外部评估 + 情景记忆
# ═══════════════════════════════════════════════════════════

@reasoning_registry.register("reflexion")
class ReflexionPattern(ReasoningPattern):
    """包装另一个推理模式，用**外部评估器**判定，反思**累积**成情景记忆。

    与 Basic Reflection 的两条硬差别：
      1. 判定权在 critic 手上，critic 读 ctx.facts（客观事实），不读模型自述
      2. 历次反思全部留在 ctx.facts["reflections"] 里往后传，而不是只带最近一条

    第一条决定了它能不能发现「模型以为做完了其实没做」；
    第二条决定了它会不会在第三轮把第一轮踩过的坑再踩一遍。
    """

    name = "Reflexion"
    description = "外部评估器判定，反思累积成情景记忆，带着历次教训重试"

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
        memory: List[str] = ctx.facts.setdefault("reflections", [])
        for attempt in range(1, self.max_attempts + 1):
            ctx.facts["attempt"] = attempt
            self.inner.reason(task, ctx, toolbox)

            complaint = self.critic(task, ctx)
            if complaint is None:
                if attempt > 1:
                    ctx.note(f"[Reflexion] 第 {attempt} 次尝试通过外部评估")
                return

            memory.append(f"第 {attempt} 次：{complaint}")
            ctx.facts["last_error"] = complaint
            ctx.note(f"[Reflexion] 外部评估不通过：{complaint}（已累积 {len(memory)} 条教训）")
            if attempt < self.max_attempts:
                if ctx.chassis is not None:
                    ctx.chassis.inject(InjectionPoint.ON_RETRY, task, ctx)
                # 清掉上一轮的工具结果，否则决策函数会以为已经做过
                ctx.facts.pop("tool_results", None)

        ctx.note(f"[Reflexion] {self.max_attempts} 次尝试后仍未通过外部评估")


__all__ = [
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
