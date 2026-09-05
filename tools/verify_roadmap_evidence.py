"""Validate portable evidence without calling a model or re-executing a task.

This checks structure and internal consistency, NOT authenticity of a provider
response or success of a production task. Hashes are content IDs, not signatures.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from agent_chassis.evidence import content_id


def _check_id(document):
    value = {key: item for key, item in document.items() if key != "content_id"}
    if document["content_id"] != content_id(value):
        raise ValueError("Content ID does not match document")


def verify_documents(manifest, evidence, *, require_live=False):
    from jsonschema import Draft202012Validator

    for name, document in [("assembly", manifest), ("evidence", evidence)]:
        schema = json.loads((ROOT / "schemas" / (name + "-v1.schema.json")).read_text(encoding="utf-8"))
        Draft202012Validator.check_schema(schema)
        errors = list(Draft202012Validator(schema).iter_errors(document))
        if errors:
            # ValidationError messages can embed the entire bad input; omit them.
            raise ValueError(f"{name} does not match v1 schema")
    _check_id(manifest)
    if not evidence["runs"]:
        raise ValueError("No runs to verify")
    for run in evidence["runs"]:
        _check_id(run)
        if run["assembly_id"] != manifest["content_id"]:
            raise ValueError("Run refers to a different assembly")
        if "mode" in manifest["runtime"] and run["mode"] != manifest["runtime"]["mode"]:
            raise ValueError("Run and assembly modes disagree")
        if run["outcome"] == "succeeded" and (run["verdict"] is None or run["verdict"]["done"] is not True):
            raise ValueError("Success without an accepting verdict")
        known = {item["content_hash"] for item in run["injections"]}
        for receipt in run["context_receipts"]:
            if not set(receipt["included"] + receipt["omitted"]).issubset(known):
                raise ValueError("Context receipt refers to an unrecorded injection")
        calls = run["model_calls"]
        if run["mode"] == "offline-contract" and calls:
            raise ValueError("Offline fixture evidence contains model calls")
        complete = bool(calls) and all(type(c[key]) is int for c in calls for key in ("input_tokens", "output_tokens"))
        if run["usage"]["complete"] is not complete:
            raise ValueError("Usage completeness mismatch")
        for key in ("input_tokens", "output_tokens"):
            expected = sum(c[key] for c in calls) if complete else None
            if run["usage"][key] != expected:
                raise ValueError("Usage totals mismatch")
        if require_live and (run["mode"] != "live" or not calls or any(c["mode"] != "live" for c in calls)):
            raise ValueError("Not live model evidence")
    return len(evidence["runs"])


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("directory", type=Path)
    parser.add_argument("--require-live", action="store_true")
    args = parser.parse_args(argv)
    paths = sorted(args.directory.glob("*.manifest.json"))
    if not paths:
        parser.error("No assembly manifests found")
    count = 0
    for path in paths:
        evidence_path = path.with_name(path.name.replace(".manifest.json", ".evidence.json"))
        evidence = json.loads(evidence_path.read_text(encoding="utf-8"))
        count += verify_documents(json.loads(path.read_text(encoding="utf-8")), evidence, require_live=args.require_live)
        for run in evidence["runs"]:
            if run["outcome"] == "succeeded":
                patch = path.with_name(path.name.replace(".manifest.json", ".patch"))
                expected = run["verdict"]["evidence"].get("patch_sha256")
                if expected is None or hashlib.sha256(patch.read_bytes()).hexdigest() != expected:
                    raise ValueError("Patch is missing a matching verifier digest")
    print(f"Verified {count} run(s): schema, content IDs, assembly links, context references and usage totals.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
