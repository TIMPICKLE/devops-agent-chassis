from __future__ import annotations

import importlib.util
import json
from pathlib import Path


def load(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


ROOT = Path(__file__).resolve().parents[1]
CAPTURE = load("claude_stream_capture", ROOT / "tools" / "claude_stream_capture.py")
RENDER = load("render_live_assembly_report", ROOT / "tools" / "render_live_assembly_report.py")


def test_capture_extracts_tool_process_and_redacts(tmp_path: Path):
    secret = "secret-value-12345"
    stream = tmp_path / "stream.jsonl"
    events = [
        {"type": "system", "subtype": "init", "session_id": "s1"},
        {"type": "assistant", "message": {"content": [
            {"type": "text", "text": "我先读取 Skill。"},
            {"type": "tool_use", "id": "t1", "name": "Read", "input": {"file_path": ".claude/skills/assemble-digital-employee/SKILL.md"}},
        ]}},
        {"type": "user", "message": {"content": [{"type": "tool_result", "tool_use_id": "t1", "content": "ok"}]}},
        {"type": "assistant", "message": {"content": [
            {"type": "tool_use", "id": "t2", "name": "Write", "input": {"file_path": "generated/employee.py", "content": "API=" + secret}},
        ]}},
        {"type": "user", "message": {"content": [{"type": "tool_result", "tool_use_id": "t2", "content": "written"}]}},
        {"type": "result", "subtype": "success", "duration_ms": 1200, "num_turns": 2, "total_cost_usd": 0.1, "result": "完成。"},
    ]
    stream.write_text("\n".join(json.dumps(e, ensure_ascii=False) for e in events) + "\n", encoding="utf-8")
    summary, process = CAPTURE.parse_stream(stream, [secret])
    assert summary["result"] == "完成。"
    assert summary["process_tool_count"] == 2
    assert any(e.get("title") == "读取工程文件" for e in process["events"])
    assert any(e.get("title") == "生成工程文件" for e in process["events"])
    assert secret not in stream.read_text(encoding="utf-8")
    assert secret not in json.dumps(process, ensure_ascii=False)
    assert "[REDACTED]" in stream.read_text(encoding="utf-8")


def test_render_contains_chinese_process_and_runtime_trace():
    report = {
        "overall_passed": True,
        "model": "glm-5.3-flash",
        "checks": [
            {"name": "secret-present-in-runner", "ok": True, "detail": "BIGMODEL_API_KEY is available to Claude Code"},
            {"name": "provider-preflight", "ok": True, "detail": "provider preflight exit=0"},
            {"name": "provider-model-is-flash", "ok": True, "detail": "provider response model='glm-5.3-flash'"},
            {"name": "claude-code-interview-ran", "ok": True, "detail": "Claude Code interview exit=0"},
            {"name": "skill-entered-interview-mode", "ok": True, "detail": "first turn returned user-facing questions"},
            {"name": "interview-made-no-project-changes", "ok": True, "detail": "working tree stayed clean during the interview turn"},
            {"name": "claude-code-assembly-ran", "ok": True, "detail": "Claude Code assembly exit=0"},
            {"name": "employee-generated", "ok": True, "detail": "generated/live_ci_employee.py"},
            {"name": "manifest-generated", "ok": True, "detail": "generated/live_ci_manifest.json"},
            {"name": "assembly-isolated-to-generated", "ok": True, "detail": "no project files changed outside generated/"},
            {"name": "secret-not-persisted", "ok": True, "detail": "no secret bytes found in generated/model-output evidence"},
            {"name": "uses-agent-chassis", "ok": True, "detail": "generated employee references Chassis + payload + orchestrator public APIs"},
            {"name": "generated-code-compiles", "ok": True, "detail": "py_compile succeeded"},
            {"name": "generated-employee-runs", "ok": True, "detail": "exit=0"},
            {"name": "done-criteria-task_key", "ok": True, "detail": "expected='INC-001', actual='INC-001'"},
            {"name": "done-criteria-severity", "ok": True, "detail": "expected='P1', actual='P1'"},
            {"name": "done-criteria-owner", "ok": True, "detail": "expected='platform', actual='platform'"},
            {"name": "done-criteria-done", "ok": True, "detail": "expected=True, actual=True"},
        ],
    }
    data = {
        "report": report,
        "interview": {"duration_ms": 1000, "num_turns": 3, "process_tool_count": 2, "total_cost_usd": 0.01, "result": "请回答 TaskSource？"},
        "assembly": {"duration_ms": 2000, "num_turns": 5, "process_tool_count": 4, "total_cost_usd": 0.02, "result": "装配完成"},
        "interview_process": {"events": [{"seq": 1, "title": "读取工程文件", "tool": "Read", "detail": "SKILL.md", "ok": True}]},
        "assembly_process": {"events": [{"seq": 1, "title": "生成工程文件", "tool": "Write", "detail": "generated/live_ci_employee.py", "ok": True}]},
        "manifest": {"name": "live-ci-incident-triage", "task_source": "IncidentFileTaskSource", "done_criteria": "TriageRecordCriteria", "orchestrator": "state_machine", "reasoning": "ReAct", "connector": "mock", "failure_policy": "zero-side-effect", "llm_provider": {"provider": "bigmodel-anthropic-compatible", "model": "glm-5.3-flash"}, "permissions": {"granted": ["repo.read", "repo.write"]}, "knowledge_injection": {"task_admitted": ["incident-mission"], "before_executor": ["runbook"], "on_retry": ["retry-feedback"]}},
        "result": {"task_key": "INC-001", "severity": "P1", "owner": "platform", "done": True},
        "trace": {"traces": [
            {"seq": 1, "kind": "task_start", "label": "devops_incident:INC-001", "at_ms": 0},
            {"seq": 2, "kind": "step", "label": "classify_and_route", "at_ms": 1},
            {"seq": 3, "kind": "tool_call", "label": "incidents.get", "at_ms": 2, "detail": {"ok": True}},
            {"seq": 4, "kind": "injection", "label": "before_executor / runbook", "at_ms": 3, "detail": {"provider": "static"}},
            {"seq": 5, "kind": "task_end", "label": "succeeded", "at_ms": 4},
        ]},
        "incident": {"id": "INC-001", "service": "payments-api", "error_rate": 0.42, "customer_impact": True},
        "prompt": "请调用 Skill 创建事故分级数字员工。",
        "answers": "state_machine + ReAct",
    }
    md, html = RENDER.build_report(data, [])
    assert "数字员工真实装配验收报告" in md
    assert "从一句业务需求到可运行数字员工" in md
    assert "AI 工程师实际操作明细" in md
    assert "读取工程文件" in md
    assert "生成工程文件" in md
    assert "新数字员工实际运行轨迹" in md
    assert "知识注入" in md
    assert "DoneCriteria：事故等级" in md
    assert "数字员工真实装配验收报告" in html
    assert "classify_and_route" in html
