# OpenAI / Anthropic 流式模型支持 · 2026-09-08

分支：`feat/roadmap-showcase-v1`。实施起点：`d99ed0fa37ea2cb686e7185ca43c2d74fab72e57`。
用户确认服务器模型服务必须使用流式，因此将前次 V5 复核中的接入能力缺口落地为可配置实现。

## 拆分与实施

| 任务 | 实施结果 |
|---|---|
| 配置与入口 | `ModelConfig.stream` 默认 false；共享 CLI 添加 `--stream`，生成和运行分别设置；OpenAI 流式用量可通过专用选项显式省略 |
| SSE 接收 | `adapters/streaming.py` 处理 UTF-8 分片、LF/CRLF/CR、多行 data 和注释；总字节、事件、行和索引有界，读到协议结束后关闭流 |
| OpenAI 重组 | 按 index 重组交错的 ID、函数名和参数片段，恢复调用顺序；要求 finish reason 与 `[DONE]`，接收末尾独立用量块 |
| Anthropic 重组 | 校验 message/content block 生命周期，重组 `input_json_delta`，要求块关闭和 `message_stop`；用量为累计更新，thinking 不写入日志 |
| 安全执行与失败 | 完整响应后复用原有参数、安全声明和预算预检；中断或错误零工具执行；完整但不合格响应保留已报告用量；HTTP 诊断及不自动重试策略保留 |
| 可观测与兼容 | 每次请求增加可选 `model_calls[].stream`，清单包含配置；默认传输仍标 live，自定义 transport 保持 test-transport；v1 schema 可读取旧报告 |
| 文档与装配指导 | 新增[流式使用说明](../docs/STREAMING.md)，同步模型接入、生成、更新、技术演示、README、当前状态和装配 Skill 参考文档 |

代码位于适配器和参考 CLI，核心 `src/agent_chassis/` 没有新增依赖或业务逻辑。旧的 `post_json()` 四参数入口与 Anthropic 模块的兼容导入保留。

## 验证

- 首批旧适配器与 HTTP / 并行回归 **122 passed**，确认默认非流式路径兼容。
- 流式专项 **52 passed**：两种协议、短网络分片、交错工具、中文、正常结束、用量缺失/累计更新、流错误、超时、IncompleteRead、HTTP 诊断、限制、非法事件顺序、整批拒绝、独立判据和证据核验。
- 两个专项测试通过标准库本地 HTTP 服务器发送真实 Transfer-Encoding chunked SSE，验证 `HTTPResponse.read1()` 接收与协议重组；其余响应为合成测试替身。
- 完整回归 **404 passed**，包含 MCP 集成测试；核心包仍不依赖新增第三方库。
- 文档中的 Python 示例通过语法检查，本地文件链接解析通过；装配 Skill 的 `quick_validate.py` 和 `git diff --check` 通过。

没有调用付费模型或企业服务。测试不冒充服务端模型实测；服务器下一次运行应给自定义 `ModelConfig` 设置 `stream=True`，或给支持共用模型参数的命令传入 `--stream`。

## 范围与边界

- 流式接收后统一执行，不做逐 token 终端展示、推理日志保存或参数尚未完成时的提前工具执行。
- 不改变默认并发和 `parallel_safe` 约束，不静默丢弃工具、不自动把不安全批次变为串行。
- 没有正确结束事件时保守记为失败，用量未知；完整响应中的最终用量不因工具参数预检失败而丢失。
- 请求 SSE 却收到其他 Content-Type 会明确失败，不自动退回 JSON。未知 Anthropic 事件或 server tool block 也不会被猜测为本地工具调用。
- 传输继续受字节/事件上限及阻塞 I/O 超时约束；不是任务硬性总时限，也不能绕过网关请求体限制。
- 支持 Chat Completions 与 Messages 原生客户端工具调用，未实现 Responses API 或自动协议/模型能力探测。

协议参考与各项限制集中在[流式说明](../docs/STREAMING.md)，使用官方 OpenAI 和 Anthropic 事件说明核对。

## 远端验证

受测实现提交：[9e26ecb92c61d2499b88abd1ccee10e13ff954e4](https://github.com/TIMPICKLE/devops-agent-chassis/commit/9e26ecb92c61d2499b88abd1ccee10e13ff954e4)。三条 CI 均通过：

- [Roadmap 合同与可移植证据](https://github.com/TIMPICKLE/devops-agent-chassis/actions/runs/34198215165)，包含 Python 3.9 / 3.13。
- [Employee Project](https://github.com/TIMPICKLE/devops-agent-chassis/actions/runs/34198215162)。
- [Employee Update](https://github.com/TIMPICKLE/devops-agent-chassis/actions/runs/34198215150)。

后续提交仅补录结果；没有触发付费模型任务或企业网关验证。
