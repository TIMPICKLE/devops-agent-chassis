"""
权限边界 —— 能力借来，权限不借。

底盘允许集成任何第三方 Agent 作为执行器（Claude Code CLI、Aider、
Cursor Agent 都可以），但集成不等于全权委托。执行器通常自带完整的
版本控制能力，底盘要做的是**在授予能力的同时收回权限**。

这不是靠提示词里写一句"请不要 commit"来实现的。提示词是软约束，
模型可以不听。硬约束是：执行器根本拿不到那个能力，调用会抛异常。
提示词只是把边界提前告知，避免它浪费轮次去试。
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, Iterable, List, Optional, Set


class PermissionDenied(RuntimeError):
    """越界调用。故意让它是异常而不是返回值，避免被静默忽略。"""


@dataclass
class Capability:
    """一项可授予的能力。"""
    key: str
    description: str = ""
    #: 不可逆操作在被拒绝时的提示更严厉，也便于审计时筛选
    irreversible: bool = False


#: 底盘预置的通用能力集。载荷可以自行扩充。
STANDARD_CAPABILITIES: Dict[str, Capability] = {
    c.key: c
    for c in [
        Capability("repo.read", "浏览与读取代码库"),
        Capability("repo.write", "修改工作区文件"),
        Capability("vcs.branch", "创建分支", irreversible=True),
        Capability("vcs.commit", "提交变更", irreversible=True),
        Capability("vcs.push", "推送到远端", irreversible=True),
        Capability("vcs.merge", "合入主干", irreversible=True),
        Capability("pr.create", "创建 Pull Request", irreversible=True),
        Capability("notify.send", "发送通知", irreversible=True),
        Capability("ticket.write", "写工单", irreversible=True),
    ]
}


@dataclass
class PermissionBoundary:
    """一个执行器的权限边界。

    用法：

        boundary = PermissionBoundary(
            subject="claude-code-cli",
            granted={"repo.read", "repo.write"},
        )
        boundary.check("repo.write")   # 通过
        boundary.check("vcs.commit")   # 抛 PermissionDenied
    """

    subject: str
    granted: Set[str] = field(default_factory=set)
    catalog: Dict[str, Capability] = field(default_factory=lambda: dict(STANDARD_CAPABILITIES))
    #: 被拒绝的调用会记在这里，用于证明边界真的生效过
    denials: List[str] = field(default_factory=list)

    def grant(self, *keys: str) -> "PermissionBoundary":
        self.granted.update(keys)
        return self

    def revoke(self, *keys: str) -> "PermissionBoundary":
        self.granted.difference_update(keys)
        return self

    def allows(self, key: str) -> bool:
        return key in self.granted

    def check(self, key: str) -> None:
        if key in self.granted:
            return
        cap = self.catalog.get(key)
        hint = cap.description if cap else key
        self.denials.append(key)
        marker = "不可逆操作" if cap and cap.irreversible else "未授予能力"
        raise PermissionDenied(
            f"{self.subject} 尝试执行「{hint}」被拒绝（{marker}）。"
            f"已授予：{sorted(self.granted) or '无'}"
        )

    def denied_keys(self) -> List[str]:
        return sorted(set(self.catalog) - self.granted)

    def as_table(self) -> List[tuple]:
        """(能力, 描述, 是否授予, 是否不可逆)，用于渲染权限清单。"""
        rows = []
        for key in sorted(self.catalog):
            cap = self.catalog[key]
            rows.append((key, cap.description, key in self.granted, cap.irreversible))
        return rows

    def summary(self) -> str:
        ok = sorted(self.granted)
        no = self.denied_keys()
        return (
            f"{self.subject}：授予 {len(ok)} 项 {ok}；"
            f"拒绝 {len(no)} 项 {no}"
        )


def borrowed_executor(subject: str, *, can_read: bool = True, can_write: bool = True) -> PermissionBoundary:
    """构造一个「借来的执行器」的典型边界。

    默认给文件系统读写，不给任何版本控制与对外通知能力。
    这正是生产里 Claude Code CLI 拿到的那份权限。
    """
    granted: Set[str] = set()
    if can_read:
        granted.add("repo.read")
    if can_write:
        granted.add("repo.write")
    return PermissionBoundary(subject=subject, granted=granted)


__all__ = [
    "Capability",
    "PermissionBoundary",
    "PermissionDenied",
    "STANDARD_CAPABILITIES",
    "borrowed_executor",
]
