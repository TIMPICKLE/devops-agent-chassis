#!/usr/bin/env python3
"""Harden Live Assembly by validating Claude Code Write/Edit targets from process evidence."""
from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
GENERATED = (ROOT / "generated").resolve()
PROCESS = Path(os.environ.get("LIVE_ASSEMBLY_PROCESS", "/tmp/live-assembly-process.json"))
REPORT = Path(os.environ.get("LIVE_ASSEMBLY_REPORT", "/tmp/live-assembly-report.json"))


def _read_json(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}
    return value if isinstance(value, dict) else {}


def _within(path: Path, root: Path) -> bool:
    try:
        path.relative_to(root)
        return True
    except ValueError:
        return False


def _target_from_event(event: dict[str, Any]) -> str:
    # claude_stream_capture formats Write/Edit details as:
    #   /absolute/path（写入约 ...） / /absolute/path（替换 ...）
    detail = str(event.get("detail") or "").strip()
    return detail.split("（", 1)[0].strip()


def main() -> int:
    report = _read_json(REPORT)
    process = _read_json(PROCESS)
    if not report:
        raise SystemExit("live assembly verification report is missing")

    unsafe: list[str] = []
    writes = 0
    for raw in process.get("events", []):
        if not isinstance(raw, dict) or raw.get("kind") != "tool_use":
            continue
        if str(raw.get("tool") or "").lower() not in {"write", "edit"}:
            continue
        writes += 1
        target_text = _target_from_event(raw)
        if not target_text:
            unsafe.append("<unknown-write-target>")
            continue
        target = Path(target_text)
        if not target.is_absolute():
            target = ROOT / target
        target = target.resolve(strict=False)
        if not _within(target, GENERATED):
            unsafe.append(str(target))

    checks = report.get("checks")
    if not isinstance(checks, list):
        checks = []
        report["checks"] = checks

    scope_check = None
    for check in checks:
        if isinstance(check, dict) and check.get("name") == "assembly-isolated-to-generated":
            scope_check = check
            break
    if scope_check is None:
        scope_check = {"name": "assembly-isolated-to-generated", "ok": True, "detail": ""}
        checks.append(scope_check)

    original_ok = bool(scope_check.get("ok"))
    process_ok = writes > 0 and not unsafe
    scope_check["ok"] = original_ok and process_ok
    if unsafe:
        scope_check["detail"] = "过程级写入边界失败：发现 generated/ 之外的 Write/Edit：" + "; ".join(unsafe[:20])
    elif writes == 0:
        scope_check["detail"] = "过程级写入边界无法验证：没有捕获到 Write/Edit 工具动作"
    else:
        scope_check["detail"] = f"仓库差异与 Claude Code 操作流水双重验证通过：{writes} 次 Write/Edit 全部位于 generated/"

    passed = all(bool(c.get("ok")) for c in checks if isinstance(c, dict))
    report["overall_passed"] = passed
    REPORT.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    print("[PASS]" if scope_check["ok"] else "[FAIL]", "assembly-isolated-to-generated:", scope_check["detail"])
    return 0 if passed else 1


if __name__ == "__main__":
    raise SystemExit(main())
