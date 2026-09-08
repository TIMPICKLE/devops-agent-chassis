# 合并验收：基础环境示例依赖边界

日期：2026-09-08
分支：`feat/roadmap-showcase-v1`

## 问题

合并请求首次触发 `Digital Employee Acceptance` 后，Python 3.9 基础兼容任务在
`tests/test_examples_smoke.py` 失败。基础任务只安装 `.[dev]`，而 smoke test 无条件运行
`examples/07_verified_assembly.py`；该示例按 README 的既有契约需要可选 `.[llm]`
依赖中的 `jsonschema`。

这属于测试分层缺陷，不是数字员工运行失败：基础底盘承诺零第三方依赖，完整模型适配、
证据 schema 核验及其测试才安装 `.[llm]`。

## 修复

- 为 smoke test 声明示例级可选依赖：`07_verified_assembly.py` 对应 `jsonschema` / `.[llm]`。
- 基础环境缺少该 extra 时，测试给出明确原因并仅跳过这一示例。
- 安装 `.[llm]` 时仍实际运行该示例，因此不会把示例自身回归隐藏成跳过。
- 不向 `agent-chassis` 基础依赖加入第三方包，保持底盘零第三方依赖约束。

## 验证

- 无 `jsonschema` 的受控检查确认该示例会以 `optional .[llm]` 原因跳过。
- 修改文件通过 Python 语法检查和 `git diff --check`。
- 修复前的完整依赖分支验证为 404 项通过；本次没有修改示例执行路径。
- 此前暴露问题的 PR 检查为 [`Digital Employee Acceptance` #34244330791](https://github.com/TIMPICKLE/devops-agent-chassis/actions/runs/34244330791)。

修复提交对应的 PR Actions 结果待远端完成后补录。
