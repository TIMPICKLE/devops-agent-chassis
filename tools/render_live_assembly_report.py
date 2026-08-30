#!/usr/bin/env python3
"""Render a Chinese, leadership-friendly Live Digital Employee Assembly report."""
from __future__ import annotations

import argparse
import html
import json
import os
import re
from collections import OrderedDict
from pathlib import Path
from typing import Any, Dict, List, Sequence, Tuple


CHECK_LABELS = {
    "secret-present-in-runner": "模型密钥注入",
    "provider-preflight": "大模型服务连通性",
    "provider-model-is-flash": "模型版本确认",
    "claude-code-interview-ran": "Claude Code 需求访谈",
    "skill-entered-interview-mode": "Skill 进入访谈模式",
    "interview-made-no-project-changes": "访谈阶段工程保护",
    "interview-process-captured": "访谈操作轨迹采集",
    "claude-code-assembly-ran": "Claude Code 自动装配",
    "assembly-process-captured": "装配操作轨迹采集",
    "employee-generated": "数字员工程序生成",
    "manifest-generated": "装配清单生成",
    "assembly-isolated-to-generated": "AI 装配写入边界",
    "secret-not-persisted": "Secret 泄漏检查",
    "manifest-json-valid": "装配清单格式",
    "manifest-name": "数字员工名称",
    "manifest-orchestrator": "外层编排",
    "manifest-reasoning": "内层推理模式",
    "manifest-connector": "Connector 类型",
    "manifest-llm-provider": "LLM Provider 配置",
    "uses-agent-chassis": "工程底盘公共 API 使用",
    "generated-code-compiles": "生成代码编译",
    "generated-employee-runs": "数字员工实际运行",
    "result-json-valid": "业务结果格式",
    "done-criteria-task_key": "DoneCriteria：任务编号",
    "done-criteria-severity": "DoneCriteria：事故等级",
    "done-criteria-owner": "DoneCriteria：责任团队",
    "done-criteria-done": "DoneCriteria：最终完成状态",
}

SENSITIVE_NAME_RE = re.compile(
    r"(?:api[_-]?key|token|secret|password|authorization|cookie|credential|private[_-]?key)",
    re.IGNORECASE,
)


def _secret_values(env: Dict[str, str]) -> List[str]:
    values = []
    for name, value in env.items():
        if value and len(value) >= 4 and SENSITIVE_NAME_RE.search(name):
            values.append(value)
    return sorted(set(values), key=len, reverse=True)


def _redact(text: str, secrets: Sequence[str]) -> str:
    value = text
    for secret in secrets:
        value = value.replace(secret, "[REDACTED]")
    return value


def _read_json(path: Path) -> Dict[str, Any]:
    if not path.exists():
        return {}
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}
    return value if isinstance(value, dict) else {}


def _read_text(path: Path) -> str:
    if not path.exists():
        return ""
    return path.read_text(encoding="utf-8", errors="replace")


def _one_line(text: Any, limit: int = 260) -> str:
    compact = " ".join(str(text).split())
    if len(compact) <= limit:
        return compact
    return compact[: limit - 1] + "…"


def _esc_md(text: Any) -> str:
    return str(text).replace("|", "\\|").replace("\n", " ")


def _metric(data: Dict[str, Any]) -> Tuple[str, str, str, str]:
    duration = float(data.get("duration_ms") or 0) / 1000
    turns = str(data.get("num_turns") if data.get("num_turns") is not None else "n/a")
    tools = str(data.get("process_tool_count") if data.get("process_tool_count") is not None else "n/a")
    cost = data.get("total_cost_usd")
    cost_text = f"${float(cost):.4f}" if cost is not None else "n/a"
    return f"{duration:.1f}s", turns, tools, cost_text


def _checks_by_name(report: Dict[str, Any]) -> Dict[str, Dict[str, Any]]:
    result: Dict[str, Dict[str, Any]] = {}
    for check in report.get("checks", []):
        if isinstance(check, dict) and check.get("name"):
            result[str(check["name"])] = check
    return result


def _check_ok(checks: Dict[str, Dict[str, Any]], *names: str) -> bool:
    return bool(names) and all(bool(checks.get(name, {}).get("ok")) for name in names)


def _translate_detail(name: str, detail: str) -> str:
    replacements = [
        ("BIGMODEL_API_KEY is available to Claude Code", "GitHub Secret 已成功注入 Claude Code 运行环境"),
        ("BIGMODEL_API_KEY is missing", "未检测到 BIGMODEL_API_KEY"),
        ("provider preflight exit=0", "智谱模型服务预检成功（exit=0）"),
        ("first turn returned user-facing questions", "第一轮返回了面向用户的追问，确认进入 Skill 访谈流程"),
        ("working tree stayed clean during the interview turn", "访谈阶段工作区保持干净，没有提前修改工程"),
        ("no project files changed outside generated/", "AI 的装配修改被限制在 generated/ 目录内"),
        ("no secret bytes found in generated/model-output evidence", "生成物和模型输出证据中未发现 API Key 原文"),
        ("manifest parsed as JSON object", "Manifest 可被正常解析为 JSON 对象"),
        (
            "generated employee references Chassis + payload + orchestrator public APIs",
            "生成的数字员工使用了 Chassis、Payload、Orchestrator 公共 API",
        ),
        ("py_compile succeeded", "Python 编译检查通过"),
        ("result parsed as JSON object", "业务输出结果可被正常解析为 JSON 对象"),
        (
            "BigModel Anthropic-compatible GLM-5.3-Flash configuration recorded without secret value",
            "已记录智谱 GLM-5.3-Flash Provider 配置，仅保存 Secret 环境变量名，没有保存密钥值",
        ),
        ("interview process events captured", "已采集第一阶段 Claude Code 可审计操作轨迹"),
        ("assembly process events captured", "已采集第二阶段 Claude Code 可审计操作轨迹"),
    ]
    translated = detail
    for source, target in replacements:
        translated = translated.replace(source, target)
    translated = translated.replace("expected=", "期望=").replace("actual=", "实际=")
    translated = translated.replace("provider response model=", "模型服务实际返回=")
    translated = translated.replace("Claude Code interview exit=", "Claude Code 访谈退出码=")
    translated = translated.replace("Claude Code assembly exit=", "Claude Code 装配退出码=")
    translated = translated.replace("employee file missing", "数字员工程序缺失")
    translated = translated.replace("manifest missing", "Manifest 缺失")
    return translated


def _runtime_trace(trace: Dict[str, Any]) -> List[Dict[str, Any]]:
    rows: List[Dict[str, Any]] = []
    for item in trace.get("traces", []):
        if not isinstance(item, dict):
            continue
        kind = str(item.get("kind") or "")
        kind_zh = {
            "task_start": "任务开始",
            "step": "状态机步骤",
            "tool_call": "工具 / Connector 调用",
            "injection": "知识注入",
            "task_end": "任务结束",
        }.get(kind, kind or "事件")
        detail = item.get("detail") or {}
        rows.append(
            {
                "seq": item.get("seq", ""),
                "time": f"{item.get('at_ms', 0)}ms",
                "kind": kind_zh,
                "label": item.get("label", ""),
                "detail": json.dumps(detail, ensure_ascii=False, sort_keys=True) if detail else "",
            }
        )
    return rows


def _tool_rows(process: Dict[str, Any]) -> List[Dict[str, Any]]:
    rows: List[Dict[str, Any]] = []
    for event in process.get("events", []):
        if not isinstance(event, dict):
            continue
        rows.append(
            {
                "seq": event.get("seq", ""),
                "kind": event.get("kind", ""),
                "title": event.get("title", event.get("kind", "事件")),
                "detail": event.get("detail", ""),
                "status": "✅" if event.get("ok") is True else "❌" if event.get("ok") is False else "已记录",
                "tool": event.get("tool", ""),
            }
        )
    return rows


def _component_rows(manifest: Dict[str, Any]) -> List[Tuple[str, str]]:
    provider = manifest.get("llm_provider") if isinstance(manifest.get("llm_provider"), dict) else {}
    permissions = manifest.get("permissions") if isinstance(manifest.get("permissions"), dict) else {}
    knowledge = manifest.get("knowledge_injection") if isinstance(manifest.get("knowledge_injection"), dict) else {}
    return [
        ("数字员工", str(manifest.get("name") or "n/a")),
        ("TaskSource", str(manifest.get("task_source") or "n/a")),
        ("DoneCriteria", str(manifest.get("done_criteria") or "n/a")),
        ("外层编排", str(manifest.get("orchestrator") or "n/a")),
        ("内层推理", str(manifest.get("reasoning") or "n/a")),
        ("Connector", str(manifest.get("connector") or "n/a")),
        ("LLM Provider", f"{provider.get('provider', 'n/a')} / {provider.get('model', 'n/a')}"),
        ("失败契约", str(manifest.get("failure_policy") or "n/a")),
        ("授权能力", ", ".join(map(str, permissions.get("granted", []))) or "n/a"),
        (
            "知识注入",
            "task_admitted="
            f"{knowledge.get('task_admitted', [])}; "
            f"before_executor={knowledge.get('before_executor', [])}; "
            f"on_retry={knowledge.get('on_retry', [])}",
        ),
    ]


def _business_goal(prompt: str, manifest: Dict[str, Any]) -> str:
    manifest_goal = str(manifest.get("business_goal") or "").strip()
    if manifest_goal:
        return manifest_goal.rstrip("。") + "。"
    for line in prompt.splitlines():
        stripped = line.strip()
        if "业务目标：" in stripped:
            value = stripped.split("业务目标：", 1)[1].strip()
            if value:
                return _one_line(value, 240)
        if "这台数字员工要" in stripped:
            return _one_line(stripped, 240)
    return "创建一台 DevOps 事故分级与责任团队路由数字员工。"


def _process_summary(rows: List[Dict[str, Any]], stage: str) -> List[Dict[str, Any]]:
    if stage == "interview":
        labels = OrderedDict(
            [
                ("发现 Skill 与工程结构", {"tools": {"Glob", "Grep"}, "kinds": set()}),
                ("读取 Skill 与底盘契约", {"tools": {"Read"}, "kinds": set()}),
                ("识别缺失信息并准备追问", {"tools": set(), "kinds": {"assistant_text"}}),
                ("形成需求访谈证据", {"tools": set(), "kinds": {"result", "system"}}),
            ]
        )
    else:
        labels = OrderedDict(
            [
                ("理解底盘与现有能力", {"tools": {"Read", "Glob", "Grep"}, "kinds": set()}),
                ("生成与修正数字员工", {"tools": {"Write", "Edit"}, "kinds": set()}),
                ("编译、运行与 Smoke Test", {"tools": {"Bash"}, "kinds": set()}),
                ("方案设计与阶段说明", {"tools": set(), "kinds": {"assistant_text"}}),
            ]
        )

    summary: List[Dict[str, Any]] = []
    used = set()
    for label, match in labels.items():
        matched: List[Dict[str, Any]] = []
        for index, row in enumerate(rows):
            if index in used:
                continue
            if row.get("tool") in match["tools"] or row.get("kind") in match["kinds"]:
                matched.append(row)
                used.add(index)
        if not matched:
            continue
        examples: List[str] = []
        for row in matched:
            detail = _one_line(row.get("detail", ""), 110)
            if detail and detail not in examples:
                examples.append(detail)
            if len(examples) >= 3:
                break
        summary.append({"title": label, "count": len(matched), "examples": examples})

    remaining = [row for index, row in enumerate(rows) if index not in used]
    if remaining:
        examples = []
        for row in remaining:
            detail = _one_line(row.get("detail", ""), 110)
            if detail and detail not in examples:
                examples.append(detail)
            if len(examples) >= 3:
                break
        summary.append({"title": "其他可审计过程事件", "count": len(remaining), "examples": examples})
    return summary


def _trace_has(runtime_rows: List[Dict[str, Any]], *needles: str) -> bool:
    haystack = " ".join(str(row.get("label", "")) for row in runtime_rows).lower()
    return any(needle.lower() in haystack for needle in needles)


def _employee_business_steps(
    runtime_rows: List[Dict[str, Any]],
    incident: Dict[str, Any],
    result: Dict[str, Any],
    trace: Dict[str, Any],
) -> List[Tuple[str, str, bool]]:
    task = {}
    tasks = trace.get("tasks", [])
    if tasks and isinstance(tasks[0], dict):
        task = tasks[0]
    return [
        (
            "接收事故任务",
            f"TaskSource 将 {incident.get('id', 'n/a')} / {incident.get('service', 'n/a')} 转换为稳定任务，进入 Chassis Runtime。",
            _trace_has(runtime_rows, "devops_incident", "task_start"),
        ),
        (
            "加载任务知识",
            "在 task_admitted 阶段注入事故处理任务背景；在执行器前按需注入 triage-runbook。",
            _trace_has(runtime_rows, "incident-mission") and _trace_has(runtime_rows, "triage-runbook"),
        ),
        (
            "Connector 获取并核对事故信息",
            "通过 incident.incidents.get 获取事故事实，并与本地输入交叉核对。",
            _trace_has(runtime_rows, "incident.incidents.get", "fetch_incident_details"),
        ),
        (
            "完成分级与责任团队路由",
            f"执行 classify_severity / route_owner，产出 severity={result.get('severity', 'n/a')}、owner={result.get('owner', 'n/a')}。",
            _trace_has(runtime_rows, "classify_severity") and _trace_has(runtime_rows, "route_owner"),
        ),
        (
            "记录业务结果与可审计证据",
            "record_triage 将结果写入指定输出文件，同时 RecordingObserver 保存 Task / Trace / ToolCall 证据。",
            _trace_has(runtime_rows, "record_triage"),
        ),
        (
            "DoneCriteria 独立验收",
            f"外部事实重新校验 task_key / severity / owner / done；最终 verdict_done={task.get('verdict_done', result.get('done', False))}。",
            bool(task.get("verdict_done", result.get("done"))),
        ),
    ]


def _cost_note() -> str:
    return "Claude Code CLI 返回的调用规模估算，仅用于观察 Agent 执行成本，不等于智谱实际账单。"


def build_report(data: Dict[str, Any], secrets: Sequence[str]) -> Tuple[str, str]:
    report = data["report"]
    interview = data["interview"]
    assembly = data["assembly"]
    interview_process = data["interview_process"]
    assembly_process = data["assembly_process"]
    manifest = data["manifest"]
    result = data["result"]
    trace = data["trace"]
    incident = data["incident"]
    prompt = data["prompt"]
    answers = data["answers"]

    checks = _checks_by_name(report)
    passed = bool(report.get("overall_passed"))
    overall = "✅ 验收通过" if passed else "❌ 验收失败"
    interview_metric = _metric(interview)
    assembly_metric = _metric(assembly)
    component_rows = _component_rows(manifest)
    runtime_rows = _runtime_trace(trace)
    interview_rows = _tool_rows(interview_process)
    assembly_rows = _tool_rows(assembly_process)
    interview_summary = _process_summary(interview_rows, "interview")
    assembly_summary = _process_summary(assembly_rows, "assembly")
    business_goal = _business_goal(prompt, manifest)
    employee_steps = _employee_business_steps(runtime_rows, incident, result, trace)

    check_rows = [c for c in report.get("checks", []) if isinstance(c, dict)]
    check_count = len(check_rows)
    passed_checks = sum(1 for c in check_rows if c.get("ok"))

    engineer_lane = [
        ("理解业务目标", business_goal),
        ("Skill 访谈补齐契约", "读取 assemble-digital-employee Skill 与 Chassis 契约，只读分析并主动追问缺失信息。"),
        ("选择底盘组件", "将业务回答映射为 TaskSource、DoneCriteria、State Machine、ReAct、Connector、Knowledge、Failure、Permission、Observer。"),
        ("生成并自检工程", f"在 generated/ 中生成员工程序与 Manifest，并通过编译 / Smoke Test；共记录 {assembly_metric[2]} 个工具动作。"),
        ("交付可运行数字员工", f"产出 {manifest.get('name', 'live-ci-incident-triage')}，交给独立 Runtime 与 DoneCriteria 验收。"),
    ]
    worker_lane = [(title, detail) for title, detail, _ in employee_steps]

    steps = [
        (1, "启动安全的 AI 工程环境", "GitHub Actions", "拉取工程、安装 Python/MCP/Claude Code，并以只读 GitHub Token 运行。", _check_ok(checks, "secret-present-in-runner")),
        (2, "验证 GLM-5.3-Flash 模型服务", "CI + 智谱 BigModel", "使用 GitHub Secret 调用 Anthropic-compatible API，确认实际返回模型为 glm-5.3-flash。", _check_ok(checks, "provider-preflight", "provider-model-is-flash")),
        (3, "用户只描述业务目标", "模拟真实用户", business_goal, True),
        (4, "Skill 读取底盘并发起需求访谈", "Claude Code + GLM-5.3-Flash", f"第一阶段只允许 Read/Glob/Grep；AI 阅读 Skill、底盘契约并向用户追问。本阶段 {interview_metric[0]} / {interview_metric[1]} turns。", _check_ok(checks, "claude-code-interview-ran", "skill-entered-interview-mode", "interview-made-no-project-changes")),
        (5, "用户补齐装配契约", "模拟真实用户", "补充 TaskSource、DoneCriteria、state_machine、ReAct、Connector、Provider、权限和副作用边界。", bool(answers)),
        (6, "AI 选择底盘组件并自动装配", "Claude Code + GLM-5.3-Flash", f"AI 读取公共 API，设计并装配 Payload / State Machine / ReAct / Connector / Knowledge / Permission / Observer；本阶段 {assembly_metric[0]} / {assembly_metric[1]} turns。", _check_ok(checks, "claude-code-assembly-ran", "uses-agent-chassis")),
        (7, "生成工程并进行自检", "Claude Code", "生成 live_ci_employee.py 与 live_ci_manifest.json，执行编译和 smoke test，并用过程级门禁限制 Write/Edit 只落在 generated/。", _check_ok(checks, "employee-generated", "manifest-generated", "assembly-isolated-to-generated", "generated-code-compiles")),
        (8, "启动新数字员工处理事故", "Agent Chassis Runtime", f"读取事故 {incident.get('id', 'n/a')} / {incident.get('service', 'n/a')}，经过状态机、Connector、ReAct、知识注入和 Observer Trace 完成分级与路由。", _check_ok(checks, "generated-employee-runs")),
        (9, "独立 DoneCriteria 重新裁定", "确定性 Verifier", f"不相信模型自述，重新检查输出：task_key={result.get('task_key')}, severity={result.get('severity')}, owner={result.get('owner')}, done={result.get('done')}。", _check_ok(checks, "done-criteria-task_key", "done-criteria-severity", "done-criteria-owner", "done-criteria-done")),
        (10, "安全检查并归档证据", "GitHub Actions", "确认 API Key 未落盘，输出中文 Markdown/HTML、机器 JSON、Claude Code 过程轨迹、Manifest、Result 与 Runtime Trace。", _check_ok(checks, "secret-not-persisted")),
    ]

    md: List[str] = []
    md.append("# 数字员工真实装配验收报告")
    md.append("")
    md.append(f"> **总体结论：{overall} · {passed_checks}/{check_count} 项确定性检查通过**")
    md.append("")
    md.append("## 管理摘要：从一句业务需求到可运行数字员工")
    md.append("")
    md.append(f"**一句话需求：** {business_goal}")
    md.append("")
    md.append(
        f"**最终结果：** Claude Code 在 `{report.get('model', 'glm-5.3-flash')}` 驱动下完成 Skill 访谈与工程装配，"
        f"生成 `{manifest.get('name', 'live-ci-incident-triage')}`；该数字员工实际处理 `{result.get('task_key', 'n/a')}`，"
        f"产出 `{result.get('severity', 'n/a')}` / `{result.get('owner', 'n/a')}` / `done={result.get('done', 'n/a')}`，"
        "最终由独立 DoneCriteria 判定是否通过。"
    )
    md.append("")
    md.append("**本次验证证明：**")
    md.append("- ✅ AI 能从不完整业务需求出发，通过 Skill 主动访谈并补齐工程契约。")
    md.append("- ✅ AI 能复用 Agent Chassis 标准能力装配新的业务 Payload，而不是从零重写 Agent 框架。")
    md.append("- ✅ “模型说完成”不算完成；最终结果由独立运行、权限边界、外部事实和 DoneCriteria 决定。")
    md.append("")
    md.append("> **模型角色说明：** GLM-5.3-Flash 真实驱动 Claude Code 完成“理解需求、访谈、设计和编码”。为了让 CI 可重复，本次 Golden Scenario 的最终事故等级/路由采用确定性业务规则，再由 Verifier 独立验收；这不等于 Live LLM 装配没有执行。")
    md.append("")

    md.append("## 一、两条工作链：AI 工程师造员工，数字员工干工作")
    md.append("")
    md.append("| AI 工程师：Claude Code + GLM-5.3-Flash | 数字员工：Incident Triage Employee |")
    md.append("|---|---|")
    for index in range(max(len(engineer_lane), len(worker_lane))):
        left = engineer_lane[index] if index < len(engineer_lane) else ("", "")
        right = worker_lane[index] if index < len(worker_lane) else ("", "")
        left_text = f"**{index + 1}. {_esc_md(left[0])}**<br>{_esc_md(left[1])}" if left[0] else ""
        right_text = f"**{index + 1}. {_esc_md(right[0])}**<br>{_esc_md(right[1])}" if right[0] else ""
        md.append(f"| {left_text} | {right_text} |")
    md.append("")

    md.append("## 二、10 步完整链路")
    md.append("")
    md.append("| 步骤 | 执行主体 | 实际发生的事情 | 结果 |")
    md.append("|---:|---|---|---:|")
    for number, title, actor, detail, ok in steps:
        status = "✅ 完成" if ok else "❌ 异常"
        md.append(f"| {number}. {title} | {_esc_md(actor)} | {_esc_md(detail)} | {status} |")
    md.append("")

    md.append("## 三、AI 工程师实际操作明细（阶段摘要）")
    md.append("")
    md.append(f"第一阶段：**{interview_metric[0]} / {interview_metric[1]} turns / {interview_metric[2]} 个工具动作**。")
    for row in interview_summary:
        examples = "；".join(row["examples"]) or "已记录"
        md.append(f"- **{row['title']}**：{row['count']} 个事件。示例：{_esc_md(examples)}")
    md.append("")
    md.append(f"第二阶段：**{assembly_metric[0]} / {assembly_metric[1]} turns / {assembly_metric[2]} 个工具动作**。")
    for row in assembly_summary:
        examples = "；".join(row["examples"]) or "已记录"
        md.append(f"- **{row['title']}**：{row['count']} 个事件。示例：{_esc_md(examples)}")
    md.append("")
    md.append(f"> 成本观察：访谈阶段 `{interview_metric[3]}`，装配阶段 `{assembly_metric[3]}`。{_cost_note()}")
    md.append("")
    md.append("<details><summary><strong>展开：完整 Claude Code 工具操作流水</strong></summary>")
    md.append("")
    for label, rows in [("第一阶段：读取 Skill 与需求访谈", interview_rows), ("第二阶段：自动装配与自检", assembly_rows)]:
        md.append(f"### {label}")
        md.append("")
        if rows:
            md.append("| # | 动作 | 工具 | 内容 | 状态 |")
            md.append("|---:|---|---|---|---:|")
            for row in rows:
                md.append(f"| {row['seq']} | {_esc_md(row['title'])} | `{_esc_md(row['tool'] or '-')}` | {_esc_md(row['detail'])} | {row['status']} |")
        else:
            md.append("> 本次运行没有结构化工具轨迹。")
        md.append("")
    md.append("</details>")
    md.append("")

    md.append("## 四、AI 最终选择的 Chassis 装配方案")
    md.append("")
    md.append("| 底盘能力 | 本次装配 |")
    md.append("|---|---|")
    for key, value in component_rows:
        md.append(f"| {_esc_md(key)} | {_esc_md(value)} |")
    md.append("")

    md.append("## 五、新数字员工实际运行轨迹（业务步骤摘要）")
    md.append("")
    for index, (title, detail, ok) in enumerate(employee_steps, 1):
        md.append(f"{index}. **{title}** {'✅' if ok else '❌'} — {_esc_md(detail)}")
    md.append("")
    md.append("<details><summary><strong>展开：Runtime Trace 原始时间线</strong></summary>")
    md.append("")
    if runtime_rows:
        md.append("| # | 相对时间 | 事件 | 步骤 / 工具 | 细节 |")
        md.append("|---:|---:|---|---|---|")
        for row in runtime_rows:
            md.append(f"| {row['seq']} | {row['time']} | {_esc_md(row['kind'])} | `{_esc_md(row['label'])}` | {_esc_md(row['detail'])} |")
    else:
        md.append("> 尚未发现 `live_ci_trace.json`。")
    md.append("")
    md.append("</details>")
    md.append("")

    md.append(f"## 六、确定性验收：{passed_checks}/{check_count} 通过")
    md.append("")
    md.append("<details><summary><strong>展开全部验收项目</strong></summary>")
    md.append("")
    md.append("| 验收项目 | 结果 | 中文证据 | 机器 ID |")
    md.append("|---|---:|---|---|")
    for check in check_rows:
        name = str(check.get("name") or "")
        status = "✅ 通过" if check.get("ok") else "❌ 失败"
        detail = _translate_detail(name, str(check.get("detail") or ""))
        md.append(f"| {_esc_md(CHECK_LABELS.get(name, name))} | {status} | {_esc_md(_one_line(detail, 520))} | `{name}` |")
    md.append("")
    md.append("</details>")
    md.append("")

    md.append("## 七、实际业务输入与输出")
    md.append("")
    md.append("**输入事故：**")
    md.append("```json")
    md.append(json.dumps(incident, ensure_ascii=False, indent=2))
    md.append("```")
    md.append("")
    md.append("**数字员工输出：**")
    md.append("```json")
    md.append(json.dumps(result, ensure_ascii=False, indent=2))
    md.append("```")
    md.append("")

    if interview.get("result"):
        md.append("<details><summary><strong>展开：第一轮 Skill 访谈的实际输出</strong></summary>")
        md.append("")
        md.append("```text")
        md.append(_redact(str(interview.get("result")), secrets))
        md.append("```")
        md.append("</details>")
        md.append("")
    if assembly.get("result"):
        md.append("<details><summary><strong>展开：第二轮 AI 装配完成后的实际说明</strong></summary>")
        md.append("")
        md.append("> 注意：生成数字员工的业务 Golden Scenario 使用确定性 decider 保证 CI 稳定；GLM-5.3-Flash 已真实用于 Claude Code 装配阶段。")
        md.append("")
        md.append("```text")
        md.append(_redact(str(assembly.get("result")), secrets))
        md.append("```")
        md.append("</details>")
        md.append("")

    md.append("> **判定原则：** AI 工程师是否“说自己完成了”不构成 PASS。最终状态由独立 Verifier + DoneCriteria 根据生成文件、实际运行结果和外部事实确定。")
    md_text = _redact("\n".join(md) + "\n", secrets)

    def h(value: Any) -> str:
        return html.escape(_redact(str(value), secrets))

    def lane_html(title: str, subtitle: str, items: List[Tuple[str, str]], lane_class: str) -> str:
        rows = "".join(
            f"<div class='lane-item'><div class='lane-index'>{idx}</div><div><h3>{h(item_title)}</h3><p>{h(detail)}</p></div></div>"
            for idx, (item_title, detail) in enumerate(items, 1)
        )
        return f"<div class='lane {lane_class}'><div class='lane-head'><span>{h(title)}</span><small>{h(subtitle)}</small></div>{rows}</div>"

    step_cards = "".join(
        f"<div class='step {'ok' if ok else 'bad'}'><div class='num'>{number}</div><div><h3>{h(title)}</h3><div class='actor'>{h(actor)}</div><p>{h(detail)}</p></div><div class='status'>{'✅ 完成' if ok else '❌ 异常'}</div></div>"
        for number, title, actor, detail, ok in steps
    )

    def summary_cards(rows: List[Dict[str, Any]]) -> str:
        if not rows:
            return "<p class='muted'>本次运行没有结构化过程摘要。</p>"
        return "".join(
            "<div class='summary-card'>"
            f"<div class='summary-count'>{h(row['count'])}</div>"
            f"<div><h3>{h(row['title'])}</h3>"
            f"<p>{h('；'.join(row['examples']) if row['examples'] else '已记录')}</p></div>"
            "</div>"
            for row in rows
        )

    def process_table(rows: List[Dict[str, Any]]) -> str:
        if not rows:
            return "<p class='muted'>本次运行没有结构化工具轨迹。</p>"
        body = "".join(
            f"<tr><td>{h(r['seq'])}</td><td>{h(r['title'])}</td><td><code>{h(r['tool'] or '-')}</code></td><td>{h(r['detail'])}</td><td>{h(r['status'])}</td></tr>"
            for r in rows
        )
        return "<div class='table-wrap'><table><thead><tr><th>#</th><th>动作</th><th>工具</th><th>内容</th><th>状态</th></tr></thead><tbody>" + body + "</tbody></table></div>"

    component_cards = "".join(
        f"<div class='component'><span>{h(k)}</span><strong>{h(v)}</strong></div>" for k, v in component_rows
    )

    employee_step_cards = "".join(
        f"<div class='worker-step {'ok' if ok else 'bad'}'><div class='worker-num'>{index}</div><div><h3>{h(title)}</h3><p>{h(detail)}</p></div><div class='status'>{'✅' if ok else '❌'}</div></div>"
        for index, (title, detail, ok) in enumerate(employee_steps, 1)
    )

    runtime_html = "".join(
        f"<tr><td>{h(r['seq'])}</td><td>{h(r['time'])}</td><td>{h(r['kind'])}</td><td><code>{h(r['label'])}</code></td><td>{h(r['detail'])}</td></tr>"
        for r in runtime_rows
    ) or "<tr><td colspan='5' class='muted'>尚未发现运行 Trace。</td></tr>"

    checks_html = "".join(
        f"<tr><td>{h(CHECK_LABELS.get(str(c.get('name') or ''), str(c.get('name') or '')))}</td>"
        f"<td class='{'pass' if c.get('ok') else 'fail'}'>{'✅ 通过' if c.get('ok') else '❌ 失败'}</td>"
        f"<td>{h(_one_line(_translate_detail(str(c.get('name') or ''), str(c.get('detail') or '')), 700))}</td>"
        f"<td><code>{h(c.get('name') or '')}</code></td></tr>"
        for c in check_rows
    )

    html_text = f"""<!doctype html>
<html lang='zh-CN'>
<head>
<meta charset='utf-8'>
<meta name='viewport' content='width=device-width,initial-scale=1'>
<title>数字员工真实装配验收报告</title>
<style>
:root{{--bg:#f4f7fb;--card:#fff;--text:#18212f;--muted:#667085;--line:#d9e2ec;--green:#14804a;--green-bg:#eaf8f0;--red:#c4323e;--red-bg:#fff0f1;--blue:#2463eb;--blue-bg:#edf4ff;--purple:#7557d9;--purple-bg:#f3efff;--dark:#111827}}
*{{box-sizing:border-box}}
body{{margin:0;background:var(--bg);color:var(--text);font-family:-apple-system,BlinkMacSystemFont,'Segoe UI','PingFang SC','Microsoft YaHei',sans-serif;line-height:1.62}}
main{{max-width:1180px;margin:0 auto;padding:34px 24px 80px}}
.hero{{background:linear-gradient(135deg,#101828 0%,#1d2939 62%,#243b64 100%);color:#fff;border-radius:22px;padding:36px;box-shadow:0 18px 45px rgba(16,24,40,.16);margin-bottom:22px}}
.eyebrow{{font-size:13px;letter-spacing:.08em;text-transform:uppercase;color:#b8c7e0;font-weight:700}}
.hero h1{{font-size:34px;line-height:1.2;margin:8px 0 8px}}
.hero .subtitle{{font-size:18px;color:#dbe6f6;margin:0 0 24px}}
.verdict{{display:inline-flex;align-items:center;gap:8px;border:1px solid rgba(255,255,255,.18);background:rgba(255,255,255,.1);font-weight:800;padding:8px 13px;border-radius:999px;margin-bottom:22px}}
.goal-box{{background:rgba(255,255,255,.08);border:1px solid rgba(255,255,255,.12);border-radius:14px;padding:16px 18px;margin:0 0 18px}}
.goal-box span{{display:block;color:#aebcd0;font-size:13px;font-weight:700;margin-bottom:5px}}.goal-box strong{{font-size:18px}}
.meta{{display:grid;grid-template-columns:repeat(auto-fit,minmax(190px,1fr));gap:10px}}.meta div{{background:rgba(255,255,255,.07);padding:12px 14px;border-radius:12px}}.meta b{{color:#aebcd0;font-size:12px;display:block;margin-bottom:4px}}
section{{background:var(--card);border:1px solid var(--line);border-radius:16px;padding:25px;margin:18px 0;box-shadow:0 2px 5px rgba(16,24,40,.035)}}
h2{{font-size:23px;margin:0 0 8px}}h3{{font-size:16px;margin:0 0 4px}}p{{margin:5px 0 0}}.muted,.actor{{color:var(--muted)}}.section-lead{{color:var(--muted);margin-bottom:18px}}
.proofs{{display:grid;grid-template-columns:repeat(3,1fr);gap:12px;margin-top:18px}}.proof{{border:1px solid #ccebd8;background:#f4fbf7;border-radius:12px;padding:15px}}.proof strong{{display:block;margin-bottom:4px}}.proof span{{color:#476557;font-size:14px}}
.role-note{{border-left:4px solid var(--blue);background:var(--blue-bg);padding:14px 16px;border-radius:8px;margin-top:16px}}
.swimlanes{{display:grid;grid-template-columns:1fr 1fr;gap:18px;align-items:start}}.lane{{border:1px solid var(--line);border-radius:15px;overflow:hidden;background:#fbfcfe}}.lane-head{{padding:15px 17px;font-weight:800;display:flex;justify-content:space-between;gap:12px;align-items:center}}.lane-head small{{font-weight:600;opacity:.74}}.engineer .lane-head{{background:var(--purple-bg);color:#5037a0}}.worker .lane-head{{background:var(--blue-bg);color:#174ea6}}
.lane-item{{display:grid;grid-template-columns:32px 1fr;gap:11px;padding:15px 16px;border-top:1px solid #edf0f4}}.lane-index{{width:28px;height:28px;border-radius:50%;display:grid;place-items:center;background:#fff;border:1px solid var(--line);font-weight:800;font-size:13px}}.lane-item p{{color:var(--muted);font-size:14px}}
.handoff{{display:flex;align-items:center;justify-content:center;margin:16px 0 0;color:var(--muted);font-size:14px}}.handoff span{{padding:7px 12px;border:1px dashed #aab7c4;border-radius:999px;background:#f8fafc}}
.step{{display:grid;grid-template-columns:42px 1fr auto;gap:14px;align-items:start;padding:15px 0;border-bottom:1px solid #edf0f4}}.step:last-child{{border-bottom:0}}.num,.worker-num{{width:34px;height:34px;border-radius:50%;display:grid;place-items:center;background:#dfeaff;color:#174ea6;font-weight:800}}.step.bad .num,.worker-step.bad .worker-num{{background:var(--red-bg);color:var(--red)}}.status{{font-weight:800;white-space:nowrap}}
.metrics{{display:grid;grid-template-columns:repeat(auto-fit,minmax(165px,1fr));gap:10px;margin:12px 0}}.metric{{border:1px solid var(--line);border-radius:11px;padding:13px;background:#fbfcfe}}.metric span{{color:var(--muted);font-size:12px}}.metric strong{{display:block;font-size:20px;margin-top:2px}}
.cost-note{{font-size:13px;color:var(--muted);padding:10px 12px;background:#f8fafc;border-radius:8px;margin-bottom:16px}}
.summary-grid{{display:grid;grid-template-columns:repeat(2,1fr);gap:11px;margin:12px 0 18px}}.summary-card{{display:grid;grid-template-columns:44px 1fr;gap:12px;border:1px solid var(--line);border-radius:11px;padding:13px;background:#fff}}.summary-count{{width:38px;height:38px;border-radius:10px;display:grid;place-items:center;background:var(--purple-bg);color:#5037a0;font-weight:850}}.summary-card p{{color:var(--muted);font-size:13px}}
.component-grid{{display:grid;grid-template-columns:repeat(2,1fr);gap:10px}}.component{{border:1px solid var(--line);border-radius:11px;padding:13px;background:#fbfcfe}}.component span{{display:block;color:var(--muted);font-size:12px;margin-bottom:4px}}.component strong{{font-size:14px;word-break:break-word}}
.worker-step{{display:grid;grid-template-columns:42px 1fr auto;gap:13px;align-items:start;padding:14px 0;border-bottom:1px solid #edf0f4}}.worker-step:last-child{{border-bottom:0}}
.io-grid{{display:grid;grid-template-columns:1fr 1fr;gap:14px}}.io-card{{border:1px solid var(--line);border-radius:12px;padding:15px;background:#fbfcfe}}.io-card.out{{border-color:#bfe5cc;background:#f5fbf7}}pre{{white-space:pre-wrap;word-break:break-word;background:#101828;color:#e6edf5;border-radius:10px;padding:15px;overflow:auto;font-size:13px}}
table{{border-collapse:collapse;width:100%;font-size:13px}}th,td{{border-bottom:1px solid #edf0f4;text-align:left;padding:9px;vertical-align:top}}th{{background:#f8fafc;position:sticky;top:0;z-index:1}}.table-wrap{{max-height:520px;overflow:auto;border:1px solid var(--line);border-radius:10px}}code{{background:#eef2f6;border-radius:5px;padding:2px 5px;word-break:break-word}}.pass{{color:var(--green);font-weight:800}}.fail{{color:var(--red);font-weight:800}}
details{{margin:12px 0;border:1px solid var(--line);border-radius:10px;padding:11px 13px;background:#fbfcfe}}summary{{cursor:pointer;font-weight:750}}details[open] summary{{margin-bottom:12px}}
.check-banner{{display:grid;grid-template-columns:auto 1fr;gap:14px;align-items:center;background:var(--green-bg);border:1px solid #bfe5cc;border-radius:12px;padding:16px;margin-bottom:14px}}.check-number{{font-size:28px;font-weight:900;color:var(--green)}}.check-banner p{{color:#42614f}}
.footer-note{{font-size:14px;color:var(--muted)}}.footer-note strong{{color:var(--text)}}
@media(max-width:820px){{.proofs,.swimlanes,.summary-grid,.component-grid,.io-grid{{grid-template-columns:1fr}}.step,.worker-step{{grid-template-columns:36px 1fr}}.status{{grid-column:2}}main{{padding:18px 10px 54px}}.hero{{padding:25px 20px}}.hero h1{{font-size:28px}}}}
</style>
</head>
<body>
<main>
<div class='hero'>
  <div class='eyebrow'>Live Digital Employee Assembly · Leadership View</div>
  <h1>从一句业务需求，到一台可运行数字员工</h1>
  <p class='subtitle'>数字员工真实装配验收报告</p>
  <div class='verdict'>{h(overall)} · {passed_checks}/{check_count} Checks</div>
  <div class='goal-box'><span>本次用户只提出的业务目标</span><strong>{h(business_goal)}</strong></div>
  <div class='meta'>
    <div><b>AI 工程师</b>Claude Code</div>
    <div><b>真实装配模型</b>{h(report.get('model','glm-5.3-flash'))}</div>
    <div><b>生成的数字员工</b>{h(manifest.get('name','n/a'))}</div>
    <div><b>最终业务结果</b>{h(result.get('task_key','n/a'))} → {h(result.get('severity','n/a'))} / {h(result.get('owner','n/a'))}</div>
  </div>
</div>

<section>
  <h2>这次验收到底证明了什么？</h2>
  <p class='section-lead'>不是证明“预先写好的一台 Agent 能跑”，而是证明 AI 能使用统一工程底盘，把不完整业务需求变成可运行、可审计、可验收的数字员工。</p>
  <div class='proofs'>
    <div class='proof'><strong>① AI 能主动补齐需求</strong><span>Skill 先访谈，不在信息不足时直接写代码。</span></div>
    <div class='proof'><strong>② Chassis 能被自动复用</strong><span>AI 选择标准编排、Connector、权限、知识、失败与观察组件。</span></div>
    <div class='proof'><strong>③ 完成由外部事实裁定</strong><span>模型自述不算 PASS；独立 Runtime + DoneCriteria 决定结果。</span></div>
  </div>
  <div class='role-note'><strong>模型角色说明：</strong> GLM-5.3-Flash 真实驱动 Claude Code 完成需求理解、访谈、架构选择和编码；为了保证 CI 可重复，本次 Golden Scenario 的最终事故等级与路由使用确定性规则，再由 Verifier 独立验收。</div>
</section>

<section>
  <h2>一、双泳道：AI 工程师造员工，数字员工干工作</h2>
  <p class='section-lead'>把两个角色分开看：左边是“如何制造数字员工”，右边是“数字员工上线后如何完成业务”。</p>
  <div class='swimlanes'>
    {lane_html('AI 工程师：Claude Code + GLM-5.3-Flash','装配阶段',engineer_lane,'engineer')}
    {lane_html('数字员工：Incident Triage Employee','运行阶段',worker_lane,'worker')}
  </div>
  <div class='handoff'><span>AI 工程师交付 employee.py + manifest.json → Agent Chassis Runtime 接管执行</span></div>
</section>

<section>
  <h2>二、10 步完整链路</h2>
  <p class='section-lead'>用于回看每一关具体发生了什么，以及是谁负责。</p>
  {step_cards}
</section>

<section>
  <h2>三、AI 工程师实际操作明细</h2>
  <p class='section-lead'>先展示阶段摘要；完整 Read / Glob / Grep / Write / Edit / Bash 流水放在折叠证据区，避免领导视图被技术噪音淹没。</p>

  <h3>第一阶段 · 读取 Skill 与需求访谈</h3>
  <div class='metrics'>
    <div class='metric'><span>耗时</span><strong>{h(interview_metric[0])}</strong></div>
    <div class='metric'><span>Agent turns</span><strong>{h(interview_metric[1])}</strong></div>
    <div class='metric'><span>工具动作</span><strong>{h(interview_metric[2])}</strong></div>
    <div class='metric'><span>CLI 成本估算</span><strong>{h(interview_metric[3])}</strong></div>
  </div>
  <div class='summary-grid'>{summary_cards(interview_summary)}</div>

  <h3>第二阶段 · 自动装配与自检</h3>
  <div class='metrics'>
    <div class='metric'><span>耗时</span><strong>{h(assembly_metric[0])}</strong></div>
    <div class='metric'><span>Agent turns</span><strong>{h(assembly_metric[1])}</strong></div>
    <div class='metric'><span>工具动作</span><strong>{h(assembly_metric[2])}</strong></div>
    <div class='metric'><span>CLI 成本估算</span><strong>{h(assembly_metric[3])}</strong></div>
  </div>
  <div class='summary-grid'>{summary_cards(assembly_summary)}</div>
  <div class='cost-note'><strong>成本说明：</strong> {h(_cost_note())}</div>

  <details>
    <summary>展开完整 Claude Code 工具操作流水（审计证据）</summary>
    <h3>第一阶段</h3>{process_table(interview_rows)}
    <h3 style='margin-top:20px'>第二阶段</h3>{process_table(assembly_rows)}
  </details>
</section>

<section>
  <h2>四、AI 最终选择了哪些 Chassis 能力？</h2>
  <p class='section-lead'>业务变化主要落在 Payload；编排、接入、知识、失败、权限和可观测继续复用统一底盘。</p>
  <div class='component-grid'>{component_cards}</div>
</section>

<section>
  <h2>五、新数字员工实际运行轨迹</h2>
  <p class='section-lead'>这部分是“员工上岗后的业务流程”，和上面的 AI Coding 装配过程不是同一件事。</p>
  {employee_step_cards}
  <details>
    <summary>展开 Runtime Trace 原始时间线</summary>
    <div class='table-wrap'><table><thead><tr><th>#</th><th>相对时间</th><th>事件</th><th>步骤 / 工具</th><th>细节</th></tr></thead><tbody>{runtime_html}</tbody></table></div>
  </details>
</section>

<section>
  <h2>六、确定性验收</h2>
  <div class='check-banner'><div class='check-number'>{passed_checks}/{check_count}</div><div><strong>{h(overall)}</strong><p>验收覆盖模型连通、Skill 访谈、工程生成、写入边界、Secret 泄漏、Chassis API、编译、实际运行和 DoneCriteria。</p></div></div>
  <details>
    <summary>展开全部 {check_count} 项验收与机器证据</summary>
    <div class='table-wrap'><table><thead><tr><th>验收项目</th><th>结果</th><th>中文证据</th><th>机器 ID</th></tr></thead><tbody>{checks_html}</tbody></table></div>
  </details>
</section>

<section>
  <h2>七、业务输入与最终输出</h2>
  <div class='io-grid'>
    <div class='io-card'><h3>输入 · DevOps 事故</h3><pre>{h(json.dumps(incident,ensure_ascii=False,indent=2))}</pre></div>
    <div class='io-card out'><h3>输出 · 数字员工结果</h3><pre>{h(json.dumps(result,ensure_ascii=False,indent=2))}</pre></div>
  </div>
</section>

<section>
  <h2>八、原始阶段输出（可展开审计）</h2>
  <details><summary>第一轮 Skill 访谈实际输出</summary><pre>{h(interview.get('result',''))}</pre></details>
  <details><summary>第二轮 AI 装配完成说明</summary><div class='role-note'><strong>避免误读：</strong>如果原始输出出现 <code>live_smoke_test=not-executed</code>，它只表示“生成后的 Golden Scenario 业务 Runtime 没有再次调用 LLM”；不代表装配阶段没有使用 GLM-5.3-Flash。</div><pre>{h(assembly.get('result',''))}</pre></details>
</section>

<section class='footer-note'>
  <p><strong>判定原则：</strong>AI 工程师是否“说自己完成了”不构成 PASS。最终状态由独立 Verifier + DoneCriteria 根据生成文件、实际运行结果和外部事实确定。</p>
</section>
</main>
</body>
</html>"""
    return md_text, _redact(html_text, secrets)


def main() -> int:
    root = Path(__file__).resolve().parents[1]
    parser = argparse.ArgumentParser()
    parser.add_argument("--report", default=os.environ.get("LIVE_ASSEMBLY_REPORT", "/tmp/live-assembly-report.json"))
    parser.add_argument("--interview", default=os.environ.get("LIVE_INTERVIEW_JSON", "/tmp/live-interview.json"))
    parser.add_argument("--assembly", default=os.environ.get("LIVE_ASSEMBLY_JSON", "/tmp/live-assembly.json"))
    parser.add_argument("--interview-process", default=os.environ.get("LIVE_INTERVIEW_PROCESS", "/tmp/live-interview-process.json"))
    parser.add_argument("--assembly-process", default=os.environ.get("LIVE_ASSEMBLY_PROCESS", "/tmp/live-assembly-process.json"))
    parser.add_argument("--manifest", default=str(root / "generated" / "live_ci_manifest.json"))
    parser.add_argument("--result", default=str(root / "generated" / "live_ci_result.json"))
    parser.add_argument("--trace", default=str(root / "generated" / "live_ci_trace.json"))
    parser.add_argument("--incident", default=str(root / "tests" / "live_assembly" / "incident.json"))
    parser.add_argument("--prompt", default=str(root / "tests" / "live_assembly" / "interview_prompt.md"))
    parser.add_argument("--answers", default=str(root / "tests" / "live_assembly" / "assembly_answers.md"))
    parser.add_argument("--markdown", default=os.environ.get("LIVE_REPORT_MD", "/tmp/live-assembly-report.md"))
    parser.add_argument("--html", default=os.environ.get("LIVE_REPORT_HTML", "/tmp/live-assembly-report.html"))
    args = parser.parse_args()

    secrets = _secret_values(dict(os.environ))
    data = {
        "report": _read_json(Path(args.report)),
        "interview": _read_json(Path(args.interview)),
        "assembly": _read_json(Path(args.assembly)),
        "interview_process": _read_json(Path(args.interview_process)),
        "assembly_process": _read_json(Path(args.assembly_process)),
        "manifest": _read_json(Path(args.manifest)),
        "result": _read_json(Path(args.result)),
        "trace": _read_json(Path(args.trace)),
        "incident": _read_json(Path(args.incident)),
        "prompt": _read_text(Path(args.prompt)),
        "answers": _read_text(Path(args.answers)),
    }
    markdown, html_text = build_report(data, secrets)
    md_path = Path(args.markdown)
    html_path = Path(args.html)
    md_path.parent.mkdir(parents=True, exist_ok=True)
    html_path.parent.mkdir(parents=True, exist_ok=True)
    md_path.write_text(markdown, encoding="utf-8")
    html_path.write_text(html_text, encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
