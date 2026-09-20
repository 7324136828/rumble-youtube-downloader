"""Safe, actionable Connector transport and completion failure diagnostics."""
import json
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

import httpx

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app import config
from app.services import connector_client as connector


class ConnectorErrorsTest(unittest.TestCase):
    def setUp(self):
        patcher = patch.object(config, "RECOMMENDATION_CONNECTOR_URL", "http://connector.test")
        patcher.start()
        self.addCleanup(patcher.stop)

    def transport(self, handler):
        client_type = httpx.Client
        return patch.object(connector.httpx, "Client", side_effect=lambda **kwargs:
                            client_type(transport=httpx.MockTransport(handler), **kwargs))

    def test_transport_errors_have_distinct_safe_diagnostics(self):
        cases = (
            (httpx.ReadTimeout, "timeout", "timed out"),
            (httpx.ConnectTimeout, "timeout", "timed out"),
            (httpx.ConnectError, "connection_error", "running"),
            (httpx.RemoteProtocolError, "transport_error", "connection"),
        )
        for error_type, code, phrase in cases:
            def handler(request):
                raise error_type("private-path secret-token", request=request)
            with self.subTest(error=error_type.__name__), self.transport(handler):
                with self.assertRaises(connector.ConnectorError) as caught:
                    connector.request("GET", "/v1/models")
                self.assertEqual(caught.exception.code, code)
                self.assertIn(phrase, str(caught.exception))
                self.assertNotIn("private-path", str(caught.exception))
                self.assertNotIn("secret-token", str(caught.exception))

    def test_known_upstream_codes_are_classified_without_exposing_messages(self):
        cases = (
            ("upstream_error", "provider"),
            ("empty_completion", "no answer"),
            ("unsupported_option", "tool support"),
            ("invalid_request_error", "rejected"),
            ("model_not_found", "Refresh"),
        )
        for code, phrase in cases:
            with self.subTest(code=code), self.transport(lambda request: httpx.Response(502, json={
                    "error": {"code": code, "message": "secret-token private prompt"}})):
                with self.assertRaises(connector.ConnectorError) as caught:
                    connector.request("POST", "/v1/chat/completions", {})
                self.assertEqual(caught.exception.code, code)
                self.assertEqual(caught.exception.status_code, 502)
                self.assertIn(phrase, str(caught.exception))
                self.assertNotIn("secret-token", str(caught.exception))

    def test_http_statuses_have_actionable_messages_for_unknown_bodies(self):
        cases = ((401, "access_denied"), (403, "access_denied"), (429, "rate_limited"),
                 (404, "model_not_found"), (400, "invalid_request_error"),
                 (422, "invalid_request_error"), (408, "timeout"), (504, "timeout"),
                 (503, "http_error"), (302, "redirect"))
        for status, code in cases:
            with self.subTest(status=status), self.transport(lambda request: httpx.Response(
                    status, text="private-path secret-token")):
                with self.assertRaises(connector.ConnectorError) as caught:
                    connector.request("GET", "/v1/models")
                self.assertEqual(caught.exception.code, code)
                self.assertEqual(caught.exception.status_code, status)
                self.assertNotIn("private-path", str(caught.exception))

    def test_unknown_or_malformed_error_codes_are_not_reflected(self):
        for code in ("secret-token", {"secret-token": True}, ["private-path"]):
            with self.subTest(code=code), self.transport(lambda request: httpx.Response(502, json={
                    "error": {"code": code, "message": "private prompt"}})):
                with self.assertRaises(connector.ConnectorError) as caught:
                    connector.request("GET", "/v1/models")
                self.assertEqual(caught.exception.code, "http_error")
                self.assertNotIn("secret-token", str(caught.exception))

    def test_error_body_size_is_bounded(self):
        class ErrorStream(httpx.SyncByteStream):
            def __iter__(self):
                yield b"x" * (connector.MAX_ERROR_BYTES + 1)
                raise AssertionError("An oversized upstream error must not be consumed")
        with self.transport(lambda request: httpx.Response(429, stream=ErrorStream())):
            with self.assertRaises(connector.ConnectorError) as caught:
                connector.request("GET", "/v1/models")
        self.assertEqual(caught.exception.code, "rate_limited")

    def test_unreadable_success_response_does_not_claim_connection_failure(self):
        for content in (b"private prompt", b"[]"):
            with self.subTest(content=content), self.transport(lambda request: httpx.Response(200, content=content)):
                with self.assertRaises(connector.ConnectorError) as caught:
                    connector.request("GET", "/v1/models")
                self.assertEqual(caught.exception.code, "invalid_response")
                self.assertIn("unreadable", str(caught.exception))
                self.assertNotIn("private prompt", str(caught.exception))

    def test_length_finish_reason_is_a_typed_truncation_error(self):
        for content in (None, '{"playlist":[', '{"playlist":[]}'):
            with self.subTest(content=content), self.transport(lambda request: httpx.Response(200, json={
                    "choices": [{"finish_reason": "length", "message": {"role": "assistant", "content": content}}]})):
                with self.assertRaises(connector.CompletionTruncatedError) as caught:
                    connector.complete("model", [{"role": "user", "content": "Rank videos"}])
                self.assertEqual(caught.exception.code, "completion_truncated")
                self.assertIn("token limit", str(caught.exception))

    def test_normal_completion_preserves_native_replay_and_token_budget(self):
        message = {"role": "assistant", "content": None,
                   "provider_specific_fields": {"signature": "native-replay"},
                   "tool_calls": [{"id": "call-1", "type": "function", "function": {
                       "name": "search_by_url", "arguments": '{"url":"https://youtu.be/abcdefghijk"}'}}]}
        sent = []
        def handler(request):
            sent.append(json.loads(request.content))
            return httpx.Response(200, json={"choices": [{"finish_reason": "tool_calls", "message": message}]})
        with self.transport(handler):
            result = connector.complete("model", [{"role": "user", "content": "Find video"}])
        self.assertEqual(result, message)
        self.assertEqual(sent[0]["max_tokens"], 1800)
        self.assertNotIn("finish_reason", result)

    def test_explicit_completion_budget_is_forwarded(self):
        sent = []
        def handler(request):
            sent.append(json.loads(request.content))
            return httpx.Response(200, json={"choices": [{"finish_reason": "stop", "message": {
                "role": "assistant", "content": '{"playlist":[]}'}}]})
        with self.transport(handler):
            connector.complete("model", [{"role": "user", "content": "Rank videos"}], max_tokens=4096)
        self.assertEqual(sent[0]["max_tokens"], 4096)


if __name__ == "__main__":
    unittest.main()
