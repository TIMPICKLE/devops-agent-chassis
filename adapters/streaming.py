"""Bounded SSE readers. Accumulate complete messages; never execute tools here.

Only protocol metadata, tool calls and usage survive aggregation. Text/thinking
are consumed but not retained. All errors use fixed messages, never wire values.
"""
import codecs
import json

MAX_STREAM_BYTES = 2_000_000
MAX_EVENT_BYTES = 256_000
MAX_EVENTS = 10_000
MAX_BLOCKS = 256


class StreamError(RuntimeError):
    pass


def _require(condition, message="Invalid stream event structure"):
    if not condition:
        raise StreamError(message)


def _lines(response):
    """read1 avoids waiting for a full buffer; decode across UTF-8 boundaries."""
    decoder = codecs.getincrementaldecoder("utf-8-sig")("strict")
    total = line_bytes = 0
    line = []
    after_cr = False
    while True:
        chunk = response.read1(4096)
        total += len(chunk)
        _require(total <= MAX_STREAM_BYTES, "Model stream exceeds 2 MB")
        text = decoder.decode(chunk, final=not chunk)
        for char in text:
            if after_cr and char == "\n":
                after_cr = False
                continue
            after_cr = char == "\r"
            if char in "\r\n":
                yield "".join(line)
                line, line_bytes = [], 0
            else:
                line_bytes += len(char.encode("utf-8"))
                _require(line_bytes <= MAX_EVENT_BYTES, "Model stream line exceeds limit")
                line.append(char)
        if not chunk:
            _require(not line, "Model stream ended inside an event")
            return


def events(response):
    data, event = [], ""
    size = count = 0
    for line in _lines(response):
        if not line:
            if data:
                count += 1
                _require(count <= MAX_EVENTS, "Model stream has too many events")
                yield event, "\n".join(data)
            data, event, size = [], "", 0
            continue
        size += len(line.encode("utf-8")) + 1
        _require(size <= MAX_EVENT_BYTES, "Model stream event exceeds limit")
        if line.startswith(":"):
            continue
        field, _, value = line.partition(":")
        if value.startswith(" "):
            value = value[1:]
        if field == "data":
            data.append(value)
        elif field == "event":
            event = value
        # id/retry/unknown SSE fields do not alter this request or cause retries.
    _require(not data, "Model stream ended inside an event")


def _object(data):
    try:
        value = json.loads(data)
    except (ValueError, RecursionError):
        raise StreamError("Model stream contains invalid JSON") from None
    _require(isinstance(value, dict))
    _require("error" not in value, "Model stream reported an error")
    return value


def _index(value):
    _require(type(value) is int and 0 <= value < MAX_BLOCKS,
             "Invalid stream block index")
    return value


def _identity(target, source):
    for key in ("id", "model"):
        value = source.get(key)
        if value is not None:
            _require(isinstance(value, str) and len(value) <= 200)
            _require(key not in target or target[key] == value,
                     "Model stream identity changed")
            target[key] = value


def openai_message(response):
    result, calls, usage = {}, {}, {}
    role = finish = None
    for event, data in events(response):
        _require(event != "error", "Model stream reported an error")
        if data == "[DONE]":
            _require(role == "assistant" and finish is not None,
                     "Model stream ended without a completion finish reason")
            _require(sorted(calls) == list(range(len(calls))), "Missing stream tool index")
            return {**result, "usage": usage, "choices": [{"index": 0,
                "finish_reason": finish, "message": {"role": role,
                "tool_calls": [calls[i] for i in sorted(calls)]}}]}
        chunk = _object(data)
        _identity(result, chunk)
        reported = chunk.get("usage")
        if reported is not None:
            _require(isinstance(reported, dict), "Invalid model stream usage")
            usage = reported
        choices = chunk.get("choices")
        _require(isinstance(choices, list) and len(choices) <= 1,
                 "Expected one stream completion choice")
        if not choices:
            _require(reported is not None, "Empty stream completion choice")
            continue
        choice = choices[0]
        _require(isinstance(choice, dict) and type(choice.get("index")) is int
                 and choice["index"] == 0, "Unexpected stream completion choice")
        delta = choice.get("delta")
        _require(isinstance(delta, dict))
        _require(finish is None, "Model stream continued after completion finish")
        if delta.get("role") is not None:
            _require(delta["role"] == "assistant", "Invalid stream message role")
            role = "assistant"
        _require(not delta.get("function_call"), "Legacy streamed function_call is unsupported")
        fragments = delta.get("tool_calls")
        if fragments is not None:
            _require(isinstance(fragments, list))
            for fragment in fragments:
                _require(isinstance(fragment, dict))
                index = _index(fragment.get("index"))
                call = calls.setdefault(index, {"id": "", "type": "function",
                                                "function": {"name": "", "arguments": ""}})
                kind = fragment.get("type")
                _require(kind in (None, "function"), "Unsupported stream tool type")
                identifier = fragment.get("id")
                if identifier is not None:
                    _require(isinstance(identifier, str))
                    call["id"] += identifier
                function = fragment.get("function")
                if function is not None:
                    _require(isinstance(function, dict))
                    for key in ("name", "arguments"):
                        value = function.get(key)
                        if value is not None:
                            _require(isinstance(value, str))
                            call["function"][key] += value
        if choice.get("finish_reason") is not None:
            finish = choice["finish_reason"]
            _require(isinstance(finish, str) and bool(finish))
    raise StreamError("Model stream ended without [DONE]")


def anthropic_message(response):
    result, blocks, closed, fragments, usage = {}, {}, set(), {}, {}
    started = False
    finish = None
    for event, data in events(response):
        _require(event != "error", "Model stream reported an error")
        chunk = _object(data)
        kind = chunk.get("type")
        _require(not event or event == kind, "Stream event type mismatch")
        if kind == "ping":
            continue
        if kind == "message_start":
            _require(not started, "Duplicate stream message start")
            message = chunk.get("message")
            _require(isinstance(message, dict) and message.get("role") == "assistant"
                     and message.get("content") == [])
            _identity(result, message)
            reported = message.get("usage", {})
            _require(isinstance(reported, dict), "Invalid model stream usage")
            # Output at message_start is provisional; only final deltas can complete it.
            usage = {"input_tokens": reported.get("input_tokens")}
            started = True
            continue
        _require(started, "Model stream event before message start")
        if kind == "message_stop":
            _require(finish is not None and len(closed) == len(blocks),
                     "Model stream stopped before content completed")
            _require(sorted(blocks) == list(range(len(blocks))), "Missing stream block index")
            content = []
            for index in sorted(blocks):
                block = blocks[index]
                if block["type"] == "tool_use":
                    argument_text = "".join(fragments[index])
                    if argument_text:
                        _require(not block["input"], "Conflicting streamed tool input")
                        try:
                            block["input"] = json.loads(argument_text)
                        except (ValueError, RecursionError):
                            # Keep malformed input for the adapter's normal preflight.
                            # Usage must still be recorded on a complete failed response.
                            block["input"] = argument_text
                    content.append(block)
            return {**result, "content": content, "stop_reason": finish, "usage": usage}
        if kind == "message_delta":
            _require(len(closed) == len(blocks), "Message delta before content completed")
            delta = chunk.get("delta")
            _require(isinstance(delta, dict))
            if delta.get("stop_reason") is not None:
                value = delta["stop_reason"]
                _require(isinstance(value, str) and bool(value) and finish in (None, value))
                finish = value
            reported = chunk.get("usage", {})
            _require(isinstance(reported, dict), "Invalid model stream usage")
            usage.update(reported)  # cumulative values, never summed across deltas
            continue
        _require(finish is None, "Content after stream message finish")
        index = _index(chunk.get("index"))
        if kind == "content_block_start":
            _require(index not in blocks, "Duplicate stream content block")
            block = chunk.get("content_block")
            _require(isinstance(block, dict))
            block_type = block.get("type")
            _require(block_type in ("tool_use", "text", "thinking", "redacted_thinking"),
                     "Unsupported stream content block")
            if block_type == "tool_use":
                _require(isinstance(block.get("id"), str) and isinstance(block.get("name"), str)
                         and isinstance(block.get("input"), dict))
                blocks[index] = {key: block[key] for key in ("type", "id", "name", "input")}
                fragments[index] = []
            else:
                blocks[index] = {"type": block_type}
        elif kind in ("content_block_delta", "content_block_stop"):
            _require(index in blocks and index not in closed, "Stream block is not open")
            if kind == "content_block_stop":
                closed.add(index)
                continue
            delta = chunk.get("delta")
            _require(isinstance(delta, dict))
            block_type = blocks[index]["type"]
            if block_type == "tool_use":
                _require(delta.get("type") == "input_json_delta"
                         and isinstance(delta.get("partial_json"), str))
                fragments[index].append(delta["partial_json"])
            else:
                allowed = {"text": ("text_delta",), "thinking": ("thinking_delta", "signature_delta"),
                           "redacted_thinking": ()}
                _require(delta.get("type") in allowed[block_type], "Unsupported stream content delta")
        else:
            raise StreamError("Unsupported stream event")
    raise StreamError("Model stream ended without message_stop")
