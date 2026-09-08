"""
Agent Chassis —— DevOps 数字员工工程底盘。

    from agent_chassis import Chassis, InjectionPoint
    from agent_chassis.orchestration import NestedOrchestrator
    from agent_chassis.permissions import borrowed_executor

底盘负责与业务无关的五件事，载荷负责与业务有关的两件事。
详见 README.md 与 examples/。
"""
from .chassis import BuildReport, Chassis, ChassisError
from .contracts import (
    Connector,
    ContextChunk,
    ContextReceipt,
    DoneCriteria,
    Injection,
    InjectionPoint,
    KnowledgeProvider,
    Observer,
    Orchestrator,
    Outcome,
    ReasoningPattern,
    RunContext,
    Registry,
    Step,
    Task,
    TaskResult,
    TaskSource,
    ToolCall,
    Verdict,
)
from .failure import Ledger, RetryThenGiveUpPolicy, WorkspaceGuard, ZeroSideEffectPolicy
from .integration import ConnectorManager, connector_registry
from .knowledge import (
    InjectionScheduler,
    SkillLibrary,
    SkillProvider,
    StaticKnowledge,
    RetryFeedback,
    by_extension,
    by_filename_markers,
)
from .observability import ConsoleObserver, RecordingObserver
from .evidence import EvidenceObserver, assembly_manifest
from .orchestration import (
    BasicReflectionPattern,
    LLMCompilerPattern,
    NestedOrchestrator,
    PlanAndSolvePattern,
    PlanExecutePattern,
    PlanNode,
    ReActPattern,
    ReWOOPattern,
    ReflexionPattern,
    SingleAgentOrchestrator,
    StateMachineOrchestrator,
    SubgraphOrchestrator,
    orchestrator_registry,
    reasoning_registry,
)
from .permissions import PermissionBoundary, PermissionDenied, borrowed_executor

__version__ = "0.1.0"

__all__ = [
    "BasicReflectionPattern",
    "Chassis",
    "ChassisError",
    "BuildReport",
    "Connector",
    "ContextChunk",
    "ContextReceipt",
    "EvidenceObserver",
    "assembly_manifest",
    "ConnectorManager",
    "ConsoleObserver",
    "DoneCriteria",
    "Injection",
    "InjectionPoint",
    "InjectionScheduler",
    "KnowledgeProvider",
    "Ledger",
    "LLMCompilerPattern",
    "NestedOrchestrator",
    "Observer",
    "Orchestrator",
    "Outcome",
    "PermissionBoundary",
    "PermissionDenied",
    "PlanAndSolvePattern",
    "PlanExecutePattern",
    "PlanNode",
    "ReActPattern",
    "ReWOOPattern",
    "ReasoningPattern",
    "RecordingObserver",
    "ReflexionPattern",
    "Registry",
    "RetryFeedback",
    "SingleAgentOrchestrator",
    "StateMachineOrchestrator",
    "SubgraphOrchestrator",
    "RetryThenGiveUpPolicy",
    "RunContext",
    "SkillLibrary",
    "SkillProvider",
    "StaticKnowledge",
    "Step",
    "Task",
    "TaskResult",
    "TaskSource",
    "ToolCall",
    "Verdict",
    "WorkspaceGuard",
    "ZeroSideEffectPolicy",
    "borrowed_executor",
    "by_extension",
    "by_filename_markers",
    "connector_registry",
    "orchestrator_registry",
    "reasoning_registry",
    "__version__",
]
