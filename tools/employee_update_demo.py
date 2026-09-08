"""Reproducible knowledge migration acceptance; fixtures are public, not production data."""
from __future__ import annotations

import argparse
import json
import re
import shutil
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / "src"), str(ROOT)]

from employee_factory.updates import project_files, read_json, write_json
from employee_factory.contracts import load_request
from employee_factory.verification import verify_project, verify_runtime
from employee_factory.workspace import compile_snapshot, load_workspace

CUSTOMIZATION = '''
# Example of a team's manual startup customization; updater must preserve it.
_generated_build_employee = build_employee
def _team_build_employee(kit):
    print("TEAM_STARTUP: billing-ops")
    return _generated_build_employee(kit)
build_employee = _team_build_employee
'''
CASES = [("old-billing", "base", "billing", "app/billing.cpp", "include/contracts/v1/"),
         ("new-billing", "updated", "billing", "app/billing.cpp", "include/contracts/v2/"),
         ("regression-pricing", "updated", "pricing", "src/main.cpp", "include/pricing/"),
         ("regression-scheduler", "updated", "scheduler", "app/worker.cpp", "include/core/")]


def prepare_requests(output):
    output.mkdir(parents=True, exist_ok=False)
    source = ROOT / "employee_requests/build_repair"
    for version in ("v1", "v2"):
        dest = output / "requests" / version
        shutil.copytree(source, dest)
        policy = (ROOT / "employee_requests/build_repair_evolution" / (version + ".md")).read_text(encoding="utf-8")
        path = dest / "knowledge/include_rules.md"
        path.write_text(path.read_text(encoding="utf-8") + "\n" + policy, encoding="utf-8")


def prepare_working(output, *, require_live=True):
    base, working = output / "base", output / "working"
    verify_project(base, require_live=require_live)
    shutil.copytree(base, working, ignore=shutil.ignore_patterns("__pycache__"))
    path = working / "employee.py"
    path.write_text(path.read_text(encoding="utf-8") + CUSTOMIZATION, encoding="utf-8")
    (working / "TEAM_RUNBOOK.md").write_text("人工定制示例：值班组 billing-ops；上线前人工复核补丁。\n", encoding="utf-8")
    write_json(output / "working.before.json", project_files(working))


def check_include_policy(candidate, prefix):
    includes = re.findall(r'^\s*#\s*include\s*"([^"\n]+)"\s*$', candidate, flags=re.MULTILINE)
    # A compiling stale header must fail this business check, not just pass the compiler.
    if len(includes) != 1 or not includes[0].startswith(prefix):
        raise ValueError("Candidate violates the independently specified include-version policy")
    return includes[0]


def verify_demo(output, *, require_live=True):
    old, parent, _ = verify_project(output / "base", require_live=require_live)
    new, revision_project, _ = verify_project(output / "updated", require_live=require_live)
    revision = read_json(output / "updated/revision.json")
    if (old != load_request(output / "requests/v1") or new != load_request(output / "requests/v2")
            or revision_project["parent_project_id"] != parent["content_id"]
            or old["content_id"] == new["content_id"]
            or revision["working_files"] != read_json(output / "working.before.json")
            or project_files(output / "working") != revision["working_files"]
            or (output / "working/employee.py").read_bytes() != (output / "updated/employee.py").read_bytes()):
        raise ValueError("Update did not preserve the original working project and its manual customization")
    results = []
    for label, project, repo, unit, prefix in CASES:
        report = output / label
        accepted = verify_runtime(output / project, report, repo=ROOT / "tests/employee_projects" / repo,
                                  require_live=require_live)
        expected = load_workspace(ROOT / "tests/employee_projects" / repo, unit)
        if read_json(report / "workspace.snapshot.json") != expected:
            raise ValueError("Report does not match the frozen demo input")
        candidate = (report / "candidate.cpp").read_text(encoding="utf-8")
        selected = check_include_policy(candidate, prefix)
        results.append({"case": label, "selected_header": selected, **accepted})
    if results[0]["workspace_id"] != results[1]["workspace_id"] or results[0]["selected_header"] == results[1]["selected_header"]:
        raise ValueError("Expected changed behavior on exactly the same source input")
    # Establish that compilation alone cannot discriminate the version choice.
    snapshot = load_workspace(ROOT / "tests/employee_projects/billing", "app/billing.cpp")
    for version in ("v1", "v2"):
        candidate = snapshot["files"][snapshot["unit"]].replace('"invoice.hpp"', f'"include/contracts/{version}/invoice.hpp"')
        if compile_snapshot(snapshot, candidate)["exit_code"] != 0:
            raise ValueError("Both header choices must compile for this policy test to be meaningful")
    generation = read_json(output / "base/generation.evidence.json")["runs"][0]
    summary = {"accepted": True, "code_ref": revision_project["code_ref"],
               "parent_project_id": parent["content_id"], "updated_project_id": revision_project["content_id"],
               "generation_mode": generation["mode"], "generation_calls": len(generation["model_calls"]),
               "generation_usage": generation["usage"], "update_model_calls": 0,
               "manual_changes_preserved": revision["preserved_user_changes"],
               "behavior_changed_on_same_input": True, "old_tasks_passed": 2, "tasks": results}
    write_json(output / "summary.json", summary)
    lines = ["# 规范更新后的数字员工验收", "", "同一账单输入：旧版规范选择 v1，新版规范选择 v2。两者都能编译，外部规则进一步检查版本选择。",
             "员工代码的人工启动定制与值班说明保留；更新本身 0 次模型调用。", "",
             "| 任务 | 选择的头文件 | 真实模型调用 | 验收 |", "|---|---|---:|---|"]
    lines += [f"| {r['case']} | {r['selected_header']} | {r['model_calls']} | 通过 |" for r in results]
    lines += ["", "公开迁移案例用于验证链路，不代表企业生产采纳、自动语义合并或成功率统计。", ""]
    (output / "summary.md").write_text("\n".join(lines), encoding="utf-8")
    return summary


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("operation", choices=["prepare-requests", "prepare-working", "verify"])
    parser.add_argument("--output-dir", type=Path, required=True)
    args = parser.parse_args(argv)
    if args.operation == "prepare-requests":
        prepare_requests(args.output_dir)
    elif args.operation == "prepare-working":
        prepare_working(args.output_dir)
    else:
        print(json.dumps(verify_demo(args.output_dir), ensure_ascii=False), flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
