# T1–T5 文档同步复核 · 2026-09-08

分支：`feat/roadmap-showcase-v1`。复核起点：`13dcec5df95865cc71f1d6ae4370258f756faf48`；
T5 代码受测提交为 `15b689a`。本次只修改文档和文档内的装配示例，不改变运行时代码或接口。

## 逐项对应关系

| 任务 | 已实现的代码 | 对应的维护文档/用法 | 既有验证入口 |
|---|---|---|---|
| T1 配置一致性 | `adapters/runtime.py` 的 `react_pattern` / `validate_react_alignment`，四处参考装配调用点；LF patch 写入 | [并行说明](../docs/PARALLEL_TOOLS.md)、[技术装配示例](../docs/TECHNICAL_USE_CASE.md)、[员工生成/运行](../docs/GENERATED_EMPLOYEE_PROJECT.md)、[T1 原记录](react-assembly-alignment-2026-09-07.md) | [配置回归](../tests/test_react_alignment.py)、[补丁证据回归](../tests/test_evidence_contract.py) |
| T2 工具诊断 | `src/agent_chassis/diagnostics.py`、ToolBox/ReAct 预检、模型适配器及 schema | [错误码与失败定位](../docs/PARALLEL_TOOLS.md)、[技术报告字段](../docs/TECHNICAL_USE_CASE.md)、[T2 原记录](task-02-tool-diagnostics.md) | [诊断回归](../tests/test_tool_diagnostics.py)、[并行协议回归](../tests/test_parallel_runtime.py) |
| T3 装配指导 | `.claude/skills/assemble-digital-employee`、example 07 | [可运行配方](../examples/07_verified_assembly.py)、README 示例入口、[T3 原记录](task-03-assembly-guidance.md) | [配方行为及引用检查](../tests/test_assembly_recipe.py) |
| T4 生产格式证据 | `production.py`、ReAct 遥测、`RunContext.record_check`、EvidenceObserver、schema/核验器 | [自动遥测与严格接线区别](../docs/PRODUCTION_EVIDENCE.md)、技术评审/员工项目说明、[T4 原记录](task-04-production-evidence.md) | [生产格式回归](../tests/test_production_evidence.py)、[批次计数回归](../tests/test_parallel_react.py) |
| T5 HTTP 诊断 | `adapters/http_diagnostics.py`、`ModelError.http_error`、schema/核验器 | [HTTP 字段与边界](../docs/HTTP_DIAGNOSTICS.md)、Skill 排错配方、CLI 报告入口、[T5 原记录](task-05-http-diagnostics.md) | [HTTP 合成回归](../tests/test_http_diagnostics.py) |

T1 的 Windows LF 修复已在 T02–T04 中保留，并改用兼容 Python 3.9 的 `Path.open(..., newline="\n")`；
原始实施过程和后续验证分别保留在对应记录中。

## 发现的缺口与修正

- README 仍将第五阶段称为最新，缺少 T4/T5 和 example 07 入口；现已更新维护导航与当前回归口径，保留历史 live 数字。
- 技术评审页停留在第五阶段，四节点示例直接构造 ReAct，容易遗漏 T1 配置传递；现改用统一入口，补充 T1–T5 报告字段及验证边界。
- 并行说明中的 `stop_when=...` 不是可执行回调；现移除占位调用并指向完整配方。补明 4 是示例并发数、生成和运行需分别传参。
- 生成/运行、更新后重验与 policy 文档未交代新诊断和实际可并行工具；现补齐。生成/policy 的共享提交工具不能因调高并发而并行，旧归档不会自动补字段。
- T4 专项文档虽有 opt-in 说明，但普通 CLI 的接入程度及两个同名 `source_revision` 函数容易混淆；现明确自动遥测、显式绑定/回执/核验三层，补充重试回执和执行前失败边界。
- Skill 缺少 T5 排错入口，并行验收措辞可能误要求单调用也做批次；现以窄范围参考文档补齐，并保留合法单调用。
- Roadmap/Backlog 缺少 T 系列任务状态，当前合同表还仅列第五阶段 195 项；现纳入 T1–T5 和受测代码索引，T6 标记未实施。
- 领导版补充维护对报告读取的实际影响，不把合同回归包装成新的企业 live 结果。
- RESEARCH 的首批接口说明补上历史时点和当前入口；带日期的阶段记录、审计基线和旧版规划保留历史结论。

## 验证与范围

- **16 份文档**的 **201 个本地文件链接**解析通过；不将外部历史链接的文件存在性误称为当前产物可下载。
- README 与四份技术使用说明共 **17 段 Python 示例**通过语法检查；业务变量占位的示意不冒充可独立运行程序。
- 从技术评审 Markdown 提取 `make_flow` 后实际运行：四阶段顺序、统一配置传递、两个工具动作和事实判据通过；阶段名无重复记账。使用合成读取回调，未运行公司编译接口。
- `examples/07_verified_assembly.py` 的单调用/并行与严格证据核验通过。
- 既有 T1–T5 定向测试 **84 passed**：`test_react_alignment`、`test_tool_diagnostics`、`test_assembly_recipe`、`test_production_evidence`、`test_http_diagnostics`。
- 装配 Skill 的 `quick_validate.py` 通过；`git diff --check` 通过。

T5 受测代码已有 **350 passed** 和三条成功 CI，见[原验证链接](task-05-http-diagnostics.md)。
本次不修改测试以匹配文案、不重写企业脚本、不自动开启 T4 严格模式，也不实施 T6。
