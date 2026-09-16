# 底盘架构与装配指南

本文从首页拆出，说明五大系统、权限边界与装配接口。先运行 [快速开始](../README.md#快速开始)，或阅读 [岗位配方库](ROLE_RECIPES.md) 复用已有员工。

代码片段中的业务回调由装配方提供；完整运行示例见 [examples](../examples/)。实际能力与验证范围见 [当前状态](../roadmap/CURRENT_STATE.md)。

## 编排：流程与推理模式

「编排形态」这个词经常把两件不同的事混在一起说。底盘把它们拆开：

| 轴 | 管什么 | 换它会影响 |
|---|---|---|
| **外层 · 流程编排** `Orchestrator` | 任务被推进的骨架：阶段顺序、分支、路由、人工介入 | 流程的形状 |
| **内层 · Agent 设计模式** `ReasoningPattern` | 在下放点内部，模型怎么想 | 轮数、token、可预测性 |

两轴独立替换。同一个流程可以换推理模式，同一个推理模式可以放进不同流程。

### 外层：流程编排

| 形态 | 说明 | 下放点 |
|---|---|---|
| `StateMachineOrchestrator` | 线性状态机，一条主路径加一条失败短路 | 被标记的那个阶段 |
| `SingleAgentOrchestrator` | 没有外层骨架，整个任务交给一个推理模式 | 整个任务 |
| `SubgraphOrchestrator` | 分析子图 → 路由 → 修复支路 → 人工介入 → 交付子图 | 各支路里的 AgentStep |
| `NestedOrchestrator` | **不是第三种形态，是两轴的组合算子**：外层骨架 + 指定阶段下放给内层模式 | 外层的一个阶段 |

### 内层：推理模式

| 模式 | 特征 | 代价 |
|---|---|---|
| `ReActPattern` | 逐步决策和工具反馈；可用 `stop_when` 客观停止，最终仍需验收 | 次数受迭代与模型预算限制 |
| `PlanExecutePattern` | 调用 planner 产出计划，逐步执行工具，异常时可重规划；并非自动每步请求模型 | 请求次数取决于 planner 和工具实现 |
| `PlanAndSolvePattern` | 一次 planner 回调生成计划，然后执行工具 | 无重规划；回调不一定调用模型 |
| `ReWOOPattern` | planner 生成带 `#E1` 证据变量的计划；执行后可选 solver 汇总 | 无 solver 时仅 planner 回调，不能固定推算模型次数 |
| `LLMCompilerPattern` | 编译成带依赖的 **DAG**，按依赖分波；当前实现波内仍串行，Joiner 决定收工或重编译 | 真正并发尚待实现与测量 |
| `BasicReflectionPattern` | **装饰器**：生成→自评→重生成，固定轮数，反思用完即弃 | 评价者就是模型自己 |
| `ReflexionPattern` | **装饰器**：外部评估器判定，在当前运行上下文内累积反馈 | 成本取决于重试和模型接入，不是已测得的费用排名 |

几组容易被混为一谈的差别，它们决定了这些为什么是独立的类：

| 常被混淆的一对 | 真正的差别 |
|---|---|
| Plan-and-Execute vs Plan-and-Solve | 当前实现中前者可在工具异常后重新调用 planner；后者执行一次计划、不重规划 |
| Plan-and-Solve vs ReWOO | ReWOO 支持证据变量替换及可选 Solver；实际模型调用取决于回调实现 |
| ReWOO vs LLMCompiler | ReWOO 的计划是线性的；LLMCompiler 只要没有显式依赖就可同波执行 |
| Basic Reflection vs Reflexion | 前者纯自评、反思用完即弃；后者由外部评估器判定、反思累积 |

模型自评不能替代业务验收。无论使用哪种反思方式，最终仍需 `DoneCriteria`
根据事实或外部状态判断任务是否完成。

底盘不替业务选哪一种，只保证能换。每个编排器通过 `delegation_points` 声明下放点，
`reasoning_name` 声明内层模式，装配报告会把两者都打出来。

```python
chassis.with_orchestrator(
    NestedOrchestrator(
        outer_steps=steps,                       # 外层骨架
        toolbox=box,
        pattern=PlanExecutePattern(planner),     # 内层模式，换这一行就够
        delegate_at="agent_fix",
        criteria=criteria,
    )
)
```

`examples/01` 分三部分证明两轴正交：固定内层换外层、固定外层换内层，
最后把 7 × 3 全矩阵**以确定性回调各跑一遍**，打出每一格的模拟决策调用次数；不是远端模型调用或真实 token 账单：

实际计数由示例运行时输出，不在文档中重复维护。

这些计数说明示例控制流的调用差异；它不能单独证明某种模式在真实模型上的效果或成本。生产模式选型需同条件 live 对照。

**关于工作流引擎**：线性五阶段只有一条主路径和一条失败短路，没有分支、并发、循环。
这种形状引入引擎不产生收益，只多一层需要理解和调试的抽象。所以 `StateMachineOrchestrator`
就是一个循环加一个异常判断。真正需要引擎的是 `SubgraphOrchestrator` 那种形状 ——
它的不同支路还可以挂不同的推理模式，例如命名类走 ReWOO，复杂问题走
Reflexion 包 ReAct。是否节省 token 或提高质量，需要在相同任务和验收条件下测量。

---

## 接入：连接器与工具

任务读取、交付等阶段可以由确定性代码直接调用接口；需要模型选择的操作再包装成工具。
底盘的连接器是**独立可直接调用**的，Agent 想用时再由编排器包装成工具暴露给它。
同一个能力可以两边都出现，但走的是两条路。

内置 `mock` / `mcp.stdio` / `mcp.http` / `rest`。加新的只需注册一个类：

```python
@connector_registry.register("jira")
class JiraConnector(Connector):
    def _discover(self): ...
    def _invoke(self, tool, args): ...

mgr.mount("tickets", "jira", project="DEV")
```

**工具名容错解析**：MCP 生态还在演进，server 升级会改工具名。生产里
`issues` / `issues/search` / `issues.search` / `issues_search` 四种写法都遇到过。
所以调用时传候选列表而不是单一名字，全部失配时退化为关键词匹配，
再失配抛出带完整可用清单的异常：

```python
mgr.call("scanner",
         preferred=["issues", "issues/search", "issues.search", "issues_search"],
         keywords=["issues"],
         args={"project": "demo-service"})
```

---

## 知识：注入时机与内容路由

项目规范可以在执行器调用前按任务和阶段提供，让通用流程与知识内容分开维护。
只有变化局限于既有知识内容时，才可能只改 Markdown；更换技术栈若涉及工具、接口或判据，也需要调整项目代码。

底盘把六个时机做成显式枚举，每个 provider 声明自己在哪些时机生效：

| 时机 | 用途 |
|---|---|
| `AGENT_BOOT` | 启动层注入点；本仓库装配约定刻意留空 |
| `TASK_ADMITTED` | 任务准入后，补充任务级背景 |
| `BEFORE_TOOL` | 每次工具调用前，约束单次调用 |
| `BEFORE_EXECUTOR` | 调外部执行器前收集知识；当前真实模型参考路径在每次模型请求前触发 |
| `ON_RETRY` | 重试前，把上次失败原因回灌 |
| `BEFORE_VERDICT` | 裁定前，补充判定口径 |

```python
SkillProvider(skills, points=[InjectionPoint.BEFORE_EXECUTOR])
```

业务知识按任务和执行阶段注入；遵守仓库约定，将 `AGENT_BOOT` 留空。

**两级路由**：当前使用显式规则选择规范，不做相似度检索。规则需要由项目提供：

```python
SkillLibrary(root="skills", rules=[
    by_extension({".cs": "abp-net-backend"}),
    by_filename_markers([".ts", ".html"],
                        markers=["component", "service", "module", "guard"],
                        hit="angular-frontend", miss="typescript-common"),
])
```

`user-list.component.ts` 和 `date-utils.ts` 拿到两份不同的规范。

---

## 失败：终态、去重与清理

默认失败策略记录终态、去重，并执行项目已注册的清理回调；它不会自动撤销所有外部副作用，也不保证无需人工处理。

`WorkspaceGuard` 可在开工前运行项目提供的检查或准备回调。处理范围应是任务自己的资源，不覆盖用户已有修改。以下函数由业务方实现：

```python
policy = ZeroSideEffectPolicy(Ledger("state/ledger.json"))
policy.register_cleanup("丢弃未推送的工作分支", discard_branch)

guard = WorkspaceGuard().add("检查任务工作区", check_task_workspace)
```

去重表同时是失败表，失败过的任务默认不再重试。这是个明确的取舍：
好处是不在同一道难题上反复烧算力，代价是模型升级后历史失败样本不会自动重跑。
`Ledger(retry_failed=True)` 把这个取舍交还给你。

---

## 可观测：记录与回放

`task` / `trace` / `tool_call` / `health` 四张表里不出现任何场景专属概念。
换载荷不需要改表结构 —— 这是「底盘与业务无关」在数据层的体现。

```python
rec = RecordingObserver(subject="quality-bot", mode="cron", path="state/obs.json")
chassis.observe(ConsoleObserver(), rec)

rec.health()          # 在岗状态、成功率
rec.replay(run_id)    # 逐步回放某一次执行
rec.snapshot()        # 喂给只读 API 或看板
```

---

## 权限边界

底盘允许集成任何第三方 Agent 作为执行器，但集成不等于全权委托。
执行器通常自带完整的版本控制能力，底盘要做的是**在授予能力的同时收回权限**。

受控工具通过 `boundary.check()` 检查能力，未授权时抛出异常。业务方需要把检查接入实际工具入口；这个 Python 权限对象本身不是操作系统沙箱，不能限制绕过工具入口启动的外部进程：

```python
boundary = borrowed_executor("claude-code-cli")   # 只给 repo.read / repo.write

def apply_fix(path, note):
    boundary.check("repo.write")   # 通过
    ...

def try_commit(message):
    boundary.check("vcs.commit")   # PermissionDenied
```

被拒绝的调用会记进 `boundary.denials`，进审计。边界生效过，是有证据的。

---

## 装配结构示例

以下是自定义 decider 的默认单调用结构示意，业务变量由装配方提供。接参考模型适配器时，
用 `react_pattern(config, decider, toolbox=box)` 统一传递配置；完整可运行接线见[示例 07](../examples/07_verified_assembly.py)。

```python
from agent_chassis import Chassis, ConsoleObserver, InjectionPoint, borrowed_executor
from agent_chassis.knowledge import SkillLibrary, SkillProvider, by_extension
from agent_chassis.orchestration import NestedOrchestrator, ReActPattern
from agent_chassis.failure import Ledger, ZeroSideEffectPolicy
from agent_chassis.observability import RecordingObserver

chassis = (
    Chassis("代码质量治理数字员工")
    .with_orchestrator(NestedOrchestrator(
        outer_steps=outer_steps,                  # 外层·流程
        toolbox=box,
        pattern=ReActPattern(decide),             # 内层·Agent 设计模式
        delegate_at="agent_fix",
    ))
    .mount("scanner", "mcp.stdio", command="sonarqube-mcp")
    .mount("vcs", "mcp.stdio", command="azure-devops-mcp")
    .with_knowledge(SkillProvider(skills, points=[InjectionPoint.BEFORE_EXECUTOR]))
    .with_failure_policy(ZeroSideEffectPolicy(Ledger("state/ledger.json")))
    .with_boundary(borrowed_executor("claude-code-cli"))
    .observe(ConsoleObserver(), RecordingObserver())
    .with_payload(task_source, done_criteria)
    .build()
)

print(chassis.report().render())   # 装配自检：这台机器由什么构成
chassis.run_once()
```

`build()` 会在装配期就检查完整性。缺编排器或缺载荷会立刻失败，
而不是等到凌晨三点跑起来才失败。

### 使用 AI 编程助手装配

仓库内置了 agent 专用的装配 skill（[.claude/skills/assemble-digital-employee/](../.claude/skills/assemble-digital-employee/SKILL.md)，
入口见 [AGENTS.md](../AGENTS.md)）。在 Claude Code / Copilot 等 AI 助手里说
「装配一台数字员工」，agent 会按五大系统的顺序逐组访谈：

| 步骤 | 问什么 | 可选项 |
|---|---|---|
| 载荷 | 任务从哪来、怎么算做完 | 复用 code_quality / pr_mention，或生成新载荷 |
| ① 编排 | 外层流程 × 内层推理 | nested / state_machine / single_agent / **subgraph 多支路** × ReAct / Plan-and-Execute / Plan-and-Solve / ReWOO / **LLMCompiler DAG**，可包 Basic Reflection / Reflexion |
| ③ 知识注入 | 挂哪些 provider、什么时机 | SkillProvider / StaticKnowledge / RetryFeedback，6 个注入点（AGENT_BOOT 刻意留空） |
| ④ 失败契约 | 失败后留下什么 | ZeroSideEffect / RetryThenGiveUp，账本可落盘 |
| 权限边界 | 授予执行器哪些能力 | 默认只给 repo.read/repo.write，不可逆能力会被提醒 |
| ⑤ 可观测 | 挂哪些观察者 | Console / Recording(cron/webhook)，另可挂载连接器 |

访谈结束后 agent 生成载荷与装配脚本，并实际运行验证。因为是 agent
在做而不是固定模板，SubgraphOrchestrator 的支路结构、按支路挂不同
推理模式、人工介入点这类需要来回商量的形态也能装出来。

[返回首页](../README.md) · [节点、知识与运行证据](TECHNICAL_USE_CASE.md)
