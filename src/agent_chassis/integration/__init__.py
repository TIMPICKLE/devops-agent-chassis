"""
② 接入层 —— 可插拔连接器与工具名容错解析。

底盘对接入层的核心主张：**确定性阶段不能被迫经过模型**。

大多数 Agent 框架把外部系统集成做成"给 Agent 用的工具"，调用要经过
LLM 的 tool loop。但拉任务、建 PR 这些阶段属于确定性编排，一旦过模型，
确定性与不确定性的分离当场就塌了。所以连接器是独立可直接调用的，
Agent 想用时再由编排器包装成工具暴露给它。

内置三种 scheme，加新的只需注册一个类：
  mcp.stdio   本地进程型 MCP server
  mcp.http    Streamable HTTP 型 MCP server
  rest        普通 REST 接口
  mock        演示与测试用的内存实现
"""
from __future__ import annotations

import json
import time
from typing import Any, Callable, Dict, Iterable, List, Optional, Sequence

from ..contracts import Connector, Registry, ToolCall

connector_registry = Registry("connector")


class ConnectorManager:
    """按名字持有多个连接器，统一调用入口与调用记账。

    这里刻意不做连接池、重试、熔断这些东西。底盘要的是可替换，
    不是大而全 —— 真需要这些能力时，写一个包装用的 Connector 即可。
    """

    def __init__(self) -> None:
        self._connectors: Dict[str, Connector] = {}
        self.calls: List[ToolCall] = []
        self._on_call: List[Callable[[ToolCall], None]] = []

    # ── 装配 ────────────────────────────────────────────
    def mount(self, name: str, scheme: str, **config: Any) -> Connector:
        """按 scheme 构造并挂载一个连接器。"""
        conn = connector_registry.build(scheme, name=name, **config)
        self._connectors[name] = conn
        return conn

    def attach(self, conn: Connector) -> Connector:
        self._connectors[conn.name] = conn
        return conn

    def subscribe(self, hook: Callable[[ToolCall], None]) -> None:
        self._on_call.append(hook)

    def names(self) -> List[str]:
        return sorted(self._connectors)

    def get(self, name: str) -> Connector:
        if name not in self._connectors:
            raise KeyError(
                f"未挂载的连接器: {name!r}。已挂载: {self.names()}"
            )
        return self._connectors[name]

    # ── 调用 ────────────────────────────────────────────
    def call(
        self,
        connector: str,
        preferred: Sequence[str],
        args: Optional[Dict[str, Any]] = None,
        keywords: Sequence[str] = (),
    ) -> Any:
        """解析工具名后调用。

        `preferred` 传候选名列表而不是单一名字，因为 MCP 生态还在演进，
        server 升级会改工具名。全部失配时退化为关键词匹配，
        再失配则抛出带完整可用工具清单的异常，便于现场诊断。
        """
        conn = self.get(connector)
        started = time.time()
        tool = ""
        try:
            tool = conn.resolve(preferred, keywords)
            result = conn.call(tool, args or {})
            rec = ToolCall(
                name=f"{connector}.{tool}",
                args=dict(args or {}),
                result=result,
                ok=True,
                elapsed_ms=int((time.time() - started) * 1000),
            )
        except Exception as exc:
            rec = ToolCall(
                name=f"{connector}.{tool or preferred[0] if preferred else '?'}",
                args=dict(args or {}),
                ok=False,
                error=f"{type(exc).__name__}: {exc}",
                elapsed_ms=int((time.time() - started) * 1000),
            )
            self._record(rec)
            raise
        self._record(rec)
        return result

    def _record(self, rec: ToolCall) -> None:
        self.calls.append(rec)
        for hook in self._on_call:
            hook(rec)

    def close(self) -> None:
        for conn in self._connectors.values():
            try:
                conn.close()
            except Exception:
                pass

    # ── 自检 ────────────────────────────────────────────
    def inventory(self) -> Dict[str, List[str]]:
        """列出每个连接器暴露的工具，用于装配后自检。"""
        out: Dict[str, List[str]] = {}
        for name, conn in self._connectors.items():
            try:
                out[name] = sorted(conn.tools())
            except Exception as exc:
                out[name] = [f"<discovery failed: {exc}>"]
        return out


# ═══════════════════════════════════════════════════════════
#  内置实现
# ═══════════════════════════════════════════════════════════

@connector_registry.register("mock")
class MockConnector(Connector):
    """内存连接器。演示、测试与离线路演用。

    handlers 形如 {"issues.search": callable}，callable 接收 args 返回结果。
    """

    scheme = "mock"

    def __init__(
        self,
        name: str,
        handlers: Optional[Dict[str, Callable[[Dict[str, Any]], Any]]] = None,
        **config: Any,
    ) -> None:
        super().__init__(name, **config)
        self.handlers = dict(handlers or {})

    def _discover(self) -> Dict[str, Dict[str, Any]]:
        return {
            k: {"name": k, "description": getattr(v, "__doc__", "") or ""}
            for k, v in self.handlers.items()
        }

    def _invoke(self, tool: str, args: Dict[str, Any]) -> Any:
        return self.handlers[tool](args)


@connector_registry.register("mcp.stdio")
class McpStdioConnector(Connector):
    """本地进程型 MCP server。

    真实实现走 mcp SDK 的 stdio_client。底盘只依赖 Connector 契约，
    所以这里在缺少 SDK 时会明确报错而不是静默降级 —— 装配期就该失败，
    而不是等到凌晨三点跑起来才失败。
    """

    scheme = "mcp.stdio"

    def __init__(
        self,
        name: str,
        command: str = "",
        args: Optional[List[str]] = None,
        env: Optional[Dict[str, str]] = None,
        **config: Any,
    ) -> None:
        super().__init__(name, **config)
        self.command = command
        self.args = list(args or [])
        self.env = dict(env or {})
        self._session = None

    def _require_sdk(self) -> Any:
        try:
            import mcp  # noqa: F401
        except ImportError as exc:  # pragma: no cover - 环境相关
            raise RuntimeError(
                f"连接器 {self.name} 需要 mcp SDK：pip install mcp"
            ) from exc
        return mcp

    def _discover(self) -> Dict[str, Dict[str, Any]]:  # pragma: no cover - 需真实 server
        self._require_sdk()
        raise NotImplementedError(
            "示例仓库不内置真实 MCP 会话。生产实现见 SonarqubeAutoFlow_MAF/mcp_manager.py，"
            "把 stdio_client + ClientSession 接进 _discover / _invoke 即可。"
        )

    def _invoke(self, tool: str, args: Dict[str, Any]) -> Any:  # pragma: no cover
        self._require_sdk()
        raise NotImplementedError


@connector_registry.register("mcp.http")
class McpHttpConnector(Connector):
    """Streamable HTTP 型 MCP server。"""

    scheme = "mcp.http"

    def __init__(self, name: str, url: str = "", headers: Optional[Dict[str, str]] = None, **config: Any) -> None:
        super().__init__(name, **config)
        self.url = url
        self.headers = dict(headers or {})

    def _discover(self) -> Dict[str, Dict[str, Any]]:  # pragma: no cover
        raise NotImplementedError(
            "接上你的 MCP HTTP endpoint：POST {url}/tools/list 后填充本方法。"
        )

    def _invoke(self, tool: str, args: Dict[str, Any]) -> Any:  # pragma: no cover
        raise NotImplementedError


@connector_registry.register("rest")
class RestConnector(Connector):
    """普通 REST 接口。用 urllib 实现，不引第三方依赖。

    routes 形如 {"dashboard.overview": ("GET", "/api/v1/dashboard/overview")}
    """

    scheme = "rest"

    def __init__(
        self,
        name: str,
        base_url: str = "",
        routes: Optional[Dict[str, Any]] = None,
        headers: Optional[Dict[str, str]] = None,
        timeout: int = 15,
        **config: Any,
    ) -> None:
        super().__init__(name, **config)
        self.base_url = base_url.rstrip("/")
        self.routes = dict(routes or {})
        self.headers = dict(headers or {})
        self.timeout = timeout

    def _discover(self) -> Dict[str, Dict[str, Any]]:
        return {
            k: {"name": k, "method": v[0], "path": v[1]}
            for k, v in self.routes.items()
        }

    def _invoke(self, tool: str, args: Dict[str, Any]) -> Any:
        import urllib.error
        import urllib.parse
        import urllib.request

        method, path = self.routes[tool]
        url = f"{self.base_url}{path}"
        data = None
        if method.upper() == "GET" and args:
            url = f"{url}?{urllib.parse.urlencode(args)}"
        elif args:
            data = json.dumps(args).encode("utf-8")

        req = urllib.request.Request(url, data=data, method=method.upper())
        req.add_header("Accept", "application/json")
        if data is not None:
            req.add_header("Content-Type", "application/json")
        for k, v in self.headers.items():
            req.add_header(k, v)

        with urllib.request.urlopen(req, timeout=self.timeout) as resp:
            body = resp.read().decode("utf-8")
        try:
            return json.loads(body)
        except json.JSONDecodeError:
            return {"text": body}


__all__ = [
    "ConnectorManager",
    "MockConnector",
    "McpStdioConnector",
    "McpHttpConnector",
    "RestConnector",
    "connector_registry",
]
