"""
⑤ 可观测与问责 —— 把执行过程变成可以事后追问的数据。

数据模型刻意保持通用：task / trace / tool_call / health 四张表里
不出现任何场景专属概念。换载荷不需要改表结构，这也是「底盘与业务
无关」在数据层的体现。

内置两个观察者：
  ConsoleObserver   路演用，把执行过程打成时间线
  RecordingObserver 落库用，产出可被只读接口消费的结构化记录
"""
from __future__ import annotations

import json
import os
import sys
import time
from dataclasses import asdict, dataclass, field
from datetime import datetime
from typing import Any, Dict, List, Optional

from .contracts import (
    Injection,
    Observer,
    Outcome,
    Registry,
    RunContext,
    Task,
    TaskResult,
    ToolCall,
)

observer_registry = Registry("observer")


# ═══════════════════════════════════════════════════════════
#  通用数据模型：四张表，零业务概念
# ═══════════════════════════════════════════════════════════

@dataclass
class TaskRecord:
    """task 表：一次执行的元信息与终态。"""
    run_id: str
    task_key: str
    task_kind: str
    outcome: str
    started_at: str
    elapsed_ms: int
    iterations: int
    orchestrator: str
    verdict_done: Optional[bool] = None
    verdict_reason: str = ""
    error: str = ""


@dataclass
class TraceRecord:
    """trace 表：编排轨迹，可逐步回放。"""
    run_id: str
    seq: int
    kind: str
    label: str
    at_ms: int
    detail: Dict[str, Any] = field(default_factory=dict)


@dataclass
class ToolCallRecord:
    """tool_call 表：每次工具调用的入参与结果。"""
    run_id: str
    seq: int
    name: str
    ok: bool
    elapsed_ms: int
    args: Dict[str, Any] = field(default_factory=dict)
    error: str = ""


@dataclass
class HealthRecord:
    """health 表：数字员工是否在岗，本身也要可监控。"""
    subject: str
    mode: str
    last_seen: str
    total_runs: int
    succeeded: int
    failed: int

    @property
    def success_rate(self) -> float:
        done = self.succeeded + self.failed
        return round(self.succeeded / done * 100, 1) if done else 0.0


# ═══════════════════════════════════════════════════════════
#  观察者
# ═══════════════════════════════════════════════════════════

@observer_registry.register("recording")
class RecordingObserver(Observer):
    """把过程写进四张通用表。可选落盘为 JSON。"""

    name = "recording"

    def __init__(self, subject: str = "chassis", mode: str = "manual", path: Optional[str] = None) -> None:
        self.subject = subject
        self.mode = mode
        self.path = path
        self.tasks: List[TaskRecord] = []
        self.traces: List[TraceRecord] = []
        self.tool_calls: List[ToolCallRecord] = []
        self._seq = 0
        self._call_seq = 0

    def _next(self) -> int:
        self._seq += 1
        return self._seq

    def on_task_start(self, task: Task, ctx: RunContext) -> None:
        self._seq = 0
        self._call_seq = 0
        self.traces.append(TraceRecord(
            run_id=ctx.run_id, seq=self._next(), kind="task_start",
            label=f"{task.kind}:{task.key}", at_ms=ctx.ms(),
        ))

    def on_step(self, step: str, task: Task, ctx: RunContext) -> None:
        self.traces.append(TraceRecord(
            run_id=ctx.run_id, seq=self._next(), kind="step",
            label=step, at_ms=ctx.ms(),
        ))

    def on_tool_call(self, call: ToolCall, task: Task, ctx: RunContext) -> None:
        self._call_seq += 1
        self.tool_calls.append(ToolCallRecord(
            run_id=ctx.run_id, seq=self._call_seq, name=call.name,
            ok=call.ok, elapsed_ms=call.elapsed_ms,
            args=call.args, error=call.error,
        ))
        self.traces.append(TraceRecord(
            run_id=ctx.run_id, seq=self._next(), kind="tool_call",
            label=call.name, at_ms=ctx.ms(),
            detail={"ok": call.ok, "error": call.error},
        ))

    def on_injection(self, inj: Injection, task: Task, ctx: RunContext) -> None:
        self.traces.append(TraceRecord(
            run_id=ctx.run_id, seq=self._next(), kind="injection",
            label=f"{inj.point.value} / {inj.label}", at_ms=inj.at_ms,
            detail={"provider": inj.provider, "chars": inj.chars},
        ))

    def on_task_end(self, result: TaskResult, ctx: RunContext) -> None:
        orch = getattr(ctx.chassis, "orchestrator_name", "") if ctx.chassis else ""
        self.tasks.append(TaskRecord(
            run_id=ctx.run_id,
            task_key=result.task.key,
            task_kind=result.task.kind,
            outcome=result.outcome.value,
            started_at=datetime.fromtimestamp(ctx.started_at).isoformat(timespec="seconds"),
            elapsed_ms=result.elapsed_ms,
            iterations=result.iterations,
            orchestrator=orch,
            verdict_done=result.verdict.done if result.verdict else None,
            verdict_reason=result.verdict.reason if result.verdict else "",
            error=result.error,
        ))
        self.traces.append(TraceRecord(
            run_id=ctx.run_id, seq=self._next(), kind="task_end",
            label=result.outcome.value, at_ms=ctx.ms(),
        ))
        if self.path:
            self.dump(self.path)

    # ── 只读视图 ────────────────────────────────────────
    def health(self) -> HealthRecord:
        ok = sum(1 for t in self.tasks if t.outcome == Outcome.SUCCEEDED.value)
        bad = sum(1 for t in self.tasks if t.outcome == Outcome.FAILED.value)
        return HealthRecord(
            subject=self.subject, mode=self.mode,
            last_seen=self.tasks[-1].started_at if self.tasks else "",
            total_runs=len(self.tasks), succeeded=ok, failed=bad,
        )

    def replay(self, run_id: Optional[str] = None) -> List[TraceRecord]:
        if run_id is None:
            return list(self.traces)
        return [t for t in self.traces if t.run_id == run_id]

    def snapshot(self) -> Dict[str, Any]:
        return {
            "health": asdict(self.health()),
            "tasks": [asdict(t) for t in self.tasks],
            "traces": [asdict(t) for t in self.traces],
            "tool_calls": [asdict(t) for t in self.tool_calls],
        }

    def dump(self, path: str) -> None:
        os.makedirs(os.path.dirname(os.path.abspath(path)) or ".", exist_ok=True)
        with open(path, "w", encoding="utf-8") as fh:
            json.dump(self.snapshot(), fh, ensure_ascii=False, indent=2, default=str)


@observer_registry.register("console")
class ConsoleObserver(Observer):
    """路演用。把一次执行打成缩进时间线。

    Windows 控制台默认 GBK，编不了 ✗ ▸ 这类字符，所以启动时探测一次
    输出编码，编不了就换成 ASCII 字形。演示不应该因为一个符号崩在台上。
    """

    name = "console"

    RICH = {"step": "▸", "tool": "·", "inject": "◆", "fail": "✗",
            "tl": "┌", "mid": "│", "bl": "└"}
    PLAIN = {"step": ">", "tool": ".", "inject": "*", "fail": "x",
             "tl": "+", "mid": "|", "bl": "+"}

    def __init__(self, verbose: bool = True) -> None:
        self.verbose = verbose
        self.g = self.RICH if self._supports(self.RICH) else self.PLAIN

    @staticmethod
    def _supports(glyphs: Dict[str, str]) -> bool:
        enc = getattr(sys.stdout, "encoding", None) or "utf-8"
        try:
            "".join(glyphs.values()).encode(enc)
            return True
        except (UnicodeEncodeError, LookupError):
            return False

    def _p(self, indent: int, text: str) -> None:
        if self.verbose:
            print(" " * indent + text)

    def on_task_start(self, task: Task, ctx: RunContext) -> None:
        self._p(0, f"\n{self.g['tl']} {task.kind}:{task.key}   run={ctx.run_id}")

    def on_step(self, step: str, task: Task, ctx: RunContext) -> None:
        self._p(2, f"{self.g['mid']} {self.g['step']} {step}")

    def on_tool_call(self, call: ToolCall, task: Task, ctx: RunContext) -> None:
        mark = "" if call.ok else f"  {self.g['fail']} " + call.error
        self._p(6, f"{self.g['mid']}   {self.g['tool']} {call.name} ({call.elapsed_ms}ms){mark}")

    def on_injection(self, inj: Injection, task: Task, ctx: RunContext) -> None:
        self._p(6, f"{self.g['mid']}   {self.g['inject']} 注入 @{inj.point.value} "
                   f"← {inj.label} ({inj.chars} 字)")

    def on_task_end(self, result: TaskResult, ctx: RunContext) -> None:
        v = result.verdict
        judged = f"  裁定={v.done} ({v.reason})" if v else ""
        self._p(0, f"{self.g['bl']} {result.outcome.value}  {result.elapsed_ms}ms  "
                   f"迭代={result.iterations}{judged}")
        if result.error:
            self._p(2, f"  {self.g['fail']} {result.error}")


class FanOutObserver(Observer):
    """把事件同时发给多个观察者。"""

    name = "fanout"

    def __init__(self, *observers: Observer) -> None:
        self.observers = list(observers)

    def add(self, obs: Observer) -> None:
        self.observers.append(obs)

    def on_task_start(self, task: Task, ctx: RunContext) -> None:
        for o in self.observers:
            o.on_task_start(task, ctx)

    def on_step(self, step: str, task: Task, ctx: RunContext) -> None:
        for o in self.observers:
            o.on_step(step, task, ctx)

    def on_tool_call(self, call: ToolCall, task: Task, ctx: RunContext) -> None:
        for o in self.observers:
            o.on_tool_call(call, task, ctx)

    def on_injection(self, inj: Injection, task: Task, ctx: RunContext) -> None:
        for o in self.observers:
            o.on_injection(inj, task, ctx)

    def on_task_end(self, result: TaskResult, ctx: RunContext) -> None:
        for o in self.observers:
            o.on_task_end(result, ctx)


__all__ = [
    "TaskRecord",
    "TraceRecord",
    "ToolCallRecord",
    "HealthRecord",
    "RecordingObserver",
    "ConsoleObserver",
    "FanOutObserver",
    "observer_registry",
]
