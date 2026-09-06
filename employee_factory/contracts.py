"""Versioned request and generated-project contracts for the first product slice."""
from __future__ import annotations

import ast
import json
from pathlib import Path

from agent_chassis.evidence import content_id

VERSION = "employee-project/v1"


class NeedsInput(ValueError):
    def __init__(self, fields):
        self.fields = fields
        super().__init__("Required input: " + ", ".join(fields))


def load_request(directory):
    path = directory / "request.json"
    if not path.exists():
        raise NeedsInput(["request.json: name, brief, task_interface, done_criteria, knowledge"])
    request = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(request, dict):
        raise NeedsInput(["request.json must be an object"])
    missing = [key for key in ("name", "brief", "task_interface", "done_criteria", "knowledge") if not request.get(key)]
    if missing:
        raise NeedsInput(missing)
    if any(not isinstance(request[k], str) or not request[k].strip() for k in ("name", "brief")):
        raise NeedsInput(["name and brief must be nonempty text"])
    if request["task_interface"] != "local_cpp_translation_unit":
        raise ValueError("This first slice supports local_cpp_translation_unit")
    if request["done_criteria"] != "compiler_and_include_only":
        raise ValueError("Specify compiler_and_include_only acceptance")
    if not isinstance(request["knowledge"], list):
        raise NeedsInput(["knowledge must list document paths"])
    documents = {}
    for name in request["knowledge"]:
        if (not isinstance(name, str) or Path(name).is_absolute() or ".." in Path(name).parts
                or not name.startswith("knowledge/") or name in documents):
            raise ValueError("Knowledge paths must be relative to the request directory")
        file = directory / name
        if not file.is_file() or not file.read_text(encoding="utf-8").strip():
            raise NeedsInput([name])
        documents[name] = file.read_text(encoding="utf-8")
    frozen = {"schema_version": VERSION, "request": request, "documents": documents}
    frozen["content_id"] = content_id(frozen)
    return frozen


def validate_project(script, manifest, readme, frozen):
    if not all(isinstance(s, str) and s.strip() for s in (script, readme)):
        raise ValueError("Employee code and README must be nonempty")
    tree = ast.parse(script, filename="employee.py")
    compile(tree, "employee.py", "exec")
    factories = [n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == "build_employee"]
    if len(factories) != 1 or len(factories[0].args.args) != 1:
        raise ValueError("Define build_employee(kit) exactly once")
    if not isinstance(manifest, dict) or manifest.get("schema_version") != VERSION:
        raise ValueError("Invalid project schema_version")
    if manifest.get("name") != frozen["request"]["name"]:
        raise ValueError("Project name must match the request")
    nodes = manifest.get("nodes")
    if not isinstance(nodes, list) or not 3 <= len(nodes) <= 8:
        raise ValueError("Describe 3 to 8 workflow nodes")
    if any(not isinstance(n, dict) or not isinstance(n.get("name"), str) or not n["name"].strip()
           or n.get("role") not in ("prepare", "model", "verify") or not n.get("purpose") for n in nodes):
        raise ValueError("Each node needs a name, role and purpose")
    roles = [n["role"] for n in nodes]
    if roles[0] != "prepare" or roles[-1] != "verify" or roles.count("model") != 1:
        raise ValueError("Start with prepare, have one model node, finish with verify")
    if len({n["name"] for n in nodes}) != len(nodes):
        raise ValueError("Node names must be unique")
    expected = [{"document": name, "point": "before_executor", "phase": "repair"}
                for name in frozen["documents"]]
    if manifest.get("knowledge_bindings") != expected:
        raise ValueError("Bind every supplied document before executor in repair phase")
    return {"script_id": content_id(script), "manifest_id": content_id(manifest), "readme_id": content_id(readme)}


API_GUIDE = '''You are generating a maintainable employee project for Agent Chassis.
Write Python employee.py defining build_employee(kit) and returning a BUILT Chassis.
Use public imports:
  from agent_chassis import Chassis
  from agent_chassis.orchestration import StateMachineOrchestrator, FnStep, AgentStep
The runtime supplies kit; do not recreate a model client, source, validator or tools:
  kit.source: real filesystem TaskSource with actual compiler diagnostics
  kit.criteria: independent CompilerCriteria, authoritative completion criteria
  kit.toolbox: read_file(path) and submit_source(content); real project file tools
  kit.pattern: ReAct with a real Anthropic/OpenAI decider and objective stop
  kit.boundary: repo.read / repo.write capability boundary
  kit.providers: versioned knowledge providers, active in phase=repair
  kit.observer: evidence observer (attach it)
  kit.prepare(task, ctx): checks baseline failed and enters repair phase
  kit.verify(task, ctx): independently checks candidate, records verified digest
Keep employee.py under 50 lines and README under 400 words; submit the tool directly.
Choose descriptive node names and explain them in assembly.json. Assemble a linear
StateMachineOrchestrator(steps) where steps is a list containing FnStep(name, kit.prepare), one
AgentStep(name, pattern=kit.pattern, toolbox=kit.toolbox), and FnStep(name, kit.verify).
Connect Chassis(name).with_payload(kit.source, kit.criteria).with_orchestrator(flow)
.with_boundary(kit.boundary).with_knowledge(*kit.providers).observe(kit.observer).build().
Do not run tasks on import. Do not use fixtures, hard-coded repairs or mock deciders.
The host will run the generated factory in a new process on repositories that are
not included in this generation request. Runtime nodes must match the manifest.
assembly.json: schema_version="employee-project/v1", name from request, nodes as
[{name, role:"prepare"|"model"|"verify", purpose}], knowledge_bindings as
[{document: original supplied path, point:"before_executor", phase:"repair"}].
README: explain inputs, node responsibilities, knowledge binding, outputs, limitations,
and command: python tools/run_generated_employee.py --project PROJECT_DIR
--repo SOURCE_DIR --unit relative/path.cpp --output-dir NEW_REPORT_DIR .
The command runs from the Chassis checkout, installed with pip install -e ".[llm]".
This is a reference C++ include-repair employee; do not claim arbitrary code repair.
Submit employee.py text, assembly.json object and README.md text through submit_project.
'''
