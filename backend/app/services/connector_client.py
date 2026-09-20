"""Small, bounded client for The Connector's saved configuration models."""
import json
import re
import time
import uuid

import httpx

from .. import config

MAX_RESPONSE_BYTES = 2 * 1024 * 1024
MAX_ERROR_BYTES = 16 * 1024

_ERROR_MESSAGES = {
    "model_not_found": "The selected Connector configuration is unavailable. Refresh the model list.",
    "unsupported_option": "The selected Connector configuration does not support this request. Check its model routes and tool support.",
    "invalid_request_error": "The Connector rejected this configuration or request. Check its routing settings.",
    "upstream_error": "The Connector could not reach a working model provider. Check its provider credentials and routing settings.",
    "empty_completion": "The selected model returned no answer or tool calls. Try again or choose another configuration.",
}


class ConnectorError(Exception):
    """A sanitized error suitable for displaying in the application."""

    def __init__(self, message: str, *, code: str | None = None, status_code: int | None = None):
        super().__init__(message)
        self.code = code
        self.status_code = status_code


class CompletionTruncatedError(ConnectorError):
    """The provider reached the output limit before completing its response."""


def connector_url() -> str:
    return config.RECOMMENDATION_CONNECTOR_URL.rstrip("/")


def _http_error(response: httpx.Response, deadline: float) -> ConnectorError:
    # Only known machine-readable codes are trusted. Provider messages can contain
    # credentials, filesystem paths, or private prompt data, so never display them.
    data = bytearray()
    for chunk in response.iter_bytes():
        if time.monotonic() > deadline:
            raise ConnectorError("The Connector request timed out. Try again or choose a faster configuration.", code="timeout")
        if len(data) + len(chunk) > MAX_ERROR_BYTES:
            break
        data.extend(chunk)
    try:
        payload = json.loads(data)
        error = payload.get("error") if isinstance(payload, dict) else None
        code = error.get("code") if isinstance(error, dict) else None
    except (ValueError, TypeError):
        code = None
    status = response.status_code
    if isinstance(code, str) and code in _ERROR_MESSAGES:
        return ConnectorError(_ERROR_MESSAGES[code], code=code, status_code=status)
    if status in (401, 403):
        message, code = "The Connector denied this request. Check its authentication and provider credentials.", "access_denied"
    elif status == 429:
        message, code = "The Connector or model provider is rate limited. Wait a moment, then try again.", "rate_limited"
    elif status == 404:
        message, code = _ERROR_MESSAGES["model_not_found"], "model_not_found"
    elif status in (400, 422):
        message, code = _ERROR_MESSAGES["invalid_request_error"], "invalid_request_error"
    elif status in (408, 504):
        message, code = "The Connector or model provider timed out. Try again or choose a faster configuration.", "timeout"
    elif response.is_redirect:
        message, code = "The Connector redirected this request. Check the configured Connector URL; redirects are not followed.", "redirect"
    else:
        message, code = "The Connector could not complete the request. Check its connection and provider settings.", "http_error"
    return ConnectorError(message, code=code, status_code=status)


def request(method: str, path: str, body=None, timeout: float = 45) -> dict:
    deadline = time.monotonic() + timeout
    try:
        # No redirects: uploads and watch context stay at the configured server.
        with httpx.Client(base_url=connector_url(), timeout=httpx.Timeout(timeout, connect=5),
                          follow_redirects=False) as client:
            with client.stream(method, path, json=body) as response:
                if response.status_code >= 400 or response.is_redirect:
                    raise _http_error(response, deadline)
                data = bytearray()
                for chunk in response.iter_bytes():
                    if time.monotonic() > deadline:
                        raise ConnectorError("The Connector request timed out. Try again or choose a faster configuration.", code="timeout")
                    if len(data) + len(chunk) > MAX_RESPONSE_BYTES:
                        raise ConnectorError("The Connector response exceeded the size limit.")
                    data.extend(chunk)
                payload = json.loads(data)
                if not isinstance(payload, dict):
                    raise ValueError("Expected an object")
                return payload
    except ConnectorError:
        raise
    except httpx.TimeoutException as exc:
        raise ConnectorError("The Connector request timed out. Try again or choose a faster configuration.", code="timeout") from exc
    except httpx.ConnectError as exc:
        raise ConnectorError("Could not connect to The Connector. Check that it is running at the configured URL.", code="connection_error") from exc
    except httpx.HTTPError as exc:
        raise ConnectorError("The connection to The Connector failed while processing this request. Please try again.", code="transport_error") from exc
    except (ValueError, TypeError) as exc:
        raise ConnectorError("The Connector returned an unreadable response. Check its server logs or choose another configuration.", code="invalid_response") from exc


def discover_models() -> dict:
    payload = request("GET", "/v1/models", timeout=15)
    entries = payload.get("data")
    if not isinstance(entries, list):
        raise ConnectorError("The Connector returned an unreadable model list.")
    models, seen = [], set()
    for entry in entries[:500]:
        if not isinstance(entry, dict):
            continue
        model_id = entry.get("id")
        if not isinstance(model_id, str) or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,99}", model_id) or model_id in seen:
            continue
        seen.add(model_id)
        models.append({"id": model_id, "name": str(entry.get("name") or model_id)[:200],
                       "description": str(entry.get("description") or "")[:500]})
    return {"models": models, "connector_url": connector_url()}


def import_config(name: str, raw_config: dict) -> dict:
    normalized = request("POST", "/api/config/validate", raw_config, timeout=15)
    if not isinstance(normalized.get("sequences"), list):
        raise ConnectorError("The Connector returned an unreadable validated configuration.")
    model_id = "recommendations-" + uuid.uuid4().hex
    saved = request("POST", "/api/configs", {"name": name, "model_id": model_id,
                    "active": True, "config": normalized}, timeout=15)
    if saved.get("model_id") != model_id:
        raise ConnectorError("The Connector did not confirm the saved configuration.")
    return {"model_id": model_id, "name": name}


def complete(model_id: str, messages: list[dict], tools: list[dict] | None = None,
             timeout: float = 45, *, max_tokens: int = 1800) -> dict:
    # Route/effort belongs to the selected saved config. Portable native routes
    # need neither forced tool choice nor response_format/temperature overrides.
    body = {"model": model_id, "messages": messages, "max_tokens": max_tokens}
    if tools:
        body.update(tools=tools, tool_choice="auto")
    payload = request("POST", "/v1/chat/completions", body, timeout=timeout)
    try:
        choice = payload["choices"][0]
        if not isinstance(choice, dict):
            raise ValueError("Missing completion choice")
        if choice.get("finish_reason") == "length":
            raise CompletionTruncatedError(
                "The selected model reached the response token limit before finishing. Try a configuration with lower reasoning effort or a different model.",
                code="completion_truncated",
            )
        message = choice["message"]
        if not isinstance(message, dict) or message.get("role") != "assistant":
            raise ValueError("Missing assistant message")
        # Replay the COMPLETE message, including native provider metadata.
        return message
    except (KeyError, IndexError, TypeError, ValueError) as exc:
        raise ConnectorError("The Connector returned an unreadable assistant message.") from exc
