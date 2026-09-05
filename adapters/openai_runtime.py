"""OpenAI-compatible, non-streaming Chat Completions function-call adapter.

This implements the common single-function-call subset, not the Responses API
or universal compatibility with every provider's optional parameters.
"""
import json

from adapters.runtime import ModelError, RuntimeDecider, SYSTEM_PROMPT


class OpenAIChatDecider(RuntimeDecider):
    adapter_name = "openai-chat-completions"
    consumer = "openai-runtime"
    endpoint = "/chat/completions"
    usage_fields = ("prompt_tokens", "completion_tokens")

    def request_headers(self, key):
        return {"Authorization": "Bearer " + key, "content-type": "application/json"}

    def request_body(self, user_input, tools):
        return {"model": self.config.model, "max_tokens": self.config.max_tokens, "stream": False,
                "messages": [{"role": "system", "content": SYSTEM_PROMPT},
                             {"role": "user", "content": user_input}],
                "tools": [{"type": "function", "function": {
                    "name": tool["name"], "description": tool["description"],
                    "parameters": tool["input_schema"],
                }} for tool in tools], "tool_choice": "auto"}

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
            if len(calls) != 1 or not isinstance(calls[0], dict) or calls[0].get("type") != "function":
                raise ModelError("Expected one function call")
            function = calls[0].get("function")
            if not isinstance(function, dict) or not isinstance(function.get("arguments"), str):
                raise ModelError("Invalid function arguments encoding")
            try:
                args = json.loads(function["arguments"])
            except ValueError:
                raise ModelError("Function arguments are not JSON") from None
            return "call", function.get("name"), args
        if finish == "stop":
            return "stop", "model ended its turn; awaiting independent verification", None
        raise ModelError("Model did not return a supported function call or stop")
