import hashlib
from http.client import IncompleteRead
from email.message import Message
from io import BytesIO
import json
from pathlib import Path
from urllib.error import HTTPError, URLError

import pytest

pytest.importorskip("jsonschema")

from adapters import runtime
from adapters.anthropic_runtime import AnthropicDecider
from adapters.openai_runtime import OpenAIChatDecider
from adapters.http_diagnostics import MAX_ERROR_BYTES, PROVIDER_CODES
from adapters.runtime import ModelConfig, ModelError, post_json
from agent_chassis import Outcome
from agent_chassis.contracts import RunContext, Task
from agent_chassis.evidence import content_id
from agent_chassis.orchestration import ToolBox
from tools.run_roadmap_showcase import assemble
from tools.verify_roadmap_evidence import verify_documents


CANARY = "private-canary-do-not-export"


def failure(status=400, body=None, headers=None, stream=None):
    message = Message()
    for key, value in (headers or {}).items():
        message[key] = value
    stream = stream if stream is not None else BytesIO(body if body is not None else b"{}")
    return HTTPError("https://example.invalid/" + CANARY, status, CANARY, message, stream), stream


def intercept(monkeypatch, error):
    calls = []

    class Opener:
        def open(self, request, *, timeout):
            calls.append(request)
            raise error

    monkeypatch.setattr(runtime, "build_opener", lambda *args: Opener())
    return calls


@pytest.mark.parametrize("status,category", [(400, "invalid_request"), (401, "authentication"),
    (403, "permission"), (404, "not_found"), (408, "request_timeout"), (413, "request_too_large"),
    (422, "invalid_request"), (429, "rate_limit"), (503, "server_error"), (418, "http_error")])
def test_status_categories_are_safe_and_request_is_not_retried(monkeypatch, status, category):
    error, stream = failure(status, body=CANARY.encode())
    calls = intercept(monkeypatch, error)
    with pytest.raises(ModelError) as exc:
        post_json("https://example.invalid", {"Authorization": CANARY}, {"prompt": CANARY}, 1)
    assert exc.value.http_error == {"status": status, "category": category}
    assert f"HTTP status {status}" in str(exc.value)
    assert CANARY not in str(exc.value) + json.dumps(exc.value.http_error)
    assert stream.closed and len(calls) == 1


def test_known_provider_codes_and_safe_header_metadata(monkeypatch):
    error, _ = failure(429, body=json.dumps({"error": {"code": "insufficient_quota",
        "type": "rate_limit_error", "message": CANARY, "param": CANARY}}).encode(),
        headers={"X-Request-ID": CANARY, "Retry-After": "17", "Authorization": CANARY})
    intercept(monkeypatch, error)
    with pytest.raises(ModelError) as exc:
        post_json("https://example.invalid", {}, {}, 1)
    assert exc.value.http_error == {"status": 429, "category": "rate_limit",
        "provider_code": "insufficient_quota", "provider_type": "rate_limit_error",
        "request_id_sha256": hashlib.sha256(CANARY.encode()).hexdigest(), "retry_after_seconds": 17}
    assert "insufficient_quota" in str(exc.value)
    assert CANARY not in str(exc.value) + json.dumps(exc.value.http_error)


@pytest.mark.parametrize("body", [b"", b"<html>private-canary-do-not-export</html>", b"\xff",
    b"[1]", b'{"error": "private-canary-do-not-export"}',
    json.dumps({"error": {"code": CANARY, "type": {"private": CANARY}, "message": CANARY}}).encode(),
    b"[" * 2000 + b"]" * 2000])
def test_untrusted_or_malformed_body_keeps_only_status(monkeypatch, body):
    error, _ = failure(body=body)
    intercept(monkeypatch, error)
    with pytest.raises(ModelError) as exc:
        post_json("https://example.invalid", {}, {}, 1)
    assert exc.value.http_error == {"status": 400, "category": "invalid_request"}


def test_error_body_read_is_bounded_and_truncated_json_is_not_parsed(monkeypatch):
    class TrackingStream(BytesIO):
        sizes = []

        def read(self, size=-1):
            self.sizes.append(size)
            return super().read(size)

    body = json.dumps({"error": {"code": "invalid_api_key"}, "padding": "x" * MAX_ERROR_BYTES}).encode()
    stream = TrackingStream(body)
    error, _ = failure(stream=stream)
    intercept(monkeypatch, error)
    with pytest.raises(ModelError) as exc:
        post_json("https://example.invalid", {}, {}, 1)
    assert "provider_code" not in exc.value.http_error
    assert stream.sizes == [MAX_ERROR_BYTES + 1] and stream.closed


@pytest.mark.parametrize("broken", [TimeoutError(CANARY), IncompleteRead(CANARY.encode())])
def test_error_body_read_failure_preserves_original_status(monkeypatch, broken):
    class BrokenStream(BytesIO):
        def read(self, size=-1):
            raise broken

    error, stream = failure(503, stream=BrokenStream())
    intercept(monkeypatch, error)
    with pytest.raises(ModelError, match="HTTP status 503") as exc:
        post_json("https://example.invalid", {}, {}, 1)
    assert stream.closed and CANARY not in str(exc.value)


@pytest.mark.parametrize("retry_after", ["-1", CANARY, "Wed, 09 Sep 2026 12:00:00 GMT", "9" * 100])
def test_arbitrary_header_text_and_oversize_ids_are_not_exported(monkeypatch, retry_after):
    error, _ = failure(headers={"Retry-After": retry_after, "Request-ID": CANARY * 20})
    intercept(monkeypatch, error)
    with pytest.raises(ModelError) as exc:
        post_json("https://example.invalid", {}, {}, 1)
    assert exc.value.http_error == {"status": 400, "category": "invalid_request"}


@pytest.mark.parametrize("adapter", [OpenAIChatDecider, AnthropicDecider])
def test_both_adapters_export_failure_without_tools_or_fake_usage(monkeypatch, adapter):
    monkeypatch.setenv("TEST_HTTP_KEY", CANARY)
    error, _ = failure(400, body=json.dumps({"error": {"type": "invalid_request_error", "message": CANARY}}).encode())
    calls = intercept(monkeypatch, error)
    config = ModelConfig("test", api_key_env="TEST_HTTP_KEY")
    # Inject the real post_json through a test transport, so evidence is labelled honestly.
    decider = adapter(config, tool_names=["submit_source"], transport=lambda *a: post_json(*a))
    chassis, _, observer, manifest = assemble("python_quality", config=config, decider=decider, code_ref="test-ref")
    try:
        result = chassis.run_once()
        evidence = json.loads(json.dumps(observer.snapshot()))
        run = evidence["runs"][0]
        record = run["model_calls"][0]
        assert result.outcome is Outcome.FAILED and len(calls) == 1 and run["tool_calls"] == []
        assert record["http_error"]["provider_type"] == "invalid_request_error"
        assert record["ok"] is False and record["error_type"] == "ModelError"
        assert record["mode"] == "test-transport"
        assert record["input_tokens"] is None and record["output_tokens"] is None
        assert run["usage"]["complete"] is False and CANARY not in json.dumps(evidence)
        assert verify_documents(manifest, evidence) == 1
        record["ok"] = True
        run["content_id"] = content_id({k: v for k, v in run.items() if k != "content_id"})
        with pytest.raises(ValueError, match="HTTP failure"):
            verify_documents(manifest, evidence)
    finally:
        chassis.close()


def test_http_failure_consumes_model_budget_and_never_retries(monkeypatch):
    monkeypatch.setenv("TEST_HTTP_KEY", CANARY)
    error, _ = failure(429)
    calls = intercept(monkeypatch, error)
    config = ModelConfig("test", api_key_env="TEST_HTTP_KEY", max_calls=1)
    decider = OpenAIChatDecider(config, tool_names=["noop"], transport=lambda *a: post_json(*a))
    box = ToolBox().add("noop", lambda: None, input_schema={"type": "object", "properties": {}})
    ctx = RunContext()
    for expected in ("HTTP status 429", "budget exhausted"):
        with pytest.raises(ModelError, match=expected):
            decider(Task("t", "test"), ctx, box)
    assert len(calls) == len(ctx.model_calls) == 1


def test_transport_failure_and_redirect_policy_remain_sanitized(monkeypatch):
    intercept(monkeypatch, URLError(CANARY))
    with pytest.raises(ModelError) as exc:
        post_json("https://example.invalid", {}, {}, 1)
    assert exc.value.http_error is None and CANARY not in str(exc.value)
    with pytest.raises(ModelError, match="redirects are not followed"):
        runtime._NoRedirect().redirect_request(None, None, 302, CANARY, {}, "https://example.invalid/" + CANARY)


def test_schema_allowlist_matches_producer_and_rejects_response_prose():
    from jsonschema import Draft202012Validator
    schema = json.loads((Path(__file__).resolve().parents[1] / "schemas/evidence-v1.schema.json").read_text())
    assert set(schema["$defs"]["provider_error_code"]["enum"]) == PROVIDER_CODES
    validator = Draft202012Validator({"$ref": "#/$defs/http_error", "$defs": schema["$defs"]})
    for extra in ({"message": CANARY}, {"provider_code": CANARY}):
        assert not validator.is_valid({"status": 400, "category": "invalid_request", **extra})
