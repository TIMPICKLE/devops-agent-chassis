import json
import shutil

import pytest

pytest.importorskip("jsonschema")

from employee_factory.updates import apply_update, preview_update, project_files
from employee_factory.verification import verify_project
from test_employee_factory import REQUEST, build_test_project
from tools.update_employee import main
from tools.employee_update_demo import CASES, check_include_policy, prepare_requests, prepare_working, verify_demo
from tools.run_generated_employee import execute
from adapters.anthropic_runtime import AnthropicDecider


def inputs(tmp_path, monkeypatch):
    base, config = build_test_project(tmp_path, monkeypatch)
    working = tmp_path / "working"
    shutil.copytree(base, working)
    request = tmp_path / "new-request"
    shutil.copytree(REQUEST, request)
    path = request / "knowledge/include_rules.md"
    path.write_text(path.read_text() + "\n新增：支付任务使用新接口头文件。\n")
    return base, working, request, config


def test_preview_and_apply_preserve_custom_code_and_files_without_model(tmp_path, monkeypatch):
    base, working, request, _ = inputs(tmp_path, monkeypatch)
    script = working / "employee.py"
    script.write_text(script.read_text().replace("def build_employee(kit):", "def build_employee(kit):\n    print('team customization')"))
    (working / "notes.txt").write_text("Human runbook\n")
    (working / "custom.bin").write_bytes(bytes([0, 255, 1]))
    before = project_files(working)
    plan = preview_update(base, working, request)
    assert plan["status"] == "ready" and plan["model_calls"] == 0
    assert [n["name"] for n in plan["affected_nodes"]] == ["repair_includes", "check_candidate"]
    assert {x["path"] for x in plan["preserved_user_changes"]} == {"employee.py", "notes.txt", "custom.bin"}
    output = tmp_path / "updated"
    project = apply_update(base, working, request, output, expected_plan_id=plan["content_id"])
    assert project["runtime_verified"] is False
    verify_project(output)
    with pytest.raises(ValueError, match="Not live"):
        verify_project(output, require_live=True)
    assert (output / "employee.py").read_bytes() == script.read_bytes()
    assert (output / "custom.bin").read_bytes() == bytes([0, 255, 1])
    assert project_files(working) == before
    verify_project(base)
    (output / "notes.txt").write_text("Changed after revision")
    with pytest.raises(ValueError, match="preserve"):
        verify_project(output)


@pytest.mark.parametrize("change,kind", [("knowledge", "knowledge_merge_conflict"),
                                       ("metadata", "managed_file_changed"), ("workflow", "workflow_change")])
def test_conflicts_do_not_overwrite_working_copy(tmp_path, monkeypatch, change, kind):
    base, working, request, _ = inputs(tmp_path, monkeypatch)
    if change == "knowledge":
        (working / "knowledge/include_rules.md").write_text("A third, conflicting version")
    elif change == "metadata":
        (working / "project.json").write_text("{}")
    else:
        spec = json.loads((working / "assembly.json").read_text())
        spec["nodes"][0]["name"] = "another_flow"
        (working / "assembly.json").write_text(json.dumps(spec))
    plan = preview_update(base, working, request)
    assert plan["status"] == "blocked"
    assert kind in [c["kind"] for c in plan["conflicts"]]
    output = tmp_path / "output"
    with pytest.raises(ValueError, match="not ready"):
        apply_update(base, working, request, output, expected_plan_id=plan["content_id"])
    assert not output.exists()


def test_stale_preview_and_changed_business_requirements_are_explicit(tmp_path, monkeypatch):
    base, working, request, _ = inputs(tmp_path, monkeypatch)
    plan = preview_update(base, working, request)
    (working / "new-note.md").write_text("Edit after preview")
    with pytest.raises(ValueError, match="stale"):
        apply_update(base, working, request, tmp_path / "output", expected_plan_id=plan["content_id"])
    req = json.loads((request / "request.json").read_text())
    req["brief"] = "Also fix Python files"
    (request / "request.json").write_text(json.dumps(req))
    plan = preview_update(base, working, request)
    assert plan["status"] == "blocked" and plan["conflicts"][0]["fields"] == ["brief"]
    assert not (tmp_path / "output").exists()


def test_update_cli_noop_preview_and_apply_new_version(tmp_path, monkeypatch, capsys):
    base, working, request, _ = inputs(tmp_path, monkeypatch)
    assert preview_update(base, working, REQUEST)["status"] == "no_change"
    args = ["--project", str(base), "--working-copy", str(working), "--request-dir", str(request),
            "--plan", str(tmp_path / "plan.json")]
    assert main(args) == 0
    assert (tmp_path / "plan.md").exists()
    assert main(args + ["--apply", "--output-dir", str(tmp_path / "output")]) == 0
    assert main(args) == 2  # never overwrite a preview
    assert main(args + ["--apply", "--output-dir", str(tmp_path / "output")]) == 2
    assert '"model_calls": 0' in capsys.readouterr().out


def test_revision_can_be_updated_again_with_archived_lineage(tmp_path, monkeypatch):
    base, working, request, _ = inputs(tmp_path, monkeypatch)
    first = tmp_path / "first"
    plan = preview_update(base, working, request)
    apply_update(base, working, request, first, expected_plan_id=plan["content_id"])
    text = request / "knowledge/include_rules.md"
    text.write_text(text.read_text() + "Another revision\n")
    second = tmp_path / "second"
    plan = preview_update(first, first, request)
    apply_update(first, first, request, second, expected_plan_id=plan["content_id"])
    verify_project(second)
    assert (second / ".chassis/parent/.chassis/parent/project.json").exists()


@pytest.mark.parametrize("version", ["v1", "v2"])
def test_compiler_alone_cannot_accept_wrong_policy_version(version):
    from test_employee_factory import FIXTURES
    from employee_factory.workspace import compile_snapshot, load_workspace
    snapshot = load_workspace(FIXTURES / "billing", "app/billing.cpp")
    candidate = snapshot["files"][snapshot["unit"]].replace('"invoice.hpp"', f'"include/contracts/{version}/invoice.hpp"')
    assert compile_snapshot(snapshot, candidate)["exit_code"] == 0
    other = "v2" if version == "v1" else "v1"
    with pytest.raises(ValueError, match="policy"):
        check_include_policy(candidate, f"include/contracts/{other}/")


def test_updated_generated_employee_runs_changed_policy_and_old_tasks(tmp_path, monkeypatch):
    from test_employee_factory import FIXTURES
    output = tmp_path / "evolution"
    prepare_requests(output)
    base, config = build_test_project(tmp_path, monkeypatch, output / "requests/v1")
    shutil.copytree(base, output / "base")
    prepare_working(output, require_live=False)
    plan = preview_update(output / "base", output / "working", output / "requests/v2")
    apply_update(output / "base", output / "working", output / "requests/v2", output / "updated",
                 expected_plan_id=plan["content_id"])
    observed_contexts = {}
    for label, project, repo, unit, prefix in CASES:
        requests = []
        def transport(url, headers, body, timeout):
            current = json.loads(body["messages"][-1]["content"])
            requests.append(current)
            if len(requests) == 1:
                name = "read_files"
                args = {"paths": [p for p in current["task"]["files"] if p.endswith(".hpp")]}
            else:
                path = next(p for p in current["task"]["files"] if p.startswith(prefix))
                lines = current["task"]["source"].splitlines(keepends=True)
                lines[0] = '#include "' + path + '"\n'
                name, args = "submit_source", {"content": "".join(lines)}
            return {"stop_reason": "tool_use", "content": [{"type": "tool_use", "name": name, "input": args}],
                    "usage": {"input_tokens": 100, "output_tokens": 30}}
        decider = AnthropicDecider(config, tool_names=["read_files", "submit_source"], transport=transport)
        report = output / label
        report.mkdir()
        assert execute(output / project, FIXTURES / repo, unit, report, config,
                       decider=decider, require_live=False)["accepted"]
        observed_contexts[label] = requests[0]["context"]
    summary = verify_demo(output, require_live=False)
    assert summary["behavior_changed_on_same_input"] and summary["old_tasks_passed"] == 2
    assert summary["update_model_calls"] == 0
    assert "尚未启用" in observed_contexts["old-billing"]
    assert "已停止使用" in observed_contexts["new-billing"]
    with pytest.raises(ValueError, match="Not live"):
        verify_demo(output, require_live=True)
