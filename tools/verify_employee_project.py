"""Verify generation provenance and recompile saved employee task artifacts."""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / "src"), str(ROOT)]

from employee_factory.verification import read_json, verify_project, verify_runtime
from tools.run_roadmap_showcase import write_json


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--project", type=Path, required=True)
    parser.add_argument("--report", type=Path, action="append", default=[])
    parser.add_argument("--require-live", action="store_true")
    parser.add_argument("--summary-dir", type=Path)
    args = parser.parse_args(argv)
    _, project, spec = verify_project(args.project, require_live=args.require_live)
    generation = read_json(args.project / "generation.evidence.json")["runs"][0]
    results = [verify_runtime(args.project, directory, require_live=args.require_live) for directory in args.report]
    if len({r["workspace_id"] for r in results}) != len(results):
        raise ValueError("Repeated workspace is not a second distinct input")
    summary = {"project_id": project["content_id"], "code_ref": project["code_ref"],
               "generation_mode": generation["mode"], "generation_calls": len(generation["model_calls"]),
               "generation_usage": generation["usage"], "nodes": spec["nodes"], "tasks": results,
               "runtime_verified": bool(results), "distinct_inputs": len(results)}
    lines = ["# 从需求生成员工，到运行任务", "",
             f"生成阶段：{generation['mode']}；模型调用 {summary['generation_calls']} 次。",
             f"独立验收通过的不同源码输入：{len(results)}。", "",
             "| 输入 | 模型调用 | 编译器退出码 | 独立验收 |", "|---|---:|---:|---|"]
    lines.extend(f"| {r['task_key']} | {r['model_calls']} | {r['compiler_exit']} | 通过 |" for r in results)
    lines += ["", "生成的节点：" + " → ".join(n["name"] for n in spec["nodes"]), "",
              "生成与运行分别留证；实际输入数量按上表统计。",
              "公开源码验收验证工程链路，不代表公司生产任务、私有保留集或通用项目生成成功率。",
              "token 为接口报告值，未完整记录缓存计量，不据此计算费用。", ""]
    if args.summary_dir:
        args.summary_dir.mkdir(parents=True, exist_ok=True)
        write_json(args.summary_dir / "summary.json", summary)
        (args.summary_dir / "summary.md").write_text("\n".join(lines), encoding="utf-8")
    print(json.dumps(summary, ensure_ascii=False), flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
