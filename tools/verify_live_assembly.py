#!/usr/bin/env python3
"""Deterministically verify the live Claude Code digital-employee assembly run."""
from __future__ import annotations

import json
import os
import py_compile
import subprocess
import sys
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
GENERATED = ROOT / "generated"
EMPLOYEE = GENERATED / "live_ci_employee.py"
MANIFEST = GENERATED / "live_ci_manifest.json"
RESULT = GENERATED / "live_ci_result.json"
INPUT = ROOT / "tests" / "live_assembly" / "incident.json"
REPORT = Path(os.environ.get("LIVE_ASSEMBLY_REPORT", "/tmp/live-assembly-report.json"))
INTERVIEW_JSON = Path(os.environ.get("LIVE_INTERVIEW_JSON", "/tmp/live-interview.json"))
ASSEMBLY_JSON = Path(os.environ.get("LIVE_ASSEMBLY_JSON", "/tmp/live-assembly.json"))
PROVIDER_JSON = Path(os.environ.get("LIVE_PROVIDER_JSON", "/tmp/live-provider.json"))


def _record(checks: list[dict[str, Any]], name: str, ok: bool, detail: str) -> None:
    checks.append({"name": name, "ok": ok, "detail": detail})


def _env_exit(name: str) -> int:
    try:
        return int(os.environ.get(name, "999"))
    except ValueError:
        return 999


def _read_json(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}
    return value if isinstance(value, dict) else {}


def _git_status() -> list[str]:
    completed = subprocess.run(
        ["git", "status", "--porcelain", "--untracked-files=all"],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    if completed.returncode != 0:
        return ["<git-status-failed>"]
    return [line for line in completed.stdout.splitlines() if line.strip()]


def _contains_secret(path: Path, secret: str) -> bool:
    if not secret or not path.exists() or not path.is_file():
        return False
    try:
        return secret in path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return False


def main() -> int:
    checks: list[dict[str, Any]] = []
    secret = os.environ.get("BIGMODEL_API_KEY", "")

    provider_exit = _env_exit("LIVE_PROVIDER_EXIT")
    interview_exit = _env_exit("LIVE_INTERVIEW_EXIT")
    assembly_exit = _env_exit("LIVE_ASSEMBLY_EXIT")
    interview_dirty = os.environ.get("LIVE_INTERVIEW_DIRTY", "1")

    _record(checks, "secret-present-in-runner", bool(secret), "BIGMODEL_API_KEY is available to Claude Code" if secret else "BIGMODEL_API_KEY is missing")
    _record(checks, "provider-preflight", provider_exit == 0, f"provider preflight exit={provider_exit}")

    provider_payload = _read_json(PROVIDER_JSON)
    provider_model = str(provider_payload.get("model") or "")
    _record(
        checks,
        "provider-model-is-flash",
        provider_exit == 0 and "glm-5.3-flash" in provider_model.lower(),
        f"provider response model={provider_model!r}",
    )

    interview_payload = _read_json(INTERVIEW_JSON)
    interview_text = str(interview_payload.get("result") or "")
    _record(checks, "claude-code-interview-ran", interview_exit == 0, f"Claude Code interview exit={interview_exit}")
    _record(
        checks,
        "skill-entered-interview-mode",
        bool(interview_text.strip()) and ("?" in interview_text or "？" in interview_text),
        "first turn returned user-facing questions" if interview_text else "first-turn result missing",
    )
    _record(
        checks,
        "interview-made-no-project-changes",
        interview_dirty == "0",
        "working tree stayed clean during the interview turn" if interview_dirty == "0" else "interview turn changed project files",
    )
    _record(checks, "claude-code-assembly-ran", assembly_exit == 0, f"Claude Code assembly exit={assembly_exit}")

    _record(checks, "employee-generated", EMPLOYEE.exists(), str(EMPLOYEE.relative_to(ROOT)))
    _record(checks, "manifest-generated", MANIFEST.exists(), str(MANIFEST.relative_to(ROOT)))

    status = _git_status()
    unsafe_changes = []
    for line in status:
        path = line[3:] if len(line) >= 4 else line
        if path.startswith("generated/"):
            continue
        unsafe_changes.append(line)
    _record(
        checks,
        "assembly-isolated-to-generated",
        not unsafe_changes,
        "no project files changed outside generated/" if not unsafe_changes else "; ".join(unsafe_changes[:20]),
    )

    generated_files = [p for p in GENERATED.rglob("*") if p.is_file()] if GENERATED.exists() else []
    evidence_files = generated_files + [p for p in (INTERVIEW_JSON, ASSEMBLY_JSON) if p.exists()]
    leaked = [str(p if p.is_absolute() else p.relative_to(ROOT)) for p in evidence_files if _contains_secret(p, secret)] if secret else []
    _record(checks, "secret-not-persisted", not leaked, "no secret bytes found in generated/model-output evidence" if not leaked else ", ".join(leaked))

    manifest: dict[str, Any] = {}
    if MANIFEST.exists():
        try:
            manifest = json.loads(MANIFEST.read_text(encoding="utf-8"))
            _record(checks, "manifest-json-valid", isinstance(manifest, dict), "manifest parsed as JSON object")
        except Exception as exc:
            _record(checks, "manifest-json-valid", False, f"{type(exc).__name__}: {exc}")
    else:
        _record(checks, "manifest-json-valid", False, "manifest missing")

    expected_manifest = {
        "name": "live-ci-incident-triage",
        "orchestrator": "state_machine",
        "reasoning": "ReAct",
        "connector": "mock",
    }
    for key, expected in expected_manifest.items():
        actual = manifest.get(key)
        _record(checks, f"manifest-{key}", actual == expected, f"expected={expected!r}, actual={actual!r}")

    provider = manifest.get("llm_provider") if isinstance(manifest, dict) else None
    provider_ok = isinstance(provider, dict)
    if provider_ok:
        provider_ok = (
            provider.get("provider") == "bigmodel-anthropic-compatible"
            and provider.get("base_url") == "https://open.bigmodel.cn/api/anthropic"
            and provider.get("model") == "glm-5.3-flash"
            and provider.get("secret_env") == "BIGMODEL_API_KEY"
        )
    _record(checks, "manifest-llm-provider", provider_ok, "BigModel Anthropic-compatible GLM-5.3-Flash configuration recorded without secret value")

    source = EMPLOYEE.read_text(encoding="utf-8", errors="replace") if EMPLOYEE.exists() else ""
    chassis_contract_ok = "Chassis" in source and "with_payload" in source and "with_orchestrator" in source
    _record(checks, "uses-agent-chassis", chassis_contract_ok, "generated employee references Chassis + payload + orchestrator public APIs")

    compile_ok = False
    if EMPLOYEE.exists():
        try:
            py_compile.compile(str(EMPLOYEE), doraise=True)
            compile_ok = True
            _record(checks, "generated-code-compiles", True, "py_compile succeeded")
        except Exception as exc:
            _record(checks, "generated-code-compiles", False, f"{type(exc).__name__}: {exc}")
    else:
        _record(checks, "generated-code-compiles", False, "employee file missing")

    if RESULT.exists():
        RESULT.unlink()

    run_ok = False
    run_detail = "not run because generated code did not compile"
    if compile_ok:
        completed = subprocess.run(
            [
                sys.executable,
                str(EMPLOYEE),
                "--input",
                str(INPUT),
                "--output",
                str(RESULT),
            ],
            cwd=ROOT,
            env=os.environ.copy(),
            capture_output=True,
            text=True,
            timeout=90,
            check=False,
        )
        run_ok = completed.returncode == 0
        stdout = completed.stdout.replace(secret, "[REDACTED]") if secret else completed.stdout
        stderr = completed.stderr.replace(secret, "[REDACTED]") if secret else completed.stderr
        run_detail = f"exit={completed.returncode}; stdout={stdout[-500:].strip()!r}; stderr={stderr[-500:].strip()!r}"
    _record(checks, "generated-employee-runs", run_ok, run_detail)

    output: dict[str, Any] = {}
    if RESULT.exists():
        try:
            output = json.loads(RESULT.read_text(encoding="utf-8"))
            _record(checks, "result-json-valid", isinstance(output, dict), "result parsed as JSON object")
        except Exception as exc:
            _record(checks, "result-json-valid", False, f"{type(exc).__name__}: {exc}")
    else:
        _record(checks, "result-json-valid", False, "generated/live_ci_result.json missing")

    expected_result = {
        "task_key": "INC-001",
        "severity": "P1",
        "owner": "platform",
        "done": True,
    }
    for key, expected in expected_result.items():
        actual = output.get(key)
        _record(checks, f"done-criteria-{key}", actual == expected, f"expected={expected!r}, actual={actual!r}")

    passed = all(check["ok"] for check in checks)
    report = {
        "overall_passed": passed,
        "provider": "bigmodel-anthropic-compatible",
        "base_url": "https://open.bigmodel.cn/api/anthropic",
        "model": "glm-5.3-flash",
        "checks": checks,
    }
    REPORT.parent.mkdir(parents=True, exist_ok=True)
    REPORT.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    for check in checks:
        icon = "PASS" if check["ok"] else "FAIL"
        print(f"[{icon}] {check['name']}: {check['detail']}")
    print(f"overall={'PASS' if passed else 'FAIL'}")
    return 0 if passed else 1


if __name__ == "__main__":
    raise SystemExit(main())
