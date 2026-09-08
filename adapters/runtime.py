"""Shared direct-API runtime; provider protocols stay outside the core package.

Each decision is a stateless request with explicit observations, not fabricated
assistant history. This module lives outside the zero-dependency core package.
No provider key, prompt body, tool arguments or model reasoning is logged here.
"""
from __future__ import annotations

import json
import os
import time
import warnings
from dataclasses import dataclass
from http.client import HTTPException
from typing import Any, Callable, Dict, List, Mapping, Optional, Sequence
from urllib.error import HTTPError, URLError
from urllib.parse import urlsplit
from urllib.request import HTTPRedirectHandler, Request, build_opener

from adapters.http_diagnostics import http_diagnostic
from agent_chassis.contracts import InjectionPoint, RunContext, Task
from agent_chassis.diagnostics import ToolDiagnostic
from agent_chassis.orchestration.reasoning import ReActPattern, ToolRequest, validate_batch


SYSTEM_PROMPT = (
    "Complete the supplied task using the available tools. Use one tool at a time. "
    "Use observations to decide the next action. Stop when the requested artifact "
    "is submitted. The external verifier, not your narrative, decides success."
)
PARALLEL_SYSTEM_PROMPT = (
    "Complete the supplied task using the available tools. You may batch independent "
    "calls only to tools whose descriptions declare parallel_safe=true. Tools without "
    "that declaration must be called alone. Never batch calls that depend on another "
    "call's result or affect the same mutable resource. Observe all batch results "
    "before deciding the next action. Stop when the requested artifact is submitted. "
    "The external verifier, not your narrative, decides success."
)
DEFAULT_ENDPOINTS = {
    "anthropic": "https://open.bigmodel.cn/api/anthropic",
    "openai": "https://open.bigmodel.cn/api/coding/paas/v4",
}


class ModelError(RuntimeError):
    """Sanitized model transport/protocol failure."""

    def __init__(self, message, *, diagnostic=None, http_error=None):
        super().__init__(message)
        self.diagnostic = diagnostic
        self.http_error = dict(http_error) if http_error is not None else None


def _schema_location(error, schema):
    """Only schema-declared keys/array indices; unknown object keys may be secrets."""
    path = "$"
    for part in error.absolute_path:
        if not isinstance(schema, dict):
            break
        if type(part) is int and schema.get("type") == "array":
            path += f"[{part}]"
            schema = schema.get("items", {})
        elif isinstance(part, str) and part in schema.get("properties", {}):
            path += "." + part
            schema = schema["properties"][part]
        else:
            break
    return path


class _NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        raise ModelError("Model endpoint redirects are not followed")


def post_json(url: str, headers: Mapping[str, str], body: Dict[str, Any], timeout: float) -> Dict[str, Any]:
    request = Request(url, data=json.dumps(body, ensure_ascii=False, allow_nan=False).encode("utf-8"),
                      headers=dict(headers), method="POST")
    try:
        with build_opener(_NoRedirect()).open(request, timeout=timeout) as response:
            data = response.read(2_000_001)
            if len(data) > 2_000_000:
                raise ModelError("Model response exceeds 2 MB")
            payload = json.loads(data)
    except HTTPError as exc:
        try:
            detail = http_diagnostic(exc)
        finally:
            try:
                exc.close()
            except (OSError, HTTPException):
                pass
        reason = detail.get("provider_code") or detail.get("provider_type") or detail["category"]
        raise ModelError(f"Model HTTP status {exc.code} ({reason})", http_error=detail) from None
    except (URLError, TimeoutError, OSError):
        raise ModelError("Model transport failed or timed out") from None
    except (UnicodeError, ValueError):
        raise ModelError("Model returned invalid JSON") from None
    if not isinstance(payload, dict) or "error" in payload:
        raise ModelError("Model returned an error or non-object response")
    return payload


@dataclass(frozen=True)
class ModelConfig:
    model: str
    base_url: str = DEFAULT_ENDPOINTS["anthropic"]
    api_key_env: str = "BIGMODEL_API_KEY"
    max_tokens: int = 2048
    timeout: float = 60.0
    max_calls: int = 8
    context_max_chars: int = 12000
    # OpenAI wire control only; execution is independently bounded below.
    openai_parallel_tool_calls: Optional[bool] = False
    max_parallel_tools: int = 1
    max_batch_calls: int = 8
    max_tool_calls: int = 64

    def __post_init__(self):
        url = urlsplit(self.base_url)
        if url.scheme != "https" or not url.hostname or url.username or url.password or url.query or url.fragment:
            raise ValueError("base_url must be HTTPS without credentials, query or fragment")
        if not self.model or not self.api_key_env or min(self.max_tokens, self.timeout, self.max_calls) <= 0:
            raise ValueError("Model, key reference and positive limits are required")
        if self.context_max_chars < 0:
            raise ValueError("context_max_chars cannot be negative")
        if self.openai_parallel_tool_calls is not None and type(self.openai_parallel_tool_calls) is not bool:
            raise ValueError("openai_parallel_tool_calls must be a boolean or None (omit)")
        for value in (self.max_parallel_tools, self.max_batch_calls, self.max_tool_calls):
            if type(value) is not int or value < 1:
                raise ValueError("Tool execution limits must be positive integers")
        if self.openai_parallel_tool_calls is True and self.max_parallel_tools < 2:
            raise ValueError("openai_parallel_tool_calls=True requires max_parallel_tools >= 2")

    def react_options(self):
        return {"max_iterations": self.max_calls, "max_parallel_tools": self.max_parallel_tools,
                "max_batch_calls": self.max_batch_calls, "max_tool_calls": self.max_tool_calls}


#: Executor limits that must agree between ModelConfig and ReActPattern.
_ALIGNMENT_FIELDS = ("max_parallel_tools", "max_batch_calls", "max_tool_calls")


def validate_react_alignment(config: ModelConfig, pattern: Any, toolbox: Any = None) -> List[str]:
    """Fail before any model request or tool call when executor limits disagree.

    Both sides hold execution limits independently: ModelConfig bounds what the
    adapter accepts, ReActPattern bounds what the executor runs. A mismatch
    stays silent until the model actually returns a multi-call batch, so it is
    checked here at assembly time instead. Raises ValueError naming the field,
    both values and the fix; returns non-fatal assembly hints.
    """
    missing = [name for name in _ALIGNMENT_FIELDS if not hasattr(pattern, name)]
    if missing:
        raise ValueError(
            "Alignment check applies to ReActPattern-style executors; "
            f"pattern is missing {missing}"
        )
    conflicts = []
    for name in _ALIGNMENT_FIELDS:
        model_side, executor_side = getattr(config, name), getattr(pattern, name)
        if model_side == executor_side:
            continue
        if name == "max_parallel_tools":
            detail = (f"model side allows batched calls ({model_side}) "
                      f"while the executor concurrency limit is {executor_side}")
        else:
            detail = f"model side is {model_side} while the executor is {executor_side}"
        conflicts.append(
            f"{name} mismatch: {detail}. Build the pattern with "
            "adapters.runtime.react_pattern(config, decider) or pass "
            "**config.react_options() so both sides agree"
        )
    if conflicts:
        raise ValueError("; ".join(conflicts))
    hints: List[str] = []
    if config.max_parallel_tools >= 2 and toolbox is not None:
        is_parallel_safe = getattr(toolbox, "is_parallel_safe", None)
        if callable(is_parallel_safe) and not any(
            is_parallel_safe(name) for name in toolbox.names()
        ):
            hints.append(
                f"Parallel execution is enabled (max_parallel_tools="
                f"{config.max_parallel_tools}) but no tool declares "
                "parallel_safe=True; every action will stay single-call"
            )
    return hints


def react_pattern(config: ModelConfig, decider: Callable, *, toolbox: Any = None,
                  **overrides: Any) -> ReActPattern:
    """Single wiring point from ModelConfig to ReActPattern.

    Applies config.react_options() (minus anything a caller overrides) and
    verifies model-side and executor-side limits agree before the assembly
    can run. Custom deciders remain valid: any Decide callable works, and
    the chassis core never imports from this adapter module.
    """
    pattern = ReActPattern(decider, **{**config.react_options(), **overrides})
    for hint in validate_react_alignment(config, pattern, toolbox):
        warnings.warn(hint, stacklevel=2)
    return pattern


class RuntimeDecider:
    """Common tool schemas, context, task-wide budget and evidence for protocols.

    Only the explicitly assembled tools are exposed. Every exposed tool needs
    an inputSchema; JSON Schema validation is an optional edge dependency.
    API keys are resolved at request time, never copied into config or evidence.
    """
    adapter_name = "abstract"
    consumer = "runtime"
    endpoint = ""
    usage_fields = ("input_tokens", "output_tokens")

    def __init__(self, config: ModelConfig, *, tool_names: Sequence[str],
                 transport: Optional[Callable[..., Dict[str, Any]]] = None):
        if not tool_names or len(set(tool_names)) != len(tool_names):
            raise ValueError("Provide distinct explicitly allowed tool names")
        self.config = config
        self.tool_names = tuple(tool_names)
        self.transport = transport if transport is not None else post_json

    @property
    def execution_mode(self) -> str:
        return "live" if self.transport is post_json else "test-transport"

    def request_body(self, user_input, tools):
        raise NotImplementedError

    def request_headers(self, key):
        raise NotImplementedError

    def parse_response(self, response):
        raise NotImplementedError

    @property
    def system_prompt(self):
        if self.config.max_parallel_tools == 1:
            return SYSTEM_PROMPT
        return PARALLEL_SYSTEM_PROMPT + f" At most {self.config.max_batch_calls} calls per batch."

    def normalize_calls(self, calls):
        if len(calls) == 1:
            return "call", calls[0].name, calls[0].args
        if self.config.max_parallel_tools < 2:
            raise ModelError(f"Expected at most one function call, received {len(calls)}; no tools executed",
                             diagnostic=ToolDiagnostic("PARALLEL_DISABLED"))
        if len(calls) > self.config.max_batch_calls:
            raise ToolDiagnostic("BATCH_LIMIT_EXCEEDED")
        return "batch", calls, None

    def __call__(self, task: Task, ctx: RunContext, toolbox: Any) -> tuple:
        try:
            from jsonschema import Draft202012Validator
        except ImportError:
            raise ModelError('Install the optional adapter dependency: pip install -e ".[llm]"') from None

        config = self.config
        if len(ctx.model_calls) >= config.max_calls:
            ctx.facts["stop_reason"] = "model_call_limit"
            raise ModelError("Model call budget exhausted")
        key = os.environ.get(config.api_key_env)
        if not key:
            raise ModelError(f"Missing environment variable: {config.api_key_env}")

        available = {item["name"]: item for item in toolbox.schema()}
        validators, tools = {}, []
        for name in self.tool_names:
            spec = available.get(name)
            if spec is None or "inputSchema" not in spec:
                raise ModelError(f"Explicit inputSchema required for tool: {name}")
            schema = spec["inputSchema"]
            Draft202012Validator.check_schema(schema)
            validators[name] = Draft202012Validator(schema)
            description = spec.get("description", "")
            if config.max_parallel_tools > 1:
                safe = getattr(toolbox, "is_parallel_safe", lambda _: False)(name)
                description += " [parallel_safe=" + str(safe).lower() + "]"
            tools.append({"name": name, "description": description, "input_schema": schema})

        # Direct-API path: this request IS the executor boundary. It does not put
        # technology-specific knowledge in AGENT_BOOT or in the Chassis core.
        if ctx.chassis is not None:
            ctx.chassis.inject(InjectionPoint.BEFORE_EXECUTOR, task, ctx)
        context = ctx.context_for(self.consumer, [
            InjectionPoint.ON_RETRY, InjectionPoint.TASK_ADMITTED, InjectionPoint.BEFORE_EXECUTOR,
        ], max_chars=config.context_max_chars)
        # Previous attempts may have been compensated. Keep their trace, but do
        # not tell the model that discarded artifacts are still present.
        attempt_calls = ctx.tool_calls[ctx.facts.get("attempt_tool_call_start", 0):]
        observations = [{"tool": call.name, "result": call.result, "ok": call.ok,
                         **({"args": call.args, "call_id": call.call_id, "batch_id": call.batch_id}
                            if call.call_id else {})}
                        for call in attempt_calls if call.name in self.tool_names]
        user_input = json.dumps({"task": task.payload, "observations": observations,
                                 "context": context}, ensure_ascii=False, allow_nan=False)
        if len(user_input) > 100_000:
            raise ModelError("Task and observations exceed the reference adapter input limit")
        record = {"adapter": self.adapter_name, "model": config.model,
                  "mode": self.execution_mode,
                  "ok": False, "input_tokens": None, "output_tokens": None}
        started = time.monotonic()
        try:
            response = self.transport(config.base_url.rstrip("/") + self.endpoint,
                                      self.request_headers(key), self.request_body(user_input, tools), config.timeout)
            if not isinstance(response, dict) or "error" in response:
                raise ModelError("Invalid model response")
            usage = response.get("usage") or {}
            if not isinstance(usage, dict):
                raise ModelError("Invalid model usage")
            for field, wire_field in zip(("input_tokens", "output_tokens"), self.usage_fields):
                value = usage.get(wire_field)
                record[field] = value if type(value) is int and value >= 0 else None
            record["request_id"] = str(response.get("id", ""))[:200]
            record["resolved_model"] = str(response.get("model", config.model))[:200]
            result = self.parse_response(response)
            actions = ([ToolRequest("", result[1], result[2])] if result[0] == "call"
                       else result[1] if result[0] == "batch" else [])
            for index, action in enumerate(actions, 1):
                name, args = action.name, action.args
                if not isinstance(name, str) or name not in validators:
                    raise ToolDiagnostic("UNKNOWN_TOOL", action_index=index)
                if not isinstance(args, dict):
                    raise ToolDiagnostic("INVALID_TOOL_ARGUMENTS", action_index=index, tool=name,
                                         argument_path="$", constraint="type")
                error = next(validators[name].iter_errors(args), None)
                if error is not None:
                    raise ToolDiagnostic("INVALID_TOOL_ARGUMENTS", action_index=index, tool=name,
                                         argument_path=_schema_location(error, validators[name].schema),
                                         constraint=str(error.validator))
            if result[0] == "batch":
                validate_batch(toolbox, actions, config.max_batch_calls)
            record["ok"] = True
            return result
        except Exception as exc:
            if isinstance(exc, ModelError) and exc.http_error is not None:
                record["http_error"] = dict(exc.http_error)
            diagnostic = exc if isinstance(exc, ToolDiagnostic) else getattr(exc, "diagnostic", None)
            if isinstance(diagnostic, ToolDiagnostic):
                record["diagnostic"] = diagnostic.as_dict()
                ctx.diagnostics.append(diagnostic.as_dict())
            record["error_type"] = "ModelError" if isinstance(exc, ToolDiagnostic) else type(exc).__name__
            if isinstance(exc, ModelError):
                raise
            if isinstance(exc, ToolDiagnostic):
                raise ModelError(str(exc), diagnostic=exc) from None
            raise ModelError(f"Model adapter failed ({type(exc).__name__})") from None
        finally:
            record["elapsed_ms"] = int((time.monotonic() - started) * 1000)
            ctx.model_calls.append(record)
