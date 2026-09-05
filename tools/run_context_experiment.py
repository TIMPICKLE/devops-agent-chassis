"""Repeatable development ablation: routed vs full vs no injected skill context.

Two public development fixtures, not a production benchmark. Offline mode only
checks the experiment/evidence plumbing and cannot measure model quality.
"""
from __future__ import annotations

import argparse
import json
import os
import random
import sys
import uuid
from dataclasses import asdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT))

from agent_chassis.evidence import content_id
from payloads.patch_showcase import HeaderBuildSource, PythonQualitySource
from tools.run_roadmap_showcase import (
    CONTEXT_POLICIES, SCENARIOS, SKILL_CONTENT, add_model_arguments, execute_scenario,
    model_config, source_revision, write_json,
)


def trial_order(repeats, seed):
    rng = random.Random(seed)
    trials = []
    for repeat in range(1, repeats + 1):
        block = [{"scenario": scenario, "context_policy": policy, "repeat": repeat}
                 for scenario in SCENARIOS for policy in CONTEXT_POLICIES]
        rng.shuffle(block)
        trials.extend(block)
    return [{"trial_id": f"trial-{index:03d}", **trial} for index, trial in enumerate(trials, 1)]


def summarize(plan, trials):
    groups = []
    for policy in CONTEXT_POLICIES:
        planned = sum(t["context_policy"] == policy for t in plan["trials"])
        selected = [t for t in trials if t["context_policy"] == policy]
        completed = [t for t in selected if t["outcome"] != "not_run"]
        usage_complete = bool(completed) and all(t["usage"]["complete"] for t in completed)
        groups.append({"context_policy": policy, "planned": planned, "completed": len(completed),
            "not_run": sum(t["outcome"] == "not_run" for t in selected), "pending": planned - len(selected),
            "accepted": sum(t["outcome"] == "succeeded" for t in completed),
            "model_calls": sum(t["model_calls"] for t in completed),
            "context_chars": sum(t["context_chars"] for t in completed),
            "elapsed_ms": sum(t["elapsed_ms"] for t in completed),
            "usage": {"complete": usage_complete,
                      "input_tokens": sum(t["usage"]["input_tokens"] for t in completed) if usage_complete else None,
                      "output_tokens": sum(t["usage"]["output_tokens"] for t in completed) if usage_complete else None}})
    complete = len(trials) == len(plan["trials"]) and all(t["outcome"] != "not_run" for t in trials)
    return {"schema_version": "agent-chassis.context-experiment/v1", "plan_id": plan["content_id"],
            "mode": plan["mode"], "code_ref": plan["code_ref"], "is_benchmark": False,
            "execution_modes": sorted({t["execution_mode"] for t in trials if t["outcome"] != "not_run"}),
            "coverage_complete": complete, "all_passed": complete and all(t["outcome"] == "succeeded" for t in trials),
            "model_calls": sum(g["model_calls"] for g in groups), "groups": groups, "trials": trials}


def markdown(summary):
    modes = summary["execution_modes"]
    label = ("真实模型 · 开发夹具对照" if modes == ["live"] else
             "离线合同回放 · 不能衡量模型质量" if modes == ["offline-contract"] else
             "尚无已完成的运行证据" if not modes else "测试或混合执行模式 · 不能声明真实模型实测")
    lines = ["# 上下文对照实验", "", label, "",
             "| 注入策略 | 通过 / 计划 | 完成 / 未运行 / 待运行 | 模型调用 | 输入 / 输出 token | 知识字符合计 | 耗时 ms |",
             "|---|---|---|---:|---|---:|---:|"]
    for group in summary["groups"]:
        usage = group["usage"]
        tokens = f"{usage['input_tokens']} / {usage['output_tokens']}" if usage["complete"] else "未知"
        lines.append(f"| {group['context_policy']} | {group['accepted']} / {group['planned']} | "
                     f"{group['completed']} / {group['not_run']} / {group['pending']} | {group['model_calls']} | "
                     f"{tokens} | {group['context_chars']} | {group['elapsed_ms']} |")
    lines += ["", "知识字符按每次请求实际读取的 receipt 求和；不是完整提示词 token。",
              "none 仅移除附加规范，任务描述、工具 schema、系统指令与观察结果保持。",
              "只有两个公开开发夹具；重复运行不是新增独立任务，不能据此宣称生产成功率或统计显著性。",
              "未运行或待运行存在时，不应直接比较各组 token 总量。耗时包含模型服务、网络和本地验收。", "",
              "| 试次 | 场景 | 策略 | 结果 | 调用 | 证据 |", "|---|---|---|---|---:|---|"]
    for trial in summary["trials"]:
        evidence = f"[JSON]({trial['evidence']})" if "evidence" in trial else trial.get("reason", "")
        lines.append(f"| {trial['trial_id']} | {trial['scenario']} | {trial['context_policy']} | "
                     f"{trial['outcome']} | {trial.get('model_calls', '—')} | {evidence} |")
    return "\n".join(lines) + "\n"


def save_summary(output, plan, trials):
    summary = summarize(plan, trials)
    write_json(output / "summary.json", summary)
    (output / "summary.md").write_text(markdown(summary), encoding="utf-8")
    return summary


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--mode", choices=["live", "offline"], default="offline")
    parser.add_argument("--flow", choices=["nested", "state_machine", "single_agent"], default="nested")
    parser.add_argument("--stop-policy", choices=["objective", "model"], default="objective")
    parser.add_argument("--repeats", type=int, default=1)
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument("--max-total-model-calls", type=int, default=24)
    parser.add_argument("--output-dir", type=Path)
    add_model_arguments(parser)
    args = parser.parse_args(argv)
    if args.repeats < 1 or args.max_total_model_calls < 1:
        parser.error("repeats and max-total-model-calls must be positive")
    config = model_config(args)
    if args.mode == "live" and not os.environ.get(config.api_key_env):
        parser.error(f"{config.api_key_env} is missing; no offline fallback")
    output = args.output_dir or ROOT / "reports" / ("roadmap-showcase-ablation-" + uuid.uuid4().hex[:10])
    revision = source_revision()
    output.mkdir(parents=True, exist_ok=False)
    plan = {"schema_version": "agent-chassis.context-experiment-plan/v1", "mode": args.mode,
            "protocol": args.protocol, "model_config": asdict(config), "flow": args.flow,
            "stop_policy": args.stop_policy, "code_ref": revision, "seed": args.seed, "repeats": args.repeats,
            "max_total_model_calls": args.max_total_model_calls, "skill_content_id": content_id(SKILL_CONTENT),
            "inputs": {name: content_id(factory().task.payload) for name, factory in
                       [("python_quality", PythonQualitySource), ("header_build", HeaderBuildSource)]},
            "trials": trial_order(args.repeats, args.seed)}
    plan["content_id"] = content_id(plan)
    write_json(output / "experiment-plan.json", plan)  # Freeze order and conditions BEFORE any request.
    trials, used_calls = [], 0
    summary = save_summary(output, plan, trials)
    for spec in plan["trials"]:
        if args.mode == "live" and args.max_total_model_calls - used_calls < config.max_calls:
            # Reserve a whole unchanged per-trial budget; do not quietly give a
            # later condition a smaller reasoning budget to make it fit.
            trials.append({**spec, "outcome": "not_run", "reason": "insufficient remaining call budget"})
        else:
            result = execute_scenario(spec["scenario"], output, prefix=spec["trial_id"],
                mode=args.mode, protocol=args.protocol, config=config, code_ref=revision, flow=args.flow,
                stop_policy=args.stop_policy, context_policy=spec["context_policy"])
            trials.append({**spec, **result})
            used_calls += result["model_calls"]
        summary = save_summary(output, plan, trials)
    print(markdown(summary), flush=True)
    print(f"Experiment evidence: {output}", flush=True)
    return 0 if summary["all_passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
