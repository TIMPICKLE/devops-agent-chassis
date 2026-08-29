#!/usr/bin/env python3
"""Run layered digital-employee acceptance checks and emit human/machine reports.

The verdict is deterministic: subprocess exit codes and JUnit facts decide PASS/FAIL.
No LLM is allowed to change the verdict. The generated Markdown/HTML only explains
those facts in a more readable form.
"""
from __future__ import annotations

import argparse
import html
import json
import os
import platform
import shlex
import subprocess
import sys
import time
import xml.etree.ElementTree as ET
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List, Sequence


ROOT = Path(__file__).resolve().parents[1]


@dataclass(frozen=True)
class CheckSpec:
    key: str
    label: str
    paths: Sequence[str]
    description: str
    recommendation: str
    mcp_only: bool = False


@dataclass
class FailureFact:
    test: str
    message: str
    detail: str = ""


@dataclass
class CheckResult:
    key: str
    label: str
    description: str
    required: bool
    status: str
    return_code: int
    duration_ms: int
    tests: int = 0
    passed: int = 0
    failed: int = 0
    skipped: int = 0
    errors: int = 0
    log: str = ""
    junit: str = ""
    recommendation: str = ""
    failures: List[FailureFact] = field(default_factory=list)
    note: str = ""


CHECKS: List[CheckSpec] = [
    CheckSpec(
        "chassis-core",
        "Chassis Core",
        ["tests/test_chassis.py"],
        "底盘装配、编排、权限、知识注入与基础完成判据。",
        "先看失败用例对应的 Chassis/Orchestrator/DoneCriteria；不要用重试掩盖确定性契约错误。",
    ),
    CheckSpec(
        "retry-policy",
        "Retry Policy",
        ["tests/test_retry_policy.py"],
        "瞬时异常重试、cleanup、ON_RETRY 与最终失败记账。",
        "检查 RetryThenGiveUpPolicy 的 retry 预算、cleanup、WorkspaceGuard 与 last_error 注入链路。",
    ),
    CheckSpec(
        "connector-trace",
        "Connector Observer Trace",
        ["tests/test_connector_trace.py"],
        "Connector 调用是否进入同一个 run_id 的统一 Observer Trace。",
        "检查 active RunContext、ConnectorManager subscriber 与 Observer.on_tool_call 绑定关系。",
    ),
    CheckSpec(
        "mcp-runtime",
        "MCP Sync Runtime",
        ["tests/test_mcp_runtime.py"],
        "同步 Chassis 与异步 MCP Client 生命周期、分页 discovery、错误传播。",
        "检查 MCP worker thread/event loop 生命周期、请求队列、分页 cursor 和 isError 转换。",
    ),
    CheckSpec(
        "mcp-transport",
        "Real MCP Transports",
        ["tests/test_mcp_integration.py"],
        "真实 stdio 与 Streamable HTTP MCP Server 的 discovery/call/session/header。",
        "检查 mcp extra、stdio command/env/cwd、HTTP URL/headers，以及 MCP Server 是否真正可启动。",
        mcp_only=True,
    ),
    CheckSpec(
        "employee-e2e",
        "Digital Employee E2E",
        ["tests/test_employee_acceptance.py"],
        "装配真实 Payload，运行任务，并由外部事实 DoneCriteria 判定完成。",
        "按 TaskSource → Reasoning/Tools → Delivery → DoneCriteria → Observer 顺序定位，不接受模型自述作为完成证据。",
    ),
    CheckSpec(
        "examples-smoke",
        "Examples Smoke",
        ["tests/test_examples_smoke.py"],
        "仓库公开示例是否都能在干净环境完整运行。",
        "打开失败脚本的完整日志；示例应保持离线、确定性、零人工交互。",
    ),
]


def _parse_junit(path: Path) -> Dict[str, object]:
    if not path.exists():
        return {
            "tests": 0,
            "passed": 0,
            "failed": 0,
            "skipped": 0,
            "errors": 0,
            "failures": [],
        }

    root = ET.parse(path).getroot()
    testcases = root.findall(".//testcase")
    skipped = 0
    failed = 0
    errors = 0
    facts: List[FailureFact] = []

    for case in testcases:
        name = case.attrib.get("name", "unknown")
        classname = case.attrib.get("classname", "")
        test_name = f"{classname}::{name}" if classname else name
        skipped_node = case.find("skipped")
        failure_node = case.find("failure")
        error_node = case.find("error")
        if skipped_node is not None:
            skipped += 1
        if failure_node is not None:
            failed += 1
            facts.append(
                FailureFact(
                    test=test_name,
                    message=failure_node.attrib.get("message", "test failed"),
                    detail=(failure_node.text or "").strip()[-4000:],
                )
            )
        if error_node is not None:
            errors += 1
            facts.append(
                FailureFact(
                    test=test_name,
                    message=error_node.attrib.get("message", "test error"),
                    detail=(error_node.text or "").strip()[-4000:],
                )
            )

    tests = len(testcases)
    passed = max(0, tests - skipped - failed - errors)
    return {
        "tests": tests,
        "passed": passed,
        "failed": failed,
        "skipped": skipped,
        "errors": errors,
        "failures": facts,
    }


def _run_check(
    spec: CheckSpec,
    output_dir: Path,
    *,
    require_mcp: bool,
    timeout: int,
) -> CheckResult:
    junit_dir = output_dir / "junit"
    log_dir = output_dir / "logs"
    junit_dir.mkdir(parents=True, exist_ok=True)
    log_dir.mkdir(parents=True, exist_ok=True)
    junit_path = junit_dir / f"{spec.key}.xml"
    log_path = log_dir / f"{spec.key}.log"

    required = require_mcp or not spec.mcp_only
    command = [
        sys.executable,
        "-m",
        "pytest",
        *spec.paths,
        "-q",
        f"--junitxml={junit_path}",
    ]
    started = time.monotonic()
    env = os.environ.copy()
    env.setdefault("PYTHONUTF8", "1")

    return_code = 1
    stdout = ""
    stderr = ""
    note = ""
    try:
        completed = subprocess.run(
            command,
            cwd=ROOT,
            env=env,
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
        )
        return_code = completed.returncode
        stdout = completed.stdout
        stderr = completed.stderr
    except subprocess.TimeoutExpired as exc:
        return_code = 124
        stdout = exc.stdout or ""
        stderr = exc.stderr or ""
        note = f"check exceeded {timeout}s timeout"

    duration_ms = int((time.monotonic() - started) * 1000)
    command_text = shlex.join(command)
    log_path.write_text(
        f"$ {command_text}\n\n--- stdout ---\n{stdout}\n\n--- stderr ---\n{stderr}\n",
        encoding="utf-8",
    )

    junit = _parse_junit(junit_path)
    tests = int(junit["tests"])
    skipped = int(junit["skipped"])
    failures = list(junit["failures"])

    if return_code != 0:
        status = "FAIL"
    elif tests == 0:
        status = "FAIL" if required else "SKIP"
        note = note or "no tests were collected"
    elif skipped == tests:
        status = "FAIL" if required else "SKIP"
        note = note or "all tests were skipped"
    else:
        status = "PASS"

    return CheckResult(
        key=spec.key,
        label=spec.label,
        description=spec.description,
        required=required,
        status=status,
        return_code=return_code,
        duration_ms=duration_ms,
        tests=tests,
        passed=int(junit["passed"]),
        failed=int(junit["failed"]),
        skipped=skipped,
        errors=int(junit["errors"]),
        log=str(log_path.relative_to(output_dir)),
        junit=str(junit_path.relative_to(output_dir)) if junit_path.exists() else "",
        recommendation=spec.recommendation,
        failures=failures,
        note=note,
    )


def _status_icon(status: str) -> str:
    return {"PASS": "✅", "FAIL": "❌", "SKIP": "⚠️"}.get(status, "❔")


def _summary_markdown(report: Dict[str, object]) -> str:
    checks: List[Dict[str, object]] = report["checks"]  # type: ignore[assignment]
    overall = bool(report["overall_passed"])
    passed_checks = sum(1 for check in checks if check["status"] == "PASS")
    failed_checks = sum(1 for check in checks if check["status"] == "FAIL")
    skipped_checks = sum(1 for check in checks if check["status"] == "SKIP")
    total_tests = sum(int(check["tests"]) for check in checks)
    failed_tests = sum(int(check["failed"]) + int(check["errors"]) for check in checks)

    lines = [
        "# Digital Employee Acceptance Report",
        "",
        f"## {_status_icon('PASS' if overall else 'FAIL')} Overall: {'PASSED' if overall else 'FAILED'}",
        "",
        f"- Checks: **{passed_checks} passed / {failed_checks} failed / {skipped_checks} skipped**",
        f"- Test cases observed: **{total_tests}**; failed/error: **{failed_tests}**",
        f"- Python: `{report['environment']['python']}`",
        f"- Commit: `{report['environment']['sha'] or 'local'}`",
        "",
        "| Acceptance layer | Status | Tests | Passed | Failed/Error | Skipped | Duration | Evidence |",
        "|---|---:|---:|---:|---:|---:|---:|---|",
    ]

    for check in checks:
        bad = int(check["failed"]) + int(check["errors"])
        evidence = str(check.get("note") or check.get("description") or "").replace("|", "\\|")
        lines.append(
            f"| {check['label']} | {_status_icon(str(check['status']))} {check['status']} "
            f"| {check['tests']} | {check['passed']} | {bad} | {check['skipped']} "
            f"| {int(check['duration_ms']) / 1000:.2f}s | {evidence} |"
        )

    failed_or_skipped = [check for check in checks if check["status"] != "PASS"]
    if failed_or_skipped:
        lines.extend(["", "## Failure / Skip details", ""])
        for check in failed_or_skipped:
            lines.append(f"### {_status_icon(str(check['status']))} {check['label']} — {check['status']}")
            if check.get("note"):
                lines.append(f"- Note: {check['note']}")
            lines.append(f"- Full log: `{check['log']}`")
            if check.get("junit"):
                lines.append(f"- JUnit: `{check['junit']}`")
            lines.append(f"- Recommended next check: {check['recommendation']}")
            failures = check.get("failures") or []
            for fact in failures[:8]:
                lines.append(f"- `{fact['test']}`: {fact['message']}")
            lines.append("")
    else:
        lines.extend(
            [
                "",
                "## Acceptance conclusion",
                "",
                "All required deterministic checks passed: the chassis is runnable, reference digital employees can be assembled and reach external-fact DoneCriteria, MCP transports are callable, and public examples complete successfully.",
            ]
        )

    lines.extend(
        [
            "",
            "## Artifact contents",
            "",
            "- `report.json` — machine-readable verdict and failure facts",
            "- `report.html` — standalone human-readable report",
            "- `junit/*.xml` — per-layer JUnit evidence",
            "- `logs/*.log` — complete stdout/stderr for each layer",
            "",
            "> PASS/FAIL is determined only by executable tests and external facts. An LLM may summarize these artifacts later, but it must not override the verdict.",
        ]
    )
    return "\n".join(lines) + "\n"


def _report_html(report: Dict[str, object]) -> str:
    checks: List[Dict[str, object]] = report["checks"]  # type: ignore[assignment]
    overall = bool(report["overall_passed"])
    rows = []
    for check in checks:
        bad = int(check["failed"]) + int(check["errors"])
        rows.append(
            "<tr>"
            f"<td>{html.escape(str(check['label']))}</td>"
            f"<td>{html.escape(_status_icon(str(check['status'])) + ' ' + str(check['status']))}</td>"
            f"<td>{check['tests']}</td><td>{check['passed']}</td><td>{bad}</td><td>{check['skipped']}</td>"
            f"<td>{int(check['duration_ms']) / 1000:.2f}s</td>"
            f"<td><code>{html.escape(str(check['log']))}</code></td>"
            "</tr>"
        )

    details = []
    for check in checks:
        if check["status"] == "PASS":
            continue
        failure_items = "".join(
            f"<li><code>{html.escape(str(fact['test']))}</code>: {html.escape(str(fact['message']))}</li>"
            for fact in (check.get("failures") or [])[:8]
        )
        details.append(
            f"<section><h3>{html.escape(str(check['label']))} — {html.escape(str(check['status']))}</h3>"
            f"<p>{html.escape(str(check.get('note') or ''))}</p>"
            f"<p><strong>Recommended:</strong> {html.escape(str(check['recommendation']))}</p>"
            f"<ul>{failure_items}</ul></section>"
        )

    return f"""<!doctype html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<title>Digital Employee Acceptance Report</title>
<style>
body{{font-family:system-ui,-apple-system,BlinkMacSystemFont,"Segoe UI",sans-serif;max-width:1200px;margin:40px auto;padding:0 20px;line-height:1.5}}
table{{border-collapse:collapse;width:100%}}th,td{{border:1px solid #ddd;padding:8px;text-align:left}}th{{background:#f5f5f5}}code{{font-family:ui-monospace,SFMono-Regular,Consolas,monospace}}.overall{{font-size:1.35rem;font-weight:700}}
</style>
</head>
<body>
<h1>Digital Employee Acceptance Report</h1>
<p class="overall">{html.escape(_status_icon('PASS' if overall else 'FAIL'))} Overall: {'PASSED' if overall else 'FAILED'}</p>
<p>Python <code>{html.escape(str(report['environment']['python']))}</code> · Commit <code>{html.escape(str(report['environment']['sha'] or 'local'))}</code></p>
<table><thead><tr><th>Layer</th><th>Status</th><th>Tests</th><th>Passed</th><th>Failed/Error</th><th>Skipped</th><th>Duration</th><th>Log</th></tr></thead><tbody>{''.join(rows)}</tbody></table>
{''.join(details)}
<p><small>Verdict is deterministic and comes from executable tests/JUnit facts.</small></p>
</body></html>"""


def _jsonable_result(result: CheckResult) -> Dict[str, object]:
    raw = asdict(result)
    return raw


def run(output_dir: Path, *, require_mcp: bool, timeout: int) -> bool:
    output_dir.mkdir(parents=True, exist_ok=True)
    results: List[CheckResult] = []
    for spec in CHECKS:
        print(f"[acceptance] {spec.label} ...", flush=True)
        result = _run_check(
            spec,
            output_dir,
            require_mcp=require_mcp,
            timeout=timeout,
        )
        results.append(result)
        print(
            f"[acceptance] {result.label}: {result.status} "
            f"({result.passed}/{result.tests} passed, {result.duration_ms}ms)",
            flush=True,
        )

    overall_passed = all(
        result.status == "PASS"
        for result in results
        if result.required
    )
    report: Dict[str, object] = {
        "schema_version": 1,
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "overall_passed": overall_passed,
        "environment": {
            "python": platform.python_version(),
            "platform": platform.platform(),
            "sha": os.getenv("GITHUB_SHA", ""),
            "ref": os.getenv("GITHUB_REF", ""),
            "event": os.getenv("GITHUB_EVENT_NAME", "local"),
        },
        "checks": [_jsonable_result(result) for result in results],
    }

    (output_dir / "report.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    (output_dir / "summary.md").write_text(
        _summary_markdown(report),
        encoding="utf-8",
    )
    (output_dir / "report.html").write_text(
        _report_html(report),
        encoding="utf-8",
    )
    return overall_passed


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--output-dir",
        default="reports/digital-employee-acceptance",
        help="directory for report.json, summary.md, HTML, JUnit and logs",
    )
    parser.add_argument(
        "--require-mcp",
        action="store_true",
        help="treat a skipped/unavailable real MCP transport test as a failure",
    )
    parser.add_argument(
        "--timeout",
        type=int,
        default=180,
        help="timeout in seconds for each acceptance layer",
    )
    args = parser.parse_args()

    passed = run(
        (ROOT / args.output_dir).resolve(),
        require_mcp=args.require_mcp,
        timeout=args.timeout,
    )
    return 0 if passed else 1


if __name__ == "__main__":
    raise SystemExit(main())
