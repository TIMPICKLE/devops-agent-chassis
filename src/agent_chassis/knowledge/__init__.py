"""
③ 知识注入 —— 时机调度与两级路由。

底盘对知识层的核心主张：**时机比内容更重要**。

同一份 ABP 规范，注入在决策层的 system prompt 里，还是在调用外部执行器
之前的最后一步拼进去，决定了底盘与业务解不解耦。前者会让 Agent 变成
「懂 ABP 的 Agent」，后者让 Agent 始终是「不知道 ABP 是什么的 Agent」。

本模块提供：
  - InjectionScheduler   按时机调度所有 provider，并记录实际发生的注入
  - SkillLibrary         文件型规范库，支持多级路由规则
  - StaticKnowledge      固定文本注入，用于任务级背景
  - RetryFeedback        把上一次失败原因在重试前回灌
"""
from __future__ import annotations

import os
import time
import hashlib
from typing import Any, Callable, Dict, List, Optional, Sequence, Tuple

from ..contracts import (
    ContextChunk,
    Injection,
    InjectionPoint,
    KnowledgeProvider,
    Registry,
    RunContext,
    Task,
)

knowledge_registry = Registry("knowledge-provider")


class InjectionScheduler:
    """在每个时机上收集应当注入的知识，并留下可审计的记录。"""

    def __init__(self, providers: Sequence[KnowledgeProvider]) -> None:
        self.providers = list(providers)

    def providers_at(self, point: InjectionPoint) -> List[KnowledgeProvider]:
        return [p for p in self.providers if point in p.points]

    def collect(
        self,
        point: InjectionPoint,
        task: Task,
        ctx: RunContext,
    ) -> str:
        """返回该时机拼好的知识文本，同时把注入记录写进 ctx。"""
        chunks: List[str] = []
        envelopes: List[ContextChunk] = []
        # 空收集也替换快照，避免沿用上一工具或上一轮重试的知识。
        ctx.knowledge[point] = ()
        for provider in self.providers_at(point):
            text = provider.provide(point, task, ctx)
            if not text:
                continue
            label = provider.label_for(point, task, ctx)
            digest = hashlib.sha256(text.encode("utf-8")).hexdigest()
            version = str(getattr(provider, "version", "unversioned"))
            envelopes.append(ContextChunk(point, provider.name, label, digest, text, version))
            inj = Injection(
                point=point,
                provider=provider.name,
                label=label,
                chars=len(text),
                at_ms=ctx.ms(),
                content_hash=digest,
                version=version,
            )
            ctx.injections.append(inj)
            if ctx.chassis is not None:
                ctx.chassis.notify_injection(inj, task, ctx)
            chunks.append(text)
        ctx.knowledge[point] = tuple(envelopes)
        return "\n\n".join(chunks)

    def timeline(self) -> List[Tuple[InjectionPoint, List[str]]]:
        """静态展示：每个时机上挂了哪些 provider。"""
        return [
            (point, [p.name for p in self.providers_at(point)])
            for point in InjectionPoint
        ]


# ═══════════════════════════════════════════════════════════
#  Skill 库：文件型规范，多级路由
# ═══════════════════════════════════════════════════════════

#: 路由规则：给定任务上下文，返回 skill 名或 None
RouteRule = Callable[[Dict[str, Any]], Optional[str]]


def by_extension(mapping: Dict[str, str]) -> RouteRule:
    """一级路由：按文件扩展名直接落库。"""
    def rule(meta: Dict[str, Any]) -> Optional[str]:
        path = str(meta.get("path", ""))
        ext = os.path.splitext(path)[1].lower()
        return mapping.get(ext)
    return rule


def by_filename_markers(
    extensions: Sequence[str],
    markers: Sequence[str],
    hit: str,
    miss: str,
) -> RouteRule:
    """二级路由：同一类扩展名里，按文件名标记再分流。

    生产里的真实例子：`user-list.component.ts` 命中 Angular 构件标记，
    走 Angular 规范；`date-utils.ts` 不命中，走 TypeScript 通用规范。
    """
    exts = {e.lower() for e in extensions}
    marks = [m.lower() for m in markers]

    def rule(meta: Dict[str, Any]) -> Optional[str]:
        path = str(meta.get("path", ""))
        ext = os.path.splitext(path)[1].lower()
        if ext not in exts:
            return None
        fname = os.path.basename(path).lower()
        return hit if any(m in fname for m in marks) else miss
    return rule


class SkillLibrary:
    """按路由规则加载规范文件。

    刻意不做向量检索。规范类知识是确定性的，"ABP 的 Application Service
    该怎么写"有唯一正确答案，相似度召回只会带来两类问题：召回不全导致
    规范缺失，召回过多导致上下文稀释。
    """

    def __init__(
        self,
        root: str,
        rules: Sequence[RouteRule] = (),
        inline: Optional[Dict[str, str]] = None,
    ) -> None:
        self.root = root
        self.rules = list(rules)
        #: 允许不落盘直接给内容，便于测试与演示
        self.inline = dict(inline or {})

    def route(self, meta: Dict[str, Any]) -> Optional[str]:
        for rule in self.rules:
            hit = rule(meta)
            if hit:
                return hit
        return None

    def load(self, skill: str) -> str:
        if skill in self.inline:
            return self.inline[skill]
        path = os.path.join(self.root, f"{skill}.md")
        if os.path.exists(path):
            with open(path, "r", encoding="utf-8") as fh:
                return fh.read()
        return ""

    def available(self) -> List[str]:
        names = set(self.inline)
        if os.path.isdir(self.root):
            names |= {
                f[:-3] for f in os.listdir(self.root) if f.endswith(".md")
            }
        return sorted(names)


@knowledge_registry.register("skills")
class SkillProvider(KnowledgeProvider):
    """把 Skill 库挂在指定时机上。

    默认挂在 BEFORE_EXECUTOR，也就是调用外部执行器之前的最后一步。
    刻意不挂 AGENT_BOOT：决策层不需要知道任何技术栈。
    """

    name = "skills"

    def __init__(
        self,
        library: SkillLibrary,
        points: Sequence[InjectionPoint] = (InjectionPoint.BEFORE_EXECUTOR,),
        meta_key: str = "target",
    ) -> None:
        self.library = library
        self.points = list(points)
        self.meta_key = meta_key

    def _meta(self, task: Task, ctx: RunContext) -> Dict[str, Any]:
        meta = dict(task.payload.get(self.meta_key) or {})
        meta.setdefault("path", task.payload.get("path", ""))
        return meta

    def provide(self, point: InjectionPoint, task: Task, ctx: RunContext) -> Optional[str]:
        skill = self.library.route(self._meta(task, ctx))
        if not skill:
            return None
        body = self.library.load(skill)
        if not body:
            return None
        return f"### 参考规范：{skill}\n\n{body}"

    def label_for(self, point: InjectionPoint, task: Task, ctx: RunContext) -> str:
        return f"skills:{self.library.route(self._meta(task, ctx)) or 'none'}"


@knowledge_registry.register("static")
class StaticKnowledge(KnowledgeProvider):
    """固定文本注入。用于任务级背景、口径说明这类不随文件变化的内容。"""

    def __init__(
        self,
        text: str,
        points: Sequence[InjectionPoint] = (InjectionPoint.TASK_ADMITTED,),
        name: str = "static",
    ) -> None:
        self.text = text
        self.points = list(points)
        self.name = name

    def provide(self, point: InjectionPoint, task: Task, ctx: RunContext) -> Optional[str]:
        return self.text or None


@knowledge_registry.register("retry_feedback")
class RetryFeedback(KnowledgeProvider):
    """重试之前，把上一次的失败原因回灌给模型。

    这是 ON_RETRY 这个时机存在的唯一理由：同样的上下文重试一次没有意义，
    带着上次为什么失败重试才有意义。
    """

    name = "retry-feedback"
    points = [InjectionPoint.ON_RETRY]

    def provide(self, point: InjectionPoint, task: Task, ctx: RunContext) -> Optional[str]:
        last = ctx.facts.get("last_error")
        if not last:
            return None
        return f"上一次尝试失败，原因：{last}\n请针对这一点调整策略，不要重复同样的做法。"


__all__ = [
    "InjectionScheduler",
    "SkillLibrary",
    "SkillProvider",
    "StaticKnowledge",
    "RetryFeedback",
    "by_extension",
    "by_filename_markers",
    "knowledge_registry",
]
