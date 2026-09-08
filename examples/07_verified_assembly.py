"""Runnable assembly/verification recipe. Test decider only: no network or API key."""
from pathlib import Path
import sys
from threading import Barrier

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / "src"), str(ROOT)]

from adapters.runtime import ModelConfig, react_pattern
from agent_chassis import Chassis, Outcome
from agent_chassis.contracts import DoneCriteria, Task, TaskSource, Verdict
from agent_chassis.evidence import EvidenceObserver, assembly_manifest, content_id
from agent_chassis.orchestration import AgentStep, FnStep, NestedOrchestrator, ToolBox, ToolRequest
from agent_chassis.production import source_revision


class Source(TaskSource):
    def fetch(self, limit=1):
        return [Task("assembly-recipe", "example", {"expected": [2, 3]})]


class Criteria(DoneCriteria):
    def judge(self, task, ctx):
        return Verdict(ctx.facts.get("independently_verified") is True, "deterministic value check")


def run_example(*, parallel=2, reject=False, source=None):
    config = ModelConfig("not-used-test-decider", max_parallel_tools=parallel)
    rendezvous = Barrier(2, timeout=5) if parallel > 1 else None

    def read(value):
        if rendezvous:
            rendezvous.wait()  # Proves overlapping workers without timing thresholds.
        return value

    schema = {"type": "object", "properties": {}, "additionalProperties": False}
    box = (ToolBox().add("read_left", lambda: read(2), parallel_safe=True, input_schema=schema)
           .add("read_right", lambda: read(99 if reject else 3), parallel_safe=True, input_schema=schema))

    def decide(task, ctx, toolbox):
        if parallel > 1:
            return "batch", [ToolRequest("left", "read_left"), ToolRequest("right", "read_right")], None
        name = "read_right" if "read_left" in ctx.facts.get("tool_results", {}) else "read_left"
        return "call", name, {}

    def ready(task, ctx):
        return "observations collected" if len(ctx.facts.get("tool_results", {})) == 2 else None

    def prepare(task, ctx):
        ctx.record_check("values_match", "not_run")

    def verify(task, ctx):
        results = ctx.facts.get("tool_results", {})
        observed = [results.get("read_left"), results.get("read_right")]
        passed = observed == task.payload["expected"]
        ctx.facts["independently_verified"] = passed
        ctx.record_check("values_match", "passed" if passed else "failed",
                         evidence_refs={"observations_sha256": content_id(observed)})

    pattern = react_pattern(config, decide, toolbox=box, stop_when=ready)
    observer = EvidenceObserver(mode="test-decider")
    chassis = (Chassis("verified-assembly-recipe").with_payload(Source(), Criteria()).observe(observer)
               .with_orchestrator(NestedOrchestrator([
                   FnStep("prepare", prepare), AgentStep("work", pattern=pattern, toolbox=box),
                   FnStep("verify", verify)], box, pattern, delegate_at="work")).build())
    manifest = assembly_manifest(chassis.report(), runtime={"mode": "test-decider",
        "source": source if source is not None else source_revision(ROOT),
        "executors": {"react": config.react_options()}, "required_checks": ["values_match"]})
    observer.bind_manifest(manifest, production=True)
    try:
        result = chassis.run_once()
        return result, manifest, observer.snapshot()
    finally:
        chassis.close()


def main():
    import json
    from tools.verify_roadmap_evidence import verify_documents
    for parallel in (1, 2):
        result, manifest, evidence = run_example(parallel=parallel)
        verify_documents(manifest, json.loads(json.dumps(evidence)), require_production=True)
        assert result.outcome is Outcome.SUCCEEDED
        actual = evidence["runs"][0]["execution"]["parallel_observed"]
        print(f"PASS: configured={parallel}, observed_parallel={actual}, independent verdict=succeeded")
    print("Test decider only; production-format evidence is not live model validation.")


if __name__ == "__main__":
    main()
