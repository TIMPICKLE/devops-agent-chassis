# 源码与证据索引

核对日期：2026-09-10。业务指标按团队提供材料沿用，未重复访问内网数据源。以下是本版报告的技术基线，未来仓库更新不会改变这些固定链接的含义。

## 1. 仓库快照

| 仓库 | 分支与源码快照 | 用途 |
|---|---|---|
| devops-agent-chassis | main · [`68264ed39ae984c1fbbc17662892f2d0aeb58ab1`](https://github.com/TIMPICKLE/devops-agent-chassis/tree/68264ed39ae984c1fbbc17662892f2d0aeb58ab1) | 本次报告所述底盘基线 |
| AzureCodeAgent_Base_Agent_chassis | main · [`eabbb4c999cfe86e47b8233959bf7927e68e7189`](https://github.com/TIMPICKLE/AzureCodeAgent_Base_Agent_chassis/tree/eabbb4c999cfe86e47b8233959bf7927e68e7189) | 含 SQLite 队列的新装配应用 |
| SonarqubeAutoFlow_MAF | master · [`02f83a17527f2db4256a5908f5d816b9715487c3`](https://github.com/TIMPICKLE/SonarqubeAutoFlow_MAF/tree/02f83a17527f2db4256a5908f5d816b9715487c3) | MAF 业务实现与经验来源 |

## 2. 声明到源码的映射

| 声明 | 固定源码入口 | 说明 |
|---|---|---|
| 核心无必需第三方依赖 | [pyproject.toml](https://github.com/TIMPICKLE/devops-agent-chassis/blob/68264ed39ae984c1fbbc17662892f2d0aeb58ab1/pyproject.toml) | `dependencies=[]`，可选依赖另列 |
| Azure 直接复用底盘 | [装配脚本](https://github.com/TIMPICKLE/AzureCodeAgent_Base_Agent_chassis/blob/eabbb4c999cfe86e47b8233959bf7927e68e7189/generated/azure_code.py) | 实际 import、build 与五系统接线 |
| 声明底盘版本 | [bootstrap.py](https://github.com/TIMPICKLE/AzureCodeAgent_Base_Agent_chassis/blob/eabbb4c999cfe86e47b8233959bf7927e68e7189/bootstrap.py) | 声明 `68264ed`，不自动核对真实 checkout SHA |
| Work Item 任务与完成判据 | [payloads/azure_code.py](https://github.com/TIMPICKLE/AzureCodeAgent_Base_Agent_chassis/blob/eabbb4c999cfe86e47b8233959bf7927e68e7189/payloads/azure_code.py) | 准入、步骤验证、PR id/url |
| 本地验证与项目测试 | [local_verification.py](https://github.com/TIMPICKLE/AzureCodeAgent_Base_Agent_chassis/blob/eabbb4c999cfe86e47b8233959bf7927e68e7189/agents/local_verification.py) | 项目测试可配置；包含辅助模型审查 |
| 交付、失败和遥测接线 | [services.py](https://github.com/TIMPICKLE/AzureCodeAgent_Base_Agent_chassis/blob/eabbb4c999cfe86e47b8233959bf7927e68e7189/clients/services.py) | 失败回复与记录；模型调用采集存在待完善部分 |
| 持久队列与恢复范围 | [队列说明](https://github.com/TIMPICKLE/AzureCodeAgent_Base_Agent_chassis/blob/eabbb4c999cfe86e47b8233959bf7927e68e7189/docs/reference/task-queue.md)、[queue_store.py](https://github.com/TIMPICKLE/AzureCodeAgent_Base_Agent_chassis/blob/eabbb4c999cfe86e47b8233959bf7927e68e7189/queue_store.py)、[task_dispatcher.py](https://github.com/TIMPICKLE/AzureCodeAgent_Base_Agent_chassis/blob/eabbb4c999cfe86e47b8233959bf7927e68e7189/task_dispatcher.py) | 单进程持锁、同工作项串行、中断人工处置 |
| Sonar 当前运行框架 | [requirements.txt](https://github.com/TIMPICKLE/SonarqubeAutoFlow_MAF/blob/02f83a17527f2db4256a5908f5d816b9715487c3/requirements.txt)、[README](https://github.com/TIMPICKLE/SonarqubeAutoFlow_MAF/blob/02f83a17527f2db4256a5908f5d816b9715487c3/README.md) | Microsoft Agent Framework，独立于当前底盘装配应用 |
| 需求生成与业务装配 | [装配 Skill](https://github.com/TIMPICKLE/devops-agent-chassis/blob/68264ed39ae984c1fbbc17662892f2d0aeb58ab1/.claude/skills/assemble-digital-employee/SKILL.md) | 围绕业务需求访谈、选择组件、生成载荷与装配脚本并实际验证；业务范围不由某个测试样例定义 |
| 知识更新保留定制 | [更新说明](https://github.com/TIMPICKLE/devops-agent-chassis/blob/68264ed39ae984c1fbbc17662892f2d0aeb58ab1/docs/EMPLOYEE_PROJECT_UPDATES.md) | 既有知识内容更新，冲突阻断 |
| SSE 与并行边界 | [STREAMING](https://github.com/TIMPICKLE/devops-agent-chassis/blob/68264ed39ae984c1fbbc17662892f2d0aeb58ab1/docs/STREAMING.md)、[PARALLEL_TOOLS](https://github.com/TIMPICKLE/devops-agent-chassis/blob/68264ed39ae984c1fbbc17662892f2d0aeb58ab1/docs/PARALLEL_TOOLS.md) | 完整重组后执行，显式安全并行 |

## 3. Actions 证据

| 运行 | 作业结果 | 能支持的结论 |
|---|---|---|
| [34245972616](https://github.com/TIMPICKLE/devops-agent-chassis/actions/runs/34245972616) | 分层验收、Python 3.9 基础兼容、Python 3.10 MCP 作业成功 | 当前底盘基线的工程回归与兼容性 |

本次比赛以通用装配 Skill、Azure 实际装配源码及业务交付为主线。底盘回归检查工程合同，不定义可装配的业务场景，也不能替代具体业务验收。产物默认有保留期，演示前归档所需材料。

## 4. 本次更新的验证范围

已阅读附件与三个仓库的核心实现、装配/验收说明及相关合同测试，并核对底盘验收 Actions 的实际步骤结果。本地执行 `examples/04_swap_payload.py` 与 `examples/05_permissions_and_failure.py`，均退出码 0。这两项属于确定性示例。

未重新触发付费模型工作流，未连接企业 Azure DevOps、SonarQube 或内网看板，未重新测量业务数据。Azure“实测可用”来自维护者本次明确反馈；旧装配报告的 58 项测试是历史记录，不作为当前含队列版本的测试总数。

HTML 已做脚本语法、文件链接与结构检查。当前浏览器预览环境禁止打开本地 HTML，未完成浏览器渲染和视觉验收；比赛前请在实际投影设备上预演。

## 5. 文档口径更新

- 将 Azure 新仓库作为正式底盘装配案例，入口明确为 Work Item 评论。
- 将 Sonar 实践与底盘应用的关系写清，保留历史业务价值。
- 业务统计保留日期；911 是处理记录，637 是成功记录；90% 为团队原测算。
- 轻量载荷接线与完整业务应用的工作量分开表达。
- 补入队列、业务需求访谈与装配、知识注入、SSE 和证据能力。
- 统一全部比赛材料中的场景口径：通用装配按业务需求生成载荷与脚本，具体参考实现或测试样例的输入限制不代表底盘的能力边界。
- 对失败、项目测试、权限、版本与遥测的范围按实际接线说明。
- “工作流蒸馏”“批量生成”分别给出当前实现和后续产品范围。
