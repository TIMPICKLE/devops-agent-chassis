"""Preview and apply a knowledge-only revision without overwriting a working project."""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / "src"), str(ROOT)]

from employee_factory.contracts import NeedsInput
from employee_factory.updates import apply_update, preview_update, read_json, render_plan, write_json
from tools.run_roadmap_showcase import source_revision


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--project", type=Path, required=True, help="Unmodified baseline project")
    parser.add_argument("--working-copy", type=Path, required=True, help="Copy containing your local edits")
    parser.add_argument("--request-dir", type=Path, required=True)
    parser.add_argument("--plan", type=Path, required=True, help="New preview file, or existing plan to apply")
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--output-dir", type=Path)
    parser.add_argument("--require-live", action="store_true", help="Require actual original generation provenance")
    args = parser.parse_args(argv)
    try:
        if args.apply:
            if args.output_dir is None:
                parser.error("--apply requires --output-dir")
            project = apply_update(args.project, args.working_copy, args.request_dir, args.output_dir,
                                   expected_plan_id=read_json(args.plan)["content_id"],
                                   require_live=args.require_live, code_ref=source_revision())
            print(json.dumps({"status": "applied_pending_runtime", "project_id": project["content_id"],
                              "model_calls": 0, "runtime_verified": False}, ensure_ascii=False))
            return 0
        if args.plan.exists() or args.plan.with_suffix(".md").exists():
            raise ValueError("Use a new plan filename; existing previews are not overwritten")
        if args.plan.suffix != ".json":
            raise ValueError("Plan filename must end with .json")
        for root in (args.project, args.working_copy, args.request_dir):
            if root.resolve() in args.plan.resolve().parents:
                raise ValueError("Write previews outside input directories")
        plan = preview_update(args.project, args.working_copy, args.request_dir, require_live=args.require_live)
        write_json(args.plan, plan)
        args.plan.with_suffix(".md").write_text(render_plan(plan), encoding="utf-8")
        print(json.dumps(plan, ensure_ascii=False))
        return 2 if plan["status"] == "blocked" else 0
    except (NeedsInput, ValueError, FileNotFoundError) as exc:
        print(json.dumps({"status": "blocked", "error": str(exc), "model_calls": 0}, ensure_ascii=False))
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
