# 第一阶段验证记录

日期：2026-09-05。分支：`feat/roadmap-showcase-v1`。以下结果针对已执行的代码快照，后续仅更新文档不会改变这个验证范围。

**代码快照：`a75f4b1a700c68205b8ed15e0bc793e06852d623`。**

[Actions 运行 33955194635](https://github.com/TIMPICKLE/devops-agent-chassis/actions/runs/33955194635) 已完成，结论为 **success**。

| 验证 | 实际结果 |
|---|---|
| 本地 Python 3.12 完整回归 | 118 passed |
| Actions Python 3.13 完整回归（含 MCP） | 118 passed |
| Actions Python 3.9 回归 | 116 passed, 1 skipped；可选 MCP 未安装 |
| Python 3.9 两载荷 × 三编排离线回放 | 6/6，证据校验全部通过 |
| Python 3.13 两载荷 × 三编排离线回放 | 6/6，证据校验全部通过 |
| 真实模型 · Python 质量修复 | succeeded；8 次模型请求；AST 独立验收通过 |
| 真实模型 · C++ 头文件构建修复 | succeeded；8 次模型请求；真实编译检查通过 |
| 真实模型证据合同校验 | 2 个运行通过 Schema、内容 ID、装配关联、上下文引用、用量一致性及 patch 摘要校验 |
| 基础包依赖检查 | `python -S` 禁用 site-packages 后仍可导入核心 |

真实模型任务使用工作流声明的 GLM-5.3-Flash 兼容入口，以及用户已手动更新的 Actions Secret `BIGMODEL_API_KEY`。没有从 GitHub 读取 Secret 值，也没有把它保存到源码、清单或报告。

## 提交边界

| 提交 | 内容 |
|---|---|
| `b569832` | 权威验收、单次失败收尾、上下文消费和基础运行证据 |
| `57a7ea8` | 模型适配器、双载荷参考场景、证据 Schema 与校验器 |
| `a75f4b1` | Roadmap、兼容性 / 复用矩阵 CI 与显式 live 验证入口 |

三个远端提交的源码树均与已验证的本地快照比对一致。未合并主分支。

## 可下载的运行制品

在上述 Actions 页面中查看：

- `roadmap-live-33955194635-1`：真实运行摘要、两份装配清单、两份证据和成功 patch；Artifact ID `9966159636`。
- `roadmap-contracts-py3.13-33955194635-1`：Python 3.13 六组离线回放证据。
- `roadmap-contracts-py3.9-33955194635-1`：Python 3.9 六组离线回放证据。

Artifacts 按工作流保留 14 天。日志已核实 live / offline 标记、结果、模型调用次数及校验通过；本记录不抄录未独立读取的 token 明细，也不估算费用。

## 本轮结论的边界

这证明本分支的实际模型执行路径已经连通，并且两种验收器可以复用相同装配和证据格式。它不是冻结测试集评测，不能据此声称生产成功率、竞品优势或接入工时收益。

每个 live 场景均使用了配置上限内的 8 次调用，调用效率仍有改进空间。后续应先复核逐次轨迹与终止条件，再在同模型、同输入、同预算下测量优化；不把“验收成功”自动等同于“已高效收敛”。

其余未完成事项见 [实施记录](./IMPLEMENTATION.md) 与 [Backlog](./BACKLOG.md)。
