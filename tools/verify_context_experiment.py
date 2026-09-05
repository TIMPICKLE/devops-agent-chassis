"""Recompute experiment summaries from their predeclared plan and run evidence."""
from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT))

from agent_chassis.evidence import content_id
from tools.run_context_experiment import summarize, trial_order
from tools.verify_roadmap_evidence import verify_documents


def verify_experiment(directory, *, require_live=False):
    def read(name):
        return json.loads((directory / name).read_text(encoding="utf-8"))

    plan, summary = read("experiment-plan.json"), read("summary.json")
    if plan.get("schema_version") != "agent-chassis.context-experiment-plan/v1":
        raise ValueError("Unsupported experiment plan")
    if plan["content_id"] != content_id({k: v for k, v in plan.items() if k != "content_id"}):
        raise ValueError("Experiment plan content ID mismatch")
    if plan["trials"] != trial_order(plan["repeats"], plan["seed"]):
        raise ValueError("Trial order does not match the declared design")
    if require_live and plan["mode"] != "live":
        raise ValueError("Not a live experiment")
    if len(summary["trials"]) != len(plan["trials"]):
        raise ValueError("Experiment did not account for all planned trials")
    used = 0
    for spec, trial in zip(plan["trials"], summary["trials"]):
        if any(trial.get(k) != v for k, v in spec.items()):
            raise ValueError("Trial identity or condition differs from plan")
        remaining = plan["max_total_model_calls"] - used
        insufficient = plan["mode"] == "live" and remaining < plan["model_config"]["max_calls"]
        if trial["outcome"] == "not_run":
            expected = {**spec, "outcome": "not_run", "reason": "insufficient remaining call budget"}
            if not insufficient or trial != expected:
                raise ValueError("Unjustified skipped trial")
            continue
        if insufficient:
            raise ValueError("Trial started without reserving its full call budget")
        prefix = spec["trial_id"]
        if trial["manifest"] != prefix + ".manifest.json" or trial["evidence"] != prefix + ".evidence.json":
            raise ValueError("Trial evidence filename mismatch")
        manifest, evidence = read(trial["manifest"]), read(trial["evidence"])
        if verify_documents(manifest, evidence, require_live=require_live) != 1:
            raise ValueError("Expected one fresh run per trial")
        runtime = manifest["runtime"]
        for field in ("protocol", "stop_policy", "flow"):
            if runtime[field] != plan[field]:
                raise ValueError("Runtime differs from fixed experiment conditions")
        if runtime["context_policy"] != spec["context_policy"] or runtime["requested_mode"] != plan["mode"]:
            raise ValueError("Runtime condition or mode mismatch")
        if runtime["config"] != plan["model_config"]:
            raise ValueError("Model configuration changed between trials")
        run = evidence["runs"][0]
        if run["code_ref"] != plan["code_ref"] or run["input_id"] != plan["inputs"][spec["scenario"]]:
            raise ValueError("Code or task input changed between trials")
        expected = {"outcome": run["outcome"], "execution_mode": run["mode"], "usage": run["usage"],
                    "model_calls": len(run["model_calls"]), "input_id": run["input_id"],
                    "stop_reason": run["stop_reason"], "elapsed_ms": run["elapsed_ms"],
                    "context_chars": sum(receipt["chars"] for receipt in run["context_receipts"]),
                    "reason": run["verdict"]["reason"] if run["verdict"] is not None else run["error"]}
        if any(trial.get(k) != v for k, v in expected.items()):
            raise ValueError("Trial metrics differ from evidence")
        used += expected["model_calls"]
        if expected["model_calls"] > plan["model_config"]["max_calls"]:
            raise ValueError("Per-trial budget exceeded")
        if run["outcome"] == "succeeded":
            digest = hashlib.sha256((directory / (prefix + ".patch")).read_bytes()).hexdigest()
            if digest != run["verdict"]["evidence"].get("patch_sha256"):
                raise ValueError("Patch does not match verifier digest")
    if summary != summarize(plan, summary["trials"]):
        raise ValueError("Experiment aggregate does not match trial evidence")
    return summary


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("directory", type=Path)
    parser.add_argument("--require-live", action="store_true")
    args = parser.parse_args(argv)
    summary = verify_experiment(args.directory, require_live=args.require_live)
    print(f"Verified experiment: {len(summary['trials'])} planned trials; "
          f"coverage_complete={summary['coverage_complete']}; model_calls={summary['model_calls']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
