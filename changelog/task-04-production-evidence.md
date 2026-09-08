# T04 · 生产格式证据与实际并发记录

## 实现

- 可选生产格式检查：完整 Git commit、dirty 状态与变更指纹、装配 ID、实际模式、声明执行限制和必需检查。
- `EvidenceObserver.bind_manifest(..., production=True)` 提供运行前元数据检查；`source_revision()` 不导出差异内容、文件名或凭据。
- ReAct 记录每次执行最终配置及工具声明；记录批次 requested / started / succeeded / failed / peak_in_flight，区分配置并行与实际在途并发。
- `ctx.record_check()` 记录 passed / failed / not_run 和证据引用；保留任务重试编号，防止旧尝试通过记录为本次验收背书。
- 公开 schema 和核验器交叉检查批次、调用 ID、计数及配置；新增不依赖 patch 业务的 `verify_production_evidence.py`。
- 基础 v1 报告保持兼容；严格检查显式开启，不使旧 demo 的未知版本标签突然失败。

## 边界

首版执行遥测覆盖 ReAct；独立验收仍由载荷提供。生产格式检查不重跑任务、不读取远端证据、不认证模型响应真伪，
不能仅凭标签或哈希声称生产可用。工作区指纹不是原子快照，也不涵盖 ignored 文件、全部子模块内容或外部依赖。
峰值是底盘工作函数侧计数，不证明同一 MCP Connector 或远端服务内部并行；T06 不在本次范围。

## 验证

测试覆盖实际重叠/默认单调用、部分提交失败、计数与 trace 矛盾、缺失/不一致元数据、当前尝试回执、未执行检查、
旧报告兼容、通用 CLI、Git 根目录/子目录和 tracked/untracked 变更指纹。完整测试和 CI 结果见本批总记录。
