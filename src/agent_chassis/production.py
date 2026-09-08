"""Opt-in production report checks. No model calls, task replay or authenticity claims."""
from __future__ import annotations

import hashlib
import os
from pathlib import Path
import re
import subprocess

from .evidence import content_id


LIMITS = ("max_iterations", "max_parallel_tools", "max_batch_calls", "max_tool_calls")


def source_revision(path):
    """Capture HEAD and a fingerprint of tracked/untracked, non-ignored changes.

    No filenames, diff contents or credentials are returned. Ignored files,
    external services and dependency versions are not attested by this snapshot.
    """
    def git(*args):
        return subprocess.check_output(["git", "-C", str(path), *args], stderr=subprocess.DEVNULL)

    try:
        root = Path(os.fsdecode(git("rev-parse", "--show-toplevel")).strip())
        path = root
        commit = git("rev-parse", "HEAD").decode("ascii").strip()
        status = git("status", "--porcelain=v1", "-z", "--untracked-files=all")
        source = {"commit": commit, "dirty": bool(status)}
        if status:
            untracked = []
            for name in git("ls-files", "--others", "--exclude-standard", "-z").split(b"\0"):
                if not name:
                    continue
                file = root / os.fsdecode(name)
                # Do not follow symlinks into unrelated files.
                data = os.fsencode(os.readlink(file)) if file.is_symlink() else file.read_bytes()
                untracked.append([hashlib.sha256(name).hexdigest(), hashlib.sha256(data).hexdigest()])
            source["changes_id"] = content_id({"status": hashlib.sha256(status).hexdigest(),
                "diff": hashlib.sha256(git("diff", "--no-ext-diff", "--no-textconv", "--binary", "HEAD", "--")).hexdigest(),
                "untracked": sorted(untracked)})
        return source
    except (OSError, UnicodeError, subprocess.CalledProcessError):
        raise ValueError("Cannot capture Git source revision") from None


def validate_production_manifest(manifest):
    if manifest.get("content_id") != content_id({k: v for k, v in manifest.items() if k != "content_id"}):
        raise ValueError("Production assembly content ID mismatch")
    runtime = manifest.get("runtime", {})
    if not isinstance(runtime, dict):
        raise ValueError("Production assembly requires runtime metadata")
    source = runtime.get("source", {})
    if not isinstance(source, dict) or not re.fullmatch(r"[0-9a-f]{40}|[0-9a-f]{64}", str(source.get("commit", ""))):
        raise ValueError("Production evidence requires a full source commit")
    if type(source.get("dirty")) is not bool:
        raise ValueError("Production evidence requires explicit worktree dirty state")
    if source["dirty"] and not re.fullmatch(r"[0-9a-f]{64}", str(source.get("changes_id", ""))):
        raise ValueError("Dirty source requires a changes_id fingerprint")
    if not isinstance(runtime.get("mode"), str) or runtime["mode"] in {"", "unknown", "unverified"}:
        raise ValueError("Production evidence requires an explicit execution mode")
    executors = runtime.get("executors")
    if not isinstance(executors, dict) or not executors:
        raise ValueError("Production evidence requires declared executor limits")
    for name, limits in executors.items():
        if (not isinstance(name, str) or not name or not isinstance(limits, dict)
                or set(limits) != set(LIMITS)
                or any(type(limits[k]) is not int or limits[k] < 1 for k in LIMITS)):
            raise ValueError("Invalid production executor limits")
    checks = runtime.get("required_checks")
    if (not isinstance(checks, list) or not checks or any(not isinstance(c, str) or not c for c in checks)
            or len(set(checks)) != len(checks)):
        raise ValueError("Production evidence requires distinct named verification checks")


def validate_execution_evidence(run):
    """Check new optional telemetry; legacy reports can omit it entirely."""
    execution = run.get("execution")
    if execution is None:
        return
    if type(run.get("attempt")) is not int or run["attempt"] < 1:
        raise ValueError("Execution evidence requires a positive run attempt")
    entries = execution["runs"]
    by_id = {entry["execution_id"]: entry for entry in entries}
    if len(by_id) != len(entries):
        raise ValueError("Duplicate execution ID")
    calls = {(call.get("batch_id"), call.get("call_id")): call for call in run["tool_calls"] if call.get("call_id")}
    if len(calls) != sum(bool(call.get("call_id")) for call in run["tool_calls"]):
        raise ValueError("Duplicate batch/call ID")
    seen = set()
    for batch in execution["batches"]:
        if batch["batch_id"] in seen or batch["execution_id"] not in by_id:
            raise ValueError("Invalid batch execution link")
        seen.add(batch["batch_id"])
        limits = by_id[batch["execution_id"]]["limits"]
        ids = batch["call_ids"]
        if batch["requested"] != len(ids) or len(ids) != len(set(ids)) or not 2 <= len(ids) <= limits["max_batch_calls"]:
            raise ValueError("Batch request count mismatch")
        started = [calls[(batch["batch_id"], cid)] for cid in ids if (batch["batch_id"], cid) in calls]
        if (batch["started"] != len(started) or batch["succeeded"] != sum(c["ok"] for c in started)
                or batch["failed"] != sum(not c["ok"] for c in started)):
            raise ValueError("Batch result counts mismatch")
        peak = batch["peak_in_flight"]
        if not 0 <= peak <= min(batch["started"], limits["max_parallel_tools"]) or bool(peak) != bool(started):
            raise ValueError("Invalid observed concurrency peak")
    if any(call["batch_id"] not in seen for call in calls.values()):
        raise ValueError("Tool call refers to an unrecorded batch")
    if execution["parallel_observed"] is not any(batch["peak_in_flight"] > 1 for batch in execution["batches"]):
        raise ValueError("Observed parallelism contradicts batch telemetry")
    if any(entry["attempt"] > run["attempt"] for entry in entries):
        raise ValueError("Execution attempt exceeds run attempt")


def validate_production_run(manifest, run):
    validate_production_manifest(manifest)
    if type(run.get("attempt")) is not int or run["attempt"] < 1:
        raise ValueError("Production evidence requires a positive run attempt")
    if any(check["attempt"] > run["attempt"] for check in run.get("checks", [])):
        raise ValueError("Check attempt exceeds run attempt")
    runtime = manifest["runtime"]
    if run["assembly_id"] != manifest["content_id"]:
        raise ValueError("Production run has no matching assembly")
    if (run["code_ref"] != runtime["source"]["commit"] or run.get("source") != runtime["source"]
            or run["mode"] != runtime["mode"]):
        raise ValueError("Production source or mode mismatch")
    validate_execution_evidence(run)
    entries = run.get("execution", {}).get("runs", [])
    if not entries:
        raise ValueError("Production report has no effective executor configuration")
    for entry in entries:
        if entry["limits"] != runtime["executors"].get(entry["key"]):
            raise ValueError("Effective executor limits disagree with assembly")
    # A previous attempt's success must not validate a later failed/skipped check.
    latest = {check["name"]: check for check in run.get("checks", []) if check["attempt"] == run["attempt"]}
    for name in runtime["required_checks"]:
        check = latest.get(name)
        if check is None:
            raise ValueError("Required check has no receipt in the current attempt")
        if check["status"] == "passed" and not check["evidence_refs"]:
            raise ValueError("Passed check requires evidence references")
        if run["outcome"] == "succeeded" and check["status"] != "passed":
            raise ValueError("Success with failed or not_run required check")
