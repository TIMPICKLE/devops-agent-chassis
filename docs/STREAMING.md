# 流式模型服务接入

参考适配器支持 OpenAI Chat Completions 与 Anthropic Messages 的 SSE 响应。服务必须使用流式时，设置 `ModelConfig(stream=True)`，或在共用模型参数的 CLI 上添加 `--stream`。默认仍为非流式，兼容已有装配。

## 自定义服务器脚本

如果脚本自行构造 `ModelConfig`，需要在该对象里开启流式；仅升级底盘不会自动改变旧脚本配置。例如 OpenAI 兼容服务：

```python
import os
from adapters.runtime import ModelConfig

config = ModelConfig(
    model=os.environ["OPENAI_MODEL"],
    base_url=os.environ["OPENAI_BASE_URL"],
    api_key_env="BIGMODEL_API_KEY",
    stream=True,
    max_parallel_tools=4,
    max_batch_calls=8,
    max_tool_calls=64,
)
```

将该配置传给现有 `OpenAIChatDecider` 和 `react_pattern()`，工具箱及业务验收保持原有接线。Anthropic 适配器使用同一个 `stream` 选项，模型和 base URL 按对应服务设置。无需安装额外流式 SDK，核心包仍零第三方依赖；参考模型适配器继续需要 `.[llm]`。

## 参考 CLI

在环境中配置模型服务、模型名及 Key 后，以下是运行已有员工的命令模板；大写目录是需要替换的占位符，输出目录必须是新目录：

```bash
python tools/run_generated_employee.py --project EMPLOYEE_DIR --repo SOURCE_DIR --unit src/main.cpp --output-dir reports/stream-run --protocol openai --stream
```

共用选项的 `assemble_employee.py`、`run_generated_employee.py`、`run_roadmap_showcase.py`、`run_policy_workflow.py` 和 `run_context_experiment.py` 都支持 `--stream`。生成和运行是不同命令，服务要求流式时，两阶段都要传入；知识更新后的员工也应在每次运行时传入。

| 参数 | 默认 | 作用 |
|---|---|---|
| `--stream` / `ModelConfig.stream` | false | 发送流式请求，读取 SSE，并重组成完整工具响应 |
| `--openai-omit-stream-usage` | 不省略 | OpenAI 流式默认请求 `stream_options.include_usage=true`；网关明确不接受该字段时可显式省略整个 `stream_options` |
| `ModelConfig.openai_stream_include_usage` | true | 自定义脚本中设为 false，与上述省略选项等效；非流式或 Anthropic 请求不发送该选项 |

省略用量选项不会关闭流式。服务未返回可信用量时，报告保留 null，不记为零；不会遇到 HTTP 错误就自动重试、删除参数或切换非流式。

## 接收、校验与执行

1. 请求带 `stream=true` 和 `Accept: text/event-stream`，响应必须是 `text/event-stream`（可带 charset）。
2. SSE 读取器处理 UTF-8 分片、LF/CRLF/CR、多行 data、注释和心跳；不按网络分块边界直接解析 JSON。
3. OpenAI 按工具 `index` 重组 ID、函数名和参数片段，并按索引恢复调用顺序；要求 completion 的结束原因和 `[DONE]`。末尾 `choices=[]` 的用量块可正常接收。
4. Anthropic 跟踪 message/content block 的起止事件，累积 `input_json_delta`；要求所有块结束、停止原因及 `message_stop`。`message_delta.usage` 的累计值按最后报告更新，不跨块求和。
5. 完整响应进入既有工具 schema、参数、安全声明与预算预检，通过后才执行工具。中途已经收到一段合法参数，也不会提前执行它。

协议依据：[OpenAI 工具调用流式](https://developers.openai.com/api/docs/guides/function-calling#streaming)、[OpenAI 流式用量](https://developers.openai.com/api/reference/resources/chat/subresources/completions/methods/create)、[Anthropic SSE 事件](https://platform.claude.com/docs/en/build-with-claude/streaming)。本实现支持参考适配器的原生客户端工具调用子集；不实现 Responses API、Anthropic server tools 或新增未知事件的自动推断。

流式传输与工具并行分别配置。`--stream` 不会自动开启并行；需要时另加 `--max-parallel-tools 4`，并核对工具的 `parallel_safe` 声明，详见[并行说明](PARALLEL_TOOLS.md)。

## 失败与证据

- 断流、缺少结束事件、非法事件顺序、错误事件、编码或 JSON 损坏：关闭响应流，该次模型调用失败，零工具执行。
- 完整流中出现 `length` / `max_tokens` 截断或非法工具参数：由原适配器拒绝；已收到的完整用量继续记账。
- HTTP 错误继续走[安全 HTTP 诊断](HTTP_DIAGNOSTICS.md)。HTTP 200 内的 SSE error 记为流错误，不伪造 HTTP 状态，也不输出服务端错误正文。
- 未完成的流不把途中用量估算成最终账单；该次调用用量保留 null，仍占模型请求次数。完整流缺失的用量字段同样保留 null。
- `model_calls[].stream` 记录该次请求的流式配置，`mode` 继续区分真实传输与测试替身；`elapsed_ms` 是完整请求耗时，不是首 token 延迟。
- `stream` 是 v1 证据格式的新增可选字段，当前核验器仍接受旧报告；旧严格读取端需同步 schema。配置清单自动记录两个新增配置字段。

当前是**流式接收、完整响应后执行**，没有终端逐字输出或向观察器推送模型推理。文本、thinking 和签名片段被消费但不保存为模型调用日志；业务工具结果仍按原有可观测约定记录。

每条流总线数据上限 2,000,000 字节（含 SSE 开销），单行/事件上限 256,000 字节，最多 10,000 个含 data 的事件和 256 个内容/工具索引。达到限制会明确失败；不能把这种失败当成候选已完整生成。`--timeout` 仍用于阻塞网络操作，不是带心跳流的任务总时限，也不保证解决所有模型或网关超时。

## 验证

```bash
python -m pytest tests/test_streaming_runtime.py -q
```

测试包含合成事件、本地 HTTP 分块传输、交错工具、中文分片、累计用量、错误/中断、拒绝不安全批次和报告核验。它们验证协议实现，不代表已经连接并验证某个企业网关；服务器可用相同业务输入再次实测。
