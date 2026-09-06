import json
from pathlib import Path

import pytest

pytest.importorskip("jsonschema")

from adapters.anthropic_runtime import AnthropicDecider
from adapters.runtime import ModelConfig
from agent_chassis.evidence import content_id
from employee_factory.contracts import NeedsInput, VERSION, load_request, validate_project
from employee_factory.generation import generate
from employee_factory.verification import verify_project, verify_runtime
from employee_factory.workspace import CompilerCriteria, compile_snapshot, load_workspace
from payloads.patch_showcase import Candidate
from tools.assemble_employee import main as assemble_main
from tools.run_generated_employee import execute
from tools.run_roadmap_showcase import write_json

ROOT = Path(__file__).resolve().parents[1]
REQUEST = ROOT / "employee_requests/build_repair"
FIXTURES = ROOT / "tests/employee_projects"
SCRIPT = '''from agent_chassis import Chassis
from agent_chassis.orchestration import StateMachineOrchestrator, FnStep, AgentStep
def build_employee(kit):
    flow = StateMachineOrchestrator([
        FnStep("inspect_build", kit.prepare),
        AgentStep("repair_includes", pattern=kit.pattern, toolbox=kit.toolbox),
        FnStep("check_candidate", kit.verify),
    ])
    return (Chassis("cpp-build-repair-employee").with_payload(kit.source, kit.criteria)
        .with_orchestrator(flow).with_boundary(kit.boundary)
        .with_knowledge(*kit.providers).observe(kit.observer).build())
'''


def spec(frozen):
    return {"schema_version": VERSION, "name": frozen["request"]["name"],
            "nodes": [{"name": n, "role": r, "purpose": n} for n, r in
                      [("inspect_build", "prepare"), ("repair_includes", "model"), ("check_candidate", "verify")]],
            "knowledge_bindings": [{"document": p, "point": "before_executor", "phase": "repair"}
                                   for p in frozen["documents"]]}


def test_missing_materials_make_no_model_request_or_project(tmp_path, capsys):
    output = tmp_path / "employee"
    assert assemble_main(["--request-dir", str(tmp_path), "--output-dir", str(output)]) == 2
    result = json.loads(capsys.readouterr().out)
    assert result["status"] == "needs_input" and result["model_calls"] == 0
    assert not output.exists()
    write_json(tmp_path / "request.json", {"name": "x", "brief": "build repair"})
    with pytest.raises(NeedsInput) as exc:
        load_request(tmp_path)
    assert "done_criteria" in exc.value.fields


def test_project_contract_rejects_missing_factory_and_incomplete_knowledge_binding():
    frozen = load_request(REQUEST)
    manifest = spec(frozen)
    assert validate_project(SCRIPT, manifest, "usage", frozen)["script_id"] == content_id(SCRIPT)
    with pytest.raises(ValueError, match="build_employee"):
        validate_project("print('done')", manifest, "usage", frozen)
    manifest["knowledge_bindings"] = []
    with pytest.raises(ValueError, match="Bind every"):
        validate_project(SCRIPT, manifest, "usage", frozen)


def build_test_project(tmp_path, monkeypatch):
    monkeypatch.setenv("EMPLOYEE_TEST_KEY", "test-only")
    frozen = load_request(REQUEST)
    config = ModelConfig("test", api_key_env="EMPLOYEE_TEST_KEY", max_calls=6)
    generation_requests = []
    def transport(url, headers, body, timeout):
        generation_requests.append(json.loads(body["messages"][-1]["content"]))
        return {"stop_reason": "tool_use", "content": [{"type": "tool_use", "name": "submit_project",
                "input": {"script": SCRIPT, "manifest": spec(frozen), "readme": "Run using the shared CLI"}}],
                "usage": {"input_tokens": 100, "output_tokens": 200}}
    decider = AnthropicDecider(config, tool_names=["submit_project"], transport=transport)
    candidate, evidence, manifest, result = generate(frozen, config, decider=decider, code_ref="test-source")
    assert result.outcome.value == "succeeded"
    assert len(generation_requests) == 1
    request_text = json.dumps(generation_requests[0])
    assert "tax_for" not in request_text and "scheduler_ready" not in request_text
    project_dir = tmp_path / "project"
    project_dir.mkdir()
    write_json(project_dir / "request.snapshot.json", frozen)
    write_json(project_dir / "generation.evidence.json", evidence)
    write_json(project_dir / "generation.manifest.json", manifest)
    write_json(project_dir / "assembly.json", candidate["manifest"])
    (project_dir / "employee.py").write_text(candidate["script"])
    (project_dir / "README.md").write_text(candidate["readme"])
    for name, text in frozen["documents"].items():
        dest = project_dir / name
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_text(text)
    project = {"request_id": frozen["content_id"], "code_ref": "test-source", "runtime_verified": False,
               **validate_project(candidate["script"], candidate["manifest"], candidate["readme"], frozen)}
    project["content_id"] = content_id(project)
    write_json(project_dir / "project.json", project)
    verify_project(project_dir)
    with pytest.raises(ValueError, match="Not live"):
        verify_project(project_dir, require_live=True)
    return project_dir, config


@pytest.mark.parametrize("case,unit,header", [("pricing", "src/main.cpp", "include/pricing/tax.hpp"),
                                             ("scheduler", "app/worker.cpp", "include/core/api.hpp")])
def test_generated_code_runs_real_files_compiler_and_model_adapter(tmp_path, monkeypatch, case, unit, header):
    project, config = build_test_project(tmp_path, monkeypatch)
    original = load_workspace(FIXTURES / case, unit)
    assert compile_snapshot(original)["exit_code"] != 0
    requests = []
    def transport(url, headers, body, timeout):
        current = json.loads(body["messages"][-1]["content"])
        requests.append(current)
        if len(requests) == 1:
            name, arguments = "read_file", {"path": header}
            if case == "scheduler":
                name, arguments = "read_files", {"paths": ["include/legacy/api.hpp", header, "missing.hpp"]}
        else:
            lines = current["task"]["source"].splitlines(keepends=True)
            lines[0] = '#include "' + header + '"\n'
            name, arguments = "submit_source", {"content": "".join(lines)}
        return {"stop_reason": "tool_use", "content": [{"type": "tool_use", "name": name, "input": arguments}],
                "usage": {"input_tokens": 50, "output_tokens": 20}}
    decider = AnthropicDecider(config, tool_names=["read_file", "read_files", "submit_source"], transport=transport)
    output = tmp_path / "run"
    output.mkdir()
    accepted = execute(project, FIXTURES / case, unit, output, config, decider=decider, require_live=False)
    assert accepted["accepted"] and accepted["model_calls"] == 2
    observed = requests[1]["observations"][0]["result"]
    if case == "scheduler":
        assert observed["files"][0]["content"] == original["files"]["include/legacy/api.hpp"]
        assert observed["files"][1]["content"] == original["files"][header]
        assert observed["files"][2] == {"found": False, "path": "missing.hpp"}
    else:
        assert observed["content"] == original["files"][header]
    assert requests[0]["task"]["build_error"]
    assert "项目引用规范" in requests[0]["context"]
    assert accepted["nodes"] == ["inspect_build", "repair_includes", "check_candidate"]
    assert load_workspace(FIXTURES / case, unit) == original
    assert verify_runtime(project, output, repo=FIXTURES / case)["accepted"]
    (output / "candidate.cpp").write_text("int main() { return 0; }\n")
    with pytest.raises(ValueError, match="independent"):
        verify_runtime(project, output)


def test_compiler_success_cannot_replace_preserved_body_requirement():
    snapshot = load_workspace(FIXTURES / "pricing", "src/main.cpp")
    baseline = compile_snapshot(snapshot)
    candidate = Candidate(snapshot["files"][snapshot["unit"]], snapshot["unit"], "int main() { return 0; }\n")
    assert compile_snapshot(snapshot, candidate.content)["exit_code"] == 0
    assert not CompilerCriteria(snapshot, candidate, baseline).validate().done


def test_generated_project_changes_invalidate_provenance(tmp_path, monkeypatch):
    project, _ = build_test_project(tmp_path, monkeypatch)
    (project / "employee.py").write_text(SCRIPT + "\n# changed after generation\n")
    with pytest.raises(ValueError, match="no longer match"):
        verify_project(project)


def test_missing_unit_and_ambiguous_header_are_real_compiler_failures():
    with pytest.raises(ValueError, match="not found"):
        load_workspace(FIXTURES / "pricing", "missing.cpp")
    snapshot = load_workspace(FIXTURES / "scheduler", "app/worker.cpp")
    wrong = snapshot["files"][snapshot["unit"]].replace('"api.hpp"', '"include/legacy/api.hpp"')
    assert compile_snapshot(snapshot, wrong)["exit_code"] != 0


def test_protocol_failure_is_reported_before_incomplete_node_trace(tmp_path, monkeypatch):
    project, config = build_test_project(tmp_path, monkeypatch)
    def transport(url, headers, body, timeout):
        return {"stop_reason": "tool_use", "content": [
            {"type": "tool_use", "name": "read_file", "input": {"path": path}}
            for path in ("include/core/api.hpp", "include/legacy/api.hpp")],
            "usage": {"input_tokens": 30, "output_tokens": 20}}
    decider = AnthropicDecider(config, tool_names=["read_file"], transport=transport)
    output = tmp_path / "failed-run"
    output.mkdir()
    with pytest.raises(ValueError, match="final verdict: ModelError: Expected one tool call"):
        execute(project, FIXTURES / "scheduler", "app/worker.cpp", output, config,
                decider=decider, require_live=False)
    run = json.loads((output / "runtime.evidence.json").read_text())["runs"][0]
    assert run["outcome"] == "failed" and len(run["model_calls"]) == 1
    assert not (output / "acceptance.json").exists()
