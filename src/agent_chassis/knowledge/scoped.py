"""Explicit task/stage routing for independently versioned knowledge documents."""
from __future__ import annotations

from typing import Mapping, Optional, Sequence

from ..contracts import InjectionPoint, KnowledgeProvider, RunContext, Task


class ScopedKnowledge(KnowledgeProvider):
    """Inject one document only when every configured scope matches.

    Keys address top-level task.payload / ctx.facts entries. Workflows set facts
    explicitly; node names never implicitly select knowledge. Register multiple
    providers for multiple documents; each retains its own hash and version.
    Missing keys do not match. An empty scope applies to every task/stage.
    """

    def __init__(self, text: str, *, name: str, version: str,
                 task_scope: Optional[Mapping[str, str]] = None,
                 fact_scope: Optional[Mapping[str, str]] = None,
                 points: Sequence[InjectionPoint] = (InjectionPoint.BEFORE_EXECUTOR,)):
        if not all(isinstance(value, str) and value.strip() for value in (text, name, version)):
            raise ValueError("Knowledge text, name and version must be nonempty strings")
        self.text, self.name, self.version = text, name, version
        self.task_scope = tuple((task_scope or {}).items())
        self.fact_scope = tuple((fact_scope or {}).items())
        if any(not isinstance(k, str) or not isinstance(v, str)
               for k, v in self.task_scope + self.fact_scope):
            raise ValueError("Scope keys and values must be strings")
        self.points = tuple(points)
        if not self.points or any(not isinstance(p, InjectionPoint) for p in self.points):
            raise ValueError("At least one valid injection point is required")
        if InjectionPoint.AGENT_BOOT in self.points:
            raise ValueError("AGENT_BOOT is intentionally empty")

    def provide(self, point: InjectionPoint, task: Task, ctx: RunContext) -> Optional[str]:
        if (point not in self.points
                or any(task.payload.get(k) != v for k, v in self.task_scope)
                or any(ctx.facts.get(k) != v for k, v in self.fact_scope)):
            return None
        return f"### {self.name} @ {self.version}\n\n{self.text}"

    def label_for(self, point: InjectionPoint, task: Task, ctx: RunContext) -> str:
        return f"{self.name}@{self.version}"
