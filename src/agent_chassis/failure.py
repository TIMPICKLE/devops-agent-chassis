"""
④ 失败契约 —— 失败之后系统留下什么。

底盘的默认要求是零副作用：落库标记、去重防重试、干净退出、不阻塞下一条。
真正被消耗的只有算力，不是人力。

这里还实现了一个容易被忽略的对偶：**开工前的清理**。
不只是失败后不留残骸，而是每轮开始前假设上一轮可能留了残骸并强行清理。
长期无人值守的系统必须这么假设。
"""
from __future__ import annotations

import json
import os
import threading
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Callable, Dict, List, Optional

from .contracts import FailurePolicy, Outcome, Registry, RunContext, Task

failure_registry = Registry("failure-policy")


@dataclass
class LedgerEntry:
    key: str
    outcome: str
    at: str
    detail: str = ""


class Ledger:
    """去重账本。成功与失败都记，读取时一并去重。

    这个设计有一个明确的取舍：失败过的任务不会再被尝试。
    好处是不会在同一道难题上反复烧算力，代价是模型升级后历史失败样本
    不会自动重跑。retryable 参数把这个取舍暴露出来，由载荷自己决定。
    """

    def __init__(self, path: Optional[str] = None, retry_failed: bool = False) -> None:
        self.path = path
        self.retry_failed = retry_failed
        self._entries: Dict[str, LedgerEntry] = {}
        self._lock = threading.Lock()
        if path and os.path.exists(path):
            self._load()

    def _load(self) -> None:
        try:
            with open(self.path, "r", encoding="utf-8") as fh:
                raw = json.load(fh)
            for item in raw if isinstance(raw, list) else []:
                key = item.get("key")
                if key:
                    self._entries[key] = LedgerEntry(
                        key=key,
                        outcome=item.get("outcome", item.get("status", "")),
                        at=item.get("at", item.get("processedDate", "")),
                        detail=item.get("detail", item.get("error", "")),
                    )
        except (OSError, json.JSONDecodeError):
            self._entries = {}

    def _flush(self) -> None:
        if not self.path:
            return
        os.makedirs(os.path.dirname(os.path.abspath(self.path)) or ".", exist_ok=True)
        payload = [
            {"key": e.key, "outcome": e.outcome, "at": e.at, "detail": e.detail}
            for e in self._entries.values()
        ]
        with open(self.path, "w", encoding="utf-8") as fh:
            json.dump(payload, fh, ensure_ascii=False, indent=2)

    def seen(self, key: str) -> bool:
        entry = self._entries.get(key)
        if entry is None:
            return False
        if self.retry_failed and entry.outcome == Outcome.FAILED.value:
            return False
        return True

    def remember(self, key: str, outcome: Outcome, detail: str = "") -> None:
        with self._lock:
            self._entries[key] = LedgerEntry(
                key=key,
                outcome=outcome.value,
                at=datetime.now().isoformat(timespec="seconds"),
                detail=detail,
            )
            self._flush()

    def stats(self) -> Dict[str, int]:
        out: Dict[str, int] = {}
        for e in self._entries.values():
            out[e.outcome] = out.get(e.outcome, 0) + 1
        out["total"] = len(self._entries)
        return out

    def __len__(self) -> int:
        return len(self._entries)

    # 定义了 __len__ 之后，空账本会变成 falsy，`ledger or Ledger()` 会静默丢弃它。
    def __bool__(self) -> bool:
        return True


@failure_registry.register("zero_side_effect")
class ZeroSideEffectPolicy(FailurePolicy):
    """默认失败契约：干净退出，不留残骸。

    cleanups 是一组补偿动作，按注册的逆序执行。典型的补偿是
    「丢弃未推送的工作分支」「撤回半成品工单」。补偿本身失败不会
    抛出，只会记进 ctx.facts，因为补偿失败不应该盖过原始失败。
    """

    name = "zero-side-effect"

    def __init__(self, ledger: Optional[Ledger] = None) -> None:
        self.ledger = ledger if ledger is not None else Ledger()
        self._cleanups: List[tuple] = []

    def register_cleanup(self, label: str, fn: Callable[[Task, RunContext], None]) -> None:
        self._cleanups.append((label, fn))

    def on_failure(self, task: Task, error: str, ctx: RunContext) -> Outcome:
        performed: List[str] = []
        for label, fn in reversed(self._cleanups):
            try:
                fn(task, ctx)
                performed.append(label)
            except Exception as exc:
                performed.append(f"{label}(补偿失败: {exc})")
        ctx.facts["cleanups"] = performed
        ctx.facts["last_error"] = error
        self.remember(task.key, Outcome.FAILED, error)
        return Outcome.FAILED

    def seen(self, key: str) -> bool:
        return self.ledger.seen(key)

    def remember(self, key: str, outcome: Outcome, detail: str = "") -> None:
        self.ledger.remember(key, outcome, detail)


@failure_registry.register("retry_then_give_up")
class RetryThenGiveUpPolicy(ZeroSideEffectPolicy):
    """带有限重试的失败契约。

    重试之前会触发 InjectionPoint.ON_RETRY，把上一次的失败原因回灌。
    没有回灌的重试是无意义的重试。
    """

    name = "retry-then-give-up"

    def __init__(self, ledger: Optional[Ledger] = None, max_retries: int = 1) -> None:
        super().__init__(ledger)
        self.max_retries = max_retries

    def should_retry(self, ctx: RunContext) -> bool:
        used = int(ctx.facts.get("retries", 0))
        return used < self.max_retries

    def consume_retry(self, ctx: RunContext) -> None:
        ctx.facts["retries"] = int(ctx.facts.get("retries", 0)) + 1


@dataclass
class WorkspaceGuard:
    """开工前的清理。失败零副作用的对偶。

    生产里对应 `git reset --hard HEAD` + `git clean -fd`。
    这里做成可插拔的动作列表，因为不是每种载荷都以 git 工作区为舞台。
    """

    actions: List[tuple] = field(default_factory=list)

    def add(self, label: str, fn: Callable[[RunContext], None]) -> "WorkspaceGuard":
        self.actions.append((label, fn))
        return self

    def prepare(self, ctx: RunContext) -> List[str]:
        done: List[str] = []
        for label, fn in self.actions:
            fn(ctx)
            done.append(label)
        ctx.facts["workspace_prepared"] = done
        return done


__all__ = [
    "Ledger",
    "LedgerEntry",
    "ZeroSideEffectPolicy",
    "RetryThenGiveUpPolicy",
    "WorkspaceGuard",
    "failure_registry",
]
