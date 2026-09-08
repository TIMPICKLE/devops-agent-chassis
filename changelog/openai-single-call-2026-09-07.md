# OpenAI 适配器：补齐单调用契约

后续更新：用户随后确认需要真正的并行能力，见 [ReAct 独立工具并行](react-parallel-tools-2026-09-07.md)。以下记录保留本次最小修复时的决策与验证；后续显式并发配置已扩展这里的单调用边界。

日期：2026-09-07。开发分支：`feat/roadmap-showcase-v1`。
基线提交：`1f99caed37ca60f63028b31fe9f91e58abb2821f`。

## 问题与决策

实机交互报告的问题 3 指出：模型返回多个 `tool_calls` 时，原适配器直接报错；本地临时适配器改为取第一个有效调用。
原实现已经明确只支持单函数调用，且测试要求拒绝多调用。这一拒绝机制不是本次要移除的缺陷。
真正的缺口是请求端仅在提示词要求一次一个工具，却未通过 OpenAI 协议参数限制多调用，与响应端的严格契约不一致。

本次保留 ReAct 的“决定一个动作 → 执行 → 观察 → 检查停止”的语义，只补齐适配层。
不会静默丢弃其他调用，也不会将整个批次直接串行或并行执行。后者需要另行设计依赖、部分失败、停止检查与预算规则。

原报告的成功记录没有原始响应调用数量或被丢弃调用明细，不能证明“取第一个”在其他任务上安全；本次使用合成响应验证契约，没有上传企业源码、内部地址、凭据或环境绕过代码。

## 已实现

| 项目 | 行为 |
|---|---|
| 默认请求约束 | OpenAI Chat Completions 请求发送 `parallel_tool_calls: false`，继续使用 `tool_choice: auto` |
| 显式兼容配置 | `ModelConfig.openai_parallel_tool_calls` 默认 `False`；只有显式 `None` 才省略字段，不发送 JSON null |
| 配置校验 | 拒绝 `True`、整数 0/1、字符串及其他类型，不允许配置开启批量执行 |
| 多调用拒绝 | 本批一个都不执行，错误包含实际调用数量，不包含工具名或参数正文 |
| 零调用 | 保留合法 `finish_reason=stop` 的正常停止；停止不代表通过最终验收 |
| 错误与预算 | 沿用现有失败、用量及请求 ID 记账；失败请求占用调用预算；不新增隐式重试或 HTTP 400 自动删参数重试 |
| CLI 与证据 | 共享 CLI 增加 `--openai-omit-parallel-tool-calls`；配置通过现有 `asdict(config)` 进入装配 manifest |
| Anthropic | 不发送 OpenAI 字段，原有 `disable_parallel_tool_use: true` 不变 |

参数语义参考 [OpenAI 官方 Function calling 文档](https://developers.openai.com/api/docs/guides/function-calling#parallel-function-calling)。官方 API 的行为不代表所有兼容网关支持该字段。

## 使用方式

默认无需修改装配代码。只有明确确认某个兼容网关不接受此参数时，才使用：

```python
from adapters.runtime import ModelConfig

config = ModelConfig(
    model="your-model",
    base_url="https://gateway.example.com/v1",
    api_key_env="MODEL_API_KEY",
    openai_parallel_tool_calls=None,
)
```

使用共享 CLI 的命令可显式添加：

```bash
python tools/run_roadmap_showcase.py --mode live --protocol openai \
  --model your-model --base-url https://gateway.example.com/v1 \
  --api-key-env MODEL_API_KEY --openai-omit-parallel-tool-calls
```

该示例需要用户配置自己的凭据，并会发起真实模型请求；本次未执行。
省略字段只影响请求，不放宽本地解析。若网关忽略单调用约束并返回多个调用，仍明确失败，应检查网关能力或使用其他适配器；不能删除保护来掩盖问题。

## 验证记录

1. 先补回归用例，在实现前运行 OpenAI 测试：`15 failed, 21 passed`；失败对应缺少请求参数、新配置及 CLI。
2. 完成适配器、配置与 CLI 实现后，运行 OpenAI、Anthropic/共享运行时、客观停止及证据契约测试：`76 passed`。
3. 完整回归（包含可选 MCP stdio / HTTP 传输）：`212 passed in 13.24s`，无跳过项。

测试覆盖默认/省略两种请求、配置序列化和 CLI、非法配置、合法零调用、正常单调用、合法多调用与含非法后续调用的批次、零工具执行、脱敏错误、失败 token 保留、预算不绕过、HTTP 错误无自动重试，以及 Anthropic 协议不变。

本地通过 `uv run --no-project --with pytest --with jsonschema` 运行相关测试；完整回归额外加载 `--with 'mcp>=2.1,<3'`，不修改项目依赖或锁文件。

## 范围与限制

- 未修改 `src/agent_chassis/`、SonarQube 载荷、本机环境、网关或 Hermes 配置。
- 未增加并行执行、流式传输、能力探测或新的重试策略。
- 本地验证使用测试 transport；没有企业网关 live 验证，不宣称企业服务已兼容。
- 默认增加协议字段可能使不支持它的网关拒绝请求；显式省略选项用于兼容，禁止自动猜测降级。
- CI 由该分支现有工作流负责；本地测试通过不替代远端 CI/live 通过。
