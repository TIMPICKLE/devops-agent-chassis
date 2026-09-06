"""Knowledge-only project revisions: preview, preserve edits, retain provenance.

No model call or generated-code execution happens during update. Runtime acceptance
remains a separate operation. This is not a general semantic merge engine.
"""
from __future__ import annotations

import hashlib
import json
import shutil
from pathlib import Path

from agent_chassis.evidence import content_id
from employee_factory.contracts import load_request, validate_project

REVISION = "employee-knowledge-revision/v1"
MANAGED = {"project.json", "request.snapshot.json", "generation.manifest.json",
           "generation.evidence.json", "revision.json", "update.md"}
IGNORED_PARTS = {".git", "__pycache__", ".chassis"}


def read_json(path):
    return json.loads(path.read_text(encoding="utf-8"))


def write_json(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def project_files(directory):
    """User-editable project files; byte hashes preserve custom and binary files."""
    result = {}
    for path in sorted(directory.rglob("*")):
        relative = path.relative_to(directory)
        if any(part in IGNORED_PARTS for part in relative.parts):
            continue
        if path.is_symlink():
            raise ValueError("Revision inputs must contain regular files, not symlinks")
        if path.is_file() and relative.as_posix() not in MANAGED:
            result[relative.as_posix()] = hashlib.sha256(path.read_bytes()).hexdigest()
    return result


def knowledge_changes(old, new):
    return [{"document": name, "before": content_id(old["documents"][name]),
             "after": content_id(new["documents"][name])}
            for name in old["documents"] if old["documents"][name] != new["documents"][name]]


def impact_nodes(spec):
    return [{"name": n["name"], "reason": "consumes repair knowledge" if n["role"] == "model"
             else "must recheck changed candidate"} for n in spec["nodes"] if n["role"] in ("model", "verify")]


def preview_update(base, working, request_dir, *, require_live=False):
    from employee_factory.verification import verify_project
    old, parent, spec = verify_project(base, require_live=require_live)
    new = load_request(request_dir)
    conflicts = []
    changed_fields = sorted(k for k in set(old["request"]) | set(new["request"])
                            if old["request"].get(k) != new["request"].get(k))
    if changed_fields:
        conflicts.append({"kind": "unsupported_request_change", "fields": changed_fields,
                          "reason": "Only contents of existing knowledge documents can change in this version"})
    # Bookkeeping files are owned by the updater, not silently taken from a dirty copy.
    for name in sorted(MANAGED):
        before, current = base / name, working / name
        if before.exists() != current.exists() or (before.exists() and before.read_bytes() != current.read_bytes()):
            conflicts.append({"kind": "managed_file_changed", "path": name})
    if (working / "assembly.json").read_bytes() != (base / "assembly.json").read_bytes():
        conflicts.append({"kind": "workflow_change", "path": "assembly.json"})
    changes = knowledge_changes(old, new) if not changed_fields else []
    for name in old["documents"]:
        path = working / name
        current = path.read_text(encoding="utf-8") if path.is_file() else None
        incoming = new["documents"].get(name)
        if current not in (old["documents"][name], incoming):
            conflicts.append({"kind": "knowledge_merge_conflict", "path": name,
                              "reason": "Working knowledge differs from both baseline and incoming version"})
    baseline_files, working_files = project_files(base), project_files(working)
    custom_changes = [{"path": name, "before": baseline_files.get(name), "after": working_files.get(name)}
                      for name in sorted(set(baseline_files) | set(working_files))
                      if baseline_files.get(name) != working_files.get(name) and name not in old["documents"]]
    try:
        validate_project((working / "employee.py").read_text(encoding="utf-8"), spec,
                         (working / "README.md").read_text(encoding="utf-8"), old)
    except (ValueError, SyntaxError, TypeError, FileNotFoundError) as exc:
        conflicts.append({"kind": "invalid_working_project", "reason": str(exc)[:500]})
    plan = {"schema_version": REVISION, "parent_project_id": parent["content_id"],
            "from_request_id": old["content_id"], "to_request_id": new["content_id"],
            "changed_documents": changes, "affected_nodes": impact_nodes(spec) if changes else [],
            "preserved_user_changes": custom_changes, "working_files": working_files,
            "conflicts": conflicts, "status": "blocked" if conflicts else ("ready" if changes else "no_change"),
            "model_calls": 0, "runtime_verified": False,
            "regression_required": ["previous accepted tasks", "task distinguishing old and new knowledge"]}
    plan["content_id"] = content_id(plan)
    return plan


def apply_update(base, working, request_dir, output, *, expected_plan_id, require_live=False, code_ref="unknown"):
    # Re-plan to reject changes made since the user's preview.
    plan = preview_update(base, working, request_dir, require_live=require_live)
    if plan["content_id"] != expected_plan_id:
        raise ValueError("Preview is stale; inspect a fresh plan before applying")
    if plan["status"] != "ready":
        raise ValueError("Update is not ready: " + plan["status"])
    output = output.resolve()
    if output.exists() or any(root.resolve() == output or root.resolve() in output.parents
                              for root in (base, working, request_dir)):
        raise ValueError("Use a new output directory outside the inputs")
    new = load_request(request_dir)
    output.mkdir(parents=True)
    # Copy only listed user files; managed files come from verified baseline.
    for name in plan["working_files"]:
        dest = output / name
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(working / name, dest)
    for name in ("generation.manifest.json", "generation.evidence.json"):
        shutil.copy2(base / name, output / name)
    for name, text in new["documents"].items():
        (output / name).write_text(text, encoding="utf-8")
    shutil.copytree(base, output / ".chassis" / "parent", ignore=shutil.ignore_patterns("__pycache__", ".git"))
    write_json(output / "request.snapshot.json", new)
    revision = dict(plan)
    revision.pop("content_id")
    revision.update(status="applied_pending_runtime", output_files=project_files(output))
    revision["content_id"] = content_id(revision)
    write_json(output / "revision.json", revision)
    ids = validate_project((output / "employee.py").read_text(encoding="utf-8"),
                           read_json(output / "assembly.json"), (output / "README.md").read_text(encoding="utf-8"), new)
    project = {"kind": REVISION, "parent_project_id": plan["parent_project_id"], "revision_id": revision["content_id"],
               "request_id": new["content_id"], "code_ref": code_ref, "runtime_verified": False, **ids}
    project["content_id"] = content_id(project)
    write_json(output / "project.json", project)
    (output / "update.md").write_text(render_plan(plan) + "\n已创建新版本；尚需运行新旧任务验收。\n", encoding="utf-8")
    from employee_factory.verification import verify_project
    verify_project(output, require_live=require_live)
    return project


def render_plan(plan):
    lines = ["# 员工项目知识更新", "", f"状态：{plan['status']}；更新模型调用：0。", "",
             "| 知识 | 变化 |", "|---|---|"]
    lines += [f"| {item['document']} | 内容版本变化 |" for item in plan["changed_documents"]]
    lines += ["", "影响节点：" + ", ".join(n["name"] for n in plan["affected_nodes"]),
              "保留人工修改：" + (", ".join(c["path"] for c in plan["preserved_user_changes"]) or "无"),
              "", "更新不重写员工代码；影响依据显式知识绑定与节点角色，不是通用语义推断。",
              "必须重跑旧任务及能区分新旧规范的任务，更新成功不等于运行已验证。", ""]
    if plan["conflicts"]:
        lines += ["冲突：", json.dumps(plan["conflicts"], ensure_ascii=False, indent=2)]
    return "\n".join(lines)


def verify_revision(directory, frozen, project, spec, *, require_live, depth):
    from employee_factory.verification import verify_project
    from tools.verify_roadmap_evidence import _check_id
    parent_dir = directory / ".chassis" / "parent"
    old, parent, parent_spec = verify_project(parent_dir, require_live=require_live, _depth=depth + 1)
    revision = read_json(directory / "revision.json")
    _check_id(revision)
    if (project["kind"] != REVISION or revision["schema_version"] != REVISION
            or project["parent_project_id"] != parent["content_id"]
            or project["revision_id"] != revision["content_id"]
            or revision["parent_project_id"] != parent["content_id"]
            or revision["from_request_id"] != old["content_id"] or revision["to_request_id"] != frozen["content_id"]
            or old["request"] != frozen["request"] or spec != parent_spec
            or revision["status"] != "applied_pending_runtime" or revision["conflicts"]
            or revision["model_calls"] != 0 or revision["runtime_verified"] is not False
            or revision["changed_documents"] != knowledge_changes(old, frozen)
            or revision["affected_nodes"] != impact_nodes(spec)):
        raise ValueError("Revision lineage or supported knowledge change does not match")
    for name in ("generation.manifest.json", "generation.evidence.json"):
        if (directory / name).read_bytes() != (parent_dir / name).read_bytes():
            raise ValueError("Inherited generation evidence changed")
    expected = dict(revision["working_files"])
    for name, text in frozen["documents"].items():
        expected[name] = hashlib.sha256(text.encode("utf-8")).hexdigest()
    if revision["output_files"] != expected or project_files(directory) != expected:
        raise ValueError("Revision did not preserve working files or apply exactly the new knowledge")
    return frozen, project, spec
