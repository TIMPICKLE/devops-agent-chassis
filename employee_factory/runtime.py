"""Services supplied to generated employees; contains no workflow definition."""
from __future__ import annotations

from agent_chassis import InjectionPoint as P, borrowed_executor
from agent_chassis.evidence import EvidenceObserver, content_id
from agent_chassis.knowledge import ScopedKnowledge
from agent_chassis.orchestration import ReActPattern
from adapters.anthropic_runtime import AnthropicDecider
from adapters.openai_runtime import OpenAIChatDecider
from adapters.runtime import RuntimeDecider
from employee_factory.workspace import CompilerCriteria, WorkspaceSource, workspace_tools
from payloads.patch_showcase import Candidate, submission_ready


class EmployeeKit:
    """The AI chooses node composition in employee.py using these shared services."""

    def __init__(self, snapshot, baseline, frozen, config, *, protocol="anthropic", code_ref="unknown", decider=None):
        self.source = WorkspaceSource(snapshot, baseline)
        self.candidate = Candidate(snapshot["files"][snapshot["unit"]], snapshot["unit"])
        self.criteria = CompilerCriteria(snapshot, self.candidate, baseline)
        self.boundary = borrowed_executor("workspace-candidate-editor")
        self.toolbox = workspace_tools(snapshot, self.candidate, self.criteria, self.boundary)
        if decider is None:
            decider = (AnthropicDecider if protocol == "anthropic" else OpenAIChatDecider)(config, tool_names=self.toolbox.names())
        self.mode = decider.execution_mode if isinstance(decider, RuntimeDecider) else "test-decider"
        self.pattern = ReActPattern(decider, **config.react_options(),
                                   stop_when=lambda t, c: submission_ready(self.candidate, c))
        self.providers = [ScopedKnowledge(text, name=name, version=content_id(text), fact_scope={"phase": "repair"})
                          for name, text in frozen["documents"].items()]
        self.observer = EvidenceObserver(code_ref=code_ref, mode=self.mode)

    def prepare(self, task, ctx):
        if self.source.baseline["exit_code"] == 0:
            raise ValueError("No failing compilation to repair")
        ctx.facts["baseline_compiler_exit"] = self.source.baseline["exit_code"]
        ctx.facts["phase"] = "repair"

    def verify(self, task, ctx):
        ctx.facts["phase"] = "verify"
        ctx.chassis.inject(P.BEFORE_EXECUTOR, task, ctx)
        verdict = self.criteria.validate()
        if verdict.done:
            ctx.facts["verified_candidate"] = self.candidate.digests()["candidate_sha256"]
