"""Run a four-node configuration workflow and a frozen public knowledge ablation."""
from __future__ import annotations

import argparse
import json
import os
import random
import sys
from dataclasses import asdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT))

from agent_chassis import Chassis, InjectionPoint as P, borrowed_executor
from agent_chassis.evidence import EvidenceObserver, assembly_manifest, content_id
from agent_chassis.knowledge import ScopedKnowledge
from agent_chassis.orchestration import AgentStep, FnStep, ReActPattern, StateMachineOrchestrator
from adapters.anthropic_runtime import AnthropicDecider
from adapters.openai_runtime import OpenAIChatDecider
from adapters.runtime import RuntimeDecider
from payloads.config_policy import ConfigCriteria, ConfigSource, expected_config
from payloads.patch_showcase import Candidate, patch_tools, submission_ready
from tools.run_roadmap_showcase import add_model_arguments, model_config, source_revision, write_json

POLICIES = ("routed", "full", "none")
NODES = ["prepare", "select_context", "repair_config", "verify_config"]
DATA = ROOT / "benchmarks/config_policy/v1.json"


def failure_kind(run):
    if run["outcome"] == "succeeded":
        return "none"
    # Chassis also copies a rejected verdict's reason into error; that is not
    # a transport or execution exception. Exceptions have no completed verdict.
    return "not_accepted" if run["verdict"] is not None and not run["verdict"]["done"] else "execution_error"


def assemble(case, snapshot, *, mode, context_policy, config, protocol="anthropic", code_ref="unknown", decider=None):
    if mode not in ("live", "offline") or context_policy not in POLICIES or protocol not in ("anthropic", "openai"):
        raise ValueError("Unsupported configuration")
    source = ConfigSource(case)
    task = source.task
    expected_config(snapshot, task)  # Validate policy completeness before any request.
    candidate = Candidate(task.payload["source"], task.payload["target"]["path"])
    criteria = ConfigCriteria(candidate, snapshot)
    boundary = borrowed_executor("config-editor")
    toolbox = patch_tools(candidate, criteria, task, boundary)

    def offline_decider(t, ctx, box):
        ctx.chassis.inject(P.BEFORE_EXECUTOR, t, ctx)
        ctx.context_for("offline-fixture-replay", [P.BEFORE_EXECUTOR], max_chars=config.context_max_chars)
        return "call", "submit_source", {"content": json.dumps(expected_config(snapshot, t), indent=2)}

    if decider is None:
        adapter = AnthropicDecider if protocol == "anthropic" else OpenAIChatDecider
        decider = offline_decider if mode == "offline" else adapter(config, tool_names=toolbox.names())
    execution_mode = ("offline-contract" if decider is offline_decider else
                      decider.execution_mode if isinstance(decider, RuntimeDecider) else "test-decider")

    def prepare(t, ctx):
        ctx.facts["phase"] = "prepare"

    def select_context(t, ctx):
        ctx.facts["phase"] = "repair"

    def verify(t, ctx):
        ctx.facts["phase"] = "verify"
        ctx.chassis.inject(P.BEFORE_EXECUTOR, t, ctx)  # Explicitly expire repair-only documents.
        if criteria.validate(t).done:
            ctx.facts["verified_candidate"] = candidate.digests()["candidate_sha256"]

    pattern = ReActPattern(decider, max_iterations=config.max_calls,
                          stop_when=lambda t, ctx: submission_ready(candidate, ctx))
    flow = StateMachineOrchestrator([FnStep("prepare", prepare), FnStep("select_context", select_context),
                                    AgentStep("repair_config", pattern=pattern, toolbox=toolbox),
                                    FnStep("verify_config", verify)])
    providers = []
    if context_policy != "none":
        for doc in snapshot["documents"]:
            # Full and routed expose byte-identical documents, changing only selection.
            text = json.dumps({"applies_to": doc["scope"], "rules": doc["rules"]}, sort_keys=True)
            providers.append(ScopedKnowledge(text, name=doc["name"], version=doc["version"],
                             task_scope=doc["scope"] if context_policy == "routed" else {},
                             fact_scope={"phase": "repair"}))
    evidence = EvidenceObserver(code_ref=code_ref, mode=execution_mode)
    chassis = (Chassis("config-policy-workflow").with_payload(source, criteria).with_orchestrator(flow)
               .with_boundary(boundary).with_knowledge(*providers).observe(evidence).build())
    manifest = assembly_manifest(chassis.report(), runtime={"mode": execution_mode, "requested_mode": mode,
        "context_policy": context_policy, "protocol": protocol, "config": asdict(config),
        "nodes": NODES, "policy_id": content_id(snapshot), "case_id": case["id"]})
    evidence.assembly_id = manifest["content_id"]
    return chassis, candidate, evidence, manifest


def trial_order(snapshot, repeats, seed):
    rng, trials = random.Random(seed), []
    for repeat in range(1, repeats + 1):
        block = [{"case_id": c["id"], "context_policy": policy, "repeat": repeat}
                 for c in snapshot["cases"] for policy in POLICIES]
        rng.shuffle(block)
        trials.extend(block)
    return [{"id": f"trial-{i:03d}", **t} for i, t in enumerate(trials, 1)]


def summarize(plan, results):
    groups = []
    for policy in POLICIES:
        rows = [r for r in results if r["context_policy"] == policy]
        complete_usage = bool(rows) and all(r["usage"]["complete"] for r in rows)
        groups.append({"context_policy": policy,
            "planned": sum(t["context_policy"] == policy for t in plan["trials"]), "completed": len(rows),
            "accepted": sum(r["outcome"] == "succeeded" for r in rows),
            "execution_errors": sum(r["failure_kind"] == "execution_error" for r in rows),
            "model_calls": sum(r["model_calls"] for r in rows),
            "context_chars": sum(r["context_chars"] for r in rows),
            "usage": {k: sum(r["usage"][k] for r in rows) if complete_usage else None
                      for k in ("input_tokens", "output_tokens")}})
    return {"plan_id": plan["content_id"], "mode": plan["mode"], "results": results, "groups": groups,
            "coverage_complete": len(results) == len(plan["trials"]), "planned": len(plan["trials"]),
            "completed": len(results), "pending": len(plan["trials"]) - len(results)}


def markdown(summary):
    label = "真实模型对照" if summary["mode"] == "live" else "离线合同回放，不能用于比较 AI 能力"
    lines = ["# 项目规范驱动的配置修复", "", label,
             f"\n完成 {summary['completed']}/{summary['planned']}；待运行 {summary['pending']}。", "",
             "| 上下文 | 验收通过 / 计划 | 执行异常 | 模型调用 | 输入 / 输出 token | 累计上下文字符 |",
             "|---|---|---:|---:|---|---:|"]
    for g in summary["groups"]:
        lines.append(f"| {g['context_policy']} | {g['accepted']} / {g['planned']} | {g['execution_errors']} | {g['model_calls']} | "
                     f"{g['usage']['input_tokens']} / {g['usage']['output_tokens']} | {g['context_chars']} |")
    lines += ["", "四个公开合成案例，多次重复不是更多独立任务；没有私有保留集。失败保留在分母。",
              "none / full 是相同模型与工具下的上下文对照，未包含独立执行器或脚本竞品基线。",
              "产出 deployment.json 和 patch，不连接集群或实际发布。", ""]
    return "\n".join(lines)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--mode", choices=["live", "offline"], default="offline")
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--repeats", type=int, default=2)
    parser.add_argument("--seed", type=int, default=42)
    add_model_arguments(parser)
    args = parser.parse_args(argv)
    if args.repeats < 1:
        parser.error("repeats must be positive")
    if args.mode == "live" and not os.environ.get(args.api_key_env):
        parser.error(f"Missing {args.api_key_env}; no offline fallback")
    config = model_config(args)
    snapshot = json.loads(DATA.read_text(encoding="utf-8"))
    plan = {"schema_version": "config-policy-experiment/v1", "snapshot": snapshot,
            "mode": args.mode, "protocol": args.protocol, "config": asdict(config),
            "code_ref": source_revision(), "repeats": args.repeats, "seed": args.seed,
            "trials": trial_order(snapshot, args.repeats, args.seed)}
    plan["content_id"] = content_id(plan)
    output = args.output_dir
    output.mkdir(parents=True, exist_ok=False)
    write_json(output / "plan.json", plan)  # Freeze all inputs and ordering before first request.
    results = []

    def checkpoint():
        summary = summarize(plan, results)
        write_json(output / "summary.json", summary)
        (output / "summary.md").write_text(markdown(summary), encoding="utf-8")
        return summary

    checkpoint()
    for trial in plan["trials"]:
        case = next(c for c in snapshot["cases"] if c["id"] == trial["case_id"])
        chassis, candidate, evidence, manifest = assemble(case, snapshot, mode=args.mode,
            context_policy=trial["context_policy"], config=config, protocol=args.protocol, code_ref=plan["code_ref"])
        prefix = output / trial["id"]
        write_json(prefix.with_suffix(".manifest.json"), manifest)
        try:
            chassis.run_once()
            evidence.dump(str(prefix.with_suffix(".evidence.json")))
            if candidate.content is not None:
                prefix.with_suffix(".candidate.json").write_text(candidate.content, encoding="utf-8")
                prefix.with_suffix(".patch").write_text(candidate.patch(), encoding="utf-8")
            run = evidence.runs[0]
            row = {**trial, "outcome": run["outcome"], "execution_mode": run["mode"],
                   "failure_kind": failure_kind(run),
                   "model_calls": len(run["model_calls"]), "usage": run["usage"],
                   "context_chars": sum(r["chars"] for r in run["context_receipts"])}
            results.append(row)
            checkpoint()
            print(f"{trial['id']} {trial['case_id']} {trial['context_policy']}: {row['outcome']}; "
                  f"calls={row['model_calls']}; usage={row['usage']}", flush=True)
        finally:
            chassis.close()
    summary = checkpoint()
    print(markdown(summary), flush=True)
    # Baseline failures are valid measurements. Routed failures fail the showcase gate.
    routed = summary["groups"][0]
    return 0 if (summary["coverage_complete"] and routed["accepted"] == routed["planned"]
                 and not any(g["execution_errors"] for g in summary["groups"])) else 1


if __name__ == "__main__":
    raise SystemExit(main())
