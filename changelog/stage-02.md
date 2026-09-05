# 第二阶段：降低无效调用，让模型与上下文可以对照

日期：2026-09-05。分支：`feat/roadmap-showcase-v1`。本阶段优先前瞻性和实用性。

## 快速概览

| 功能 | 你能直接得到什么 | 当前状态 |
|---|---|---|
| S2-01 客观检查后及时停止 | 补丁通过提交检查后结束推理，避免继续重复调用；最终验收照常执行 | 本地 40 项相关测试通过；live 待本阶段集成验证 |
| S2-02 模型协议互换 | 用相同任务/工具/判据切换 Anthropic 和 OpenAI 兼容接口 | 待实现 |
| S2-03 上下文对照实验 | 一键比较按需、全量、不注入三种策略；输出调用量、token、验收结果 | 待实现 |

## S2-01 · 客观检查后及时停止

上一轮两个 live 场景各消耗了 8 次请求。现在新增可选 `ReActPattern(stop_when=...)`：每次工具执行后检查客观停止信号；参考装配在候选已通过提交检查且内容未变化时停止推理。它不写成功 verdict，最终仍由 `DoneCriteria` 独立检查。

- 默认参考装配使用 `--stop-policy objective`；`--stop-policy model` 保留原先由模型决定何时停止的方式，方便对照。
- 错误候选仍会把检查反馈送到下一轮；通过后的信号绑定当前候选摘要，旧检查不能用于新内容。
- 报告新增停止原因和 token 汇总；每个场景开始/结束都会及时输出进度。
- 验证覆盖：反复提交的 decider 只调用一次、先错后改、三个编排共用停止策略、最终判据仍可拒绝、旧信号失效。
- 本地结果：`python -m pytest tests/test_objective_stop.py tests/test_roadmap_showcase.py tests/test_model_runtime.py -q`，**40 passed**。

运行入口：`python tools/run_roadmap_showcase.py --mode offline`。真实节省多少请求以新一轮 live 结果为准，不根据单元测试推算生产收益。
