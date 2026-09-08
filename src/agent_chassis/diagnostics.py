"""Value-free tool validation diagnostics shared by the core and edge adapters."""
from typing import Optional


MESSAGES = {
    "PARALLEL_DISABLED": "Parallel tool execution is disabled",
    "TOOL_NOT_PARALLEL_SAFE": "Tool must declare parallel_safe=True before batching",
    "UNKNOWN_TOOL": "Unknown tool; only assembled tools may execute",
    "DUPLICATE_CALL_ID": "Duplicate call ID within the batch",
    "INVALID_CALL_ID": "Call ID must be a non-empty string of at most 200 characters",
    "INVALID_TOOL_ARGUMENTS": "Tool arguments do not match inputSchema or function signature",
    "INVALID_BATCH": "Batch must contain at least two valid ToolRequest actions",
    "BATCH_LIMIT_EXCEEDED": "Model response exceeds max_batch_calls; no tools executed",
    "TOOL_BUDGET_EXHAUSTED": "Task-wide tool call budget exhausted",
}


class ToolDiagnostic(ValueError):
    """Never pass model values/IDs/unknown names or raw validator messages here.

    tool and argument_path may contain only names from trusted registration or
    schema metadata. action_index is one-based; call IDs are deliberately absent.
    """

    def __init__(self, code: str, *, action_index: Optional[int] = None,
                 tool: Optional[str] = None, argument_path: Optional[str] = None,
                 constraint: Optional[str] = None):
        if code not in MESSAGES:
            raise ValueError("Unknown diagnostic code")
        self.code = code
        self.details = {key: value for key, value in {
            "action_index": action_index, "tool": tool,
            "argument_path": argument_path, "constraint": constraint,
        }.items() if value is not None}
        detail = ", ".join(f"{key}={value}" for key, value in self.details.items())
        super().__init__(f"[{code}] {MESSAGES[code]}" + (f" ({detail})" if detail else ""))

    def as_dict(self):
        return {"code": self.code, **self.details}
