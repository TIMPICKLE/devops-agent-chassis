---
name: assemble-digital-employee
description: Use when 用户要基于本仓库（agent-chassis 工程底盘）装配、搭建、配置一台数字员工/DevOps Agent，包括：新建载荷（TaskSource/DoneCriteria/工具集）、选择编排形态（state_machine/nested/single_agent/subgraph）、选择推理模式（ReAct/Plan-and-Execute/Plan-and-Solve/ReWOO/LLMCompiler/Reflexion）、配置知识注入时机、失败契约、权限边界，或把规则模拟换成真实 LLM。触发词：装配数字员工、搭建 Agent、新载荷、换编排、SubgraphOrchestrator、assemble、payload。
---

# 装配一台数字员工（agent 访谈式装配）

## Overview

本仓库是零依赖的 Python 工程底盘：**底盘五大系统一行不改，业务方只写载荷 + 装配脚本**。
你（agent）的工作是：**逐组访谈用户 → 生成载荷与装配脚本 → 实际运行验证**。

不要把装配做成固定模板填空：和用户来回商量，装出任意合法形态——包括
SubgraphOrchestrator 多支路分流、LLMCompiler DAG 并行、按支路挂不同推理模式、
人工介入点等需要根据业务量身设计的结构。

## 必读文件（按需，不要全读）

| 文件 | 什么时候读 |
|---|---|
| [src/agent_chassis/contracts.py](../../../src/agent_chassis/contracts.py) | 开工前必读，唯一必须读懂的文件：全部抽象与 InjectionPoint 枚举 |
| [payloads/code_quality.py](../../../payloads/code_quality.py) | 生成新载荷前读：TaskSource/DoneCriteria/ToolBox/decider/planner/critic 的范本 |
| [examples/04_swap_payload.py](../../../examples/04_swap_payload.py) | 生成装配脚本前读：Chassis 链式接线 + 双载荷对照的范本 |
| [examples/01_swap_orchestration.py](../../../examples/01_swap_orchestration.py) | 用户要 subgraph / llm_compiler / basic_reflection 时读：全部编排组合的构造配方 |

## 访谈流程

**一次只问一组问题，每组都给出推荐默认值。** 用户答不上来就用默认值继续，
不要在一个问题上卡住。顺序如下：

1. **载荷**：任务从哪来（扫描器告警 / PR 评论 / 构建失败 / 工单…）？
   怎么算做完（读哪个外部系统的什么客观状态）？现成载荷
   （code_quality / pr_mention）够用就复用，不够就生成新载荷。
2. **① 编排·外层**：见下方选型表。关键探询：任务要不要按类型/难度分流？
   不同类别要不要不同的思考方式？要不要人工介入点？——任一为是 → subgraph。
3. **① 编排·内层**：见下方选型表。关键探询：上下文完整吗（完整→规划类省 token，
   不完整→ReAct）？做错了要不要基于外部事实自动重试（要→Reflexion 包一层）？
4. **③ 知识注入**：挂哪些 provider、什么时机。默认 SkillProvider@BEFORE_EXECUTOR +
   RetryFeedback@ON_RETRY。**AGENT_BOOT 刻意留空**，用户坚持才挂并说明代价
   （决策层会绑定技术栈，底盘与业务不再解耦）。
5. **④ 失败契约**：ZeroSideEffect（默认）还是 RetryThenGiveUp(max_retries)？
   Ledger 要不要落盘（跨进程去重）？
6. **权限边界**：执行器授予哪些能力。默认只给 `repo.read` + `repo.write`；
   用户要授予不可逆能力（vcs.commit/push、pr.create、notify.send…）时必须提醒：
   生产上这些通常由确定性代码持有，不给执行器。
7. **⑤ 可观测 + 运行**：ConsoleObserver / RecordingObserver(cron|webhook)、跑几轮、
   连接器挂载（scheme：`mock` / `mcp.stdio` / `mcp.http` / `rest`）。

## 选型表

### 外层流程（任务被推进的骨架）

| 形态 | 选它当 | 构造 |
|---|---|---|
| `NestedOrchestrator` | 默认推荐：确定性骨架 + 单一下放点 | `NestedOrchestrator(steps, box, pattern, delegate_at="agent_work", criteria)` |
| `StateMachineOrchestrator` | 线性阶段，下放点是某个 AgentStep | `StateMachineOrchestrator(steps, criteria)`，AgentStep 传 `pattern=` 和 `toolbox=` |
| `SingleAgentOrchestrator` | 开放式求解，无需确定性阶段把守 | `SingleAgentOrchestrator(box, pattern, criteria)` |
| `SubgraphOrchestrator` | 按类型分流、支路挂不同模式、要人工介入 | 见下方配方 |

### 内层推理模式（下放点内部，模型怎么想）

| 模式 | 需要载荷提供 | 选它当 |
|---|---|---|
| `ReActPattern(decide, max_iterations, executor_tools)` | decider 函数 | 上下文不完整，边取证边调整 |
| `PlanExecutePattern(planner, executor_tools)` | planner 函数 | 计划需可审查，失败要重规划 |
| `PlanAndSolvePattern(planner, executor_tools)` | planner 函数 | 一次调用出计划即答案，最省事 |
| `ReWOOPattern(planner, solver=None, executor_tools)` | planner（kwargs 可写 `"#E1"` 引用前步结果） | token 最省，固定两次模型调用 |
| `LLMCompilerPattern(dag_planner, joiner, executor_tools)` | DagPlanner 返回 `List[PlanNode]`（带 deps） | 工具调用有真实延迟，无依赖步骤要并行 |
| `BasicReflectionPattern(inner, reflector, rounds)` | reflector（只看模型自述） | 修表达和完整性，修不了"事实上没做对" |
| `ReflexionPattern(inner, critic, max_attempts)` | critic（**只读客观事实**） | 要发现"模型以为做完了其实没做" |

后两个是装饰器，包住前五个任意一个。

### Subgraph 配方（需要和用户商量出结构）

和用户商量清楚：分析阶段收集什么事实 → 路由函数按什么分流 → 每条支路
挂什么模式 → 什么条件触发人工介入。骨架（完整版见 examples/01 的 `flow_subgraph`）：

```python
SubgraphOrchestrator(
    analysis=Subgraph("分析", [FnStep("pull", pull), FnStep("classify", classify)]),
    router=lambda t, c: "deep" if c.facts["difficulty"] == "high" else "light",
    branches={
        "light": Subgraph("轻量", [FnStep("prepare", prepare),
                                   AgentStep("fix", pattern=rewoo, toolbox=box)]),
        "deep": Subgraph("深度", [FnStep("prepare", prepare),
                                  AgentStep("fix", pattern=reflexion_react, toolbox=box)]),
    },
    delivery=Subgraph("交付", [FnStep("deliver", deliver)]),
    criteria=criteria,
    interrupt_if=lambda t, c: c.facts.get("difficulty") == "high",  # 可选
    on_interrupt=lambda t, c: True,  # 返回 False 则任务 SKIPPED
)
```

路由函数返回的支路名必须在 `branches` 里，否则 KeyError。

### 知识注入时机

`TASK_ADMITTED`（任务级背景）｜`BEFORE_TOOL`（约束单次调用）｜
`BEFORE_EXECUTOR`（生产推荐，调外部执行器前最后一步）｜
`ON_RETRY`（回灌上次失败原因）｜`BEFORE_VERDICT`（补充判定口径）｜
`AGENT_BOOT`（刻意留空，慎用）。
注意：`executor_tools` 列表决定哪些工具触发 BEFORE_EXECUTOR 而非 BEFORE_TOOL，忘配则 Skill 注不进去。

## 生成产物

- 新载荷 → `payloads/<模块名>.py`，照 code_quality.py 的骨架：TaskSource、
  DoneCriteria、build_toolbox、make_decider/make_planner（规则模拟 + TODO 标注）、
  make_critic（若用 Reflexion）
- 装配脚本 → `generated/<名字>.py`，照 examples/04 的链式接线，顶部注入
  `sys.path`（`src/` 和仓库根），末尾 `print(chassis.report().render())` + 运行循环

## 硬约束（生成的代码必须满足）

1. **DoneCriteria.judge 只读 ctx.facts 和外部系统状态，绝不读 ctx.model_notes**。
   模型说"我做完了"不算数。Reflexion 的 critic 同理。
2. TaskSource 的 payload 带 `"target": {"path": ...}`，否则 SkillProvider 两级路由失效。
3. 执行器权限默认只给 `repo.read`/`repo.write`；不可逆能力走确定性代码。
4. 规则模拟的 decider/planner 必须标 TODO：真实系统换成 LLM function calling
   （把 `box.schema()` 与 `ctx.facts` 喂给模型，返回 `("call", 工具名, 参数)` 或
   `("stop", 理由, None)`）。
5. Ledger 定义了 `__len__`，空账本是 falsy——判空用 `is None`，别写 `ledger or Ledger()`。

## 验证（不跑不算完成）

```bash
python generated/<名字>.py    # 预期：装配报告 + 任务 succeeded + 裁定来自客观事实
python -m pytest tests/ -q    # 预期：全部通过（确认没改坏底盘）
```

装配报告里检查三件事：决策下放点是不是只有商量好的那一个；
injection timeline 的 agent_boot 行是否为空；权限边界拒绝清单是否符合预期。
