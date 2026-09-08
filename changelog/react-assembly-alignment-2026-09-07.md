# ReAct 装配配置一致性检查 · 2026-09-07

分支：`feat/roadmap-showcase-v1`。对应维护任务 T01（装配配置一致性检查），承接 [ReAct 独立工具并行](react-parallel-tools-2026-09-07.md)。

## 问题

模型适配器和 ReAct 执行器各自持有执行配置：`ModelConfig`（协议侧解析上限）与 `ReActPattern`（执行器侧并发与预算）。两者靠装配方手工用 `**config.react_options()` 对齐；生成的装配脚本漏传参数时不会报错，直到模型真的返回多个调用，才在运行中抛出 `Parallel tool execution is disabled`。冲突暴露得太晚，且报错不含两侧配置值。

## 修改

| 项目 | 行为 |
|---|---|
| 统一装配入口 | `adapters.runtime.react_pattern(config, decider, *, toolbox=None, **overrides)`：构造 `ReActPattern(decider, **config.react_options(), **overrides)` 并立即做一致性检查 |
| 运行前冲突检查 | `adapters.runtime.validate_react_alignment(config, pattern, toolbox=None)`：`max_parallel_tools` / `max_batch_calls` / `max_tool_calls` 三项两侧不一致立即 `ValueError`，报错包含字段名、模型侧与执行器侧的值和修正方式（`react_pattern(...)` 或 `**config.react_options()`）；双向不匹配都算冲突 |
| 并行声明提示 | 开启并行（`max_parallel_tools >= 2`）但工具箱中没有任何工具声明 `parallel_safe=True` 时，通过 `warnings.warn` 提示所有动作将保持单调用；不阻止装配运行 |
| 非 ReAct 执行器 | 传入缺少上述字段的 pattern 时明确报错，说明该检查只适用于 ReAct 风格执行器 |
| 核心包边界 | 新代码全部在 `adapters/runtime.py`；`src/agent_chassis/` 不 import 适配器，自定义 decider（普通 `Decide` 可调用对象）继续可用 |
| 参考入口切换 | `tools/run_roadmap_showcase.py`、`tools/run_policy_workflow.py`、`employee_factory/runtime.py`、`employee_factory/generation.py` 的四处 `ReActPattern(decider, **config.react_options(), ...)` 全部改用 `react_pattern`；CLI 行为不变，装配期新增检查 |
| 文档 | [PARALLEL_TOOLS.md](../docs/PARALLEL_TOOLS.md) 第 2 节改为推荐统一入口，说明手工装配的替代检查方式 |

检查发生在装配期（构造 pattern 时），早于任何模型请求和工具执行；`openai_parallel_tool_calls` 等协议字段校验仍由 `ModelConfig.__post_init__` 负责，本次未改动。

## 验证

- 新增 `tests/test_react_alignment.py` 8 项：统一入口等价于手工接线、冲突在 decider 调用前报错（无模型请求、无工具执行）、三个字段双向检查、冲突性 override 被拒绝、无并行声明工具时提示但单工具装配仍可运行、单一 `parallel_safe` 工具可组批且不提示、旧单调用装配与自定义 decider 静默通过、非 ReAct 执行器被拒绝。
- 本地（Windows，uv 环境 `mcp>=2.1,<3` + pytest + jsonschema）非编译器依赖测试 **191 passed, 3 skipped**，含并行、模型运行时、证据契约、示例冒烟与 showcase/policy CLI 回归。
- 本机无 `c++` 编译器，依赖编译器的测试（employee_factory / employee_updates / header_build 场景等 24 项）无法本地执行；已用 `git stash` 在原始代码上复跑对照，失败集合与改动前一致（缺编译器 + Windows 临时文件替换的文件锁竞争），确认非本次改动引入，需由远端 CI 确认。
- 对照中发现 `test_context_experiment.py::test_report_verifier_detects_inconsistent_evidence[patch]` 在原始 Windows 代码上是通过的**假阳性**：CRLF 换行让所有 patch 摘要都不匹配，篡改检测碰巧触发。修复 CRLF 后该用例在本机因 header_build 试次无法成功而暴露为失败，Linux CI 上应为正常通过。
- 另修复一个 Windows 环境问题：`tools/run_roadmap_showcase.py` 写 `.patch` 文件未固定 `newline="\n"`，Windows 默认换行转换导致 `verify_roadmap_evidence.py` 的 sha256 摘要校验失败；修复后 `tests/test_evidence_contract.py` 全部通过。

## 边界

- 一致性检查覆盖任务指定的三项执行上限；`max_iterations` / `max_calls`（模型请求预算）与 OpenAI 协议字段的组合语义未纳入强制检查，维持既有文档约定。
- 提示（warning）不阻止运行：没有并行安全声明时装配照常工作，只是全部单调用。
- 手工 `ReActPattern(...)` 装配若不经过入口也不调用检查函数，运行期行为与之前完全一致；入口是推荐路径而非强制约束。
