# Agent Chassis

**DevOps 数字员工工程底盘。**

一台数字员工 = 底盘（与业务无关的五大系统）+ 载荷（与业务有关的两项定义）。

底盘回答的是任何 DevOps 数字员工都要回答的同一组问题：它可不可靠、怎么接外部系统、
输出符不符合规范、失败了谁收拾、凭什么敢上生产。这五个问题与你做的是代码治理还是
测试补齐完全无关，所以它们只该被回答一次。

载荷回答的是剩下的两个问题：任务从哪来，怎么算做完。

```
底盘 Chassis（本仓库）              载荷 Payload（业务方提供）
├─ ① 编排契约  Orchestrator          ├─ TaskSource    任务从哪来
│    └ 外层流程 + 内层 ReasoningPattern  └─ DoneCriteria  怎么算做完
├─ ② 接入层    Connector                 + 领域工具集
├─ ③ 知识注入  KnowledgeProvider
├─ ④ 失败契约  FailurePolicy
└─ ⑤ 可观测    Observer
```

---

## 五分钟看懂

零依赖，Python 3.9+，直接跑：

```bash
python examples/01_swap_orchestration.py   # 换编排方式，载荷代码一行不动
python examples/02_plug_connector.py       # 加一个外部系统，注册一个类就够
python examples/03_injection_timing.py     # 知识注入时机可视化
python examples/04_swap_payload.py         # 换载荷，底盘装配代码一字不差
python examples/05_permissions_and_failure.py  # 能力借来，权限不借
```

---

## ① 编排契约：编排是两个正交的轴

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

### 内层：Agent 设计模式

| 模式 | 特征 | 代价 |
|---|---|---|
| `ReActPattern` | 无计划，每步观察后重新决策，收敛点由模型判断 | 轮次不可预测 |
| `PlanExecutePattern` | 计划是显式产物可被审查，**每步再问一次**，失败可重规划 | token 接近 ReAct |
| `PlanAndSolvePattern` | **一次调用**出计划即答案，执行期不再问 | 无重规划，计划错了就错到底 |
| `ReWOOPattern` | 计划带 `#E1` 证据变量表达依赖，执行后 Solver 汇总 | 固定两次调用，中途不能调整 |
| `LLMCompilerPattern` | 编译成带依赖的 **DAG**，无依赖节点并行成波，Joiner 决定收工或重编译 | 规划器得能写对依赖 |
| `BasicReflectionPattern` | **装饰器**：生成→自评→重生成，固定轮数，反思用完即弃 | 评价者就是模型自己 |
| `ReflexionPattern` | **装饰器**：外部评估器判定，反思累积成情景记忆 | 最贵 |

几组容易被混为一谈的差别，它们决定了这些为什么是独立的类：

| 常被混淆的一对 | 真正的差别 |
|---|---|
| Plan-and-Execute vs Plan-and-Solve | 前者每步都再问一次模型且可重规划；后者全程只有一次调用 |
| Plan-and-Solve vs ReWOO | ReWOO 多一个 Solver 汇总调用，且计划里带证据变量做依赖替换 |
| ReWOO vs LLMCompiler | ReWOO 的计划是线性的；LLMCompiler 只要没有显式依赖就可同波执行 |
| Basic Reflection vs Reflexion | 前者纯自评、反思用完即弃；后者由外部评估器判定、反思累积 |

最后一行最要紧：Basic Reflection 的评价者读不到客观事实，所以它能修「取证不足」，
修不了「根本没改成」—— 模型觉得自己做对了，它就会一直觉得自己做对了。

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
最后把 7 × 3 全矩阵**真的各跑一遍**，打出每一格的模型调用次数：

```
内层模式 \ 外层流程     线性状态机      单 Agent      分层子图
ReAct                 10            10            10
Plan-and-Execute      8             8             8
Plan-and-Solve        2             2             2
ReWOO                 4             4             4
LLMCompiler           4             4             4
Basic Reflection      12            12            12
Reflexion             10            10            10
```

这张表里最值得看的是 Basic Reflection 的 12：它比裸 ReAct 多花了两次调用，
却因为看不到客观事实而没改变任何结果。反思不是免费的。

**关于工作流引擎**：线性五阶段只有一条主路径和一条失败短路，没有分支、并发、循环。
这种形状引入引擎不产生收益，只多一层需要理解和调试的抽象。所以 `StateMachineOrchestrator`
就是一个循环加一个异常判断。真正需要引擎的是 `SubgraphOrchestrator` 那种形状 ——
它的不同支路还可以挂不同的推理模式：命名类走 ReWOO 省 token，认知复杂度类走
Reflexion 包 ReAct 换准确率。这是两轴分离带来的最直接的好处。

---

## ② 接入层：连接器是可插拔的

大多数 Agent 框架把外部系统集成做成「给 Agent 用的工具」，调用要经过 LLM 的 tool loop。
但拉任务、建 PR 这些阶段属于确定性编排，一旦过模型，确定性与不确定性的分离当场就塌。

所以底盘的连接器是**独立可直接调用**的，Agent 想用时再由编排器包装成工具暴露给它。
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

## ③ 知识注入：时机是一等公民

**时机比内容更重要。**

同一份 ABP 规范，注入在决策层的 system prompt 里，Agent 就变成了「懂 ABP 的 Agent」，
换技术栈要改 Agent；注入在调用外部执行器之前的最后一步，Agent 始终是「不知道 ABP
是什么的 Agent」，换技术栈只改 markdown 文件。

底盘把六个时机做成显式枚举，每个 provider 声明自己在哪些时机生效：

| 时机 | 用途 |
|---|---|
| `AGENT_BOOT` | 决策层 system prompt。**底盘刻意建议留空** |
| `TASK_ADMITTED` | 任务准入后，补充任务级背景 |
| `BEFORE_TOOL` | 每次工具调用前，约束单次调用 |
| `BEFORE_EXECUTOR` | 调外部执行器前的最后一步。**生产实际用的点** |
| `ON_RETRY` | 重试前，把上次失败原因回灌 |
| `BEFORE_VERDICT` | 裁定前，补充判定口径 |

```python
SkillProvider(skills, points=[InjectionPoint.BEFORE_EXECUTOR])
```

改成 `AGENT_BOOT` 也能跑，但装配报告会显示决策层不再干净。
底盘不禁止，只是把这个选择变成一行显式代码，而不是藏在 prompt 拼接里的隐式约定。

**两级路由**：规范类知识是确定性的，不需要相似度检索。路由规则本身承载工程判断：

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

## ④ 失败契约：失败之后系统留下什么

默认要求是零副作用：落库标记、去重防重试、干净退出、不阻塞下一条。
被消耗的只有算力，不是人力。

还有一个容易被忽略的对偶：**开工前的清理**。不只是失败后不留残骸，
而是每轮开始前假设上一轮可能留了残骸并强行清理。长期无人值守必须这么假设。

```python
policy = ZeroSideEffectPolicy(Ledger("state/ledger.json"))
policy.register_cleanup("丢弃未推送的工作分支", discard_branch)

guard = WorkspaceGuard().add("reset --hard + clean -fd", reset_workspace)
```

去重表同时是失败表，失败过的任务默认不再重试。这是个明确的取舍：
好处是不在同一道难题上反复烧算力，代价是模型升级后历史失败样本不会自动重跑。
`Ledger(retry_failed=True)` 把这个取舍交还给你。

---

## ⑤ 可观测与问责：四张表，零业务概念

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

## 权限边界：能力借来，权限不借

底盘允许集成任何第三方 Agent 作为执行器，但集成不等于全权委托。
执行器通常自带完整的版本控制能力，底盘要做的是**在授予能力的同时收回权限**。

这不是靠提示词里写一句「请不要 commit」实现的。提示词是软约束，模型可以不听。
硬约束是执行器根本拿不到那个能力，调用会抛异常：

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

## 装配一台数字员工

```python
from agent_chassis import Chassis, ConsoleObserver, InjectionPoint, borrowed_executor
from agent_chassis.knowledge import SkillLibrary, SkillProvider, by_extension
from agent_chassis.orchestration import NestedOrchestrator, ReActPattern

chassis = (
    Chassis("代码质量治理数字员工")
    .with_orchestrator(NestedOrchestrator(
        outer_steps=outer_steps,                  # 外层·流程
        toolbox=box,
        pattern=ReActPattern(decide),             # 内层·Agent 设计模式
        delegate_at="agent_fix",
        criteria=criteria,
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

### 不想手写装配脚本？用装配向导

```bash
python assemble.py
```

按底盘五大系统的顺序逐步问答（回车即接受默认值）：

| 步骤 | 问什么 | 可选项 |
|---|---|---|
| 载荷 | 任务从哪来、怎么算做完 | code_quality / pr_mention / **生成新载荷骨架** |
| ① 编排 | 外层流程 × 内层推理 | nested / state_machine / single_agent × ReAct / Plan-and-Execute / Plan-and-Solve / ReWOO，可再包一层 Reflexion |
| ③ 知识注入 | 挂哪些 provider、什么时机 | SkillProvider / StaticKnowledge / RetryFeedback，6 个注入点带说明（选 AGENT_BOOT 会警告） |
| ④ 失败契约 | 失败后留下什么 | ZeroSideEffect / RetryThenGiveUp，账本可落盘 |
| 权限边界 | 授予执行器哪些能力 | 9 项能力多选，选了不可逆能力会提醒 |
| ⑤ 可观测 | 挂哪些观察者 | Console / Recording(cron/webhook) |

确认后产出一份**可直接运行的装配脚本**到 `generated/`；选「生成新
载荷骨架」还会生成 `payloads/<模块名>.py`——自带演示任务与外部系统
替身，生成即可离线跑通，搜 `TODO` 逐段替换成真实业务逻辑。

向导会根据载荷能力过滤选项：只有 decider 没有 planner 的载荷，
规划类推理模式不会出现在菜单里；没有 critic 的载荷不会被问 Reflexion。
向导与底盘一样零第三方依赖。

---

## 目录结构

```
src/agent_chassis/
├── contracts.py          所有可插拔点的抽象。整套底盘唯一必须读懂的文件
├── chassis.py            装配器与自检报告
├── permissions.py        权限边界
├── failure.py            失败契约、去重账本、开工前清理
├── observability.py      四张通用表与两个观察者
├── wizard.py             终端问答式装配向导（python assemble.py 启动）
├── orchestration/        外层流程编排
│   └── reasoning.py      内层 Agent 设计模式
├── integration/          连接器与工具名容错解析
└── knowledge/            注入时机调度与多级路由

payloads/
├── code_quality.py       载荷 ① 代码质量治理
└── pr_mention.py         载荷 ② PR 评论区 @Agent

examples/                 五个可直接运行的演示
```

---

## 与生产实现的关系

底盘的抽象来自两套已在生产运行的系统：

| 仓库 | 贡献的抽象 |
|---|---|
| [SonarqubeAutoFlow-public](https://github.com/TIMPICKLE/SonarqubeAutoFlow-public) | `BaseTool` 与 `ToolRegistry` 两阶段选择、手写 ReAct 循环、子图重构设计 |
| [SonarqubeAutoFlow_MAF](https://github.com/TIMPICKLE/SonarqubeAutoFlow_MAF) | `MCPManager` 工具名解析、Skills 两级路由、五阶段状态机、失败零副作用 |

本仓库把两者的共同部分提取为可插拔的框架，并补上了它们各自缺的那一半：
前者有工具抽象但编排写死，后者有编排但工具直接绑死在 Agent 上。

生产系统里这套底盘对应的载荷已累计执行 900 次以上，跨 3 个 BU、4 种语言，
零主干污染。相关数据与设计论证见参赛材料。

---

## 状态

早期版本。契约层已稳定，`mcp.stdio` 与 `mcp.http` 连接器留了接入点但未接真实 SDK
（示例用 `mock` 跑通全链路）。欢迎按 `Connector` 契约补齐。
