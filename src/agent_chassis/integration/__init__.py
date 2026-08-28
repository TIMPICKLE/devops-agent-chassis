"""
② 接入层 —— 可插拔连接器与工具名容错解析。

底盘对接入层的核心主张：**确定性阶段不能被迫经过模型**。

大多数 Agent 框架把外部系统集成做成"给 Agent 用的工具"，调用要经过
LLM 的 tool loop。但拉任务、建 PR 这些阶段属于确定性编排，一旦过模型，
确定性与不确定性的分离当场就塌了。所以连接器是独立可直接调用的，
Agent 想用时再由编排器包装成工具暴露给它。

内置四种 scheme，加新的只需注册一个类：
  mcp.stdio   本地进程型 MCP server
  mcp.http    Streamable HTTP 型 MCP server
  rest        普通 REST 接口
  mock        演示与测试用的内存实现
"""
from __future__ import annotations

import asyncio
import json
import threading
import time
from concurrent.futures import Future
from contextlib import asynccontextmanager
from importlib.metadata import PackageNotFoundError, version as package_version
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


def _require_mcp_v2(connector_name: str) -> None:
    """Require the stable v2 MCP SDK without making it a chassis core dependency."""
    try:
        import mcp  # noqa: F401
        installed = package_version("mcp")
    except (ImportError, PackageNotFoundError) as exc:  # pragma: no cover - 环境相关
        raise RuntimeError(
            f"连接器 {connector_name} 需要 MCP Python SDK v2："
            'pip install "agent-chassis[mcp]"'
        ) from exc

    try:
        parts = installed.split(".")
        major = int(parts[0])
        minor = int(parts[1])
    except (ValueError, IndexError):  # pragma: no cover - 非标准版本字符串
        major, minor = 0, 0
    if major != 2 or minor < 1:
        raise RuntimeError(
            f"连接器 {connector_name} 需要 mcp>=2.1,<3，当前版本是 {installed}。"
            '请执行 pip install -U "agent-chassis[mcp]"。'
        )


class _McpSyncRuntime:
    """在同步 Connector API 后面维持一个真实的异步 MCP Client 生命周期。

    Client context 的 enter / request / exit 全部在同一个 worker task 中执行。
    这避免 AnyIO transport 的 task-local cancel scope 被跨 task 进入/退出，
    同时不把整个 Chassis API 传染成 async。
    """

    _CLOSE = object()

    def __init__(
        self,
        context_factory: Callable[[], Any],
        *,
        timeout: float,
        label: str,
    ) -> None:
        self._context_factory = context_factory
        self._timeout = float(timeout)
        self._label = label
        self._ready = threading.Event()
        self._state_lock = threading.Lock()
        self._thread: Optional[threading.Thread] = None
        self._loop: Optional[asyncio.AbstractEventLoop] = None
        self._queue: Optional[asyncio.Queue[Any]] = None
        self._startup_error: Optional[BaseException] = None
        self._fatal_error: Optional[BaseException] = None
        self._closed = False

    def start(self) -> None:
        with self._state_lock:
            if self._closed:
                raise RuntimeError(f"MCP runtime {self._label} 已关闭")
            if self._thread is None:
                self._thread = threading.Thread(
                    target=self._thread_main,
                    name=f"agent-chassis-mcp-{self._label}",
                    daemon=True,
                )
                self._thread.start()
            thread = self._thread

        if not self._ready.wait(self._timeout):
            raise TimeoutError(
                f"MCP 连接器 {self._label} 在 {self._timeout:g}s 内未完成连接"
            )
        if self._startup_error is not None:
            raise RuntimeError(
                f"MCP 连接器 {self._label} 建立连接失败: {self._startup_error}"
            ) from self._startup_error
        if thread is None or not thread.is_alive():
            error = self._fatal_error or RuntimeError("MCP worker 启动后意外退出")
            raise RuntimeError(
                f"MCP 连接器 {self._label} 建立连接失败: {error}"
            ) from error

    def call(self, method: str, *args: Any, **kwargs: Any) -> Any:
        self.start()
        with self._state_lock:
            if self._closed:
                raise RuntimeError(f"MCP runtime {self._label} 已关闭")
            thread = self._thread
            loop = self._loop
            queue = self._queue
            fatal = self._fatal_error

        if fatal is not None:
            raise RuntimeError(
                f"MCP 连接器 {self._label} worker 已停止: {fatal}"
            ) from fatal
        if thread is None or not thread.is_alive() or loop is None or queue is None:
            raise RuntimeError(f"MCP 连接器 {self._label} worker 未运行")
        if threading.current_thread() is thread:
            raise RuntimeError("MCP Connector 不能从自己的 worker thread 同步递归调用")

        future: Future[Any] = Future()
        loop.call_soon_threadsafe(
            queue.put_nowait,
            (method, args, kwargs, future),
        )
        try:
            # MCP Client 自己有 read timeout；这里多留 5 秒给调度/清理。
            return future.result(timeout=self._timeout + 5.0)
        except TimeoutError as exc:
            # concurrent.futures.TimeoutError 在现代 Python 中就是内置 TimeoutError；
            # 若底层 MCP 调用自己抛 TimeoutError，future 已完成，必须保留原异常。
            if future.done():
                raise
            raise TimeoutError(
                f"MCP 连接器 {self._label} 请求 {method} 超过 {self._timeout:g}s"
            ) from exc

    def close(self) -> None:
        with self._state_lock:
            if self._closed:
                return
            self._closed = True
            thread = self._thread
            loop = self._loop
            queue = self._queue

        if thread is None:
            return
        if thread.is_alive() and loop is not None and queue is not None:
            future: Future[Any] = Future()
            try:
                loop.call_soon_threadsafe(
                    queue.put_nowait,
                    (self._CLOSE, (), {}, future),
                )
                future.result(timeout=self._timeout + 5.0)
            except Exception:
                # close() 是清理路径，不用清理异常覆盖原业务异常。
                pass
        thread.join(timeout=self._timeout + 5.0)

    def _thread_main(self) -> None:
        try:
            asyncio.run(self._serve())
        except BaseException as exc:  # pragma: no cover - transport 崩溃路径
            self._fatal_error = exc
            if not self._ready.is_set():
                self._startup_error = exc
                self._ready.set()

    async def _serve(self) -> None:
        self._loop = asyncio.get_running_loop()
        self._queue = asyncio.Queue()
        close_future: Optional[Future[Any]] = None
        try:
            async with self._context_factory() as client:
                self._ready.set()
                while True:
                    method, args, kwargs, future = await self._queue.get()
                    if method is self._CLOSE:
                        close_future = future
                        break
                    try:
                        result = await getattr(client, method)(*args, **kwargs)
                    except BaseException as exc:
                        if not future.done():
                            future.set_exception(exc)
                    else:
                        if not future.done():
                            future.set_result(result)
        except BaseException as exc:
            if not self._ready.is_set():
                self._startup_error = exc
                self._ready.set()
            if close_future is not None and not close_future.done():
                close_future.set_exception(exc)
            raise
        else:
            if close_future is not None and not close_future.done():
                close_future.set_result(None)


def _tool_description(tool: Any) -> Dict[str, Any]:
    out: Dict[str, Any] = {
        "name": tool.name,
        "description": getattr(tool, "description", None) or "",
        "inputSchema": getattr(tool, "input_schema", None) or {},
    }
    title = getattr(tool, "title", None)
    if title:
        out["title"] = title
    output_schema = getattr(tool, "output_schema", None)
    if output_schema is not None:
        out["outputSchema"] = output_schema
    return out


def _discover_mcp_tools(runtime: _McpSyncRuntime) -> Dict[str, Dict[str, Any]]:
    """Fetch all tool pages so Connector.resolve sees the complete server inventory."""
    discovered: Dict[str, Dict[str, Any]] = {}
    cursor: Optional[str] = None
    while True:
        if cursor is None:
            response = runtime.call("list_tools")
        else:
            response = runtime.call("list_tools", cursor=cursor)
        for tool in response.tools:
            discovered[tool.name] = _tool_description(tool)
        cursor = getattr(response, "next_cursor", None)
        if not cursor:
            return discovered


def _model_dump_json(value: Any) -> Any:
    if hasattr(value, "model_dump"):
        return value.model_dump(mode="json", by_alias=True, exclude_none=True)
    return value


def _mcp_error_text(payload: Any) -> str:
    if not isinstance(payload, dict):
        return str(payload)
    texts: List[str] = []
    for block in payload.get("content", []) or []:
        if isinstance(block, dict) and block.get("type") == "text" and block.get("text"):
            texts.append(str(block["text"]))
    return "; ".join(texts) or json.dumps(payload, ensure_ascii=False)


def _invoke_mcp_tool(
    runtime: _McpSyncRuntime,
    tool: str,
    args: Dict[str, Any],
) -> Any:
    result = runtime.call("call_tool", tool, args)
    payload = _model_dump_json(result)
    is_error = False
    if isinstance(payload, dict):
        is_error = bool(payload.get("isError", payload.get("is_error", False)))
    else:
        is_error = bool(getattr(result, "is_error", False))
    if is_error:
        raise RuntimeError(f"MCP tool {tool!r} 返回失败: {_mcp_error_text(payload)}")
    return payload


@connector_registry.register("mcp.stdio")
class McpStdioConnector(Connector):
    """真实本地进程型 MCP server，使用官方 MCP Python SDK v2。"""

    scheme = "mcp.stdio"

    def __init__(
        self,
        name: str,
        command: str = "",
        args: Optional[List[str]] = None,
        env: Optional[Dict[str, str]] = None,
        cwd: Optional[str] = None,
        timeout: float = 30.0,
        **config: Any,
    ) -> None:
        super().__init__(name, **config)
        self.command = command
        self.args = list(args or [])
        self.env = dict(env or {})
        self.cwd = cwd
        self.timeout = float(timeout)
        self._runtime: Optional[_McpSyncRuntime] = None

    def _build_runtime(self) -> _McpSyncRuntime:
        _require_mcp_v2(self.name)
        if not self.command.strip():
            raise ValueError(f"mcp.stdio 连接器 {self.name} 缺少 command")

        @asynccontextmanager
        async def client_context():
            from mcp import Client, StdioServerParameters
            from mcp.client.stdio import stdio_client

            server = StdioServerParameters(
                command=self.command,
                args=self.args,
                env=self.env or None,
                cwd=self.cwd,
            )
            transport = stdio_client(server)
            async with Client(
                transport,
                read_timeout_seconds=self.timeout,
            ) as client:
                yield client

        return _McpSyncRuntime(
            client_context,
            timeout=self.timeout,
            label=self.name,
        )

    def _get_runtime(self) -> _McpSyncRuntime:
        if self._runtime is None:
            self._runtime = self._build_runtime()
        return self._runtime

    def _discover(self) -> Dict[str, Dict[str, Any]]:
        return _discover_mcp_tools(self._get_runtime())

    def _invoke(self, tool: str, args: Dict[str, Any]) -> Any:
        return _invoke_mcp_tool(self._get_runtime(), tool, args)

    def close(self) -> None:
        if self._runtime is not None:
            self._runtime.close()
            self._runtime = None


@connector_registry.register("mcp.http")
class McpHttpConnector(Connector):
    """真实 Streamable HTTP MCP server，使用官方 MCP Python SDK v2。"""

    scheme = "mcp.http"

    def __init__(
        self,
        name: str,
        url: str = "",
        headers: Optional[Dict[str, str]] = None,
        timeout: float = 30.0,
        **config: Any,
    ) -> None:
        super().__init__(name, **config)
        self.url = url
        self.headers = dict(headers or {})
        self.timeout = float(timeout)
        self._runtime: Optional[_McpSyncRuntime] = None

    def _build_runtime(self) -> _McpSyncRuntime:
        _require_mcp_v2(self.name)
        if not self.url.strip():
            raise ValueError(f"mcp.http 连接器 {self.name} 缺少 url")

        @asynccontextmanager
        async def client_context():
            from mcp import Client

            if not self.headers:
                async with Client(
                    self.url,
                    read_timeout_seconds=self.timeout,
                ) as client:
                    yield client
                return

            import httpx2
            from mcp.client.streamable_http import streamable_http_client

            async with httpx2.AsyncClient(
                headers=self.headers,
                timeout=self.timeout,
            ) as http_client:
                transport = streamable_http_client(
                    self.url,
                    http_client=http_client,
                )
                async with Client(
                    transport,
                    read_timeout_seconds=self.timeout,
                ) as client:
                    yield client

        return _McpSyncRuntime(
            client_context,
            timeout=self.timeout,
            label=self.name,
        )

    def _get_runtime(self) -> _McpSyncRuntime:
        if self._runtime is None:
            self._runtime = self._build_runtime()
        return self._runtime

    def _discover(self) -> Dict[str, Dict[str, Any]]:
        return _discover_mcp_tools(self._get_runtime())

    def _invoke(self, tool: str, args: Dict[str, Any]) -> Any:
        return _invoke_mcp_tool(self._get_runtime(), tool, args)

    def close(self) -> None:
        if self._runtime is not None:
            self._runtime.close()
            self._runtime = None


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
