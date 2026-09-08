from __future__ import annotations

import os
import socket
import subprocess
import sys
import textwrap
import time
from pathlib import Path

import pytest

pytest.importorskip("mcp")

from agent_chassis.integration import McpHttpConnector, McpStdioConnector


def _text(result):
    for block in result.get("content", []):
        if block.get("type") == "text":
            return block.get("text", "")
    return ""


def _free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def _wait_port(port: int, timeout: float = 10.0) -> None:
    deadline = time.time() + timeout
    while time.time() < deadline:
        with socket.socket() as sock:
            sock.settimeout(0.2)
            if sock.connect_ex(("127.0.0.1", port)) == 0:
                return
        time.sleep(0.05)
    raise TimeoutError(f"server on port {port} did not start")


def _write_stdio_server(path: Path) -> None:
    path.write_text(textwrap.dedent('''
        import os
        from mcp.server import MCPServer

        mcp = MCPServer("stdio-test")
        counter = 0

        @mcp.tool()
        def echo(value: str) -> str:
            return f"{os.environ.get('MCP_TEST_ENV', '')}:{value}:{os.getcwd()}"

        @mcp.tool()
        def next_count() -> str:
            global counter
            counter += 1
            return str(counter)

        @mcp.tool()
        def fail() -> str:
            raise RuntimeError("stdio boom")

        if __name__ == "__main__":
            mcp.run("stdio")
    '''), encoding="utf-8")


def _write_http_server(path: Path) -> None:
    path.write_text(textwrap.dedent('''
        import sys
        import uvicorn
        from mcp.server import MCPServer

        mcp = MCPServer("http-test")

        @mcp.tool()
        def echo(value: str) -> str:
            return f"http:{value}"

        class HeaderGate:
            def __init__(self, app):
                self.app = app

            async def __call__(self, scope, receive, send):
                if scope["type"] == "http":
                    headers = {k.lower(): v for k, v in scope.get("headers", [])}
                    if headers.get(b"x-mcp-test") != b"secret":
                        await send({
                            "type": "http.response.start",
                            "status": 401,
                            "headers": [(b"content-type", b"text/plain")],
                        })
                        await send({"type": "http.response.body", "body": b"unauthorized"})
                        return
                await self.app(scope, receive, send)

        if __name__ == "__main__":
            port = int(sys.argv[1])
            app = HeaderGate(mcp.streamable_http_app(json_response=True))
            uvicorn.run(app, host="127.0.0.1", port=port, log_level="error")
    '''), encoding="utf-8")


def test_real_stdio_server_discovery_calls_env_cwd_errors_and_reuses_session(tmp_path: Path):
    server = tmp_path / "stdio_server.py"
    _write_stdio_server(server)
    connector = McpStdioConnector(
        "stdio",
        command=sys.executable,
        args=[str(server)],
        env={"MCP_TEST_ENV": "from-env"},
        cwd=str(tmp_path),
        timeout=10,
    )
    try:
        tools = connector.tools()
        assert {"echo", "next_count", "fail"}.issubset(tools)

        first = connector.call("echo", {"value": "hello"})
        assert _text(first) == f"from-env:hello:{tmp_path}"

        assert _text(connector.call("next_count")) == "1"
        assert _text(connector.call("next_count")) == "2", "stdio session/process must be reused"

        with pytest.raises(RuntimeError, match="Error executing tool fail"):
            connector.call("fail")
    finally:
        connector.close()


def test_real_streamable_http_server_discovery_call_and_headers(tmp_path: Path, monkeypatch):
    # 此测试只连接自己创建的 loopback server；不要继承宿主出网代理。
    # monkeypatch 在测试结束后恢复，不改变真实 Connector 的代理政策。
    for name in ("HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY", "http_proxy", "https_proxy", "all_proxy"):
        monkeypatch.delenv(name, raising=False)
    server = tmp_path / "http_server.py"
    _write_http_server(server)
    port = _free_port()
    process = subprocess.Popen(
        [sys.executable, str(server), str(port)],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        env=os.environ.copy(),
    )
    connector = McpHttpConnector(
        "http",
        url=f"http://127.0.0.1:{port}/mcp",
        headers={"X-MCP-Test": "secret"},
        timeout=10,
    )
    try:
        _wait_port(port)
        assert "echo" in connector.tools()
        assert _text(connector.call("echo", {"value": "hello"})) == "http:hello"
    finally:
        connector.close()
        process.terminate()
        try:
            process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait(timeout=5)
