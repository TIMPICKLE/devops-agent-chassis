# ReAct 装配与验证配方

## 统一接线

复用仓库检出中的 `adapters.runtime.react_pattern(config, decider, toolbox=box, ...)`。
它传递 ModelConfig 的并发/批量/任务预算，复用 T01 配置一致性校验。自定义 decider 也可使用。
直接构造 `ReActPattern` 时，运行前调用 `validate_react_alignment(config, pattern, box)`。

普通只读工具应先检查路径范围、共享状态和客户端线程安全，再显式 `parallel_safe=True`。
返回多个动作并不代表实际并行；同一个工具用不同参数可组成独立批次，依赖前项结果的操作必须下一轮决策。
共享候选写入和 contextual 工具保持单调用。完整说明见 [并行工具](../../../../docs/PARALLEL_TOOLS.md)。

## 失败定位（T02 / T05）

先区分装配期配置冲突、模型 HTTP 失败、模型响应/工具预检失败和实际工具执行失败。
参考适配器的 `ModelError.diagnostic` 是 T02 工具诊断；`ModelError.http_error` 是 T05 HTTP 元数据，
分别进入 `model_calls[].diagnostic` / `model_calls[].http_error`。批次级错误不一定有动作序号。
HTTP 400 不能直接推断为并行缺陷；仅采用状态和固定白名单代码，不打印服务端原文或凭据。
Retry-After 只是记录，不触发重试或自动改参数；详见 [HTTP 诊断](../../../../docs/HTTP_DIAGNOSTICS.md)。

## 独立验收与证据

先约定具体检查和证据来源。候选有 diff 只能证明产生了修改；具体业务标准由载荷决定，核心不规定必须编译。
使用 T04 的 `ctx.record_check()` 记录 `passed` / `failed` / `not_run`，结果必须来自确定性检查或外部状态。
DoneCriteria 仍负责最终裁定，不能根据模型自述或步骤名称判定成功。

生产证据通过 `source_revision()` 获取实际源码提交及工作区状态，以 `assembly_manifest()` 关联配置；
`EvidenceObserver.bind_manifest(..., production=True)` 在运行前校验必需元数据。
运行后用 `verify_documents(..., require_production=True)` 检查配置、检查回执和实际并发统计是否一致。
这不是签名认证、checkpoint 或重新执行验收；详情见 [生产证据](../../../../docs/PRODUCTION_EVIDENCE.md)。
ReAct 遥测自动生成；严格绑定、必需检查和回执需显式接线，当前普通生成/运行 CLI 没有自动启用全部严格字段。
任务重试时在本次流程准备阶段重新记录 `not_run`，不能沿用旧尝试的通过记录。

## 可运行参考

[examples/07_verified_assembly.py](../../../../examples/07_verified_assembly.py) 使用固定不可变数据、同步屏障和外部确定性检查，
演示单调用/并行装配、最终判据和生产格式证据核验。其模式明确为 `test-decider`，不调用模型，不能当作 live 效果证据。
生成生产员工时替换为已验证的原生协议适配器，并使用其 `execution_mode`，不要直接把模式标签改成 live。

```bash
python -m pip install -e ".[dev,llm]"
python examples/07_verified_assembly.py
python -m pytest tests/test_assembly_recipe.py -q
```

MCP 的 stdio / Streamable HTTP 已有真实集成测试；单连接内部仍串行，不能通过多线程包装宣称远端并行。
参考模型适配器没有 structured-output 自动兜底或 SSE 流式解析；新增适配需单独实现和验证。
