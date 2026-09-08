# T05 · 安全 HTTP 错误诊断

分支：`feat/roadmap-showcase-v1`。基于已完成 T01–T04 的 `9a1dd77cfa3dec6ad73ae6f5ca0f1e5ed6a37ad1`。

## 问题与实施

原 `post_json()` 对 HTTP 失败仅记录状态码，无法在服务端有明确错误代码时区分参数错误、认证、限流/配额等原因。
直接拼接响应正文会把网关回显的输入或凭据带入日志，因此本次采用有界解析和固定符号白名单。

- 新增适配层 `http_diagnostics.py`：HTTP 分类、JSON error.code/type 白名单、请求 ID 指纹及数字 Retry-After。
- `ModelError.http_error` 保持旧异常类型与文本前缀；两种协议共享实现，将失败元数据写入模型调用证据。
- 错误正文读取有大小上限，异常/HTML/截断响应降级保留状态，关闭错误响应流。
- v1 schema 增加可选 HTTP 字段；独立核验器拒绝成功调用携带 HTTP 失败标记。
- 增加合成响应测试及[使用与边界说明](../docs/HTTP_DIAGNOSTICS.md)。

## 验证

- 新增 **30 项**合成测试，覆盖状态分类、服务端符号白名单、敏感信息不回显、读取上限/中断、两种协议导出、usage 未知、预算记账、禁止重定向、无自动重试以及证据 schema/语义检查。
- 首轮定向验证发现新增测试夹具误用空工具白名单，已按现有适配器合同注册测试工具；产品合同保持不变。
- 最终完整回归 **350 passed**（Python 3.12，包含可选 MCP 和编译器用例）。命令：`uv run --offline --no-project --with 'mcp>=2.1,<3' --with pytest --with jsonschema pytest tests/ -q`。
- `git diff --check` 通过。相较 T01–T04 的 320 项回归新增 30 项，全部通过。

受测代码：[15b689ac825ca5b9708415d654d1792b8cde7f13](https://github.com/TIMPICKLE/devops-agent-chassis/commit/15b689ac825ca5b9708415d654d1792b8cde7f13)。

| CI | 结果 |
|---|---|
| [Roadmap 合同与证据](https://github.com/TIMPICKLE/devops-agent-chassis/actions/runs/34174349224) | 通过：Python 3.9 / 3.13 合同回归、双载荷 × 三流程及离线证据核验 |
| [Employee Project](https://github.com/TIMPICKLE/devops-agent-chassis/actions/runs/34174349243) | 通过：员工项目生成与运行验证回归 |
| [Employee Update](https://github.com/TIMPICKLE/devops-agent-chassis/actions/runs/34174349285) | 通过：保留定制与行为更新回归 |

付费 live 任务按配置跳过；后续纯文档提交仅补录本表，不扩大受测代码的验证范围。以上结果不代表企业实机或真实模型验证。

## 范围

保留 T01–T04；不修改 Hermes、本机业务载荷或 T06 的 MCP 内部并发，不执行上传脚本或付费模型调用。
原始错误正文及任意供应商代码不公开；HTTP 200 内业务错误和 DNS/TLS 细分留在现有行为中。
本次仅增强诊断，不承诺自动恢复，也不把 HTTP 状态分类当成已证明的业务根因。
