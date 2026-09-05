"""Shared direct-API runtime; provider protocols stay outside the core package.

Each decision is a stateless request with explicit observations, not fabricated
assistant history. This module lives outside the zero-dependency core package.
No provider key, prompt body, tool arguments or model reasoning is logged here.
"""
from __future__ import annotations

import json
import os
import time
from dataclasses import dataclass
from typing import Any, Callable, Dict, Mapping, Optional, Sequence
from urllib.error import HTTPError, URLError
from urllib.parse import urlsplit
from urllib.request import HTTPRedirectHandler, Request, build_opener

from agent_chassis.contracts import InjectionPoint, RunContext, Task


SYSTEM_PROMPT = (
    "Complete the supplied task using the available tools. Use one tool at a time. "
    "Use observations to decide the next action. Stop when the requested artifact "
    "is submitted. The external verifier, not your narrative, decides success."
)
DEFAULT_ENDPOINTS = {
    "anthropic": "https://open.bigmodel.cn/api/anthropic",
    "openai": "https://open.bigmodel.cn/api/coding/paas/v4",
}


class ModelError(RuntimeError):
    """Sanitized model transport/protocol failure."""


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
        # Do not include the response body: gateways can echo input or credentials.
        raise ModelError(f"Model HTTP status {exc.code}") from None
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

    def __post_init__(self):
        url = urlsplit(self.base_url)
        if url.scheme != "https" or not url.hostname or url.username or url.password or url.query or url.fragment:
            raise ValueError("base_url must be HTTPS without credentials, query or fragment")
        if not self.model or not self.api_key_env or min(self.max_tokens, self.timeout, self.max_calls) <= 0:
            raise ValueError("Model, key reference and positive limits are required")
        if self.context_max_chars < 0:
            raise ValueError("context_max_chars cannot be negative")


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
            tools.append({"name": name, "description": spec.get("description", ""), "input_schema": schema})

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
        observations = [{"tool": call.name, "result": call.result, "ok": call.ok}
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
            if result[0] == "call":
                _, name, args = result
                if not isinstance(name, str) or name not in validators or not isinstance(args, dict):
                    raise ModelError("Unknown tool or invalid tool arguments")
                if list(validators[name].iter_errors(args)):
                    raise ModelError("Tool arguments do not match inputSchema")
            record["ok"] = True
            return result
        except Exception as exc:
            record["error_type"] = type(exc).__name__
            if isinstance(exc, ModelError):
                raise
            raise ModelError(f"Model adapter failed ({type(exc).__name__})") from None
        finally:
            record["elapsed_ms"] = int((time.monotonic() - started) * 1000)
            ctx.model_calls.append(record)
