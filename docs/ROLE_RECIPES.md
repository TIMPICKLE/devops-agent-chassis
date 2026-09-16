# 岗位配方库：把已有员工部署到新项目

选择版本化岗位，填写项目配置和环境变量引用，再从固定版本业务源码创建独立实例。首批配方复用 Azure CodeAgent 和 SonarQube AutoFlow 的现有实现，保留任务源、工具、知识和验收行为。**创建过程没有模型调用；真实业务任务在实例启动后由原员工执行。**

这是 B1 岗位配方库：提供目录、配置校验、单实例创建和启动入口。`role_recipe.py` 是配方专用 CLI；尚未合并原有生成、更新、运行 CLI（B2），也没有批量项目调度、员工目录或失败项重跑（B3）。

## 1. 先选择适合的入口

| 需要做什么 | 入口 | 交付内容 |
|---|---|---|
| 把已有岗位用于另一个项目 | 本文 `tools/role_recipe.py` | 固定业务源码、项目配置、配方快照、文件摘要和独立启动器 |
| 让模型按受限需求生成员工节点 | [生成员工项目](GENERATED_EMPLOYEE_PROJECT.md) | AI 生成的接线代码、节点清单与模型调用记录；当前首条链路限定 C++ include 修复 |
| 设计全新业务岗位 | [装配 Skill](../.claude/skills/assemble-digital-employee/SKILL.md) | 按实际接口编写或复用载荷、工具与装配脚本，再逐项目验证 |

岗位配方不会从任意自然语言自动推断新的业务实现，也不会将 SonarQube 的原运行框架改写进底盘核心。

| 岗位与版本 | 固定业务源码 commit | 原运行方式 |
|---|---|---|
| `azure-codeagent@1.0.0` | `a92270a231496bd9ec228d1981025f2933fcafd9` | Azure 工作项评论 → 底盘编排 → Claude Code → 业务验收 → 按配置交付 |
| `sonarqube-autoflow@1.0.0` | `02f83a17527f2db4256a5908f5d816b9715487c3` | 沿用 SonarQube AutoFlow 的 MAF 流程、扫描、修复和交付 |

## 2. 查看目录与预览配置

下面的配方 CLI 命令从本仓库根目录运行。创建和检查本身使用 Python 标准库；创建还需要 Git。业务执行需要 Python 3.10+ 及各岗位依赖。

```bash
python tools/role_recipe.py list
python tools/role_recipe.py show azure-codeagent --version 1.0.0
python tools/role_recipe.py show sonarqube-autoflow --version 1.0.0
python tools/role_recipe.py plan --config role_recipes/azure-codeagent/hsi.example.json
```

`show` 返回源码版本、组成文件、配置字段、运行依赖与限制。`plan` 检查字段和类型、补齐默认值，返回规范化配置；它不检出源码、不读取凭据、不连接企业接口，也不创建实例。未知字段、缺失必填项、非法类型或不支持的岗位版本会失败，CLI 退出码为 2。

配置结构包含五个字段：

| 字段 | 含义 |
|---|---|
| `name` | 实例名称；1–80 位字母、数字、下划线或连字符，首位为字母或数字 |
| `recipe`、`version` | 明确选择岗位 ID 和版本 |
| `settings` | 普通项目配置；支持文本、HTTP(S) URL、整数、布尔值、命令 argv |
| `env_refs` | 原员工所需环境变量 → 部署环境中变量名的映射 |

例如 `"AZURE_DEVOPS_PAT": "HSI_ADO_PAT"` 表示启动时读取环境变量 `HSI_ADO_PAT`，再传给业务进程的 `AZURE_DEVOPS_PAT`。**右侧只能填变量名，不能填 PAT、密钥或文件路径本身。** 对文件/目录引用也是如此：`"SONAR_MCP_CONFIG": "HSI_MCP_CONFIG"` 的实际绝对文件路径应放在运行环境的 `HSI_MCP_CONFIG` 中。

未提供别名时默认使用配方声明的名称。配方 JSON、实例 JSON 和 `environment.example` 只记录引用名；`environment.example` 是部署提示文件，不会被启动器自动当作 `.env` 加载。

## 3. Azure：用同一配方创建两个项目员工

参考配置为 [HSI](../role_recipes/azure-codeagent/hsi.example.json) 和 [Portals](../role_recipes/azure-codeagent/portals.example.json)。先复制成自己的配置，替换示例地址、仓库、模型和测试命令，再运行 `plan`。示例测试命令是 `python -m pytest -q`，应换成目标项目实际可执行的验收命令。

| 配置 | HSI 示例 | Portals 示例 |
|---|---|---|
| `name` | `hsi-codeagent` | `portals-codeagent` |
| `settings.project` | `HSI` | `PORTALS` |
| `settings.repository` | `hsi-service` | `portals-service` |
| `settings.port` | `8081` | `8082` |
| `env_refs.AZURE_DEVOPS_PAT` | `HSI_ADO_PAT` | `PORTALS_ADO_PAT` |
| `env_refs.LLM_API_KEY` | `HSI_LLM_KEY` | `PORTALS_LLM_KEY` |

Azure 普通必填项为 `organization_url`、`project`、`repository`、`model_url`、`model`、`test_command`。`test_command` 是 JSON 字符串数组，例如 `["python", "-m", "pytest", "-q"]`，不经 shell 解释。URL 不能包含内嵌凭据、查询参数或片段。

默认分支为 `main`，监听 `127.0.0.1:8080`，最多 5 个计划步骤、2 次重规划；`delivery_enabled` 和 `notifications_enabled` 默认 `false`。需要其他设置时先用 `show` 查看可选字段；触发账号沿用原代码中的 `common.ois`。

先检出业务仓库：

```bash
git clone https://github.com/TIMPICKLE/AzureCodeAgent_Base_Agent_chassis.git /absolute/sources/azure-codeagent
```

`--source-dir` 必须是包含配方固定 commit 的本地 Git 仓库。创建使用该 commit 的 `git archive`，与工作区当前分支或未提交修改无关；不自动联网 clone/fetch。若浅克隆缺少固定 commit，需要先自行 fetch。命令里的 `/absolute/...` 均替换为自己的绝对路径。

```bash
python tools/role_recipe.py create --config role_recipes/azure-codeagent/hsi.example.json --source-dir /absolute/sources/azure-codeagent --output-dir /absolute/employees/hsi-codeagent
python tools/role_recipe.py create --config role_recipes/azure-codeagent/portals.example.json --source-dir /absolute/sources/azure-codeagent --output-dir /absolute/employees/portals-codeagent
python tools/role_recipe.py verify /absolute/employees/hsi-codeagent
```

这是两次单实例创建，不是批量任务。输出目录必须不存在；更换配置时应从配置文件重新创建新实例，已有目录不会被覆盖。

创建后，每个实例都有以下文件：

| 产物 | 用途 |
|---|---|
| `application/` | 固定版本业务源码、依赖清单和岗位知识 |
| `instance.json` | 补齐默认值后的项目配置和环境变量引用名 |
| `recipe.json` | 本次使用的完整配方快照 |
| `recipe.lock.json` | 受管文件的 SHA-256 摘要和岗位版本绑定 |
| `launch.py`、`recipe_support.py` | 可独立使用的实例检查和运行入口 |
| `environment.example`、`README.md` | 环境变量名清单和使用说明 |

创建会检查必须的业务组成文件和 Python 语法，并在临时目录完成后交付输出目录。源码 `.env`、数据库和 Python 缓存不带入实例。Azure 的 `localJSON/mcp.json` 从固定源码的 MCP 示例复制，知识 `knowledge/code-execution.md` 随源码一起冻结。队列、日志和工作区使用各实例自身的位置。

实例启动不再依赖工厂目录。Azure 业务代码仍需要一个兼容的底盘源码检出，通过 `CHASSIS_ROOT` 引用；配方没有把底盘 SDK 复制进业务源码，也没有自动安装依赖。

## 4. 检查环境并启动 Azure 员工

在实例目录运行：

```bash
python launch.py check
python -m pip install -r application/requirements.txt
python launch.py check --environment
```

由部署环境注入 HSI 示例引用的 `HSI_ADO_PAT`、`HSI_LLM_KEY`，以及 `CHASSIS_ROOT`（兼容底盘检出的绝对目录）。可选 Webhook Token 通过 `COMMENT_WEBHOOK_TOKEN` 注入。不要将真实凭据填入实例 JSON 或提交进仓库。

`check --environment` 额外检查必填引用是否存在、文件/目录有效性、Python 最低版本、声明的 Python 模块和命令是否可找到。Azure 需要 Git、npx、Claude Code CLI；MCP 示例使用 `@tiberriver256/mcp-server-azure-devops`。这个检查不请求模型、不验证网络连通性或账号授权，也不核验依赖的精确版本。

启动器在业务模块导入前注入配置，包括 `CHASSIS_ROOT`；这样可满足 Azure 的 bootstrap 先于 `.env` 加载的顺序。实例路径会覆盖继承环境中的共享路径，业务子进程在 `application/` 中运行。

```bash
python launch.py run --help
python launch.py run serve
```

HSI 示例监听 `127.0.0.1:8081/webhook/ado-comment`。要供 Azure Service Hook 访问，创建前将 `host`、`port` 及部署网络配置调整为实际可达设置。也可直接提供单个事件文件：

```bash
python launch.py run run /absolute/event.json
```

第一个 `run` 属于实例启动器，第二个 `run` 是 Azure 原 CLI 的子命令。`run` 会先检查实例和环境，再将后续参数原样传给岗位入口。即使运行 `--help`，也会先检查完整运行依赖与引用。

实例完整性通过不代表编码任务验收通过。Azure 的真实交付仍由原员工的本地检查、项目测试及交付闸门决定。要启用 PR 和评论通知，应在配置中明确开启对应选项并重新创建实例。

## 5. SonarQube：保留原员工行为与知识

使用 [SonarQube HSI 配置](../role_recipes/sonarqube-autoflow/hsi.example.json)。替换项目 key、模型、Azure 仓库、任务 ID、Git 地址及 PR 地址前缀，然后：

```bash
git clone https://github.com/TIMPICKLE/SonarqubeAutoFlow_MAF.git /absolute/sources/sonar-autoflow
python tools/role_recipe.py plan --config role_recipes/sonarqube-autoflow/hsi.example.json
python tools/role_recipe.py create --config role_recipes/sonarqube-autoflow/hsi.example.json --source-dir /absolute/sources/sonar-autoflow --output-dir /absolute/employees/hsi-sonar
```

原固定实现按 `master` 拉取源码，因此此版本仅接受 `branch=master`、`target_branch=refs/heads/master`。需要其他分支应先修改并验证源员工，再发布支持新行为的配方版本；只改配置不能消除源实现约束。

配方保留 MAF 业务代码及 `.claude/skills/` 知识，包括 ABP .NET 后端、Angular 前端、TypeScript 通用规范。创建时复制这些知识文件并记录摘要，知识并未因隐藏目录被遗漏；配方不会改写原员工的知识消费逻辑。

在实例中安装 `application/requirements.txt`，由环境注入 `HSI_LLM_KEY` 以及 `HSI_MCP_CONFIG`。后者必须是已按原项目要求配置好的 Azure/Sonar MCP JSON 文件的绝对路径。可选飞书接入沿用 `FEISHU_APP_ID`、`FEISHU_APP_SECRET`。

```bash
python launch.py check
python launch.py check --environment
python launch.py run status
python launch.py run run
```

外部 MCP 配置不会在创建时读取或写入交付物。启动时临时复制到 `application/localJSON/mcp.json`，子进程结束后删除；已有同名文件时拒绝覆盖。若进程被强制终止或断电，确认相关进程已结束后清理残留再运行。

Sonar 的 `run` 沿用原应用的提交、推送、PR 和通知行为，配方没有另加 Azure 配方的关闭交付开关。Git 仓库工作目录绑定到实例自己的 `application/workspace`。运行之前仍须按 `application/README.md` 准备企业环境。

## 6. 验证结果应该怎样理解

| 命令或证据 | 能证明什么 | 不覆盖什么 |
|---|---|---|
| `plan` | 输入符合所选配方字段合同 | Git 源码、依赖和真实任务 |
| `create` 后的检查、`verify`、`launch.py check` | 固定源码组成完整、受管文件摘要一致、岗位版本绑定一致 | 业务进程已成功运行 |
| `launch.py check --environment` | 当前解释器可找到依赖和声明的外部变量/路径/命令 | 账号授权、服务可用性和模型任务成功 |
| Azure 离线业务合同测试 | 替身业务服务下，真实底盘执行、知识消费和验收契约 | 企业 Azure、Claude Code 和模型实测 |
| 目标环境业务验收 | 原员工对该真实任务的检查与交付事实 | 其他项目或未经测试任务的成功保证 |

配方创建和静态检查报告明确记录 `creation_mode=recipe-instantiation`、`model_calls=0`、`runtime_verified=false`。环境检查通过不会将 `runtime_verified` 改为 true；`launch.py run` 也没有自动汇总业务成功证据。文件摘要用于发现受管文件被修改或遗漏，不是数字签名。

本次岗位配方功能未运行企业模型、Azure 或 Sonar 的真实业务任务；不得将配方实例化、环境检查或离线测试表述为新增 live 业务成功。

Azure 可以追加原员工的离线合同测试，需另安装 pytest，并在实例目录设置底盘路径：

```bash
CHASSIS_ROOT=/absolute/devops-agent-chassis PYTHONPATH=/absolute/devops-agent-chassis/src python -m pytest -q application/tests/test_chassis_payload.py
```

不要仅依赖 Azure 原 `cli.py smoke` 的退出码作为业务验收：原入口可能在自检跳过后仍成功退出。具体本次回归结果与 Actions 证据见[变更索引](../changelog/README.md)。

## 7. 新增岗位与版本

在受信任的配方目录新增 `<岗位目录>/recipe.json`，使用 `role-recipe/v1` 结构，声明岗位 ID、语义版本、完整 40 位源码 commit、复制范围、七类组成引用、运行入口、依赖、配置字段和限制。新增岗位应先有可运行且已验证的源实现。

目录可以通过 CLI 顶层参数切换，参数放在子命令前：

```bash
python tools/role_recipe.py --catalog /absolute/my-recipes list
python tools/role_recipe.py --catalog /absolute/my-recipes plan --config /absolute/my-instance.json
```

也可以从仓库 Python 环境注册 `Recipe` 对象：

```python
import json
from pathlib import Path
from employee_factory.recipes import Recipe, RecipeRegistry

registry = RecipeRegistry()
definition = json.loads(Path("my-recipe.json").read_text(encoding="utf-8"))
registry.register(Recipe(definition))
recipe = registry.get("my-role", "1.0.0")
config = json.loads(Path("my-instance.json").read_text(encoding="utf-8"))
recipe.instantiate(config, "/absolute/source", "/absolute/new-instance")
```

重复的 ID/版本注册会被拒绝。新增岗位配方无需修改 `src/agent_chassis/`；业务逻辑留在源项目，目录和实例化逻辑留在 `employee_factory/`。更换业务源码或配置合同后，应发布新配方版本、重新验证并创建实例；当前不提供已运行实例的自动迁移、批量升级或知识在线更新。

## 8. 复现本轮源项目验证

在底盘仓库安装测试依赖，准备含固定 commit 的两个业务源码 checkout，然后运行：

```bash
python -m pip install -e ".[dev,llm]" rich python-dotenv
python -m pytest tests/ -q
python tools/verify_role_recipe_examples.py --azure-source /absolute/sources/azure-codeagent --sonar-source /absolute/sources/sonar-autoflow --output-dir /absolute/reports/role-recipes
```

输出目录必须不存在。该脚本创建 Azure HSI/Portals 两个实例和一个 Sonar 实例，读取原应用的 Config 验证配置实际生效，再对两个 Azure 实例分别执行原载荷的离线测试。它不连接真实企业服务、不发通知、不调用模型。结果保存在 `summary.json` 及各实例测试日志中。

本轮本地完整回归 418 项通过、1 项因缺少 MCP SDK 跳过；Azure 每个实例的 9 项离线载荷测试通过。Sonar 验证源码、知识文件和配置，未新增业务运行成功声明。版本、Actions 与范围见[本轮变更记录](../changelog/role-recipes-2026-09-16.md)。
