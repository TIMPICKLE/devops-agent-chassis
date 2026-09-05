"""Anthropic Messages wire format, including the existing GLM gateway.

ModelConfig, ModelError and post_json remain importable here for compatibility.
"""
from adapters.runtime import ModelConfig, ModelError, RuntimeDecider, SYSTEM_PROMPT, post_json


class AnthropicDecider(RuntimeDecider):
    adapter_name = "anthropic-messages"
    consumer = "anthropic-runtime"
    endpoint = "/v1/messages"

    def request_headers(self, key):
        return {"x-api-key": key, "anthropic-version": "2023-06-01", "content-type": "application/json"}

    def request_body(self, user_input, tools):
        return {"model": self.config.model, "max_tokens": self.config.max_tokens,
                "system": SYSTEM_PROMPT, "messages": [{"role": "user", "content": user_input}],
                "tools": tools, "tool_choice": {"type": "auto", "disable_parallel_tool_use": True}}

    def parse_response(self, response):
        content = response.get("content")
        if not isinstance(content, list) or any(not isinstance(x, dict) for x in content):
            raise ModelError("Invalid content blocks")
        calls = [x for x in content if x.get("type") == "tool_use"]
        if response.get("stop_reason") == "max_tokens":
            raise ModelError("Model output was truncated")
        if calls:
            if response.get("stop_reason") != "tool_use":
                raise ModelError("Tool call is inconsistent with stop_reason")
            if len(calls) != 1:
                raise ModelError("Expected one tool call, received multiple")
            return "call", calls[0].get("name"), calls[0].get("input")
        if response.get("stop_reason") == "end_turn":
            return "stop", "model ended its turn; awaiting independent verification", None
        raise ModelError("Model did not return a supported tool call or end_turn")
