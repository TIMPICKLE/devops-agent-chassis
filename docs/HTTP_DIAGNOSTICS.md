# 模型 HTTP 错误诊断（T05）

OpenAI / Anthropic 参考适配器共用 `post_json()`。HTTP 错误仍抛出 `ModelError`，
保留 `Model HTTP status 400` 等原始前缀，并附已识别的服务端代码或状态分类，例如：

```text
Model HTTP status 400 (unsupported_parameter)
Model HTTP status 429 (insufficient_quota)
Model HTTP status 503 (server_error)
```

括号中的服务端代码只在响应明确提供且命中固定白名单时显示；否则回退到根据状态码推导的类别。
不根据任意错误文本猜测根因。单凭 400 不能认定为工具调用不兼容，单凭 429 也不能区分配额耗尽与瞬时限流。

## 如何读取

```python
from adapters.runtime import ModelError

try:
    action = decider(task, ctx, toolbox)
except ModelError as error:
    if error.http_error is not None:
        print(error.http_error)  # 参考 post_json 产生的安全结构化字段
    raise
```

运行时同时写入 `ctx.model_calls[i].http_error`，由 EvidenceObserver 导出到报告中的对应模型调用。
它与 T02 的工具预检 `diagnostic` 分开记录。

| 字段 | 含义与限制 |
|---|---|
| `status` | 实际 HTTP 状态码 |
| `category` | 400/422：invalid_request；401：authentication；403：permission；404：not_found；408：request_timeout；413：request_too_large；429：rate_limit；5xx：server_error；其余：http_error |
| `provider_code` / `provider_type` | 可选；仅从 JSON `error.code/type` 提取固定白名单符号。未知符号、数字代码和任意文本均不复制 |
| `request_id_sha256` | 可选；`x-request-id`（优先）或 `request-id` 的 SHA-256；仅接受 1–256 字符，原值不导出 |
| `retry_after_seconds` | 可选；Retry-After 为 1–9 位十进制数字时记录秒数。日期形式、负数、超长及任意文本省略 |

白名单见 [http_diagnostics.py](../adapters/http_diagnostics.py)；其与 schema 的一致性受测试约束。
请求 ID 指纹用于比对受控服务端日志中相同原值的散列，不能直接当作厂商支持系统的原始 request ID。
指纹不是凭据加密存储，也不是报告真实性证明。

## 行为边界

- 错误正文最多读取 16 KiB + 1 字节，超出 16 KiB 整体跳过正文解析；仍保留状态及安全头字段。
- 不导出响应正文、`message`、参数名、请求 URL、Authorization、API key、prompt 或原始请求 ID。
- HTML、乱码、畸形 JSON、过深嵌套、读超时/中断不覆盖原 HTTP 状态；关闭错误响应流。
- 每次失败请求仍占模型请求预算。无可信 usage 时维持 `null` 和 `complete=false`，不把未知成本记为零。
- 不增加自动重试、sleep、参数自动删减或接口降级。Retry-After 只作诊断；任务层已显式装配的重试策略继续独立生效。
- 原有禁止重定向、HTTPS 配置约束、成功响应解析及网络异常行为保留。HTTP 200 内的业务错误、DNS/TLS 详细分类未纳入本次。
- v1 schema 新增可选字段，旧报告继续可读；旧严格读取端需更新。核验器拒绝同一模型调用同时标记成功和 HTTP 失败。

自定义 transport 若自行构造 `ModelError(http_error=...)`，须遵守相同的安全字段合同；本功能不替任意自定义异常内容自动脱敏。
本次测试使用合成响应，不需要真实 key 或企业服务器。
