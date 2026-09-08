# 当前能力与验证状态

更新：2026-09-08。分支：`feat/roadmap-showcase-v1`。本页汇总第五阶段及后续底盘维护状态；早期缺陷复现保留在[历史审计基线](AUDIT_BASELINE_V2.md)，不作为当前缺陷清单。

最新实测复核：[V5 归属与修复](../changelog/server-v5-review-2026-09-08.md)。无安全工具的装配告警现明确说明单调用仍允许、批次整批拒绝，不暗示自动降级；[模型接入说明](../docs/MODEL_COMPATIBILITY.md)补充流式能力限制、超时排查及业务验收边界。

## 已验证什么

| 能力 | 当前实现与限制 | 证据 |
|---|---|---|
| 模型流式接收 | OpenAI Chat Completions / Anthropic Messages SSE，显式开启，完整重组后预检；不提前执行工具、不自动降级 | [流式用法](../docs/STREAMING.md)、[实现与验证](../changelog/streaming-2026-09-08.md)；未验证企业网关 |
| 模型 HTTP 诊断 | 状态类别、固定白名单错误代码、请求 ID 指纹及数字 Retry-After 进入证据；不输出正文、不自动重试 | [T05 维护记录](../changelog/task-05-http-diagnostics.md)、[使用说明](../docs/HTTP_DIAGNOSTICS.md) |
| 工具失败诊断与装配配方 | 区分并行开关、安全声明、工具/参数、批次/预算错误；不回显参数值和未知名称；Skill 配方经可运行示例验证 | [T02–T04 维护记录](../changelog/tasks-02-04.md) |
| 生产格式证据（显式开启） | 关联源码/装配、ReAct 实际配置、批次在途峰值和当前尝试验收回执；基础 v1 兼容；不认证报告真实性或普遍生产可用性 | [使用说明](../docs/PRODUCTION_EVIDENCE.md)、[实现与验证](../changelog/task-04-production-evidence.md) |
| ReAct 装配一致性检查 | `react_pattern` 统一入口 + 装配期核对模型侧与执行器三项上限；冲突在模型请求前报错，无并行声明工具时提示；检查位于适配层，核心包不依赖适配器 | [原实现与验证](../changelog/react-assembly-alignment-2026-09-07.md)；[后续 T02–T04 完整回归及 Python 3.9 / 3.13 CI 通过](../changelog/tasks-02-04.md) |
| ReAct 独立工具并行 | OpenAI / Anthropic 批量动作、有限线程并发、整批预检、失败等待、调用 ID；默认单调用，需声明工具可并行 | [本次实现与验证](../changelog/react-parallel-tools-2026-09-07.md)、[使用说明](../docs/PARALLEL_TOOLS.md)；未进行企业 live 性能测量 |
| 需求生成可运行员工 | 模型生成 Python 节点接线、清单和说明；首版仅 C++ include 修复，复用共享工具与验收器 | [第四阶段真实链路](https://github.com/TIMPICKLE/devops-agent-chassis/actions/runs/34020348896)：一次生成，同一项目处理两个源码输入，独立验收通过 |
| 规范更新并保留定制 | 只更新既有知识文件内容；预览影响、检测冲突、保留工作副本中的代码和自定义文件，创建新版本 | [第五阶段真实链路](https://github.com/TIMPICKLE/devops-agent-chassis/actions/runs/34033606604)：更新 0 次模型调用，新旧规范改变同一输入的选择，两个旧任务通过 |
| 知识注入与消费 | 显式事件、任务/阶段匹配、版本和消费回执；不是通用语义路由或自动理解文档 | [第三阶段](../changelog/stage-03.md)、[第五阶段](../changelog/stage-05.md)；第三阶段整体 live 门禁仍为失败，不改写历史 |
| 运行期模型适配 | Anthropic / OpenAI 兼容接口的受限工具调用子集；第二阶段访问同一 GLM 服务 | [第二阶段](../changelog/stage-02.md)；不是跨模型厂商效果比较 |
| 底盘合同与回归 | 权威判据、失败清理、上下文、项目生命周期、T1–T5、V5 告警与流式接入；核心基础安装无必需第三方依赖 | [流式验证](../changelog/streaming-2026-09-08.md)：本地 404 passed；前序 [V5](../changelog/server-v5-review-2026-09-08.md) 为 352 passed、[T5](../changelog/task-05-http-diagnostics.md) 为 350 passed，第五阶段 195 项属历史口径 |

第四阶段受测源码为 `34f49a23daba64d50e97588e90a0d4a47dfce655`；第五阶段为 `68a5a9c1b0be8b54e0b46d138974c229f9cdf545`。后续文档提交不扩大对应 live 验证范围。报告下载入口在各阶段记录中，Actions 产物保留 14 天。

## 三种检查必须区分

1. **生成合同检查**：代码能解析、工厂入口与节点/知识清单符合约定；不代表已经运行。
2. **源码任务验收**：真实编译器检查候选与修改范围，核对执行和知识记录；当前为单编译单元检查，没有完整链接和程序行为测试。
3. **业务变化验收**：第五阶段另用外部检查器核对实际头文件版本。通用运行器不会把任意新规范文本自动转成验收逻辑；业务方还须提供对应标准。

项目记录中的 `runtime_verified: false` 表示创建版本时未运行。后续通过的报告只证明报告列出的具体任务；它不会自动把整个员工认证为生产可用。

T4 另提供报告完整性/内部一致性的严格格式检查，需显式来源绑定及业务回执。普通 CLI 目前自动获得执行遥测，
不会自动把既有报告升级成严格格式。T2/T5 的工具/HTTP 错误字段用于定位失败，不替代上述业务验收。
使用入口与文档同步范围见[T1–T5 复核](../changelog/docs-sync-t01-t05.md)。

## 仍未完成什么

- 任意需求生成、自动推断公司接口、工作流/工具变化后的通用代码合并。
- 企业完整构建配置、持续业务接入、正式发布，以及真实采纳率和接入工时测量。
- 同条件竞品/脚本基线比较；当前没有证明普遍优于其他方案或节约费用。
- DAG 波内并发、跨进程挂起续跑；已有结构和账本不等于这两项能力。
- T6 的 MCP Connector 内部并发；同一连接仍串行，不能用 ReAct 在途峰值证明远端并发。
- 所有推理模式的真实模型验证；当前主要真实运行路径使用 ReAct。

`adapters/`、`employee_factory/` 和参考 CLI 依赖仓库检出及可选依赖，不随核心 wheel 独立交付。源码版本会被记录，但工具不自动下载或锁定 SDK 版本。

## 下一步与阅读入口

优先接入实际项目的构建配置/命令和业务验收材料，然后测量接入与维护收益。见 [Roadmap v3](README.md)、[Backlog](BACKLOG.md)、[变更概览](../changelog/README.md)。

领导阅读：[两分钟案例](../docs/LEADERS_USE_CASE.md)。技术评委阅读：[节点、知识与验证](../docs/TECHNICAL_USE_CASE.md)。
