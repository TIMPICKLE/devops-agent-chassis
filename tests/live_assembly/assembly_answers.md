下面是你上一轮访谈所需的完整答案。请继续同一个会话，并真正完成装配与验证。

## 数字员工目标
名称：`live-ci-incident-triage`
用途：读取一条本地 DevOps 事故 JSON，判断事故等级并路由责任团队。

## TaskSource
- 输入文件固定为：`tests/live_assembly/incident.json`
- 每次运行只产生 1 个 Task。
- Task key 使用输入中的 `id`。

## DoneCriteria
必须由程序事实判断，不接受模型自述：
- 输出 JSON 存在；
- `task_key == "INC-001"`；
- `severity == "P1"`；
- `owner == "platform"`；
- `done == true`。

## 确定性业务规则
为了让 CI 的 PASS/FAIL 稳定，业务判断规则是明确的：
- `customer_impact == true` 且 `error_rate >= 0.20` → `severity = "P1"`；
- service 以 `payments-` 开头 → `owner = "platform"`。

## 编排与推理
- 外层：`state_machine`
- 内层：`ReAct`
- 允许使用确定性 Python decider 实现这条 Golden Scenario；本次 Live Acceptance 的大模型验证点是“Claude Code 是否能正确使用 Skill 从自然语言装配工程”，不是让最终业务分类结果依赖模型随机性。

## Connector
- 使用本地 `mock` Connector 即可，不访问真实外部系统。
- 至少通过 ConnectorManager 进行一次只读事故详情获取，以覆盖 Connector → Observer Trace。

## LLM Provider 配置
这次装配的 Provider 配置必须记录为：
- provider: `bigmodel-anthropic-compatible`
- base_url: `https://open.bigmodel.cn/api/anthropic`
- model: `glm-5.3-flash`
- secret env: `BIGMODEL_API_KEY`

绝对不能把真实 API Key 写进任何源码、JSON、Markdown、日志或生成物；只能保留环境变量名称。

## 权限与副作用
- 数字员工运行时只允许读 `tests/live_assembly/incident.json`；
- 数字员工运行时只允许写 `generated/live_ci_result.json` 与运行 Trace；
- 装配阶段所有由 Claude Code `Write` / `Edit` 创建或修改的文件都必须位于本仓库 `generated/` 目录；
- **不要写入 `~/.claude/`、Claude Code memory、HOME 下的任何记忆文件，或仓库 `generated/` 之外的任何路径**；本次 CI 是一次性验收，不需要保存会话记忆；
- 不允许网络副作用、Git push、PR、issue/comment 等真实外部写操作。

## 强制交付契约
你必须最终生成：
1. `generated/live_ci_employee.py`
2. `generated/live_ci_manifest.json`

`generated/live_ci_employee.py` 必须：
- 使用本仓库 `agent_chassis.Chassis` 的公共 API，而不是绕过底盘写一个独立脚本；
- 能通过：
  `python generated/live_ci_employee.py --input tests/live_assembly/incident.json --output generated/live_ci_result.json`
- DoneCriteria 成功时进程退出码为 0；失败时非 0。

`generated/live_ci_manifest.json` 必须至少包含：
- `name`
- `task_source`
- `done_criteria`
- `orchestrator`
- `reasoning`
- `connector`
- `llm_provider`

完成后请你自己运行生成的数字员工做 smoke test，并修复你发现的问题。不要修改 `src/agent_chassis/`、现有 payload、现有 Skill 或现有测试来让验收通过。不要创建任何持久化 Claude memory；所有工程写入只允许发生在 `generated/`。
