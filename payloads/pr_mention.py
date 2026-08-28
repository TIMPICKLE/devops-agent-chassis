"""
载荷 ② —— PR 评论区 @Agent 编码助手。

这个场景与代码质量治理**毫无关系**：任务不是扫描器扫出来的，是人在
评论区打出来的；完成判据不是「文件有没有变」，是「回帖有没有发出去」。

但它复用了同一套底盘：同样的编排器、同样的连接器管理、同样的知识注入
时机、同样的失败契约、同样的可观测数据模型。

这个文件的长度就是「换载荷的成本」本身。
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional

from agent_chassis import DoneCriteria, RunContext, Task, TaskSource, Verdict
from agent_chassis.orchestration import ToolBox
from agent_chassis.permissions import PermissionBoundary


MENTIONS: List[Dict[str, Any]] = [
    {
        "id": "pr-8842-c17",
        "author": "reviewer.zhang",
        "pr": 8842,
        "body": "@Agent 把这个方法里的重复校验抽成私有方法",
        "file": "src/Application/Order/OrderAppService.cs",
    },
    {
        "id": "wi-29104-c3",
        "author": "pm.li",
        "pr": 0,
        "body": "@Agent 按这个工作项的描述实现接口骨架和单测",
        "file": "src/Application/Report/IReportAppService.cs",
    },
]


class Thread:
    """评论区的离线替身。完成判据读它，而不是读模型说了什么。"""

    def __init__(self) -> None:
        self.replies: List[Dict[str, Any]] = []

    def reply(self, mention_id: str, body: str) -> Dict[str, Any]:
        rec = {"mention_id": mention_id, "body": body, "seq": len(self.replies) + 1}
        self.replies.append(rec)
        return rec

    def replies_for(self, mention_id: str) -> List[Dict[str, Any]]:
        return [r for r in self.replies if r["mention_id"] == mention_id]


class MentionTaskSource(TaskSource):
    """任务源：评论区里 @Agent 的自然语言指令。"""

    name = "PR / 工作项评论区 @Agent"

    def __init__(self, mentions: Optional[List[Dict[str, Any]]] = None) -> None:
        self.mentions = list(mentions if mentions is not None else MENTIONS)
        self._cursor = 0

    def fetch(self, limit: int = 1) -> List[Task]:
        out: List[Task] = []
        while self._cursor < len(self.mentions) and len(out) < limit:
            m = self.mentions[self._cursor]
            self._cursor += 1
            out.append(Task(
                key=m["id"],
                kind="mention",
                source=self.name,
                payload={
                    "instruction": m["body"].replace("@Agent", "").strip(),
                    "author": m["author"],
                    "pr": m["pr"],
                    "path": m["file"],
                    "target": {"path": m["file"]},
                },
            ))
        return out


class RepliedCriteria(DoneCriteria):
    """完成判据：回帖必须真的发出去了。

    和载荷 ① 的判据结构完全一样：读外部系统的客观状态，
    不读模型的自述。换的只是「读哪个外部系统」。
    """

    name = "评论区已收到 Agent 回帖（读线程状态）"

    def __init__(self, thread: Thread) -> None:
        self.thread = thread

    def judge(self, task: Task, ctx: RunContext) -> Verdict:
        replies = self.thread.replies_for(task.key)
        if not replies:
            return Verdict(False, "未检测到回帖", {"replies": 0})
        return Verdict(True, f"已回帖 {len(replies)} 条", {"replies": len(replies)})


def build_toolbox(thread: Thread, boundary: PermissionBoundary) -> ToolBox:
    box = ToolBox()

    def understand_instruction(text: str = "") -> Dict[str, Any]:
        """解析自然语言指令，判断它要做什么。"""
        kind = "refactor" if "抽" in text or "改" in text else "implement"
        return {"intent": kind, "needs_context": kind == "refactor"}

    def read_context(path: str = "") -> Dict[str, Any]:
        """读取被提及的文件上下文。"""
        boundary.check("repo.read")
        return {"path": path, "symbols": 12}

    def draft_change(path: str = "", intent: str = "") -> Dict[str, Any]:
        """生成变更草案。执行器只有文件系统写权限。"""
        boundary.check("repo.write")
        return {"path": path, "intent": intent, "hunks": 2}

    def post_reply(mention_id: str = "", body: str = "") -> Dict[str, Any]:
        """向评论区回帖。这是本载荷的交付动作。"""
        return thread.reply(mention_id, body)

    for fn in (understand_instruction, read_context, draft_change, post_reply):
        box.add(fn.__name__, fn)
    return box


def decide(task: Task, ctx: RunContext, box: ToolBox) -> tuple:
    """本载荷的决策函数。结构与载荷 ① 一致，判断内容完全不同。"""
    seen = ctx.facts.setdefault("tool_results", {})
    p = task.payload

    if "understand_instruction" not in seen:
        return ("call", "understand_instruction", {"text": p["instruction"]})

    intent = seen["understand_instruction"]
    if intent.get("needs_context") and "read_context" not in seen:
        return ("call", "read_context", {"path": p["path"]})

    if "draft_change" not in seen:
        return ("call", "draft_change",
                {"path": p["path"], "intent": intent.get("intent", "")})

    if "post_reply" not in seen:
        return ("call", "post_reply", {
            "mention_id": task.key,
            "body": f"已按「{p['instruction']}」提交变更草案，请审阅。",
        })

    ctx.note("已回帖，收敛")
    return ("stop", "已交付", None)


__all__ = [
    "MENTIONS",
    "MentionTaskSource",
    "RepliedCriteria",
    "Thread",
    "build_toolbox",
    "decide",
]
