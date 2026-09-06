"""Generate an employee project from a request directory, using a real LLM."""
from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / "src"), str(ROOT)]

from agent_chassis.evidence import content_id
from employee_factory.contracts import NeedsInput, load_request
from employee_factory.generation import generate
from tools.run_roadmap_showcase import add_model_arguments, model_config, source_revision, write_json


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--request-dir", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    add_model_arguments(parser)
    parser.set_defaults(max_calls=3, max_tokens=16384, timeout=240)
    args = parser.parse_args(argv)
    try:
        frozen = load_request(args.request_dir)
    except NeedsInput as exc:
        print(json.dumps({"status": "needs_input", "missing": exc.fields, "model_calls": 0}, ensure_ascii=False))
        return 2
    if not os.environ.get(args.api_key_env):
        parser.error(f"Missing {args.api_key_env}; generation requires a real model")
    config, code_ref = model_config(args), source_revision()
    output = args.output_dir
    output.mkdir(parents=True, exist_ok=False)
    write_json(output / "request.snapshot.json", frozen)
    candidate, evidence, manifest, result = generate(frozen, config, protocol=args.protocol, code_ref=code_ref)
    write_json(output / "generation.manifest.json", manifest)
    write_json(output / "generation.evidence.json", evidence)
    if result.outcome.value == "succeeded":
        (output / "employee.py").write_text(candidate["script"], encoding="utf-8")
        (output / "README.md").write_text(candidate["readme"], encoding="utf-8")
        write_json(output / "assembly.json", candidate["manifest"])
        for name, text in frozen["documents"].items():
            dest = output / name
            dest.parent.mkdir(parents=True, exist_ok=True)
            dest.write_text(text, encoding="utf-8")
        project = {"request_id": frozen["content_id"], "code_ref": code_ref,
                   "script_id": content_id(candidate["script"]), "manifest_id": content_id(candidate["manifest"]),
                   "readme_id": content_id(candidate["readme"]), "runtime_verified": False}
        project["content_id"] = content_id(project)
        write_json(output / "project.json", project)
    run = evidence["runs"][0]
    print(json.dumps({"phase": "generation", "outcome": run["outcome"], "model_calls": len(run["model_calls"]),
                      "usage": run["usage"], "error": run["error"], "runtime_verified": False}, ensure_ascii=False))
    return 0 if result.outcome.value == "succeeded" else 1


if __name__ == "__main__":
    raise SystemExit(main())
