from __future__ import annotations

import asyncio
import threading
from contextlib import asynccontextmanager
from types import SimpleNamespace

import pytest

from agent_chassis.integration import _McpSyncRuntime, _discover_mcp_tools, _invoke_mcp_tool


class _FakeModel:
    def __init__(self, payload):
        self.payload = payload

    def model_dump(self, **kwargs):
        return self.payload


class _FakeClient:
    def __init__(self):
        self.owner_task = None

    async def ping(self, value):
        assert asyncio.current_task() is self.owner_task
        return value + 1

    async def list_tools(self, cursor=None):
        assert asyncio.current_task() is self.owner_task
        if cursor is None:
            return SimpleNamespace(
                tools=[SimpleNamespace(
                    name="alpha", description="first", input_schema={"type": "object"},
                    title=None, output_schema=None,
                )],
                next_cursor="page-2",
            )
        return SimpleNamespace(
            tools=[SimpleNamespace(
                name="beta", description="second", input_schema={},
                title="Beta", output_schema={"type": "string"},
            )],
            next_cursor=None,
        )

    async def call_tool(self, name, args):
        assert asyncio.current_task() is self.owner_task
        if name == "fail":
            return _FakeModel({
                "isError": True,
                "content": [{"type": "text", "text": "expected failure"}],
            })
        return _FakeModel({
            "isError": False,
            "structuredContent": {"value": args["value"]},
        })


@asynccontextmanager
async def _factory():
    client = _FakeClient()
    client.owner_task = asyncio.current_task()
    yield client


def test_sync_runtime_keeps_async_lifecycle_on_one_worker_task():
    runtime = _McpSyncRuntime(_factory, timeout=2, label="fake")
    assert runtime.call("ping", 4) == 5
    assert runtime.call("ping", 10) == 11
    runtime.close()


def test_sync_runtime_serializes_concurrent_sync_callers():
    runtime = _McpSyncRuntime(_factory, timeout=2, label="fake")
    results = []
    threads = [
        threading.Thread(target=lambda value=i: results.append(runtime.call("ping", value)))
        for i in range(6)
    ]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    runtime.close()
    assert sorted(results) == [1, 2, 3, 4, 5, 6]


def test_mcp_helpers_page_discovery_and_surface_tool_errors():
    runtime = _McpSyncRuntime(_factory, timeout=2, label="fake")
    tools = _discover_mcp_tools(runtime)
    assert set(tools) == {"alpha", "beta"}
    assert tools["beta"]["title"] == "Beta"
    assert tools["beta"]["outputSchema"] == {"type": "string"}
    assert _invoke_mcp_tool(runtime, "ok", {"value": 7})["structuredContent"] == {"value": 7}
    with pytest.raises(RuntimeError, match="expected failure"):
        _invoke_mcp_tool(runtime, "fail", {})
    runtime.close()
