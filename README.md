# 🏗️ Agent Chassis

**DevOps 数字员工工程底盘。**

**首次了解项目？[图解使用全过程：从需求到可复用的 AI 数字员工](docs/LEADERS_USE_CASE.md)**，沿着七步用户交互和四张流程图，看清每一步的输入、产物、验收与后续更新。

**技术评审入口：[需求如何变成节点、知识注入与运行逻辑](docs/TECHNICAL_USE_CASE.md)**，附源码对应关系、当前能力边界和复现命令。

🚗 一台数字员工 = 底盘（与业务无关的五大系统）+ 载荷（与业务有关的两项定义）。

底盘回答的是任何 DevOps 数字员工都要回答的同一组问题：它可不可靠、怎么接外部系统、
输出符不符合规范、失败了谁收拾、凭什么敢上生产。这五个问题与你做的是代码治理还是
测试补齐完全无关，所以它们只该被回答一次。

载荷回答的是剩下的两个问题：任务从哪来，怎么算做完。

### Roadmap 实现分支

**当前按 [Roadmap v3](roadmap/README.md) 开发：需求输入 → 生成员工项目 → 运行源码任务 → 独立验收。** 首条完整链路与实际验证见[第四阶段](changelog/stage-04.md)；前三阶段作为基础能力保留。

使用入口：[从需求生成员工项目，再运行与验收](docs/GENERATED_EMPLOYEE_PROJECT.md)。支持指定本地源码目录，生成与运行两个阶段分别记录模型证据。

维护入口：[规范变化后更新员工项目](docs/EMPLOYEE_PROJECT_UPDATES.md)。预览影响、保留人工修改，并对新旧任务重新验收；实现回顾与 Actions 结果见[第五阶段](changelog/stage-05.md)。

**当前已验证**：生成员工后运行两个源码项目；更新知识后，同一输入按新规范改变选择，两个旧任务继续通过。第五阶段 CI 回归 195 项通过。能力、限制和受测版本统一见[当前状态](roadmap/CURRENT_STATE.md)；以下各阶段数字保留其历史口径。

**第三阶段：[项目规范驱动的配置修复](docs/CONFIG_POLICY_WORKFLOW.md)**。按项目、环境和阶段匹配知识，通过四个实际节点生成并验收配置，配套冻结案例、三种知识策略对照与独立核验。进度与 Actions 结果见[第三阶段记录](changelog/stage-03.md)。

第三阶段回归 177 项通过；真实模型 routed / full 各 8/8 验收通过，24 个试次证据核验通过。none 组 0/8，含 4 次执行异常，因此 live 工作流整体未通过。完整结果与成本计量限制见[实测说明](changelog/stage-03.md#真实模型结果--2026-09-06)。

**快速了解每阶段变更：[变更概览](changelog/README.md)**。每项功能都记录用途、入口、测试与限制；最新见[第五阶段](changelog/stage-05.md)，此前完整生成链路见[第四阶段](changelog/stage-04.md)。

| 第二阶段新增 | 直接用途 |
|---|---|
| 客观检查后及时停止 | 合格候选及时结束推理，保留最终独立验收 |
| Anthropic / OpenAI 兼容协议互换 | 同一载荷、工具和判据切换模型接入协议 |
| 三种上下文策略对照 | 一键比较按需、全量、不注入；报告附原始证据和独立核验入口 |

第二阶段已通过完整 160 项回归；两种协议与上下文实验共 10/10 个真实模型任务验收通过，每个任务仅调用模型一次。结果与适用范围见[阶段实测记录](changelog/stage-02.md)。

首批实现增加了**上下文消费回执、运行期模型适配器、版本化运行证据**，以及共享装配代码的 Python 质量修复 / C++ 构建修复两个参考载荷。两种载荷支持切换三种编排。详见 [实施进度与复现命令](roadmap/IMPLEMENTATION.md)；完整规划见 [Roadmap](roadmap/README.md)。

```bash
python -m pip install -e ".[dev,llm]"
python tools/run_roadmap_showcase.py --mode offline --output-dir reports/roadmap-showcase-demo
python tools/verify_roadmap_evidence.py reports/roadmap-showcase-demo
```

上述命令是**离线合同回放，不是 AI 实测或生产评测**。运行期真实模型需使用 `--mode live`，通过环境变量提供 `BIGMODEL_API_KEY`，并另行留存 live 证据。这里只输出本地 patch，不创建业务 PR 或发布变更。

```
底盘 Chassis（本仓库）              载荷 Payload（业务方提供）
├─ ① 🧭 编排契约  Orchestrator       ├─ 📥 TaskSource    任务从哪来
│    └ 外层流程 + 内层 ReasoningPattern  └─ ✅ DoneCriteria  怎么算做完
├─ ② 🔌 接入层    Connector              + 🧰 领域工具集
├─ ③ 📚 知识注入  KnowledgeProvider
├─ ④ 🧹 失败契约  FailurePolicy
└─ ⑤ 📊 可观测    Observer
```

---

## ⏱️ 五分钟看懂

基础底盘和以下确定性示例无必需第三方依赖，Python 3.9+，在仓库根目录运行。真实模型参考入口需安装 `.[llm]`，MCP 传输需可选 SDK；这些示例不代替真实模型验证：

```bash
python examples/01_swap_orchestration.py   # 换编排方式，载荷代码一行不动
python examples/02_plug_connector.py       # 加一个外部系统，注册一个类就够
python examples/03_injection_timing.py     # 知识注入时机可视化
python examples/04_swap_payload.py         # 换载荷，底盘装配代码一字不差
python examples/05_permissions_and_failure.py  # 能力借来，权限不借
```

---

## 🧭 ① 编排契约：编排是两个正交的轴

「编排形态」这个词经常把两件不同的事混在一起说。底盘把它们拆开：

| 轴 | 管什么 | 换它会影响 |
|---|---|---|
| **外层 · 流程编排** `Orchestrator` | 任务被推进的骨架：阶段顺序、分支、路由、人工介入 | 流程的形状 |
| **内层 · Agent 设计模式** `ReasoningPattern` | 在下放点内部，模型怎么想 | 轮数、token、可预测性 |

两轴独立替换。同一个流程可以换推理模式，同一个推理模式可以放进不同流程。

### 🦴 外层：流程编排

| 形态 | 说明 | 下放点 |
|---|---|---|
| `StateMachineOrchestrator` | 线性状态机，一条主路径加一条失败短路 | 被标记的那个阶段 |
| `SingleAgentOrchestrator` | 没有外层骨架，整个任务交给一个推理模式 | 整个任务 |
| `SubgraphOrchestrator` | 分析子图 → 路由 → 修复支路 → 人工介入 → 交付子图 | 各支路里的 AgentStep |
| `NestedOrchestrator` | **不是第三种形态，是两轴的组合算子**：外层骨架 + 指定阶段下放给内层模式 | 外层的一个阶段 |

### 🧠 内层：Agent 设计模式

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

⚠️ 最后一行最要紧：Basic Reflection 的评价者读不到客观事实，所以它能修「取证不足」，
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
最后把 7 × 3 全矩阵**以确定性回调各跑一遍**，打出每一格的模拟决策调用次数；不是远端模型调用或真实 token 账单：

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

这张表说明示例控制流的调用差异；它不能单独证明某种模式在真实模型上的效果或成本。生产模式选型需同条件 live 对照。

**关于工作流引擎**：线性五阶段只有一条主路径和一条失败短路，没有分支、并发、循环。
这种形状引入引擎不产生收益，只多一层需要理解和调试的抽象。所以 `StateMachineOrchestrator`
就是一个循环加一个异常判断。真正需要引擎的是 `SubgraphOrchestrator` 那种形状 ——
它的不同支路还可以挂不同的推理模式，例如命名类走 ReWOO，复杂问题走
Reflexion 包 ReAct。是否节省 token 或提高质量，需要在相同任务和验收条件下测量。

---

## 🔌 ② 接入层：连接器是可插拔的

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

## 📚 ③ 知识注入：时机是一等公民

💡 **时机比内容更重要。**

项目规范可以在执行器调用前按任务和阶段提供，让通用流程与知识内容分开维护。
只有变化局限于既有知识内容时，才可能只改 Markdown；更换技术栈若涉及工具、接口或判据，也需要调整项目代码。

底盘把六个时机做成显式枚举，每个 provider 声明自己在哪些时机生效：

| 时机 | 用途 |
|---|---|
| `AGENT_BOOT` | 决策层 system prompt。**底盘刻意建议留空** |
| `TASK_ADMITTED` | 任务准入后，补充任务级背景 |
| `BEFORE_TOOL` | 每次工具调用前，约束单次调用 |
| `BEFORE_EXECUTOR` | 调外部执行器前收集知识；当前真实模型参考路径在每次模型请求前触发 |
| `ON_RETRY` | 重试前，把上次失败原因回灌 |
| `BEFORE_VERDICT` | 裁定前，补充判定口径 |

```python
SkillProvider(skills, points=[InjectionPoint.BEFORE_EXECUTOR])
```

改成 `AGENT_BOOT` 也能跑，但装配报告会显示决策层不再干净。
底盘不禁止，只是把这个选择变成一行显式代码，而不是藏在 prompt 拼接里的隐式约定。

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

## 🧹 ④ 失败契约：失败之后系统留下什么

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

## 📊 ⑤ 可观测与问责：四张表，零业务概念

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

## 🔒 权限边界：能力借来，权限不借

底盘允许集成任何第三方 Agent 作为执行器，但集成不等于全权委托。
执行器通常自带完整的版本控制能力，底盘要做的是**在授予能力的同时收回权限**。

⛔ 这不是靠提示词里写一句「请不要 commit」实现的。提示词是软约束，模型可以不听。
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

## 🔧 装配一台数字员工

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

### 🤖 不想手写装配脚本？让 AI 助手装配

仓库内置了 agent 专用的装配 skill（[.claude/skills/assemble-digital-employee/](.claude/skills/assemble-digital-employee/SKILL.md)，
入口见 [AGENTS.md](AGENTS.md)）。在 Claude Code / Copilot 等 AI 助手里说
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

---

## 🗂️ 目录结构

```
src/agent_chassis/
├── contracts.py          所有可插拔点的抽象。整套底盘唯一必须读懂的文件
├── chassis.py            装配器与自检报告
├── permissions.py        权限边界
├── failure.py            失败契约、去重账本、开工前清理
├── observability.py      四张通用表与两个观察者
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

## 🏭 与生产实现的关系

仓库原有设计说明将以下两个项目列为抽象来源；这不等于本仓库当前版本已完成企业生产验证：

| 仓库 | 贡献的抽象 |
|---|---|
| [SonarqubeAutoFlow-public](https://github.com/TIMPICKLE/SonarqubeAutoFlow-public) | `BaseTool` 与 `ToolRegistry` 两阶段选择、手写 ReAct 循环、子图重构设计 |
| [SonarqubeAutoFlow_MAF](https://github.com/TIMPICKLE/SonarqubeAutoFlow_MAF) | `MCPManager` 工具名解析、Skills 两级路由、五阶段状态机、失败零副作用 |

本仓库提供可替换的载荷、编排、知识和接入边界，复用收益仍需同条件验证。

历史 README 曾记载来源系统“累计 900 次以上、3 个 BU、4 种语言、零主干污染”。仓库当前未附完整统计时间窗、原始记录及与此版本的映射，因此保留为待核验的来源项目背景，不作为本仓库成功率或生产成熟度证据。参赛引用前需补齐口径和材料。

---

## 🚧 状态

早期版本。`mcp.stdio` 与 `mcp.http` 已有可选 SDK 适配和传输回归；公司具体接口仍需接入验证。当前能力与限制以[变更概览](changelog/README.md)和对应 Actions 实测为准。
