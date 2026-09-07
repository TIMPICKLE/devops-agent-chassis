# ReAct 多工具并行调用

ReAct 支持一轮提出多个独立动作、并发执行、收齐观察后再决策。单调用接口仍可用，旧装配默认并发上限为 1。这里的并行适合多个独立 I/O 等待；并不代表 `LLMCompilerPattern` 已经支持 DAG 波内并发。

## 1. 声明哪些工具可以并行

```python
from agent_chassis.orchestration import ToolBox

def read_file(path):
    boundary.check("repo.read")
    return immutable_snapshot[path]

toolbox = ToolBox().add(
    "read_file", read_file, parallel_safe=True,
    input_schema={
        "type": "object", "properties": {"path": {"type": "string"}},
        "required": ["path"], "additionalProperties": False,
    },
)
```

`parallel_safe=True` 是工具作者对线程安全、动作独立性和外部资源使用方式的声明，不授予权限，也不是沙箱。函数及其 Connector/client 必须允许并发使用，不能修改共享 RunContext、共享候选或同一个文件；不确定时保持默认 `False`。权限检查仍由现有工具包装器执行，运行期权限失败会作为工具失败记录，兄弟调用可能已经完成。

`add_contextual()` 工具共享 task/ctx，只允许单独调用。替换工具注册会清除之前的并行声明，需要重新明确设置。需要动态读取知识上下文的工具先保持 contextual 单调用；普通并行工具如需固定知识，由装配代码通过不可变配置提供。

内置员工工具中的 `read_file`、`read_files` 读取固定源码快照，已声明可并行；`submit_source`、`submit_project` 等修改共享候选的工具仍须单独调用。后一个动作如果依赖前一个动作的结果，必须放到下一轮，底盘不会替模型推断依赖。

## 2. 配置协议和执行器

参考 CLI 共用以下参数：

```bash
--max-parallel-tools 4 --max-batch-calls 8 --max-tool-calls 64
```

这是追加到原运行命令的参数片段。CLI 会同时配置模型协议和 ReAct：OpenAI 发送 `parallel_tool_calls: true`，Anthropic 发送 `disable_parallel_tool_use: false`。模型不保证每轮都会选择多个工具。只有提交类工具的装配仍只能单调用，不会因开启开关自动产生可并行工作。

直接在 Python 中装配 OpenAI：

```python
from adapters.runtime import ModelConfig
from adapters.openai_runtime import OpenAIChatDecider
from agent_chassis.orchestration import ReActPattern

config = ModelConfig(
    model="YOUR_MODEL", base_url="https://YOUR_GATEWAY/v1",
    api_key_env="OPENAI_API_KEY",
    max_parallel_tools=4, max_batch_calls=8, max_tool_calls=64,
    openai_parallel_tool_calls=True,
)
decider = OpenAIChatDecider(config, tool_names=toolbox.names())
pattern = ReActPattern(decider, **config.react_options())
```

以上示例接入装配方提供的 `toolbox`、`boundary` 和不可变快照。参考适配器依赖仓库检出及 `.[llm]` 可选依赖；底盘核心仍零第三方依赖。Anthropic 使用 `AnthropicDecider`，省略 OpenAI 专用字段即可，并发能力由 `max_parallel_tools` 决定。

对于明确拒绝该 OpenAI 参数的网关，可追加 `--openai-omit-parallel-tool-calls`，或将 `openai_parallel_tool_calls=None`。省略参数不会放宽本地限制，也不触发自动重试。旧配置 `False` 继续请求单调用；手工装配应同时设置协议字段与执行器参数，使用 `config.react_options()` 避免两侧上限不一致。

协议字段参考：[OpenAI function calling](https://developers.openai.com/api/docs/guides/function-calling)、[Anthropic parallel tool use](https://platform.claude.com/docs/en/agents-and-tools/tool-use/parallel-tool-use)。第三方网关的实际支持仍需实测。

## 3. 动作、预算和执行顺序

自定义 decider 可返回：

```python
from agent_chassis.orchestration import ToolRequest

action = ("batch", [
    ToolRequest("read-a", "read_file", {"path": "a.hpp"}),
    ToolRequest("read-b", "read_file", {"path": "b.hpp"}),
], None)
```

旧 `("call", name, args)` 和 `("stop", reason, None)` 保持有效。批次至少包含两个动作，调用 ID 在批内唯一。同名工具允许传不同参数。两种参考模型适配器对整批做工具白名单、JSON Schema、ID、并行声明和函数参数校验；底盘自定义 decider 路径做结构、ID、工具声明和函数签名检查，不依赖 JSON Schema 库。

| 限制 | 默认 | 含义 |
|---|---:|---|
| `max_parallel_tools` | 1 | 同时运行的工具数；1 表示拒绝批量动作 |
| `max_batch_calls` | 8 | 一轮最多可提交多少个动作；可大于并发数，超出的动作排队 |
| `max_tool_calls` | 64 | 同一 RunContext 的 ReAct 动作总预算，跨失败尝试保留；启动前整批预留，不因失败返还 |
| `max_calls` | 8 | 参考适配器的模型请求总预算；一个批次响应仍计一次请求 |

`iterations` 每轮加 1。嵌套 Connector 记录不额外占用 ReAct 动作预算，该预算不是外部 API 请求数限制。所有工具及参数预检通过后才提交工作线程；超过任务剩余预算时，整批零执行。知识注入先由协调线程按调用次序完成；工作线程不接收共享 task/ctx，参数逐调用深拷贝。

## 4. 结果、失败与验收

- 一旦批次提交，排队动作也属于这批工作，整批采用等待所有动作结束的语义。任一失败不会自动撤销、重试或忽略其他动作。
- 工作线程全部结束后，按请求顺序合并成功/失败记录、更新 `tool_results`、通知 Observer。Connector 记录先在线程局部缓冲，再由协调线程归入当前任务；工具自行创建的额外线程不自动继承该上下文。
- 同名工具的 `tool_results[name]` 为请求顺序中的最后一个成功结果，属于旧兼容视图；完整逐调用结果保留在 `ctx.tool_calls`，后续模型观察携带 `batch_id`、`call_id`、参数、结果与成功状态。
- 批次失败时先完整记账，再抛出 `BatchToolError` 进入已装配的失败策略。显式任务重试策略仍可重试整轮，需自行保证补偿或幂等；底盘不额外自动重试工具。
- 只有成功批次才检查 `stop_when`。无论模型停止还是客观停止，最终成功仍由 `DoneCriteria` 根据事实/外部状态判定。

线程无法安全强杀，因此工具必须自己设置 I/O 超时。底盘不会在后台调用尚未结束时回滚工作区。本能力不提供分布式事务、进程隔离或强制取消，也不自动推断资源冲突。

可移植证据新增可选 `call_id` / `batch_id`，保持原有不导出工具参数/结果的默认做法；新 schema 接受旧报告，使用旧版严格 schema 的读取端需一并更新。RecordingObserver 也记录这两个字段。记录按请求顺序展示，不应把记录顺序当作实际完成先后。

## 5. 验证与演示

```bash
python examples/06_parallel_tools.py
python -m pytest tests/test_parallel_react.py tests/test_parallel_runtime.py -q
```

演示无需 API key。测试通过同步屏障证明两个调用同时运行，不用机器速度阈值判断；另覆盖并发上限、整批拒绝、失败收尾、Connector 归属、参数隔离、两种协议、token 记账和公开证据核验。参考模型响应是测试替身，不是 live 模型效果证据；这次未测企业任务的实际加速比。
