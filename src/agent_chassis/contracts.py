"""
底盘契约层 —— 所有可插拔点的抽象。

这个文件是整套底盘唯一必须读懂的地方。它定义了「与业务无关的五大系统」
各自的接口，以及「与业务有关的载荷」需要提供的两项定义。

    底盘 Chassis（本包）          载荷 Payload（业务方提供）
    ├─ ① 编排契约 Orchestrator     ├─ TaskSource    任务从哪来
    ├─ ② 接入层   Connector        └─ DoneCriteria  怎么算做完
    ├─ ③ 知识注入 KnowledgeProvider
    ├─ ④ 失败契约 FailurePolicy
    └─ ⑤ 可观测   Observer

本文件不 import 任何第三方库，也不出现任何具体业务概念。
"""
from __future__ import annotations

import time
import uuid
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Callable, Dict, Iterable, List, Optional, Sequence, Tuple


# ═══════════════════════════════════════════════════════════
#  基础数据模型
# ═══════════════════════════════════════════════════════════

class Outcome(str, Enum):
    """一次任务执行的终态。"""
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    SKIPPED = "skipped"


@dataclass
class Task:
    """一个待处理的工作单元。

    底盘不关心 payload 里装的是代码异味、PR 评论还是构建失败，
    只要求它有一个稳定的 key 用于去重。
    """
    key: str
    kind: str
    payload: Dict[str, Any] = field(default_factory=dict)
    source: str = ""

    def __repr__(self) -> str:  # pragma: no cover - 展示用
        return f"<Task {self.kind}:{self.key}>"


@dataclass
class Verdict:
    """完成判据给出的裁定。

    关键约束：裁定由确定性代码产生，模型的自然语言输出不参与判定。
    reason 用于落库与复盘，evidence 保留裁定所依据的原始事实。
    """
    done: bool
    reason: str = ""
    evidence: Dict[str, Any] = field(default_factory=dict)


@dataclass
class TaskResult:
    """一次任务执行的完整结果。"""
    task: Task
    outcome: Outcome
    verdict: Optional[Verdict] = None
    error: str = ""
    steps: List[str] = field(default_factory=list)
    iterations: int = 0
    elapsed_ms: int = 0
    artifacts: Dict[str, Any] = field(default_factory=dict)


@dataclass
class ToolCall:
    """一次工具调用的记录。"""
    name: str
    args: Dict[str, Any]
    result: Any = None
    ok: bool = True
    error: str = ""
    elapsed_ms: int = 0


# ═══════════════════════════════════════════════════════════
#  载荷侧：换场景只换这两个
# ═══════════════════════════════════════════════════════════

class TaskSource(ABC):
    """载荷定义之一：任务从哪来。

    实现示例：SonarQube 未解决异味、PR 评论区 @Agent、CI 构建失败、工单。
    """

    name: str = "task-source"

    @abstractmethod
    def fetch(self, limit: int = 1) -> List[Task]:
        """拉取待处理任务。底盘负责去重，实现方不必自己记账。"""

    def describe(self) -> str:
        return self.name


class DoneCriteria(ABC):
    """载荷定义之二：怎么算做完。

    这是底盘里最不能让模型插手的地方。实现必须基于可观察的外部事实
    （版本控制状态、构建结果、工单状态），而不是 Agent 的自述。
    """

    name: str = "done-criteria"

    @abstractmethod
    def judge(self, task: Task, ctx: "RunContext") -> Verdict:
        """给出裁定。禁止读取 ctx.last_agent_message 之类的模型输出。"""

    def describe(self) -> str:
        return self.name


# ═══════════════════════════════════════════════════════════
#  ① 编排契约
# ═══════════════════════════════════════════════════════════

class Orchestrator(ABC):
    """外层流程编排：决定一个任务被怎样推进。

    这是「骨架」那一轴。线性状态机、分层子图、DAG 都是合法实现。
    它不规定模型怎么思考，只规定在哪一个阶段把控制权交出去。
    """

    name: str = "orchestrator"
    #: 声明本编排器把决策权交给模型的位置，用于生成信任边界报告
    delegation_points: List[str] = []

    @abstractmethod
    def run(self, task: Task, ctx: "RunContext") -> TaskResult:
        """推进一个任务直到终态。"""

    def describe(self) -> str:
        pts = ", ".join(self.delegation_points) or "无"
        return f"{self.name}（下放点：{pts}）"


class ReasoningPattern(ABC):
    """内层推理模式：在下放点内部，模型怎么想。

    这是「思考方式」那一轴，也就是通常说的 Agent 设计模式：
    ReAct、Plan-and-Execute、ReWOO、Reflexion 等。

    与 Orchestrator 正交：同一个流程可以换推理模式，
    同一个推理模式可以放进不同流程。
    """

    name: str = "reasoning"
    description: str = ""

    @abstractmethod
    def reason(self, task: Task, ctx: "RunContext", toolbox: Any) -> None:
        """就地推进 ctx。工具调用请走 orchestration.reasoning.invoke_tool，
        它统一处理知识注入时机与调用记账。"""

    def describe(self) -> str:
        return f"{self.name}：{self.description}" if self.description else self.name


class Step(ABC):
    """编排器内部的一个阶段。状态机与子图编排都用它组装。"""

    name: str = "step"
    #: 该阶段是否允许模型介入。False 表示纯确定性代码。
    delegates_to_model: bool = False

    @abstractmethod
    def execute(self, task: Task, ctx: "RunContext") -> None:
        """就地推进 ctx。抛异常即视为该阶段失败，由失败契约接管。"""


# ═══════════════════════════════════════════════════════════
#  ② 接入层
# ═══════════════════════════════════════════════════════════

class Connector(ABC):
    """外部系统连接器。

    刻意与 Agent 工具循环解耦：确定性阶段需要直接调用外部系统，
    不能被迫经过模型的 tool loop。
    """

    scheme: str = "connector"

    def __init__(self, name: str, **config: Any) -> None:
        self.name = name
        self.config = config
        self._tools: Optional[Dict[str, Dict[str, Any]]] = None

    @abstractmethod
    def _discover(self) -> Dict[str, Dict[str, Any]]:
        """返回 {工具名: 工具描述}。"""

    @abstractmethod
    def _invoke(self, tool: str, args: Dict[str, Any]) -> Any:
        """真正发起调用。"""

    def tools(self) -> Dict[str, Dict[str, Any]]:
        if self._tools is None:
            self._tools = self._discover()
        return self._tools

    def resolve(self, preferred: Iterable[str], keywords: Iterable[str] = ()) -> str:
        """按候选名解析工具，失配时退化为关键词匹配。

        上游 MCP server 升级经常改工具名（issues / issues.search /
        issues_search 都见过），接入层不硬编码单一名字。
        """
        available = self.tools()
        for cand in preferred:
            if cand in available:
                return cand
        kws = [k.lower() for k in keywords]
        if kws:
            for tool_name in available:
                if all(k in tool_name.lower() for k in kws):
                    return tool_name
        raise LookupError(
            f"连接器 {self.name} 未找到匹配工具。"
            f"候选={list(preferred)} 关键词={list(keywords)} "
            f"可用={sorted(available)}"
        )

    def call(self, tool: str, args: Optional[Dict[str, Any]] = None) -> Any:
        return self._invoke(tool, args or {})

    def close(self) -> None:  # pragma: no cover - 子类按需覆写
        pass


# ═══════════════════════════════════════════════════════════
#  ③ 知识注入
# ═══════════════════════════════════════════════════════════

class InjectionPoint(str, Enum):
    """知识注入的时机。

    时机比内容更重要。同一份规范注入在决策层还是执行器侧，
    决定了底盘与业务解不解耦：

      AGENT_BOOT      决策层的 system prompt。底盘刻意保持这里为空，
                      Agent 不该知道 ABP 或 Angular 是什么。
      TASK_ADMITTED   任务准入之后，用于补充任务级背景。
      BEFORE_TOOL     每次工具调用之前，用于约束单次调用。
      BEFORE_EXECUTOR 调用外部执行器之前的最后一步。生产实际用的就是这里。
      ON_RETRY        重试之前，用于把上一次的失败原因回灌。
      BEFORE_VERDICT  裁定之前，用于补充判定所需的口径说明。
    """
    AGENT_BOOT = "agent_boot"
    TASK_ADMITTED = "task_admitted"
    BEFORE_TOOL = "before_tool"
    BEFORE_EXECUTOR = "before_executor"
    ON_RETRY = "on_retry"
    BEFORE_VERDICT = "before_verdict"


@dataclass
class Injection:
    """一次实际发生的注入，用于时机可视化与审计。"""
    point: InjectionPoint
    provider: str
    label: str
    chars: int
    at_ms: int
    content_hash: str = ""
    version: str = "unversioned"


@dataclass(frozen=True)
class ContextChunk:
    """任务内的不可变知识片段；正文只在内存中供明确的消费者读取。"""
    point: InjectionPoint
    provider: str
    label: str
    content_hash: str
    text: str = field(repr=False)
    version: str = "unversioned"


@dataclass(frozen=True)
class ContextReceipt:
    """本地上下文读取回执，不等于远端模型已经接受了请求。"""
    consumer: str
    points: Tuple[str, ...]
    included: Tuple[str, ...]
    omitted: Tuple[str, ...]
    chars: int
    at_ms: int


class KnowledgeProvider(ABC):
    """知识提供者。

    每个实现声明自己在哪些时机生效，底盘在对应时机统一调度，
    并把实际发生的注入记录下来。
    """

    name: str = "knowledge"
    points: List[InjectionPoint] = []

    @abstractmethod
    def provide(self, point: InjectionPoint, task: Task, ctx: "RunContext") -> Optional[str]:
        """返回要注入的文本，返回 None 表示本次不注入。"""

    def label_for(self, point: InjectionPoint, task: Task, ctx: "RunContext") -> str:
        """给这次注入起个短名字，用于时机表展示。"""
        return self.name


# ═══════════════════════════════════════════════════════════
#  ④ 失败契约
# ═══════════════════════════════════════════════════════════

class FailurePolicy(ABC):
    """失败契约：失败之后系统留下什么。

    底盘的默认要求是零副作用：落库标记、去重防重试、干净退出、
    不阻塞下一条。实现可以在此基础上加重试或降级。
    """

    name: str = "failure-policy"

    @abstractmethod
    def on_failure(self, task: Task, error: str, ctx: "RunContext") -> Outcome:
        """处理一次失败，返回 FAILED 或 SKIPPED；不能替代验收器宣告成功。"""

    @abstractmethod
    def seen(self, key: str) -> bool:
        """该任务是否已处理过或已失败过。"""

    @abstractmethod
    def remember(self, key: str, outcome: Outcome, detail: str = "") -> None:
        """记账。"""


# ═══════════════════════════════════════════════════════════
#  ⑤ 可观测与问责
# ═══════════════════════════════════════════════════════════

class Observer(ABC):
    """观察者：把执行过程变成可以事后追问的数据。

    数据模型刻意通用（task / trace / tool_call / health），
    不允许出现任何场景专属概念作为主键或核心结构。
    """

    name: str = "observer"

    def on_task_start(self, task: Task, ctx: "RunContext") -> None: ...
    def on_step(self, step: str, task: Task, ctx: "RunContext") -> None: ...
    def on_tool_call(self, call: ToolCall, task: Task, ctx: "RunContext") -> None: ...
    def on_injection(self, inj: Injection, task: Task, ctx: "RunContext") -> None: ...
    def on_task_end(self, result: TaskResult, ctx: "RunContext") -> None: ...


# ═══════════════════════════════════════════════════════════
#  运行时上下文
# ═══════════════════════════════════════════════════════════

@dataclass
class RunContext:
    """一次任务执行期间，所有插件共享的上下文。

    这是底盘唯一的可变状态容器。编排器就地推进它，
    观察者只读它，完成判据只允许读 facts 而不能读 model_notes。
    """
    run_id: str = field(default_factory=lambda: uuid.uuid4().hex[:12])
    started_at: float = field(default_factory=time.time)

    #: 确定性阶段采集到的客观事实，完成判据只能依据这里
    facts: Dict[str, Any] = field(default_factory=dict)
    #: 模型产生的自然语言，仅供人类阅读与复盘，不参与任何判定
    model_notes: List[str] = field(default_factory=list)

    tool_calls: List[ToolCall] = field(default_factory=list)
    injections: List[Injection] = field(default_factory=list)
    steps: List[str] = field(default_factory=list)
    iterations: int = 0

    #: 由 Chassis 注入，插件通过它访问其余能力
    chassis: Any = None

    #: 最近一次同一时机的快照；知识不是事实，不能混入 facts。
    knowledge: Dict[InjectionPoint, Tuple[ContextChunk, ...]] = field(default_factory=dict)
    context_receipts: List[ContextReceipt] = field(default_factory=list)
    #: 可选模型 adapter 的用量/时延元数据，不记录 Prompt 或隐藏思维。
    model_calls: List[Dict[str, Any]] = field(default_factory=list)

    def context_for(
        self,
        consumer: str,
        points: Sequence[InjectionPoint],
        max_chars: Optional[int] = None,
    ) -> str:
        """按调用方给定优先级装配完整片段，并记录预算省略项。

        字符预算不是 token 预算。只读取指定时机，不自动扩大知识作用域；
        调用方必须把返回值传给实际执行器，回执本身不证明远端执行成功。
        """
        if not consumer or (max_chars is not None and max_chars < 0):
            raise ValueError("consumer 不能为空，max_chars 不能为负")
        selected: List[str] = []
        included: List[str] = []
        omitted: List[str] = []
        chars = 0
        ordered = list(dict.fromkeys(points))
        for point in ordered:
            for chunk in self.knowledge.get(point, ()):
                size = len(chunk.text) + (2 if selected else 0)
                if max_chars is not None and chars + size > max_chars:
                    omitted.append(chunk.content_hash)
                    continue
                selected.append(chunk.text)
                included.append(chunk.content_hash)
                chars += size
        self.context_receipts.append(ContextReceipt(
            consumer, tuple(p.value for p in ordered), tuple(included),
            tuple(omitted), chars, self.ms(),
        ))
        return "\n\n".join(selected)

    def ms(self) -> int:
        return int((time.time() - self.started_at) * 1000)

    def note(self, text: str) -> None:
        self.model_notes.append(text)

    def record_step(self, name: str) -> None:
        self.steps.append(name)


# ═══════════════════════════════════════════════════════════
#  通用注册表
# ═══════════════════════════════════════════════════════════

class Registry:
    """按名字注册与构造插件。可插拔的实现基础。"""

    def __init__(self, kind: str) -> None:
        self.kind = kind
        self._factories: Dict[str, Callable[..., Any]] = {}

    def register(self, key: str) -> Callable[[Callable[..., Any]], Callable[..., Any]]:
        def deco(factory: Callable[..., Any]) -> Callable[..., Any]:
            if key in self._factories:
                raise ValueError(f"{self.kind} 已存在同名实现: {key}")
            self._factories[key] = factory
            return factory
        return deco

    def add(self, key: str, factory: Callable[..., Any]) -> None:
        self._factories[key] = factory

    # 首参用 key 而不是 name，避免与被构造对象自己的 name 参数冲突
    def build(self, key: str, **kwargs: Any) -> Any:
        if key not in self._factories:
            raise KeyError(
                f"未注册的 {self.kind}: {key!r}。已注册: {sorted(self._factories)}"
            )
        return self._factories[key](**kwargs)

    def names(self) -> List[str]:
        return sorted(self._factories)

    def __contains__(self, key: object) -> bool:
        return key in self._factories
