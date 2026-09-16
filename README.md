# Agent Chassis

**DevOps 数字员工工程底盘：复用编排、接入、知识、失败处理与运行记录，按岗位定义任务和验收。**

业务方提供任务源（`TaskSource`）、完成标准（`DoneCriteria`）和领域工具，再通过装配代码组成一台数字员工。已有岗位可以封装为版本化配方，在不同项目中创建独立实例。

[快速开始](#快速开始) · [岗位配方库](docs/ROLE_RECIPES.md) · [架构指南](docs/ARCHITECTURE.md) · [当前状态](roadmap/CURRENT_STATE.md) · [Roadmap](roadmap/README.md)

## 从哪里开始

| 你想做什么 | 入口 |
|---|---|
| 先了解项目如何使用 | [Azure CodeAgent 全流程案例](docs/LEADERS_USE_CASE.md) |
| 给新项目部署已有岗位 | [岗位配方库：配置、创建、检查与启动](docs/ROLE_RECIPES.md) |
| 定义一个新岗位 | [架构与装配指南](docs/ARCHITECTURE.md)，或使用 [AI 装配 Skill](.claude/skills/assemble-digital-employee/SKILL.md) |
| 试用“需求 → 生成员工 → 运行验收” | [生成员工项目](docs/GENERATED_EMPLOYEE_PROJECT.md)，当前 CLI 限定 C++ include 修复场景 |
| 审查节点、知识与客观验收如何落地 | [技术使用案例](docs/TECHNICAL_USE_CASE.md) |
| 查看比赛材料 | [报告、演示与答辩脚本](Final%20Report/README.md) |

## 快速开始

需要 Python 3.9+。克隆后在仓库根目录执行；以下基础示例只使用标准库，不需要模型密钥。

```bash
git clone https://github.com/TIMPICKLE/devops-agent-chassis.git
cd devops-agent-chassis

python examples/01_swap_orchestration.py
python examples/04_swap_payload.py
```

第一个示例固定载荷切换流程和推理模式；第二个示例切换业务载荷，复用底盘装配。两者使用确定性回调，帮助理解接口和控制流。

更多可运行示例：

| 示例 | 内容 |
|---|---|
| [02 · 接入连接器](examples/02_plug_connector.py) | 注册与调用外部能力 |
| [03 · 知识注入](examples/03_injection_timing.py) | 在不同生命周期事件提供知识 |
| [05 · 权限与失败](examples/05_permissions_and_failure.py) | 权限检查、去重与清理 |
| [06 · 并行工具](examples/06_parallel_tools.py) | 显式声明独立工具后并发执行 |
| [07 · 装配与验收](examples/07_verified_assembly.py) | 装配检查、独立验收和证据核验；需下方可选依赖 |

运行示例 07 和模型参考工具前安装可选依赖：

```bash
python -m pip install -e ".[llm]"
python examples/07_verified_assembly.py
```

示例 07 不发起模型请求。接真实模型时，按 [模型接入说明](docs/MODEL_COMPATIBILITY.md) 配置协议、服务地址与密钥；接 MCP 时另按需安装 `.[mcp]`，要求 Python 3.10+。

## 岗位配方库

配方固定已有员工的源码版本、组成和配置合同。创建时导出岗位源码与知识，生成独立配置、文件摘要和启动入口，**不调用模型**。执行任务时沿用原岗位的业务逻辑与运行依赖。

| 配方 | 用途 | 当前边界 |
|---|---|---|
| `azure-codeagent@1.0.0` | 将 Azure DevOps 工作项指令交给 CodeAgent 处理 | 默认关闭交付与通知；需要兼容底盘检出、模型和外部工具 |
| `sonarqube-autoflow@1.0.0` | 复用 SonarQube 问题修复员工 | 保留原 MAF 运行时；当前固定实现仅支持 `master` |

先查看岗位和预览项目配置，无需业务源码或密钥：

```bash
python tools/role_recipe.py list
python tools/role_recipe.py show azure-codeagent --version 1.0.0
python tools/role_recipe.py plan --config role_recipes/azure-codeagent/hsi.example.json
```

然后按 [岗位配方指南](docs/ROLE_RECIPES.md) 修改示例配置，准备包含固定 commit 的业务源码检出，执行 `create` → `verify` → `launch.py check --environment` → `launch.py run ...`。

实例配置保存环境变量引用名，凭据由运行环境提供。完整性检查和环境检查各有独立结果；实际任务是否完成，仍由原岗位的业务验收决定。

## 核心设计

底盘核心 `src/agent_chassis/` 只依赖 Python 标准库，不包含具体业务概念。

| 系统 | 职责 |
|---|---|
| 编排 | 外层推进阶段、分支和路由，内层选择推理模式；两者可分别替换 |
| 接入 | 通过连接器和工具访问外部能力，支持 mock、REST 和可选 MCP 传输 |
| 知识 | 按生命周期事件、任务与阶段选择知识，并记录消费情况 |
| 失败契约 | 记录终态、去重、按策略重试，并执行业务方注册的清理回调 |
| 可观测 | 记录任务、执行轨迹、工具调用与健康状态，支持回放和证据核验 |

装配遵守三个约束：

- **验收依据客观事实**：`DoneCriteria` 读取事实和外部状态，不用模型自述判定成功。
- **权限在工具入口检查**：默认授予执行器 `repo.read` / `repo.write`，提交、推送等能力由确定性代码控制；边界对象本身不提供进程沙箱。
- **失败处理由业务明确**：清理回调处理已注册的资源，不承诺自动撤销所有外部副作用。

流程类型、七种推理模式、知识路由与装配代码见 [架构指南](docs/ARCHITECTURE.md)。ReAct 已支持显式开启的独立工具并行；`LLMCompilerPattern` 当前仍按依赖分波、波内串行。

## 能力与验证状态

当前提供底盘装配、模型适配、知识注入、运行证据、受限员工生成与更新，以及已有岗位配方复用。核心的通用装配能力、特定生成 CLI 的范围、现有岗位自身的运行框架分别有各自边界。

| 验证层次 | 已有证据 |
|---|---|
| 核心与配方回归 | 岗位配方版本本地 418 passed / 1 skipped；对应 CI 和受测 commit 见 [配方实施记录](changelog/role-recipes-2026-09-16.md) |
| 岗位实例离线验证 | Azure HSI / Portals 各通过 9 项原应用离线测试；Sonar 完成源码、知识与配置映射检查 |
| 历史真实模型链路 | 特定 C++ 修复员工完成生成、两个源码任务验收及知识更新回归，见 [第四阶段](changelog/stage-04.md) / [第五阶段](changelog/stage-05.md) |

本次岗位配方未新增企业 Azure / Sonar 或模型实测。任意需求生成、批量调度、跨进程续跑和完整企业构建验证仍在后续范围。能力、限制与证据统一查阅 [当前状态](roadmap/CURRENT_STATE.md)；历次测试数量留在 [变更记录](changelog/README.md)。

## 专题文档

| 主题 | 文档 |
|---|---|
| 员工生成与维护 | [生成和运行](docs/GENERATED_EMPLOYEE_PROJECT.md) · [知识更新与保留定制](docs/EMPLOYEE_PROJECT_UPDATES.md) |
| 知识驱动流程 | [项目规范与配置修复](docs/CONFIG_POLICY_WORKFLOW.md) |
| 模型接入 | [兼容性与接入边界](docs/MODEL_COMPATIBILITY.md) · [SSE 流式接收](docs/STREAMING.md) |
| 工具执行 | [ReAct 并行工具与装配检查](docs/PARALLEL_TOOLS.md) |
| 证据与排错 | [生产格式证据](docs/PRODUCTION_EVIDENCE.md) · [HTTP 诊断](docs/HTTP_DIAGNOSTICS.md) |
| 开发规划 | [Roadmap](roadmap/README.md) · [Backlog](roadmap/BACKLOG.md) · [变更概览](changelog/README.md) |

## 仓库结构与开发

| 路径 | 内容 |
|---|---|
| [`src/agent_chassis/`](src/agent_chassis/) | 核心合同、装配器与五大系统 |
| [`payloads/`](payloads/) | 参考业务载荷 |
| [`adapters/`](adapters/) | 模型协议、统一装配入口与诊断 |
| [`employee_factory/`](employee_factory/) | 员工生成、更新与配方实例化 |
| [`role_recipes/`](role_recipes/) | 版本化岗位配方和项目配置示例 |
| [`tools/`](tools/) | 运行、创建、检查与证据核验 CLI |
| [`examples/`](examples/) / [`tests/`](tests/) | 可运行演示与回归测试 |
| [`schemas/`](schemas/) | 装配和运行证据 schema |

`adapters/`、`employee_factory/` 和参考 CLI 使用仓库检出，不随核心 wheel 独立交付。

```bash
python -m pip install -e ".[dev,llm]"
python -m pytest tests/ -q
```

MCP SDK 未安装时，相关可选测试会跳过。其他专项验证按对应文档准备依赖；开发约束见 [AGENTS.md](AGENTS.md)。
