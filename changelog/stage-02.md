# 第二阶段：降低无效调用，让模型与上下文可以对照

日期：2026-09-05。分支：`feat/roadmap-showcase-v1`。本阶段优先前瞻性和实用性。

## 快速概览

| 功能 | 你能直接得到什么 | 当前状态 |
|---|---|---|
| S2-01 客观检查后及时停止 | 补丁通过提交检查后结束推理，避免继续重复调用；最终验收照常执行 | CI / live 通过；本轮 10 个任务均一次请求后客观停止 |
| S2-02 模型协议互换 | 用相同任务/工具/判据切换 Anthropic 和 OpenAI 兼容接口 | CI / live 通过；两种协议各 2/2 验收通过 |
| S2-03 上下文对照实验 | 一键比较按需、全量、不注入三种策略；输出调用量、token、验收结果 | CI / live 通过；6/6 试次完成，报告经独立核验 |

**本阶段已完成：160 项完整回归通过，真实模型 10/10 任务通过，共 10 次请求。** 三种上下文策略在这两个简单任务上均成功，不能据此宣称注入知识提高了成功率。详细实测见本文最后一节。

## S2-01 · 客观检查后及时停止

上一轮两个 live 场景各消耗了 8 次请求。现在新增可选 `ReActPattern(stop_when=...)`：每次工具执行后检查客观停止信号；参考装配在候选已通过提交检查且内容未变化时停止推理。它不写成功 verdict，最终仍由 `DoneCriteria` 独立检查。

- 默认参考装配使用 `--stop-policy objective`；`--stop-policy model` 保留原先由模型决定何时停止的方式，方便对照。
- 错误候选仍会把检查反馈送到下一轮；通过后的信号绑定当前候选摘要，旧检查不能用于新内容。
- 报告新增停止原因和 token 汇总；每个场景开始/结束都会及时输出进度。
- 验证覆盖：反复提交的 decider 只调用一次、先错后改、三个编排共用停止策略、最终判据仍可拒绝、旧信号失效。
- 本地结果：`python -m pytest tests/test_objective_stop.py tests/test_roadmap_showcase.py tests/test_model_runtime.py -q`，**40 passed**。

运行入口：`python tools/run_roadmap_showcase.py --mode offline`。真实节省多少请求以新一轮 live 结果为准，不根据单元测试推算生产收益。

## S2-02 · 模型协议互换

新增 `OpenAIChatDecider`，实现非流式 Chat Completions 单函数调用；Anthropic 和 OpenAI 协议共用上下文读取、任务级调用预算、工具参数校验和证据记录。协议差异只留在适配层，两个载荷、三种编排及最终验收器无需修改。

```bash
python tools/run_roadmap_showcase.py --mode live --protocol anthropic --max-calls 4
python tools/run_roadmap_showcase.py --mode live --protocol openai --max-calls 4
```

- CLI 支持 `--model`、`--base-url`、`--api-key-env`、调用/输出/上下文预算和超时；环境变量分别读取 `ANTHROPIC_*` 或 `OPENAI_*`，避免切换协议却误用旧地址。
- OpenAI 协议默认连接智谱 Coding Plan 专用地址 `https://open.bigmodel.cn/api/coding/paas/v4`，参见[官方接入说明](https://docs.bigmodel.cn/cn/guide/develop/opencode)。复用现有 `BIGMODEL_API_KEY` 环境变量引用。
- 统一导出输入/输出 token；响应没有 usage 时保持未知。测试 transport 明确标记，不能被声明成 live 证据。
- 本地结果：原适配器、第二协议、载荷装配与及时停止相关测试 **62 passed**，其中新增协议测试 22 项。
- 边界：当前支持单函数调用的公共子集，不含 Responses API、流式输出或所有厂商扩展参数。两个协议连接同一 GLM 服务，仅证明协议可互换，不代表跨厂商效果已验证。

## S2-03 · 可重复的上下文对照实验

新增 `run_context_experiment.py`，把“按需注入是否有用”变成可以运行、检查和讨论的实验。三种策略为 `routed`（仅当前任务规范）、`full`（全部两份规范）、`none`（不注入附加规范）。任务描述、系统指令、工具及验收器保持相同，`AGENT_BOOT` 仍为空。

```bash
# 不调用模型：验证试次设计、上下文路由、验收和报告链路
python tools/run_context_experiment.py --mode offline --output-dir reports/roadmap-showcase-ablation-demo
python tools/verify_context_experiment.py reports/roadmap-showcase-ablation-demo

# 显式真实实验：2 场景 × 3 策略 × 1 次，最多 24 次请求
python tools/run_context_experiment.py --mode live --max-calls 4 --max-total-model-calls 24 --seed 42 --repeats 1 --output-dir reports/roadmap-showcase-ablation-live
python tools/verify_context_experiment.py reports/roadmap-showcase-ablation-live --require-live
```

每次使用新输出目录，也可省略目录自动生成。安装依赖与编译器要求同第一阶段。

| 文件 / 行为 | 解决什么问题 |
|---|---|
| `experiment-plan.json` | 请求前固定模型配置、输入摘要、知识版本摘要、随机种子和试次顺序；每一轮包含全部 6 个条件 |
| `summary.md` | 一页看到三组通过数 / 计划数、完成情况、请求、token、知识字符和耗时，并能打开每个试次证据 |
| `summary.json` | 保留逐试次数据；失败和未运行都计入计划分母；中途异常仍保留已完成结果及待运行数 |
| `trial-*.manifest.json` / `*.evidence.json` / `*.patch` | 同一证据契约串起任务、装配、模型调用和验收；成功 patch 与判据摘要绑定 |
| `verify_context_experiment.py` | 从原始证据重算汇总，检查固定配置、任务输入、试次身份、预算和 patch 是否一致 |
| 完整试次预算预留 | 剩余额度不够单试次预算时标记 `not_run`，不会暗中缩小后面策略的推理预算 |

本地离线实验 6/6 验收通过。单轮知识字符合计 `routed=246`、`full=496`、`none=0`，只证明实际注入量不同；离线 token 保持未知，不能用这些字符数代替真实 token 费用。

测试覆盖两种协议实际请求的上下文差异、相同任务输入、失败及预算耗尽、不完整报告、汇总与证据不一致。实际 live 结果见下方，离线回放另行标记。

## Actions 与本阶段验证

普通提交执行 Python 3.9 / 3.13 回归、双载荷 × 三编排离线矩阵和 6 试次离线实验；报告进入 Actions Summary 与 artifact。

显式 `live=true` 或提交信息 `[roadmap-live]` 才运行付费模型验证：两种协议各两个场景，单场景最多 4 次请求；另运行 6 试次上下文实验，最多 24 次请求。整项 live job 最多 **40 次模型请求**，使用已有 `BIGMODEL_API_KEY` Secret，不自动重试服务失败。每组报告均独立留存，验证器重新核对实际调用与用量。

本地完整回归：`python -m pytest tests/ -q`，**160 passed**（Python 3.12，包含 MCP 传输测试）。[本阶段 Actions](https://github.com/TIMPICKLE/devops-agent-chassis/actions/runs/33969106344) 测试源提交：`08d43fb6b76b6c831ce8ea1435646a3661d5a642`。

| 验证层次 | 实际结果 |
|---|---|
| 本地 Python 3.12 | 160 passed，包含 MCP stdio / HTTP 传输测试 |
| Actions Python 3.13 | 160 passed；6 个离线复用案例及 6 试次上下文实验均通过验收和证据核验 |
| Actions Python 3.9 | 158 passed、1 skipped（该版本未安装可选 MCP 传输依赖）；相同离线矩阵与实验通过 |

没有将本阶段完成等同于整个 Roadmap 完成：B03/B06 的开发对照链路得到补齐，代表性保留集、企业任务迁移成本和与脚本方案的公平对照仍是后续工作。

## 真实模型结果 · 2026-09-05

[Actions 运行成功](https://github.com/TIMPICKLE/devops-agent-chassis/actions/runs/33969106344)；[live 原始报告、清单与补丁](https://github.com/TIMPICKLE/devops-agent-chassis/actions/runs/33969106344/artifacts/9970394560)，artifact 名称 `roadmap-live-33969106344-1`，保留 14 天。以下数值取自该次任务日志；原始结构化证据由 Actions 内独立校验器核验。

模型为 `glm-5.3-flash`，测试源提交 `08d43fb6b76b6c831ce8ea1435646a3661d5a642`。全部 **10/10 任务通过**，实际 **10 次请求**（上限 40），输入 token 合计 **3229**、输出 token 合计 **1592**。所有任务停止原因均为 `objective_stop`，最终验收全部通过。

### 协议互换与及时停止

| 接口协议 | 场景 | 验收 | 请求数 | 输入 / 输出 token |
|---|---|---|---:|---|
| Anthropic Messages | Python 质量修复 | 通过 | 1 | 297 / 195 |
| Anthropic Messages | C++ 头文件修复 | 通过 | 1 | 348 / 190 |
| OpenAI Chat Completions | Python 质量修复 | 通过 | 1 | 297 / 102 |
| OpenAI Chat Completions | C++ 头文件修复 | 通过 | 1 | 348 / 144 |

上一阶段两个 live 任务各记录 8 次请求，本阶段对应任务各 1 次后停止。这个观察只适用于已运行的公开夹具；两轮并非严格随机对照，不能直接推广成所有任务的成本下降比例。两种接口访问同一智谱服务，不是两个模型厂商的效果排名。

### 上下文对照

Anthropic 协议，同一模型、嵌套编排、客观停止策略；`seed=42`，每条件一次。每组包含同样的 Python / C++ 两个任务。

| 策略 | 通过 / 计划 | 实际请求 | 输入 token | 输出 token | 知识字符合计 | 耗时合计 ms |
|---|---|---:|---:|---:|---:|---:|
| 按需 routed | 2 / 2 | 2 | 645 | 282 | 246 | 14576 |
| 全量 full | 2 / 2 | 2 | 706 | 486 | 496 | 16361 |
| 不注入 none | 2 / 2 | 2 | 588 | 193 | 0 | 13284 |

没有未运行或待运行试次，证据核验确认 `coverage_complete=True`。按需组比全量组少 61 个输入 token，但不注入组也能成功且用量更少。当前任务描述本身提供了足够线索，尚不能证明附加知识带来质量收益；输出长度与耗时只有单次样本，不作统计显著性结论。

因此下一步应引入**确实依赖企业规范的任务和保留集**，保留三组公平对照，再判断什么知识值得注入。实验可以呈现“没有收益”的结果，本身就是评委能复核的工程能力。

### 功能提交索引

- [S2-01：及时停止 + 变更目录](https://github.com/TIMPICKLE/devops-agent-chassis/commit/675464e3824bcfd5f04c68c0a36cb118a6ec1691)
- [S2-02：协议互换 + 本地验证记录](https://github.com/TIMPICKLE/devops-agent-chassis/commit/d9f901947955d0d829b87359a894539a810cf2ce)
- [S2-03：上下文实验 + 报告核验 + Actions](https://github.com/TIMPICKLE/devops-agent-chassis/commit/08d43fb6b76b6c831ce8ea1435646a3661d5a642)
