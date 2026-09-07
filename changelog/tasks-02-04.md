# T02–T04 · 工程底盘维护实施记录

分支：`feat/roadmap-showcase-v1`。起点：用户完成 T01 的 `f6fadb84991384b4e95e54fc6351a15cd0035838`。
先同步并核对分支，再执行 T02、T03、T04；不覆盖 T01 的统一装配入口和参数一致性检查。

## 逐项完成

| 任务 | 修改与验收 | 详细记录 |
|---|---|---|
| T02 · 工具诊断 | 零依赖分类异常；适配器保留 ModelError；安全字段路径/动作序号；整批拒绝和 usage 不变 | [T02](task-02-tool-diagnostics.md) |
| T03 · 装配指导 | 更新 Skill 实际能力与边界；按需 reference；可执行的单调用/并行、客观验收和证据接线配方 | [T03](task-03-assembly-guidance.md) |
| T04 · 生产格式证据 | 完整源码与 dirty 指纹；装配绑定；实际限制/并发计数；当前尝试验收回执；独立于 patch 业务的显式核验入口 | [T04](task-04-production-evidence.md) |

T01 附带的 Windows 换行修复保留：将唯一一处 `Path.write_text(newline=...)` 改为
`Path.open("w", newline="\n")` 后写入，保持 LF 字节不变，同时兼容仓库 CI 保留的 Python 3.9。
未改动 T01 其余接线、配置检查及测试，也未改写其历史验证结论。

## 本地验证

- 同步后原始基线：**278 passed**。
- T02 定向回归：**130 passed**。
- T03/T04 及并行/诊断联合测试：首次 **55 passed**，随后增加 4 项尝试归属测试。
- 最终完整回归命令：`uv run --offline --no-project --with 'mcp>=2.1,<3' --with pytest --with jsonschema pytest tests/ -q`。
- 完整回归 **320 passed**（Python 3.12，包含可选 MCP 测试和编译器依赖测试）；最终提交前再次运行确认。
- `examples/04_swap_payload.py`、`examples/06_parallel_tools.py`、`examples/07_verified_assembly.py` 运行通过。
- example 07 的默认单调用观察为 false；两线程同步屏障确认实际并发为 true；二者均经独立事实验收及生产格式核验。
- 反例覆盖整批零执行、敏感键值不回显、统计矛盾、缺失/不一致元数据、重试后的 stale / not_run 回执和旧报告兼容。
- 装配 Skill 的 `quick_validate.py` 与本地引用链接测试通过；`git diff --check` 通过。

## 范围与限制

未执行上传的实机脚本、未复制企业源码或凭据、未修改 Hermes/本机环境、未调用付费模型。
不执行 T05（HTTP 错误诊断）或 T06（MCP Connector 内部并发）。
新增遥测仅覆盖 ReAct；生产格式是完整性/内部一致性检查，不是可信签名、业务重放、真实模型认证或上线批准。
旧报告继续通过基础 v1 核验；生产严格模式需显式接线并由载荷提供真实检查回执。

## 提交与 CI

代码提交后在本节补录对应提交与 CI 链接；本地通过不替代远端 CI 或企业 live 验证。
