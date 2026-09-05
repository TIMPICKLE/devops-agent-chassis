# Roadmap 实施记录 · 第一阶段

> 本文保留第一阶段实施快照。当前新增功能、Actions 运行范围和验证结果见[阶段变更概览](../changelog/README.md)与[第二阶段记录](../changelog/stage-02.md)。

更新：2026-09-05。开发分支：`feat/roadmap-showcase-v1`；起点：`bdd2b0a`。本阶段优先前瞻性与可拓展性，未建设新的安全平台，也未合并主分支。

## 1. 这一阶段交付了什么

| 可展示能力 | 实际实现 | 评委可以复核的证据 | 不能据此宣称 |
|---|---|---|---|
| 按任务装配上下文 | 不可变知识片段、来源/版本/哈希；按时机、优先级、字符预算消费；记录省略项 | `test_context_delivery.py`、模型请求 spy、运行回执 | 已降低 token 成本、远端必然理解了规范 |
| 模型不绑定底盘内核 | 装配边缘的 Anthropic Messages 兼容适配器；原生 `tool_use` 与工具 Schema | `test_model_runtime.py`、可显式启用的 live 流水线 | 已兼容所有厂商、已完成多模型效果对照 |
| 载荷与编排可互换 | Python 质量修复 / C++ 头文件构建修复，共用三个编排入口 | 两载荷 × 三编排合同回归；编译器真实检查 C++ | 两个公开小任务代表真实企业任务成功率 |
| 可移植的运行证据 | 版本化装配清单和证据 Schema；独立离线校验器 | 代码引用、输入哈希、模型用量、判据、清理、patch 哈希 | 清单能自动重建任意工作流，或哈希等于可信签名 |
| 统一客观验收 | `with_payload` 是权威判据入口；失败统一收尾 | F01/F02 回归、模型自报成功但产物错误被拒绝 | 任意插件/任意业务都已得到隔离保护 |

遵循装配 Skill 的架构边界：基础 `src/agent_chassis` 保持零必需第三方依赖；`jsonschema` 作为可选 `llm` extra，在参考适配器与证据校验工具使用。`adapters/`、`payloads/`、`tools/` 是仓库参考实现，不随当前核心 wheel 打包。

## 2. 与原 Backlog 的对应关系

| 任务 | 本阶段状态 | 尚缺内容 |
|---|---|---|
| B01 | 权威验收接线完成，已校准本轮涉及的能力说明 | 不是对所有历史宣传和部署的认证 |
| B02 | 否决/异常终态收尾完成；账本异常不重复 cleanup | 通用取消、异步外部补偿与人工对账 |
| B03 | 上下文真实消费完成；知识与 facts 分开 | 通用判据只读视图、真实模型上下文 A/B |
| B04 | 按用户要求推迟完整安全扩展 | 不据此开放任意代码执行或模型发布权限 |
| B05 | 运行期适配、双场景接线和首轮 live 验证通过 | 仍需扩展到代表性企业任务，不能将两个公开案例当作生产成功率 |
| B06 | 版本化证据与校验器完成 | 冻结保留集、重复 live 采样、费用和错误分布 |
| B07 | 第二参考载荷与共享装配清单完成 | 企业真实任务迁移成本与维护工时，不能事后虚构 |
| B08 | 未实施 | 与执行器/小脚本的同条件对照与业务净收益 |

因此：**M0 核心合同修复已完成；M1 的受限参考路径已有 live 证据，完整安全扩展按用户要求延后；M2/M3 尚未完整验收。**

## 3. 从代码到报告怎么运行

环境：Python 3.9+；C++ 场景需 `c++` 可执行文件。完整 MCP 集成测试需 Python 3.10+。

```bash
python -m pip install -e ".[dev,llm]"
python -m pytest tests/ -q
python tools/run_roadmap_showcase.py --mode offline --output-dir reports/roadmap-showcase-demo
python tools/verify_roadmap_evidence.py reports/roadmap-showcase-demo
```

输出目录必须是新目录，不覆盖旧证据。重复运行可省略 `--output-dir`，工具会生成新目录。额外安装 `.[mcp]` 和 `uvicorn` 后可运行真实 stdio/HTTP MCP 传输回归。

切换编排只改参数：`--flow nested`、`--flow state_machine` 或 `--flow single_agent`。切换任务只改 `--scenario python_quality` / `header_build`。业务判断不放入核心。

真实模型运行（先通过本地环境或 Secret 管理器提供 `BIGMODEL_API_KEY`，不要写进仓库）：

```bash
python tools/run_roadmap_showcase.py --mode live --output-dir reports/roadmap-showcase-live-demo
python tools/verify_roadmap_evidence.py reports/roadmap-showcase-live-demo --require-live
```

CLI 默认 `live`。缺少凭据、API 错误、非法工具参数、截断输出都会明确失败，**不会降级成固定补丁并标成成功**。本地测试注入的 transport 标为 `test-transport`，与真实网络调用分开。

默认预算：每任务最多 8 次模型请求，每请求最多 2048 输出 token、60 秒网络超时；上下文上限 12000 字符。字符预算不是 token 计费限额；这是参考适配器的调用边界，不是端到端硬实时或账户费用上限。两个任务总计最多 16 次请求；不自动购买或充值额度。

Python 场景对比期望 AST 与候选 AST，不执行模型代码；C++ 场景只允许已知 include 行变化，然后调用真实编译器 `-fsyntax-only`。它们是刻意收窄的演示验证器，不能直接替代任意仓库的完整测试/构建环境。

## 4. Actions 与模型 Key

工作流：[roadmap-showcase.yml](../.github/workflows/roadmap-showcase.yml)。

- 普通 `feat/roadmap-*` 推送 / PR：Python 3.9、3.13 回归；两载荷 × 三编排离线矩阵；上传报告、清单和 patch。不注入模型 Key。
- 付费 live：合同任务通过后，手动 `workflow_dispatch` 选择 `live=true`；或明确在该分支 push 的最后一个提交信息中加入 `[roadmap-live]`。
- live job 仅使用 `${{ secrets.BIGMODEL_API_KEY }}`。2026-09-05 用户已告知手动更新此 Secret；是否有效仍以模型服务实际响应为准。没有把 Key 写入代码或配置。
- 只运行两个公开参考任务；默认不重试服务故障或额度不足。失败证据仍上传，退出码保持失败。
- 既有 Live Assembly / Pages 工作流保持原样；新报告不覆盖旧的在线装配报告。

新工作流尚未合并默认分支时，GitHub 页面可能没有手动运行按钮；首次可用显式 push 标记触发，不必因此合并主分支。该行为依据 [GitHub workflow_dispatch 说明](https://docs.github.com/en/actions/reference/workflows-and-actions/events-that-trigger-workflows#workflow_dispatch)。

没有当前账户周额度/重置卡操作接口，未声称已检查余额或使用卡。模型服务报 quota/rate limit 时保留失败，不尝试绕过限制。

## 5. 如何读证据

| 文件 | 用途 |
|---|---|
| `*.manifest.json` | 装配元件、任务源/判据、允许能力、注入时机、模型配置引用及内容 ID；描述性清单，不是可执行 DSL |
| `*.evidence.json` | 运行结果、代码引用、输入哈希、上下文来源/消费回执、工具摘要、模型元数据、用量、最终验收与清理 |
| `*.patch` | 只有成功时输出；验收证据绑定 patch SHA-256 |
| `summary.json` / `summary.md` | 明确 live / offline、结果与调用次数；`is_benchmark=false` |

证据校验分两层：JSON Schema 检查结构，校验器检查内容 ID、装配关联、回执引用、用量完整性及 patch 摘要。它能发现不一致，不能认证日志产生者的真实性，也不会重执行任务。缺失 token 用量是 `null`，不伪装成零费用；没有模型价格表则不估算美元成本。

上下文回执仅证明本地消费；模型响应及调用记录另行留存。默认导出不含 Prompt、源码、完整工具参数或模型思维正文。但自定义判据的 `evidence` / 错误文本 / runtime 元数据仍由可信调用者负责筛选，不能宣称对任意插件输出自动脱敏。

## 6. 兼容性与行为变化

- 只在 `with_payload(source, criteria)` 配置判据即可；旧代码同时配置同一判据实例仍支持。两处配置不同实例会在 build 阶段报错。
- 内置编排器与自定义成功返回共享权威验收；判据在同一尝试不重复读取外部系统。单独使用编排器仍保留原可选 criteria 行为。
- `FAILED` 现在执行注册的终态清理，但确定性否决不触发整链重试。策略/账本异常独立记录，不能覆盖原始错误或再次进入 cleanup。
- 失败策略不能将未验收的异常路径改写成成功。`SKIPPED` 仍不执行最终判据。
- 旧 `ToolBox.add/call` 用法保留；新增 `input_schema` 和显式 `add_contextual`，不向所有工具自动塞入额外参数。
- `context_for` 不自动消费所有注入点；消费者必须选择时机并真实传递返回值。原有不接消费接口的回调不会神奇获得知识。
- `LLMCompilerPattern` 当前仍是波内串行。已校正文档，不把规划波次数写成实测加速。

## 7. 本地验证与远端结果

本地 Python 3.12：**118 passed**。远端 Python 3.13：**118 passed**；Python 3.9：**116 passed, 1 skipped**（未安装可选 MCP）。两个环境中的两载荷 × 三编排离线回放和证据校验均通过。

2026-09-05 首轮真实模型验证：Python / C++ 两场景均成功，每场景记录 8 次模型调用，live 证据校验通过。使用的是 Actions 当前 `BIGMODEL_API_KEY`，未读取或导出其值。完整运行已成功结束，代码快照 `a75f4b1a700c68205b8ed15e0bc793e06852d623`。见 [验证记录](./VALIDATION.md) 与 [Actions 运行](https://github.com/TIMPICKLE/devops-agent-chassis/actions/runs/33955194635)。

尚未执行的验证不记为通过。离线两载荷结果不折算为真实 AI 成功率；公开任务不属于冻结保留集。本地 Python 3.9 AST 语法检查不替代 3.9 运行时测试，运行时兼容性由 Actions 分别执行。

## 8. 下一阶段按什么顺序推进

1. **复核并改善调用效率**：首轮 live 已通过，但每场景使用了 8 次调用；下一批验证应检查逐次轨迹，加入“客观检查已接受候选后及时停止”的策略，保持最终独立验收。不要只因结果成功就声称高效。
2. **固定小型评测集与上下文 A/B**：同模型、同预算比较全量/时机化/无注入；将失败类型和费用缺失明确展示。
3. **扩展第二执行器**：使用同一任务和验收契约接另一模型或 Coding Agent，证明互换不改业务判据，而非只换配置字符串。
4. **选择一项有证据支持的前瞻能力**：工具串行是瓶颈时实现有界 DAG 并发；真实任务需跨进程等待时实现持久化续跑。先测需求，不同时重造两套平台。
5. **完成公平对照**：底盘与执行器 + 小脚本用相同输入/规则；呈现复用收益、失败案例和不适用范围。A2A 等待实际 peer 后再做互操作验证。

公司正式评分表、截止时间与实测业务样本仍未提供；这些会影响下一阶段的取舍，不影响本阶段已完成代码的有效性。
