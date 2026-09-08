"""Bounded, allowlisted HTTP failure metadata. Never export response prose."""
import hashlib
from http.client import HTTPException
import json
import re


MAX_ERROR_BYTES = 16_384
PROVIDER_CODES = frozenset({
    "invalid_request_error", "authentication_error", "permission_error",
    "rate_limit_error", "insufficient_quota", "model_not_found",
    "context_length_exceeded", "unsupported_parameter", "invalid_api_key",
    "overloaded_error", "api_error", "server_error", "not_found_error",
    "request_too_large",
})


def http_diagnostic(error):
    """Best-effort inspection; a broken error stream must not hide HTTP status.

    The caller owns and closes the HTTPError. Only exact known code/type symbols
    are copied; arbitrary messages, parameter names and headers are omitted.
    """
    status = error.code
    category = {400: "invalid_request", 401: "authentication", 403: "permission",
                404: "not_found", 408: "request_timeout", 413: "request_too_large",
                422: "invalid_request", 429: "rate_limit"}.get(status,
                "server_error" if 500 <= status <= 599 else "http_error")
    result = {"status": status, "category": category}
    headers = error.headers or {}
    request_id = headers.get("x-request-id") or headers.get("request-id")
    if isinstance(request_id, str) and 0 < len(request_id) <= 256:
        result["request_id_sha256"] = hashlib.sha256(request_id.encode("utf-8")).hexdigest()
    retry_after = headers.get("retry-after")
    if isinstance(retry_after, str) and re.fullmatch(r"[0-9]{1,9}", retry_after):
        result["retry_after_seconds"] = int(retry_after)
    try:
        data = error.read(MAX_ERROR_BYTES + 1)
        if len(data) > MAX_ERROR_BYTES:
            return result
        payload = json.loads(data)
        detail = payload.get("error") if isinstance(payload, dict) else None
        if isinstance(detail, dict):
            for field in ("code", "type"):
                value = detail.get(field)
                if isinstance(value, str) and value in PROVIDER_CODES:
                    result["provider_" + field] = value
    except (OSError, HTTPException, ValueError, RecursionError):
        pass  # Retain status/header metadata for malformed, HTML or unreadable bodies.
    return result
