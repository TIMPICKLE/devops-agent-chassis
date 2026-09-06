"""External checks: generated code never defines or edits this acceptance gate."""
from __future__ import annotations

import json
import hashlib
from pathlib import Path

from agent_chassis.evidence import content_id
from employee_factory.contracts import validate_project
from employee_factory.generation import RequestSource
from employee_factory.workspace import CompilerCriteria, WorkspaceSource, compile_snapshot, load_workspace
from payloads.patch_showcase import Candidate
from tools.verify_roadmap_evidence import _check_id, verify_documents


def read_json(path):
    return json.loads(path.read_text(encoding="utf-8"))


def verify_project(directory, *, require_live=False, _depth=0):
    if _depth > 10:
        raise ValueError("Reference verifier supports at most 10 project revisions")
    frozen = read_json(directory / "request.snapshot.json")
    project = read_json(directory / "project.json")
    _check_id(frozen)
    _check_id(project)
    script = (directory / "employee.py").read_text(encoding="utf-8")
    readme = (directory / "README.md").read_text(encoding="utf-8")
    manifest = read_json(directory / "assembly.json")
    ids = validate_project(script, manifest, readme, frozen)
    if any(project.get(k) != v for k, v in ids.items()) or project["request_id"] != frozen["content_id"]:
        raise ValueError("Generated files no longer match their project record")
    for name, text in frozen["documents"].items():
        if (directory / name).read_text(encoding="utf-8") != text:
            raise ValueError("Knowledge changed since generation; regenerate/version the project")
    if "kind" in project:
        from employee_factory.updates import verify_revision
        return verify_revision(directory, frozen, project, manifest, require_live=require_live, depth=_depth)
    assembly = read_json(directory / "generation.manifest.json")
    evidence = read_json(directory / "generation.evidence.json")
    verify_documents(assembly, evidence, require_live=require_live)
    if len(evidence["runs"]) != 1:
        raise ValueError("Expected one generation run")
    run = evidence["runs"][0]
    if (run["outcome"] != "succeeded" or run["verdict"]["evidence"] != ids
            or run["input_id"] != content_id(RequestSource(frozen).task.payload)
            or run["code_ref"] != project["code_ref"] or assembly["runtime"]["phase"] != "generation"):
        raise ValueError("Project is not linked to the accepting generation run")
    return frozen, project, manifest


def verify_runtime(project_dir, report_dir, *, repo=None, require_live=False):
    frozen, project, spec = verify_project(project_dir, require_live=require_live)
    snapshot = read_json(report_dir / "workspace.snapshot.json")
    baseline = compile_snapshot(snapshot)
    if baseline["exit_code"] == 0:
        raise ValueError("The original input did not reproduce a failing compilation")
    if repo is not None and load_workspace(repo, snapshot["unit"]) != snapshot:
        raise ValueError("Input repository changed during the employee run")
    manifest, evidence = read_json(report_dir / "runtime.manifest.json"), read_json(report_dir / "runtime.evidence.json")
    verify_documents(manifest, evidence, require_live=require_live)
    if len(evidence["runs"]) != 1:
        raise ValueError("Expected one employee task")
    run = evidence["runs"][0]
    if run["outcome"] != "succeeded" or run["verdict"] is None or not run["verdict"]["done"]:
        raise ValueError("Employee task did not pass its final verdict: " + str(run.get("error", "")))
    source = WorkspaceSource(snapshot, baseline)
    if (run["input_id"] != content_id(source.task.payload) or manifest["runtime"]["project_id"] != project["content_id"]
            or run["steps"] != [n["name"] for n in spec["nodes"]]):
        raise ValueError("Runtime input, project link or observed nodes do not match")
    candidate = Candidate(snapshot["files"][snapshot["unit"]], snapshot["unit"],
                          (report_dir / "candidate.cpp").read_text(encoding="utf-8"))
    actual = CompilerCriteria(snapshot, candidate, baseline).validate()
    if not actual.done or actual.evidence != run["verdict"]["evidence"]:
        raise ValueError("Saved source failed independent compiler or unchanged-body checks")
    if (report_dir / "candidate.patch").read_text(encoding="utf-8") != candidate.patch():
        raise ValueError("Saved patch does not describe the candidate")
    expected_providers = set(frozen["documents"])
    actual_providers = {i["provider"] for i in run["injections"]}
    if actual_providers != expected_providers or not run["context_receipts"]:
        raise ValueError("Declared project knowledge was not injected/consumed")
    for injection in run["injections"]:
        name = injection["provider"]
        text = frozen["documents"][name]
        expected = f"### {name} @ {content_id(text)}\n\n{text}"
        if (injection["content_hash"] != hashlib.sha256(expected.encode("utf-8")).hexdigest()
                or injection["version"] != content_id(text) or injection["point"] != "before_executor"):
            raise ValueError("Knowledge text, version or injection point differs from the generated project")
    included = {h for receipt in run["context_receipts"] for h in receipt["included"]}
    if not all(i["content_hash"] in included for i in run["injections"]):
        raise ValueError("Required knowledge was omitted from model context")
    return {"accepted": True, "project_id": project["content_id"], "task_key": run["task_key"],
            "workspace_id": content_id(snapshot), "nodes": run["steps"], "mode": run["mode"],
            "model_calls": len(run["model_calls"]), "usage": run["usage"],
            "compiler_exit": actual.evidence["compiler_exit"], "original_unchanged": True if repo is not None else None}
