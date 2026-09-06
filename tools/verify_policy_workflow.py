"""Recompute config experiment results from frozen inputs and portable evidence."""
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
from payloads.config_policy import ConfigCriteria, ConfigSource, matching_documents
from payloads.patch_showcase import Candidate
from tools.run_policy_workflow import NODES, failure_kind, summarize, trial_order
from tools.verify_roadmap_evidence import _check_id, verify_documents


def verify(directory, *, require_live=False):
    def read(name):
        return json.loads((directory / name).read_text(encoding="utf-8"))

    plan, summary = read("plan.json"), read("summary.json")
    _check_id(plan)
    if (plan["schema_version"] != "config-policy-experiment/v1" or plan["repeats"] < 1
            or plan["mode"] not in ("live", "offline")):
        raise ValueError("Invalid plan")
    if require_live and plan["mode"] != "live":
        raise ValueError("Not a live plan")
    if plan["trials"] != trial_order(plan["snapshot"], plan["repeats"], plan["seed"]):
        raise ValueError("Trial order or coverage differs from frozen plan")
    if len({c["id"] for c in plan["snapshot"]["cases"]}) != len(plan["snapshot"]["cases"]):
        raise ValueError("Duplicate case IDs")
    if len(summary["results"]) != len(plan["trials"]):
        raise ValueError("Incomplete experiment: keep pending trials in denominator")
    rows = []
    for trial in plan["trials"]:
        prefix = trial["id"]
        manifest, evidence = read(prefix + ".manifest.json"), read(prefix + ".evidence.json")
        verify_documents(manifest, evidence, require_live=require_live)
        if len(evidence["runs"]) != 1:
            raise ValueError("Expected one run per trial")
        run, runtime = evidence["runs"][0], manifest["runtime"]
        case = next(c for c in plan["snapshot"]["cases"] if c["id"] == trial["case_id"])
        task = ConfigSource(case).task
        expected_runtime = {"mode": run["mode"], "requested_mode": plan["mode"],
            "context_policy": trial["context_policy"], "protocol": plan["protocol"], "config": plan["config"],
            "nodes": NODES, "policy_id": content_id(plan["snapshot"]), "case_id": case["id"]}
        if runtime != expected_runtime or run["code_ref"] != plan["code_ref"]:
            raise ValueError("Runtime does not match frozen plan")
        if run["input_id"] != content_id(task.payload) or run["task_key"] != case["id"]:
            raise ValueError("Task identity mismatch")
        if len(run["model_calls"]) > plan["config"]["max_calls"]:
            raise ValueError("Model budget exceeded")
        if plan["mode"] == "offline" and run["mode"] != "offline-contract":
            raise ValueError("Offline plan contains another execution mode")
        docs = ([] if trial["context_policy"] == "none" else plan["snapshot"]["documents"]
                if trial["context_policy"] == "full" else matching_documents(plan["snapshot"], task))
        chunks = [f"### {d['name']} @ {d['version']}\n\n" +
                  json.dumps({"applies_to": d["scope"], "rules": d["rules"]}, sort_keys=True) for d in docs]
        hashes = [hashlib.sha256(text.encode("utf-8")).hexdigest() for text in chunks]
        included, omitted, chars = [], [], 0
        for digest, text in zip(hashes, chunks):
            cost = len(text) + (2 if included else 0)
            if chars + cost <= plan["config"]["context_max_chars"]:
                included.append(digest)
                chars += cost
            else:
                omitted.append(digest)
        for receipt in run["context_receipts"]:
            if (receipt["included"], receipt["omitted"], receipt["chars"]) != (included, omitted, chars):
                raise ValueError("Knowledge consumption differs from planned document selection")
        for injection in run["injections"]:
            if injection["content_hash"] not in hashes or injection["point"] != "before_executor":
                raise ValueError("Unexpected knowledge injection")
            doc = docs[hashes.index(injection["content_hash"])]
            if injection["version"] != doc["version"] or injection["provider"] != doc["name"]:
                raise ValueError("Knowledge version mismatch")
        candidate_path = directory / (prefix + ".candidate.json")
        candidate = Candidate(task.payload["source"], "deployment.json")
        if candidate_path.exists():
            candidate.content = candidate_path.read_text(encoding="utf-8")
            if (directory / (prefix + ".patch")).read_text(encoding="utf-8") != candidate.patch():
                raise ValueError("Patch does not describe saved configuration")
        verdict = ConfigCriteria(candidate, plan["snapshot"]).validate(task)
        if run["outcome"] == "succeeded":
            if (not verdict.done or run["steps"] != NODES or run["verdict"]["evidence"] != verdict.evidence
                    or not run["context_receipts"]):
                raise ValueError("Success not supported by configuration, workflow and knowledge evidence")
        rows.append({**trial, "outcome": run["outcome"], "execution_mode": run["mode"],
                     "failure_kind": failure_kind(run),
                     "model_calls": len(run["model_calls"]), "usage": run["usage"],
                     "context_chars": sum(r["chars"] for r in run["context_receipts"])})
    if summary != summarize(plan, rows):
        raise ValueError("Summary does not match independently recomputed results")
    return summary


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("directory", type=Path)
    parser.add_argument("--require-live", action="store_true")
    args = parser.parse_args(argv)
    result = verify(args.directory, require_live=args.require_live)
    print(f"Verified {result['completed']} trials: frozen inputs, config oracle, patches, nodes, context and usage")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
