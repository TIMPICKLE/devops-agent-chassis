"""Run an AI-generated employee against a supplied local source workspace."""
from __future__ import annotations

import argparse
import importlib.util
import json
import os
import sys
from dataclasses import asdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / "src"), str(ROOT)]

from agent_chassis.evidence import assembly_manifest, content_id
from employee_factory.runtime import EmployeeKit
from employee_factory.verification import verify_project, verify_runtime
from employee_factory.workspace import compile_snapshot, load_workspace
from tools.run_roadmap_showcase import add_model_arguments, model_config, source_revision, write_json


def execute(project_dir, repo, unit, output, config, *, protocol="anthropic", decider=None, require_live=True):
    frozen, project, spec = verify_project(project_dir, require_live=require_live)
    snapshot = load_workspace(repo, unit)
    baseline = compile_snapshot(snapshot)
    write_json(output / "workspace.snapshot.json", snapshot)
    write_json(output / "baseline.compiler.json", baseline)
    if baseline["exit_code"] == 0:
        raise ValueError("Original compilation already succeeds; no repair is needed")
    kit = EmployeeKit(snapshot, baseline, frozen, config, protocol=protocol, code_ref=source_revision(), decider=decider)
    module_spec = importlib.util.spec_from_file_location("generated_employee_" + project["content_id"], project_dir / "employee.py")
    module = importlib.util.module_from_spec(module_spec)
    module_spec.loader.exec_module(module)
    chassis = module.build_employee(kit)
    manifest = assembly_manifest(chassis.report(), runtime={"mode": kit.mode, "phase": "employee-runtime",
        "project_id": project["content_id"], "workspace_id": content_id(snapshot),
        "protocol": protocol, "config": asdict(config)})
    kit.observer.assembly_id = manifest["content_id"]
    write_json(output / "runtime.manifest.json", manifest)
    try:
        result = chassis.run_once()
        write_json(output / "runtime.evidence.json", kit.observer.snapshot())
        if kit.candidate.content is not None:
            (output / "candidate.cpp").write_text(kit.candidate.content, encoding="utf-8")
            (output / "candidate.patch").write_text(kit.candidate.patch(), encoding="utf-8")
        print(json.dumps({"phase": "employee-runtime", "outcome": result.outcome.value,
                          "error": result.error, "model_calls": len(kit.observer.runs[0]["model_calls"])}, ensure_ascii=False), flush=True)
        verified = verify_runtime(project_dir, output, repo=repo, require_live=require_live)
        write_json(output / "acceptance.json", verified)
        return verified
    finally:
        chassis.close()


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--project", type=Path, required=True)
    parser.add_argument("--repo", type=Path, required=True)
    parser.add_argument("--unit", required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    add_model_arguments(parser)
    parser.set_defaults(max_calls=6, max_tokens=3072, timeout=90)
    args = parser.parse_args(argv)
    if not os.environ.get(args.api_key_env):
        parser.error(f"Missing {args.api_key_env}; runtime never falls back to mock")
    config = model_config(args)
    args.output_dir.mkdir(parents=True, exist_ok=False)
    try:
        result = execute(args.project, args.repo, args.unit, args.output_dir, config, protocol=args.protocol)
    except Exception as exc:
        error = {"accepted": False, "phase": "runtime-or-independent-verification",
                 "error_type": type(exc).__name__, "error": str(exc)[:1200]}
        write_json(args.output_dir / "failure.json", error)
        print(json.dumps(error, ensure_ascii=False), flush=True)
        return 1
    print(json.dumps(result, ensure_ascii=False), flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
