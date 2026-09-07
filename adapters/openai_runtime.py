"""OpenAI-compatible, non-streaming Chat Completions function-call adapter.

This implements single calls and opt-in independent batches, not the Responses API
or universal compatibility with every provider's optional parameters.
Requests disable parallel tool calls by default. Explicit wire-parameter omission
does not relax response validation or cause automatic retries.
"""
import json

from adapters.runtime import ModelError, RuntimeDecider, ToolRequest
from agent_chassis.diagnostics import ToolDiagnostic


class OpenAIChatDecider(RuntimeDecider):
    adapter_name = "openai-chat-completions"
    consumer = "openai-runtime"
    endpoint = "/chat/completions"
    usage_fields = ("prompt_tokens", "completion_tokens")

    def request_headers(self, key):
        return {"Authorization": "Bearer " + key, "content-type": "application/json"}

    def request_body(self, user_input, tools):
        body = {"model": self.config.model, "max_tokens": self.config.max_tokens, "stream": False,
                "messages": [{"role": "system", "content": self.system_prompt},
                             {"role": "user", "content": user_input}],
                "tools": [{"type": "function", "function": {
                    "name": tool["name"], "description": tool["description"],
                    "parameters": tool["input_schema"],
                }} for tool in tools], "tool_choice": "auto"}
        if self.config.openai_parallel_tool_calls is not None:
            body["parallel_tool_calls"] = self.config.openai_parallel_tool_calls
        return body

    def parse_response(self, response):
        choices = response.get("choices")
        if not isinstance(choices, list) or len(choices) != 1 or not isinstance(choices[0], dict):
            raise ModelError("Expected exactly one completion choice")
        choice = choices[0]
        message = choice.get("message")
        if not isinstance(message, dict) or message.get("role") != "assistant":
            raise ModelError("Invalid assistant message")
        finish = choice.get("finish_reason")
        if finish == "length":
            raise ModelError("Model output was truncated")
        calls = message.get("tool_calls", [])
        if calls is None:
            calls = []
        if not isinstance(calls, list):
            raise ModelError("Invalid tool_calls")
        if calls:
            if finish != "tool_calls":
                raise ModelError("Tool call is inconsistent with finish_reason")
            if len(calls) > 1 and self.config.max_parallel_tools < 2:
                # Reject the entire batch before selecting/parsing any action.
                # Names and arguments may contain sensitive data; report count only.
                raise ModelError(f"Expected at most one function call, received {len(calls)}; no tools executed",
                                 diagnostic=ToolDiagnostic("PARALLEL_DISABLED"))
            if len(calls) > self.config.max_batch_calls:
                raise ToolDiagnostic("BATCH_LIMIT_EXCEEDED")
            parsed = []
            for index, call in enumerate(calls, 1):
                if not isinstance(call, dict) or call.get("type") != "function":
                    raise ModelError("Expected function call")
                function = call.get("function")
                if not isinstance(function, dict) or not isinstance(function.get("arguments"), str):
                    raise ToolDiagnostic("INVALID_TOOL_ARGUMENTS", action_index=index, constraint="encoding")
                try:
                    args = json.loads(function["arguments"])
                except ValueError:
                    raise ToolDiagnostic("INVALID_TOOL_ARGUMENTS", action_index=index, constraint="json") from None
                parsed.append(ToolRequest(call.get("id", ""), function.get("name"), args))
            return self.normalize_calls(parsed)
        if finish == "stop":
            return "stop", "model ended its turn; awaiting independent verification", None
        raise ModelError("Model did not return a supported function call or stop")
