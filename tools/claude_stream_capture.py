#!/usr/bin/env python3
"""Normalize Claude Code stream-json output for CI reporting.

The raw stream is kept as redacted JSONL evidence. A compact summary JSON preserves
fields consumed by the existing verifier, while a separate process JSON records only
human-auditable actions (file reads/writes, searches, commands and assistant status
messages). Thinking/reasoning blocks are intentionally ignored.
"""
from __future__ import annotations

import argparse
import json
import os
import re
from pathlib import Path
from typing import Any, Dict, List, Sequence, Tuple


SENSITIVE_NAME_RE = re.compile(
    r"(?:api[_-]?key|token|secret|password|authorization|cookie|credential|private[_-]?key)",
    re.IGNORECASE,
)


def secret_values(env: Dict[str, str]) -> List[str]:
    values: List[str] = []
    for name, value in env.items():
        if value and len(value) >= 4 and SENSITIVE_NAME_RE.search(name):
            values.append(value)
    return sorted(set(values), key=len, reverse=True)


def redact_text(text: str, secrets: Sequence[str]) -> str:
    value = text
    for secret in secrets:
        value = value.replace(secret, "[REDACTED]")
    return value


def redact_obj(value: Any, secrets: Sequence[str]) -> Any:
    if isinstance(value, str):
        return redact_text(value, secrets)
    if isinstance(value, list):
        return [redact_obj(item, secrets) for item in value]
    if isinstance(value, dict):
        return {str(key): redact_obj(item, secrets) for key, item in value.items()}
    return value


def _clip(value: str, limit: int = 360) -> str:
    text = " ".join(value.split())
    if len(text) <= limit:
        return text
    return text[: limit - 1] + "…"


def _content(message: Dict[str, Any]) -> List[Dict[str, Any]]:
    content = message.get("content", [])
    if not isinstance(content, list):
        return []
    return [block for block in content if isinstance(block, dict)]


def _message_from_event(event: Dict[str, Any]) -> Dict[str, Any]:
    message = event.get("message")
    if isinstance(message, dict):
        return message
    return event


def _tool_action(name: str, tool_input: Any) -> Tuple[str, str]:
    data = tool_input if isinstance(tool_input, dict) else {}
    lowered = name.lower()
    if lowered == "read":
        path = data.get("file_path") or data.get("path") or "未知文件"
        return "读取工程文件", str(path)
    if lowered == "write":
        path = data.get("file_path") or data.get("path") or "未知文件"
        content = str(data.get("content") or "")
        return "生成工程文件", f"{path}（写入约 {len(content)} 字符）"
    if lowered == "edit":
        path = data.get("file_path") or data.get("path") or "未知文件"
        old = str(data.get("old_string") or "")
        new = str(data.get("new_string") or "")
        return "修改工程文件", f"{path}（替换 {len(old)} → {len(new)} 字符）"
    if lowered == "glob":
        pattern = data.get("pattern") or "*"
        path = data.get("path") or "."
        return "扫描工程结构", f"{path} / {pattern}"
    if lowered == "grep":
        pattern = data.get("pattern") or ""
        path = data.get("path") or "."
        return "搜索工程能力", f"在 {path} 中搜索 {_clip(str(pattern), 160)}"
    if lowered == "bash":
        command = str(data.get("command") or "")
        description = str(data.get("description") or "")
        detail = _clip(command, 280)
        if description:
            detail = f"{_clip(description, 120)}：{detail}"
        return "执行工程验证命令", detail or "Bash"
    compact = json.dumps(data, ensure_ascii=False, sort_keys=True, default=str)
    return f"调用工具 {name}", _clip(compact, 300)


def parse_stream(path: Path, secrets: Sequence[str]) -> Tuple[Dict[str, Any], Dict[str, Any]]:
    events: List[Dict[str, Any]] = []
    assistant_texts: List[str] = []
    tool_events: Dict[str, Dict[str, Any]] = {}
    final: Dict[str, Any] = {}
    redacted_lines: List[str] = []
    seq = 0

    if not path.exists():
        return {"result": "", "is_error": True, "subtype": "stream-missing"}, {"events": []}

    for raw_line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        if not raw_line.strip():
            continue
        try:
            original = json.loads(raw_line)
        except json.JSONDecodeError:
            redacted_lines.append(redact_text(raw_line, secrets))
            continue
        if not isinstance(original, dict):
            redacted_lines.append(json.dumps(redact_obj(original, secrets), ensure_ascii=False))
            continue
        event = redact_obj(original, secrets)
        redacted_lines.append(json.dumps(event, ensure_ascii=False, separators=(",", ":")))
        event_type = str(event.get("type") or "")

        if event_type == "result":
            final = dict(event)
            continue

        if event_type == "assistant":
            message = _message_from_event(event)
            for block in _content(message):
                block_type = str(block.get("type") or "")
                if block_type == "text":
                    text = str(block.get("text") or "").strip()
                    if text:
                        assistant_texts.append(text)
                        seq += 1
                        events.append({
                            "seq": seq,
                            "kind": "assistant_text",
                            "title": "AI 阶段说明",
                            "detail": _clip(text, 520),
                            "ok": True,
                        })
                elif block_type == "tool_use":
                    tool_name = str(block.get("name") or "unknown")
                    title, detail = _tool_action(tool_name, block.get("input"))
                    seq += 1
                    item = {
                        "seq": seq,
                        "kind": "tool_use",
                        "tool": tool_name,
                        "title": title,
                        "detail": detail,
                        "ok": None,
                    }
                    tool_id = str(block.get("id") or "")
                    if tool_id:
                        item["tool_use_id"] = tool_id
                        tool_events[tool_id] = item
                    events.append(item)
            continue

        if event_type == "user":
            message = _message_from_event(event)
            for block in _content(message):
                if str(block.get("type") or "") != "tool_result":
                    continue
                tool_id = str(block.get("tool_use_id") or "")
                target = tool_events.get(tool_id)
                if target is None:
                    continue
                is_error = bool(block.get("is_error"))
                target["ok"] = not is_error
                content = block.get("content")
                if isinstance(content, str):
                    target["result_preview"] = _clip(content, 300)
                elif isinstance(content, list):
                    text_parts = []
                    for part in content:
                        if isinstance(part, dict) and part.get("type") == "text":
                            text_parts.append(str(part.get("text") or ""))
                    if text_parts:
                        target["result_preview"] = _clip(" ".join(text_parts), 300)
            continue

        if event_type == "system" and str(event.get("subtype") or "") == "init":
            seq += 1
            events.append({
                "seq": seq,
                "kind": "system",
                "title": "Claude Code 会话初始化",
                "detail": f"session={event.get('session_id') or 'n/a'}",
                "ok": True,
            })

    if redacted_lines:
        path.write_text("\n".join(redacted_lines) + "\n", encoding="utf-8")

    if not final:
        final = {"type": "result", "subtype": "missing", "is_error": True}
    final = redact_obj(final, secrets)
    result_text = str(final.get("result") or "").strip()
    if not result_text and assistant_texts:
        result_text = assistant_texts[-1]
    final["result"] = result_text
    final["process_event_count"] = len(events)
    final["process_tool_count"] = sum(1 for item in events if item.get("kind") == "tool_use")

    process = {
        "event_count": len(events),
        "tool_count": final["process_tool_count"],
        "events": events,
    }
    return final, process


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--input", required=True)
    parser.add_argument("--summary", required=True)
    parser.add_argument("--process", required=True)
    parser.add_argument("--exit-code", type=int, default=0)
    args = parser.parse_args()

    secrets = secret_values(dict(os.environ))
    summary, process = parse_stream(Path(args.input), secrets)
    summary["cli_exit_code"] = args.exit_code
    process["cli_exit_code"] = args.exit_code

    summary_path = Path(args.summary)
    process_path = Path(args.process)
    summary_path.parent.mkdir(parents=True, exist_ok=True)
    process_path.parent.mkdir(parents=True, exist_ok=True)
    summary_path.write_text(json.dumps(summary, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    process_path.write_text(json.dumps(process, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
