"""
演示 2 —— 接入层可插拔：加一个外部系统只需注册一个类。

三件事：
  1. 内置 scheme 一览（mock / mcp.stdio / mcp.http / rest）
  2. 现场注册一个新 scheme，挂载后立刻可用
  3. 工具名容错解析：上游改名不会打断调用

    python examples/02_plug_connector.py
"""
from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from agent_chassis.contracts import Connector
from agent_chassis.integration import ConnectorManager, connector_registry

BAR = "─" * 74


def section(title: str) -> None:
    print(f"\n{BAR}\n▌ {title}\n{BAR}")


# ═══════════════════════════════════════════════════════════
#  1. 内置 scheme
# ═══════════════════════════════════════════════════════════
section("1. 底盘内置的连接器 scheme")
for name in connector_registry.names():
    print(f"  · {name}")


# ═══════════════════════════════════════════════════════════
#  2. 现场加一个新 scheme
# ═══════════════════════════════════════════════════════════
section("2. 新增一个 scheme：注册一个类就够了")


@connector_registry.register("jira")
class JiraConnector(Connector):
    """工单系统连接器。真实实现里换成 HTTP 调用即可。"""

    scheme = "jira"

    def __init__(self, name: str, project: str = "", **cfg):
        super().__init__(name, **cfg)
        self.project = project
        self._store = {"DEV-1": {"status": "open", "title": "构建偶发失败"}}

    def _discover(self):
        return {
            "issue/search": {"name": "issue/search", "description": "按 JQL 查工单"},
            "issue/transition": {"name": "issue/transition", "description": "流转工单状态"},
        }

    def _invoke(self, tool, args):
        if tool == "issue/search":
            return {"issues": [{"key": k, **v} for k, v in self._store.items()]}
        if tool == "issue/transition":
            self._store[args["key"]]["status"] = args["to"]
            return {"ok": True, "key": args["key"], "status": args["to"]}
        raise KeyError(tool)


print("  已注册 scheme: jira")
print(f"  现在可用: {connector_registry.names()}")


# ═══════════════════════════════════════════════════════════
#  3. 挂载并调用
# ═══════════════════════════════════════════════════════════
section("3. 挂载三个异构外部系统")

mgr = ConnectorManager()
mgr.mount("tickets", "jira", project="DEV")
mgr.mount(
    "scanner",
    "mock",
    handlers={
        # 故意用一个不常见的工具名，演示容错解析
        "issues_search": lambda a: {"total": 907, "issues": [{"key": "AZq-0001"}]},
        "metrics/component": lambda a: {"ncloc": 397265, "quality_gate": "ERROR"},
    },
)
mgr.mount(
    "chat",
    "mock",
    handlers={"message.send": lambda a: {"ok": True, "to": a.get("to")}},
)

for conn, tools in mgr.inventory().items():
    print(f"  {conn:<10} {tools}")


section("4. 工具名容错解析：上游改名也能接上")

# 生产里见过四种写法：issues / issues.search / issues.search / issues_search
result = mgr.call(
    "scanner",
    preferred=["issues", "issues/search", "issues.search", "issues_search"],
    args={"project": "WebOIS", "status": "OPEN"},
    keywords=["issues"],
)
print(f"  候选四选一 → 命中 issues_search，返回 total={result['total']}")

# 全部失配时，异常里带完整可用清单，便于现场诊断
try:
    mgr.call("scanner", preferred=["does_not_exist"], keywords=["nope"])
except LookupError as exc:
    print(f"  全部失配 → {exc}")


section("5. 调用记账：确定性阶段的调用也进 tool_call 表")
for call in mgr.calls:
    flag = "ok" if call.ok else "FAIL"
    print(f"  [{flag:<4}] {call.name:<28} {call.elapsed_ms}ms")

print(f"\n{BAR}")
print("加一个外部系统 = 注册一个 Connector 子类。底盘其余四大系统不用动。")
print(BAR)
