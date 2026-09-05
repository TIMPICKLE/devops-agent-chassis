"""Run the same assembly against two payloads; live is the default, offline explicit.

Examples:
  python tools/run_roadmap_showcase.py --mode live
  python tools/run_roadmap_showcase.py --mode offline --flow nested
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import uuid
from dataclasses import asdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT))

from agent_chassis import Chassis, InjectionPoint as P, Outcome, borrowed_executor
from agent_chassis.evidence import EvidenceObserver, assembly_manifest
from agent_chassis.failure import ZeroSideEffectPolicy
from agent_chassis.knowledge import SkillLibrary, SkillProvider, by_extension
from agent_chassis.orchestration import AgentStep, FnStep, NestedOrchestrator, ReActPattern, SingleAgentOrchestrator, StateMachineOrchestrator
from adapters.anthropic_runtime import AnthropicDecider, ModelConfig
from adapters.openai_runtime import OpenAIChatDecider
from adapters.runtime import DEFAULT_ENDPOINTS, RuntimeDecider
from payloads.patch_showcase import Candidate, HeaderBuildCriteria, HeaderBuildSource, PythonNoneCriteria, PythonQualitySource, fixture_solution, patch_tools, submission_ready


def assemble(scenario, *, mode="live", flow="nested", config=None, code_ref="unknown", decider=None,
             stop_policy="objective", protocol="anthropic"):
    factories = {"python_quality": (PythonQualitySource, PythonNoneCriteria),
                 "header_build": (HeaderBuildSource, HeaderBuildCriteria)}
    if (mode not in {"live", "offline"} or flow not in {"nested", "state_machine", "single_agent"}
            or stop_policy not in {"objective", "model"} or protocol not in DEFAULT_ENDPOINTS):
        raise ValueError("Unsupported mode or flow")
    source_type, criteria_type = factories[scenario]
    source = source_type()
    task = source.task
    candidate = Candidate(task.payload["source"], task.payload["target"]["path"])
    criteria = criteria_type(candidate)
    boundary = borrowed_executor("patch-executor")
    toolbox = patch_tools(candidate, criteria, task, boundary)
    config = config if config is not None else ModelConfig(model="glm-5.3-flash", base_url=DEFAULT_ENDPOINTS[protocol])

    def offline_decide(task, ctx, box):
        ctx.chassis.inject(P.BEFORE_EXECUTOR, task, ctx)
        ctx.context_for("offline-fixture-replay", [P.BEFORE_EXECUTOR])
        if ctx.tool_calls:
            return "stop", "offline fixture replay complete (not a model call)", None
        return "call", "submit_source", {"content": fixture_solution(task)}

    if decider is None:
        adapter = AnthropicDecider if protocol == "anthropic" else OpenAIChatDecider
        decider = offline_decide if mode == "offline" else adapter(config, tool_names=toolbox.names())
    execution_mode = ("offline-contract" if decider is offline_decide else
                      decider.execution_mode if isinstance(decider, RuntimeDecider) else "test-decider")
    pattern = ReActPattern(decider, max_iterations=config.max_calls,
                          stop_when=(lambda t, c: submission_ready(candidate, c)) if stop_policy == "objective" else None)
    if flow == "single_agent":
        orchestrator = SingleAgentOrchestrator(toolbox, pattern)
    elif flow == "state_machine":
        orchestrator = StateMachineOrchestrator([AgentStep("work", pattern=pattern, toolbox=toolbox)])
    else:
        orchestrator = NestedOrchestrator([FnStep("work", lambda t, c: None)], toolbox, pattern, "work")
    skills = SkillLibrary("", rules=[by_extension({".py": "python-quality", ".cpp": "cpp-build"})], inline={
        "python-quality": "Use identity comparisons for None. Keep the public function signature and all other AST structure.",
        "cpp-build": "Use a header path from the supplied inventory. Change only the include line; preserve the program body.",
    })
    policy = ZeroSideEffectPolicy()
    policy.register_cleanup("discard-owned-candidate", candidate.cleanup)
    evidence = EvidenceObserver(code_ref=code_ref, mode=execution_mode)
    chassis = (Chassis("patch-showcase").with_orchestrator(orchestrator)
               .with_payload(source, criteria).with_boundary(boundary)
               .with_knowledge(SkillProvider(skills)).with_failure_policy(policy).observe(evidence).build())
    runtime = {"mode": execution_mode, "requested_mode": mode,
               "stop_policy": stop_policy, "protocol": protocol,
               "adapter": decider.adapter_name if isinstance(decider, RuntimeDecider) else
                          "fixture-replay" if decider is offline_decide else "custom-decider"}
    if mode == "live":
        runtime["config"] = asdict(config)  # Contains the ENV NAME, never its value.
    manifest = assembly_manifest(chassis.report(), runtime=runtime)
    evidence.assembly_id = manifest["content_id"]
    return chassis, candidate, evidence, manifest


def add_model_arguments(parser):
    parser.add_argument("--protocol", choices=list(DEFAULT_ENDPOINTS), default="anthropic")
    parser.add_argument("--model")
    parser.add_argument("--base-url")
    parser.add_argument("--api-key-env", default="BIGMODEL_API_KEY")
    parser.add_argument("--max-calls", type=int, default=8)
    parser.add_argument("--max-tokens", type=int, default=2048)
    parser.add_argument("--context-max-chars", type=int, default=12000)
    parser.add_argument("--timeout", type=float, default=60.0)


def model_config(args):
    prefix = args.protocol.upper()
    return ModelConfig(model=args.model or os.environ.get(prefix + "_MODEL", "glm-5.3-flash"),
                       base_url=args.base_url or os.environ.get(prefix + "_BASE_URL", DEFAULT_ENDPOINTS[args.protocol]),
                       api_key_env=args.api_key_env, max_calls=args.max_calls, max_tokens=args.max_tokens,
                       timeout=args.timeout, context_max_chars=args.context_max_chars)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--mode", choices=["live", "offline"], default="live")
    parser.add_argument("--flow", choices=["nested", "state_machine", "single_agent"], default="nested")
    parser.add_argument("--scenario", choices=["all", "python_quality", "header_build"], default="all")
    parser.add_argument("--stop-policy", choices=["objective", "model"], default="objective")
    parser.add_argument("--output-dir", type=Path)
    add_model_arguments(parser)
    args = parser.parse_args(argv)
    if args.mode == "live" and not os.environ.get(args.api_key_env):
        parser.error(f"{args.api_key_env} is missing. Live mode will NOT fall back to a fixture.")
    config = model_config(args)
    output = args.output_dir or ROOT / "reports" / ("roadmap-showcase-" + uuid.uuid4().hex[:10])
    output.mkdir(parents=True, exist_ok=False)
    revision = subprocess.run(["git", "rev-parse", "HEAD"], cwd=ROOT, capture_output=True, text=True)
    code_ref = revision.stdout.strip() if revision.returncode == 0 else "unknown"
    dirty = subprocess.run(["git", "status", "--porcelain"], cwd=ROOT, capture_output=True, text=True)
    if dirty.stdout.strip():
        code_ref += "+dirty"
    scenarios = ["python_quality", "header_build"] if args.scenario == "all" else [args.scenario]
    summary = {"schema_version": "agent-chassis.showcase/v1", "mode": args.mode, "flow": args.flow,
               "code_ref": code_ref, "stop_policy": args.stop_policy, "protocol": args.protocol,
               "cases": [], "is_benchmark": False}
    for scenario in scenarios:
        print(f"Starting {scenario} ({args.mode}, {args.stop_policy})", flush=True)
        chassis, candidate, evidence, manifest = assemble(scenario, mode=args.mode, flow=args.flow,
                                                         config=config, code_ref=code_ref, stop_policy=args.stop_policy,
                                                         protocol=args.protocol)
        try:
            result = chassis.run_once()
            evidence.dump(str(output / (scenario + ".evidence.json")))
            (output / (scenario + ".manifest.json")).write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
            if result.outcome is Outcome.SUCCEEDED:
                (output / (scenario + ".patch")).write_text(candidate.patch(), encoding="utf-8")
            summary["cases"].append({"scenario": scenario, "outcome": result.outcome.value,
                                     "reason": result.verdict.reason if result.verdict else result.error,
                                     "model_calls": len(evidence.runs[0]["model_calls"]),
                                     "stop_reason": evidence.runs[0]["stop_reason"],
                                     "usage": evidence.runs[0]["usage"],
                                     "evidence": scenario + ".evidence.json"})
            print(f"Finished {scenario}: {result.outcome.value}; model_calls={summary['cases'][-1]['model_calls']}; "
                  f"stop={summary['cases'][-1]['stop_reason']}", flush=True)
        finally:
            chassis.close()
    summary["all_passed"] = all(case["outcome"] == "succeeded" for case in summary["cases"])
    (output / "summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    label = "真实模型运行" if args.mode == "live" else "离线合同回放（不是 AI 实测）"
    lines = ["# Roadmap Showcase", "", label, "", "| 场景 | 结果 | 模型调用 | 停止原因 | 输入 / 输出 token |", "|---|---|---:|---|---|"]
    lines += [f'| {case["scenario"]} | {case["outcome"]} | {case["model_calls"]} | {case["stop_reason"]} | '
              f'{case["usage"]["input_tokens"]} / {case["usage"]["output_tokens"]} |' for case in summary["cases"]]
    lines += ["", "只验证两个公开窄场景，不是生产基准或竞品优劣结论。", ""]
    (output / "summary.md").write_text("\n".join(lines), encoding="utf-8")
    print("\n".join(lines))
    print(f"Evidence: {output}")
    return 0 if summary["all_passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
