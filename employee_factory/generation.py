"""Generate project code with the same runtime adapter used by employees."""
from __future__ import annotations

from dataclasses import asdict

from agent_chassis import Chassis, borrowed_executor
from agent_chassis.contracts import DoneCriteria, Task, TaskSource, Verdict
from agent_chassis.evidence import EvidenceObserver, assembly_manifest
from agent_chassis.orchestration import ReActPattern, SingleAgentOrchestrator, ToolBox
from adapters.anthropic_runtime import AnthropicDecider
from adapters.openai_runtime import OpenAIChatDecider
from adapters.runtime import RuntimeDecider
from employee_factory.contracts import API_GUIDE, validate_project


class RequestSource(TaskSource):
    name = "employee-requirements"

    def __init__(self, frozen):
        self.sent = False
        self.task = Task(frozen["content_id"][:16], "generate_employee_project", {
            "requirements": frozen["request"], "knowledge_documents": frozen["documents"],
            "sdk_contract": API_GUIDE,
        }, self.name)

    def fetch(self, limit=1):
        if self.sent or limit <= 0:
            return []
        self.sent = True
        return [self.task]


class ProjectCriteria(DoneCriteria):
    name = "generated-project-contract/v1"

    def __init__(self, candidate, frozen):
        self.candidate, self.frozen = candidate, frozen

    def judge(self, task, ctx):
        if not self.candidate:
            return Verdict(False, "No valid employee project submitted")
        ids = validate_project(**self.candidate, frozen=self.frozen)
        return Verdict(True, "Generated project is syntactically valid; runtime validation is still required", ids)


def generate(frozen, config, *, protocol="anthropic", code_ref="unknown", decider=None):
    candidate = {}
    def submit_project(script, manifest, readme):
        """Submit employee.py source, assembly.json object and README.md text."""
        try:
            ids = validate_project(script, manifest, readme, frozen)
        except (ValueError, SyntaxError, TypeError) as exc:
            return {"accepted": False, "reason": str(exc)[:800]}
        candidate.update(script=script, manifest=manifest, readme=readme)
        return {"accepted": True, **ids}

    toolbox = ToolBox().add("submit_project", submit_project, input_schema={
        "type": "object", "properties": {"script": {"type": "string", "maxLength": 20000},
            "manifest": {"type": "object"}, "readme": {"type": "string", "maxLength": 12000}},
        "required": ["script", "manifest", "readme"], "additionalProperties": False})
    if decider is None:
        decider = (AnthropicDecider if protocol == "anthropic" else OpenAIChatDecider)(config, tool_names=toolbox.names())
    mode = decider.execution_mode if isinstance(decider, RuntimeDecider) else "test-decider"
    observer = EvidenceObserver(code_ref=code_ref, mode=mode)
    pattern = ReActPattern(decider, max_iterations=config.max_calls,
                          stop_when=lambda t, c: "Valid project submitted" if candidate else None)
    chassis = (Chassis("employee-project-generator").with_payload(RequestSource(frozen), ProjectCriteria(candidate, frozen))
               .with_orchestrator(SingleAgentOrchestrator(toolbox, pattern)).with_boundary(borrowed_executor("project-writer"))
               .observe(observer).build())
    manifest = assembly_manifest(chassis.report(), runtime={"mode": mode, "phase": "generation",
        "request_id": frozen["content_id"], "protocol": protocol, "config": asdict(config)})
    observer.assembly_id = manifest["content_id"]
    try:
        result = chassis.run_once()
        return candidate, observer.snapshot(), manifest, result
    finally:
        chassis.close()
