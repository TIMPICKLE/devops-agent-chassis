"""Synthetic SSE and loopback HTTP only; no model services or private data."""
from email.message import Message
from http.client import IncompleteRead
from http.server import BaseHTTPRequestHandler, HTTPServer
from io import BytesIO
import json
from threading import Thread
from urllib.error import HTTPError

import pytest

pytest.importorskip("jsonschema")

from adapters import runtime, streaming
from adapters.anthropic_runtime import AnthropicDecider
from adapters.openai_runtime import OpenAIChatDecider
from adapters.runtime import ModelConfig, ModelError, react_pattern
from agent_chassis.contracts import RunContext, Task
from agent_chassis.orchestration import ToolBox

CANARY = "private-stream-canary"
ADAPTERS = {"openai": OpenAIChatDecider, "anthropic": AnthropicDecider}
TRANSPORTS = {"openai": runtime.post_openai_stream, "anthropic": runtime.post_anthropic_stream}


def event(data, name="", newline="\n"):
    text = data if isinstance(data, str) else json.dumps(data, ensure_ascii=False)
    return (("event: " + name + newline if name else "") + "data: " + text + newline * 2).encode()


def openai_wire(*, finish="tool_calls", usage=True, args=None):
    def chunk(delta, stop=None):
        return event({"id": "completion-1", "model": "synthetic", "choices": [
            {"index": 0, "delta": delta, "finish_reason": stop}], "usage": None})
    output = [b"\xef\xbb\xbf: heartbeat\r\n\r\n", chunk({"role": "assistant", "reasoning_content": CANARY})]
    values = args if args is not None else ['{"value":"中文"}', '{"value":"second"}']
    # Indices arrive interleaved and out of order. IDs, names and JSON may split.
    for part in (0, 1):
        for index in reversed(range(len(values))):
            value = values[index]
            split = max(1, len(value) // 2)
            output.append(chunk({"tool_calls": [{"index": index,
                "id": "call-" if part == 0 else str(index), "type": "function" if part == 0 else None,
                "function": {"name": "re" if part == 0 else "ad",
                             "arguments": value[:split] if part == 0 else value[split:]}}]}))
    output.append(chunk({}, finish))
    if usage:
        output.append(event({"choices": [], "usage": {"prompt_tokens": 12, "completion_tokens": 5}}))
    output.append(event("[DONE]"))
    return b"".join(output)


def anthropic_wire(*, finish="tool_use", usage=True, args=None):
    def emit(kind, **fields):
        return event({"type": kind, **fields}, kind, "\r\n")
    output = [emit("message_start", message={"id": "message-1", "model": "synthetic",
        "role": "assistant", "content": [], **({"usage": {"input_tokens": 12, "output_tokens": 1}} if usage else {})}),
        emit("ping"), emit("content_block_start", index=0, content_block={"type": "thinking", "thinking": ""}),
        emit("content_block_delta", index=0, delta={"type": "thinking_delta", "thinking": CANARY}),
        emit("content_block_delta", index=0, delta={"type": "signature_delta", "signature": CANARY}),
        emit("content_block_stop", index=0)]
    values = args if args is not None else ['{"value":"中文"}', '{"value":"second"}']
    for i, value in enumerate(values, 1):
        output.append(emit("content_block_start", index=i,
                           content_block={"type": "tool_use", "id": f"call-{i-1}", "name": "read", "input": {}}))
        for part in (value[:3], value[3:]):
            output.append(emit("content_block_delta", index=i, delta={"type": "input_json_delta", "partial_json": part}))
        output.append(emit("content_block_stop", index=i))
    output.append(emit("message_delta", delta={"stop_reason": None}, **({"usage": {"output_tokens": 3}} if usage else {})))
    output.append(emit("message_delta", delta={"stop_reason": finish}, **({"usage": {"output_tokens": 5}} if usage else {})))
    output.append(emit("message_stop"))
    return b"".join(output)


WIRES = {"openai": openai_wire, "anthropic": anthropic_wire}


class Response(BytesIO):
    def __init__(self, data, *, media_type="text/event-stream; charset=utf-8", fragment=3, fail=None):
        super().__init__(data)
        self.headers = Message()
        self.headers["Content-Type"] = media_type
        self.fragment, self.fail = fragment, fail

    def read1(self, size=-1):
        if self.fail and self.tell() >= len(self.getvalue()) // 2:
            raise self.fail
        return super().read(min(size, self.fragment))


def intercept(monkeypatch, response):
    requests = []

    class Opener:
        def open(self, request, *, timeout):
            requests.append((json.loads(request.data), timeout))
            if isinstance(response, Exception):
                raise response
            return response

    monkeypatch.setattr(runtime, "build_opener", lambda *args: Opener())
    return requests


def setup(monkeypatch, protocol, response, *, safe=True, **options):
    requests = intercept(monkeypatch, response)
    monkeypatch.setenv("TEST_STREAM_KEY", "synthetic-not-a-credential")
    config = ModelConfig("synthetic", api_key_env="TEST_STREAM_KEY", stream=True,
                         max_parallel_tools=2, **options)
    decider = ADAPTERS[protocol](config, tool_names=["read"],
                                transport=lambda *args: TRANSPORTS[protocol](*args))
    calls = []

    def read(value):
        assert response.closed  # Full response accepted and closed before execution.
        calls.append(value)
        return value

    box = ToolBox().add("read", read, parallel_safe=safe, input_schema={"type": "object",
        "properties": {"value": {"type": "string"}}, "required": ["value"], "additionalProperties": False})
    return config, decider, box, calls, requests


@pytest.mark.parametrize("protocol", ADAPTERS)
def test_fragmented_stream_executes_complete_batch_in_index_order_and_keeps_usage(monkeypatch, protocol):
    response = Response(WIRES[protocol]())
    config, decider, box, calls, requests = setup(monkeypatch, protocol, response)
    pattern = react_pattern(config, decider, toolbox=box, stop_when=lambda t, c: "checked" if c.tool_calls else None)
    ctx = RunContext()
    pattern.reason(Task("stream", "test"), ctx, box)
    assert sorted(calls) == ["second", "中文"]
    assert [call.call_id for call in ctx.tool_calls] == ["call-0", "call-1"]
    assert [call.result for call in ctx.tool_calls] == ["中文", "second"]
    assert len(requests) == len(ctx.model_calls) == 1
    assert requests[0][0]["stream"] is True
    record = ctx.model_calls[0]
    assert record["ok"] and record["stream"] and record["mode"] == "test-transport"
    assert (record["input_tokens"], record["output_tokens"]) == (12, 5)
    assert CANARY not in json.dumps(record)


@pytest.mark.parametrize("protocol", ADAPTERS)
def test_absent_stream_usage_stays_unknown(monkeypatch, protocol):
    response = Response(WIRES[protocol](usage=False))
    _, decider, box, _, _ = setup(monkeypatch, protocol, response)
    ctx = RunContext()
    decider(Task("t", "test"), ctx, box)
    assert ctx.model_calls[0]["input_tokens"] is None and ctx.model_calls[0]["output_tokens"] is None


@pytest.mark.parametrize("protocol", ADAPTERS)
@pytest.mark.parametrize("damage", ["eof", "event-json", "event-error", "timeout", "incomplete-read", "wrong-content-type"])
def test_incomplete_or_failed_stream_never_executes_or_retries(monkeypatch, protocol, damage):
    wire = WIRES[protocol]()
    if damage == "eof":
        wire = wire.rsplit(b"data: [DONE]", 1)[0] if protocol == "openai" else wire.rsplit(b"event: message_stop", 1)[0]
    elif damage == "event-json":
        wire = event("{invalid-" + CANARY)
    elif damage == "event-error":
        # Even a stream with complete tool argument fragments may subsequently fail.
        marker = event("[DONE]") if protocol == "openai" else b'event: message_stop'
        wire = wire.split(marker)[0] + event({"type": "error", "error": {"message": CANARY}}, "error")
    broken = TimeoutError(CANARY) if damage == "timeout" else IncompleteRead(CANARY.encode()) if damage == "incomplete-read" else None
    response = Response(wire, fail=broken, media_type="application/json" if damage == "wrong-content-type" else "text/event-stream")
    config, decider, box, calls, requests = setup(monkeypatch, protocol, response, max_calls=1)
    ctx = RunContext()
    pattern = react_pattern(config, decider, toolbox=box)
    with pytest.raises(ModelError) as error:
        pattern.reason(Task("t", "test"), ctx, box)
    assert response.closed and calls == [] and ctx.tool_calls == []
    assert CANARY not in str(error.value) + json.dumps(ctx.model_calls)
    assert ctx.model_calls[0]["ok"] is False and ctx.model_calls[0]["stream"] is True
    assert ctx.model_calls[0]["input_tokens"] is None and ctx.model_calls[0]["output_tokens"] is None
    with pytest.raises(ModelError, match="budget exhausted"):
        decider(Task("t", "test"), ctx, box)
    assert len(requests) == len(ctx.model_calls) == 1


@pytest.mark.parametrize("protocol", ADAPTERS)
@pytest.mark.parametrize("failure", ["unsafe", "truncated", "bad-arguments"])
def test_completed_but_invalid_stream_preserves_usage_and_rejects_all_tools(monkeypatch, protocol, failure):
    options = {}
    if failure == "truncated":
        options["finish"] = "length" if protocol == "openai" else "max_tokens"
    if failure == "bad-arguments":
        options["args"] = ['{"value":"valid"}', '{"value":']
    response = Response(WIRES[protocol](**options))
    config, decider, box, calls, _ = setup(monkeypatch, protocol, response, safe=failure != "unsafe")
    if failure == "unsafe":
        with pytest.warns(UserWarning):
            pattern = react_pattern(config, decider, toolbox=box)
    else:
        pattern = react_pattern(config, decider, toolbox=box)
    ctx = RunContext()
    with pytest.raises(ModelError):
        pattern.reason(Task("t", "test"), ctx, box)
    assert calls == [] and ctx.tool_calls == [] and response.closed
    assert (ctx.model_calls[0]["input_tokens"], ctx.model_calls[0]["output_tokens"]) == (12, 5)


@pytest.mark.parametrize("protocol", ADAPTERS)
def test_stream_http_error_reuses_diagnostics_and_closes_response(monkeypatch, protocol):
    body = BytesIO(json.dumps({"error": {"code": "unsupported_parameter", "message": CANARY}}).encode())
    error = HTTPError("https://example.invalid", 400, CANARY, Message(), body)
    _, decider, box, calls, requests = setup(monkeypatch, protocol, error)
    ctx = RunContext()
    with pytest.raises(ModelError, match="HTTP status 400") as caught:
        decider(Task("t", "test"), ctx, box)
    assert caught.value.http_error["provider_code"] == "unsupported_parameter"
    assert body.closed and not calls and len(requests) == 1
    assert ctx.model_calls[0]["http_error"]["status"] == 400


@pytest.mark.parametrize("protocol", ADAPTERS)
@pytest.mark.parametrize("limit", ["MAX_STREAM_BYTES", "MAX_EVENT_BYTES", "MAX_EVENTS"])
def test_stream_limits_close_without_executing(monkeypatch, protocol, limit):
    monkeypatch.setattr(streaming, limit, 1)
    response = Response(WIRES[protocol]())
    _, decider, box, calls, _ = setup(monkeypatch, protocol, response)
    with pytest.raises(ModelError):
        decider(Task("t", "test"), RunContext(), box)
    assert response.closed and not calls


@pytest.mark.parametrize("newline", ["\n", "\r\n", "\r"])
def test_sse_multiline_comments_and_utf8_boundaries(newline):
    wire = (": comment" + newline + "event: message" + newline + 'data: {"text":' + newline
            + 'data: "中文"}' + newline + "id: ignored" + newline + "retry: 1" + newline * 2).encode()
    assert list(streaming.events(Response(wire, fragment=1))) == [("message", '{"text":\n"中文"}')]


@pytest.mark.parametrize("protocol", ADAPTERS)
def test_plain_text_stop_and_transport_mode(protocol):
    wire = WIRES[protocol](finish="stop" if protocol == "openai" else "end_turn", args=[])
    reader = streaming.openai_message if protocol == "openai" else streaming.anthropic_message
    config = ModelConfig("synthetic", stream=True)
    decider = ADAPTERS[protocol](config, tool_names=["read"])
    assert decider.execution_mode == "live"
    assert decider.parse_response(reader(Response(wire)))[0] == "stop"
    assert ADAPTERS[protocol](config, tool_names=["read"], transport=lambda *a: {}).execution_mode == "test-transport"


def test_stream_cli_configuration_and_openai_usage_omission():
    from argparse import ArgumentParser
    from tools.run_roadmap_showcase import add_model_arguments, model_config
    parser = ArgumentParser()
    add_model_arguments(parser)
    for options, stream, include in [([], False, True), (["--stream"], True, True),
                                      (["--stream", "--openai-omit-stream-usage"], True, False)]:
        cfg = model_config(parser.parse_args(options))
        assert cfg.stream is stream and cfg.openai_stream_include_usage is include
        body = OpenAIChatDecider(cfg, tool_names=["read"]).request_body("test", [])
        assert body["stream"] is stream
        assert ("stream_options" in body) is (stream and include)
    for field in ("stream", "openai_stream_include_usage"):
        with pytest.raises(ValueError, match="booleans"):
            ModelConfig("synthetic", **{field: "true"})


@pytest.mark.parametrize("protocol,damage", [
    ("openai", "choice-index"), ("openai", "tool-index"), ("openai", "identity"),
    ("openai", "role"), ("openai", "missing-finish"), ("openai", "late-tool"),
    ("anthropic", "duplicate-block"), ("anthropic", "unclosed-block"),
    ("anthropic", "event-mismatch"), ("anthropic", "missing-start"),
    ("anthropic", "unsupported-block"), ("anthropic", "late-tool"),
])
def test_protocol_sequence_corruption_never_becomes_tool_actions(monkeypatch, protocol, damage):
    frames = list(streaming.events(Response(WIRES[protocol]())))
    decoded = [(name, json.loads(data) if data != "[DONE]" else data) for name, data in frames]
    if damage == "choice-index":
        decoded[0][1]["choices"][0]["index"] = 1
    elif damage == "tool-index":
        decoded[1][1]["choices"][0]["delta"]["tool_calls"][0]["index"] = -1
    elif damage == "identity":
        decoded[1][1]["id"] = "different-completion"
    elif damage == "role":
        decoded[0][1]["choices"][0]["delta"]["role"] = "user"
    elif damage == "missing-finish":
        for _, data in decoded:
            if isinstance(data, dict) and data.get("choices"):
                data["choices"][0]["finish_reason"] = None
    elif damage == "late-tool":
        frame = next(frame for frame in decoded if isinstance(frame[1], dict) and (
            frame[1].get("type") == "content_block_start" or
            (frame[1].get("choices") and frame[1]["choices"][0]["delta"].get("tool_calls"))))
        decoded.insert(-1, frame)
    elif damage == "duplicate-block":
        frame = next(frame for frame in decoded if frame[1].get("type") == "content_block_start")
        decoded.insert(3, frame)
    elif damage == "unclosed-block":
        decoded = [frame for frame in decoded if frame[1].get("type") != "content_block_stop"]
    elif damage == "event-mismatch":
        decoded[0] = ("message_stop", decoded[0][1])
    elif damage == "missing-start":
        decoded.pop(0)
    elif damage == "unsupported-block":
        next(data for _, data in decoded if data.get("type") == "content_block_start")["content_block"]["type"] = "server_tool_use"
    wire = b"".join(event(data, name) for name, data in decoded)
    response = Response(wire)
    _, decider, box, calls, _ = setup(monkeypatch, protocol, response)
    with pytest.raises(ModelError):
        decider(Task("t", "test"), RunContext(), box)
    assert not calls and response.closed


@pytest.mark.parametrize("protocol", ADAPTERS)
def test_stream_evidence_export_and_old_records_remain_valid(monkeypatch, protocol):
    from agent_chassis import Chassis
    from agent_chassis.contracts import DoneCriteria, TaskSource, Verdict
    from agent_chassis.evidence import EvidenceObserver, assembly_manifest, content_id
    from agent_chassis.orchestration import SingleAgentOrchestrator
    from tools.verify_roadmap_evidence import verify_documents

    class Source(TaskSource):
        def fetch(self, limit=1):
            return [Task("stream", "test")]

    class Criteria(DoneCriteria):
        def judge(self, task, ctx):
            return Verdict([call.result for call in ctx.tool_calls] == ["中文", "second"], "actual values")

    config, decider, box, _, _ = setup(monkeypatch, protocol, Response(WIRES[protocol]()))
    pattern = react_pattern(config, decider, toolbox=box, stop_when=lambda t, c: "done" if c.tool_calls else None)
    observer = EvidenceObserver(mode="test-transport")
    chassis = (Chassis("stream-test").with_payload(Source(), Criteria()).observe(observer)
               .with_orchestrator(SingleAgentOrchestrator(box, pattern)).build())
    manifest = assembly_manifest(chassis.report())
    observer.assembly_id = manifest["content_id"]
    try:
        assert chassis.run_once().verdict.done
        evidence = json.loads(json.dumps(observer.snapshot()))
        run = evidence["runs"][0]
        assert run["model_calls"][0]["stream"] is True
        assert run["usage"] == {"complete": True, "input_tokens": 12, "output_tokens": 5}
        assert CANARY not in json.dumps(evidence)
        assert verify_documents(manifest, evidence) == 1
        run["model_calls"][0].pop("stream")
        run["content_id"] = content_id({k: v for k, v in run.items() if k != "content_id"})
        assert verify_documents(manifest, evidence) == 1  # older records lack this optional field
    finally:
        chassis.close()


@pytest.mark.parametrize("protocol", ADAPTERS)
def test_real_chunked_http_response_reassembles_stream_without_sdk(protocol):
    received = []
    wire = WIRES[protocol]()

    class Handler(BaseHTTPRequestHandler):
        def do_POST(self):
            received.append(json.loads(self.rfile.read(int(self.headers["Content-Length"]))))
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream")
            self.send_header("Transfer-Encoding", "chunked")
            self.end_headers()
            try:
                for start in range(0, len(wire), 7):
                    chunk = wire[start:start + 7]
                    self.wfile.write(f"{len(chunk):x}\r\n".encode() + chunk + b"\r\n")
                    self.wfile.flush()
                self.wfile.write(b"0\r\n\r\n")
            except (BrokenPipeError, ConnectionResetError):
                pass  # client closes at protocol terminator, not HTTP EOF

        def log_message(self, *args):
            pass

    server = HTTPServer(("127.0.0.1", 0), Handler)
    thread = Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        result = TRANSPORTS[protocol](f"http://127.0.0.1:{server.server_port}", {}, {"stream": True}, 3)
        action = ADAPTERS[protocol](ModelConfig("synthetic", stream=True, max_parallel_tools=2),
                                   tool_names=["read"]).parse_response(result)
        assert action[0] == "batch" and [r.args["value"] for r in action[1]] == ["中文", "second"]
        assert received == [{"stream": True}]
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=3)
