"""Versioned, portable run evidence independent of payload and model SDKs.

This is a reviewable report, not an execution checkpoint or tamper-proof audit store.
Context and model request bodies are intentionally excluded.
"""
from __future__ import annotations

import hashlib
import json
from copy import deepcopy
from dataclasses import asdict
from pathlib import Path
from typing import Any, Dict, Mapping, Optional

from .contracts import Observer, RunContext, TaskResult


def content_id(value: Any) -> str:
    raw = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def assembly_manifest(report: Any, *, runtime: Optional[Mapping[str, Any]] = None) -> Dict[str, Any]:
    """Serialize the assembled components; runtime must contain references, not secrets."""
    data = asdict(report)
    data["injection_timeline"] = [[point.value, providers] for point, providers in report.injection_timeline]
    document = {"schema_version": "agent-chassis.assembly/v1", "assembly": data,
                "runtime": deepcopy(dict(runtime or {}))}
    document["content_id"] = content_id(document)
    return document


class EvidenceObserver(Observer):
    """Small evidence exporter; callers choose whether/where to persist a snapshot."""
    name = "run-evidence"

    def __init__(self, *, code_ref: str = "unknown", mode: str = "unverified",
                 assembly_id: str = ""):
        self.code_ref, self.mode, self.assembly_id = code_ref, mode, assembly_id
        self.runs: list = []
        self.source = None

    def bind_manifest(self, manifest, *, production=False):
        """Bind before running. Production validation is opt-in; old demos stay valid."""
        if manifest.get("content_id") != content_id({k: v for k, v in manifest.items() if k != "content_id"}):
            raise ValueError("Assembly content ID mismatch")
        if production:
            from .production import validate_production_manifest
            validate_production_manifest(manifest)
        runtime = manifest.get("runtime", {})
        source = runtime.get("source")
        mode = runtime.get("mode", self.mode)
        if self.mode != "unverified" and self.mode != mode:
            raise ValueError("Observer and assembly modes disagree")
        if source is not None and self.code_ref not in {"unknown", source["commit"]}:
            raise ValueError("Observer and assembly source revisions disagree")
        self.assembly_id, self.mode = manifest["content_id"], mode
        self.source = deepcopy(source)
        if source is not None:
            self.code_ref = source["commit"]
        return self

    def on_task_end(self, result: TaskResult, ctx: RunContext) -> None:
        model_calls = deepcopy(ctx.model_calls)
        usage_complete = bool(model_calls) and all(
            type(call.get(key)) is int for call in model_calls for key in ("input_tokens", "output_tokens")
        )
        document = {
            "schema_version": "agent-chassis.run/v1",
            "run_id": ctx.run_id, "task_key": result.task.key, "task_kind": result.task.kind,
            "input_id": content_id(result.task.payload),
            "code_ref": self.code_ref, "assembly_id": self.assembly_id, "mode": self.mode,
            "outcome": result.outcome.value,
            "verdict": asdict(result.verdict) if result.verdict is not None else None,
            "error": result.error,
            "cleanup": {"actions": list(result.artifacts.get("cleanups", [])),
                        "policy_error": result.artifacts.get("failure_policy_error", "")},
            "steps": list(ctx.steps), "elapsed_ms": result.elapsed_ms,
            "iterations": ctx.iterations, "stop_reason": ctx.facts.get("stop_reason", ""),
            "injections": [{"point": item.point.value, "provider": item.provider, "label": item.label,
                            "content_hash": item.content_hash, "version": item.version, "chars": item.chars}
                           for item in ctx.injections],
            "context_receipts": [asdict(receipt) for receipt in ctx.context_receipts],
            "tool_calls": [{"name": call.name, "ok": call.ok, "elapsed_ms": call.elapsed_ms,
                            **({"call_id": call.call_id, "batch_id": call.batch_id} if call.call_id else {})}
                           for call in ctx.tool_calls],
            "model_calls": model_calls,
            "attempt": max(1, ctx.attempt),
            **({"source": deepcopy(self.source)} if self.source is not None else {}),
            **({"execution": {"runs": deepcopy(ctx.executions), "batches": deepcopy(ctx.tool_batches),
                              "parallel_observed": any(b["peak_in_flight"] > 1 for b in ctx.tool_batches)}}
               if ctx.executions else {}),
            **({"checks": deepcopy(ctx.verification_checks)} if ctx.verification_checks else {}),
            **({"diagnostics": deepcopy(ctx.diagnostics)} if ctx.diagnostics else {}),
            "usage": {
                "complete": usage_complete,
                "input_tokens": sum(c["input_tokens"] for c in model_calls) if usage_complete else None,
                "output_tokens": sum(c["output_tokens"] for c in model_calls) if usage_complete else None,
            },
        }
        document["content_id"] = content_id(document)
        self.runs.append(document)

    def snapshot(self) -> Dict[str, Any]:
        return {"schema_version": "agent-chassis.evidence/v1", "runs": deepcopy(self.runs)}

    def dump(self, path: str) -> None:
        dest = Path(path)
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_text(json.dumps(self.snapshot(), ensure_ascii=False, indent=2, allow_nan=False) + "\n",
                        encoding="utf-8")
