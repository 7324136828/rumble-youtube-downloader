"""Offline registration, system-session delivery, and recovery contracts."""
import copy
import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import httpx

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app import config
from app.services import connector_activity as activity, connector_bridge as bridge
from app.services import connector_client, connector_tools, db


class ConnectorBridgeTest(unittest.TestCase):
    def setUp(self):
        self.folder = Path(self.enterContext(tempfile.TemporaryDirectory()))
        self.enterContext(patch.object(config, "JOBS_DB_PATH", self.folder / "jobs.db"))
        self.enterContext(patch.object(config, "RECOMMENDATION_CONNECTOR_URL", "http://connector.example:8301"))
        for key, value in (("_REGISTRY_KEY", None), ("_SESSION_KEY", None), ("_LAST_REGISTRY_CHECK", 0.0)):
            self.enterContext(patch.object(bridge, key, value))
        self.enterContext(patch.object(bridge, "wake"))
        db.init_db()
        activity.init_db()
        self.calls = []
        self.registry = []
        self.session_count = 0
        self.session_details = {}
        self.chat_replies = []
        self.chat_handler = None
        self.request = self.enterContext(patch.object(connector_client, "request", side_effect=self.remote))

    def remote(self, method, path, body=None, **kwargs):
        self.calls.append((method, path, copy.deepcopy(body), kwargs))
        if (method, path) == ("POST", "/api/agent/register-tool"):
            self.registry = [item for item in self.registry if item["name"] != body["name"]]
            self.registry.append(copy.deepcopy(body))
            return {"registered": body["name"]}
        if (method, path) == ("GET", "/api/agent/tools"):
            self.assertTrue(kwargs.get("expect_list"))
            return [{key: item[key] for key in ("name", "description", "parameters")} for item in self.registry]
        if (method, path) == ("POST", "/api/sessions"):
            self.session_count += 1
            session_id = f"system-{self.session_count}"
            self.session_details[session_id] = {"user_session": False, "status": "active"}
            return {"session_id": session_id, "user_session": False}
        if method == "GET" and path.startswith("/api/sessions/"):
            detail = self.session_details[path.rsplit("/", 1)[-1]]
            if isinstance(detail, Exception):
                raise detail
            return {"session": copy.deepcopy(detail)}
        if (method, path) == ("POST", "/api/chat"):
            if self.chat_handler:
                return self.chat_handler(body)
            if self.chat_replies:
                reply = self.chat_replies.pop(0)
                if isinstance(reply, Exception):
                    raise reply
                return reply
            return {"session_id": body["session_id"], "content": "message received"}
        self.fail(f"Unexpected remote request: {method} {path}")

    def enable(self):
        return bridge.configure({"enabled": True, "base_url": "http://downloader.example:8000"})

    def messages(self):
        return [body for method, path, body, _ in self.calls if (method, path) == ("POST", "/api/chat")]

    def test_disabled_bridge_does_not_contact_connector(self):
        result = bridge.sync_once(force=True)
        self.assertFalse(result["enabled"])
        self.request.assert_not_called()

    def test_registers_raw_tool_webhooks_and_creates_mock_system_session(self):
        self.enable()
        token = activity.get_state()["token"]
        state = bridge.sync_once(force=True)
        definitions = connector_tools.tool_definitions()
        self.assertEqual(len(self.registry), len(definitions))
        for registered, definition in zip(self.registry, definitions):
            self.assertEqual(registered, {**definition, "endpoint":
                f"http://downloader.example:8000/api/connector/hooks/{token}/{definition['name']}"})
        body = next(body for method, path, body, _ in self.calls if (method, path) == ("POST", "/api/sessions"))
        self.assertIs(body["user_session"], False)
        self.assertEqual(body["config"]["sequences"], [{"provider": "mock", "model": "mock-assistant", "retries": 0}])
        self.assertTrue(body["config"]["past_memory"])
        self.assertEqual(state["session_id"], "system-1")
        self.assertEqual(state["registered_tools"], [item["name"] for item in definitions])
        self.assertNotIn(token, json.dumps(state))
        self.assertNotIn("token", state)

    def test_outbox_retry_preserves_event_and_session_and_sanitizes_error(self):
        self.enable()
        bridge.sync_once()
        activity.record_search("bird photography", "all", 4, event_id="stable-event")
        self.chat_replies.append(connector_client.ConnectorError("credential: must-not-leak", status_code=503))
        with self.assertRaises(connector_client.ConnectorError):
            bridge.sync_once()
        pending = activity.pending_events()
        self.assertEqual(len(pending), 1)
        self.assertEqual(pending[0]["id"], "stable-event")
        self.assertEqual(pending[0]["attempts"], 1)
        self.assertEqual(activity.get_state()["session_id"], "system-1")
        self.assertNotIn("must-not-leak", json.dumps(bridge.status()))
        activity.init_db()
        result = bridge.sync_once()
        self.assertEqual(result["pending_events"], 0)
        self.assertIsNone(result["last_error"])
        self.assertEqual(self.session_count, 1)
        messages = self.messages()
        self.assertEqual(len(messages), 2)
        self.assertEqual(messages[0], messages[1])
        event = json.loads(messages[1]["message"].split("\n", 1)[1])
        self.assertEqual(event["event_id"], "stable-event")
        self.assertEqual(event["data"]["query"], "bird photography")
        stored = activity.list_events()["items"][0]
        self.assertIsNotNone(stored["delivered_at"])
        self.assertEqual(stored["attempts"], 2)

    def test_acknowledgement_must_match_session_and_exact_system_content(self):
        self.enable()
        bridge.sync_once()
        event_id = activity.record_search("birds", "all", 1)
        for reply in ({"session_id": "system-1", "content": "message received "},
                      {"session_id": "other-session", "content": "message received"},
                      {"session_id": "system-1", "content": "Generated answer"}):
            self.chat_replies.append(reply)
            with self.subTest(reply=reply), self.assertRaises(connector_client.ConnectorError):
                bridge.sync_once()
            self.assertEqual(activity.pending_events()[0]["id"], event_id)
        bridge.sync_once()
        self.assertEqual(activity.pending_events(), [])
        self.assertEqual(self.session_count, 1)

    def test_force_recreates_closed_or_wrong_kind_session_without_inference(self):
        self.enable()
        bridge.sync_once()
        self.session_details["system-1"] = {"user_session": False, "status": "closed"}
        self.assertEqual(bridge.sync_once(force=True)["session_id"], "system-2")
        self.session_details["system-2"] = {"user_session": True, "status": "active"}
        self.assertEqual(bridge.sync_once(force=True)["session_id"], "system-3")
        self.assertEqual(self.session_count, 3)
        self.assertFalse(any(path.startswith("/v1/") for _, path, _, _ in self.calls))

    def test_transient_session_verification_keeps_existing_session(self):
        self.enable()
        bridge.sync_once()
        self.session_details["system-1"] = connector_client.ConnectorError("temporary", status_code=503)
        with self.assertRaises(connector_client.ConnectorError):
            bridge.sync_once(force=True)
        self.assertEqual(self.session_count, 1)
        self.assertEqual(activity.get_state()["session_id"], "system-1")

    def test_periodic_registry_probe_restores_tools_after_connector_restart(self):
        self.enable()
        bridge.sync_once()
        registrations = len(self.registry)
        self.registry = []
        bridge._LAST_REGISTRY_CHECK -= bridge.REGISTRY_INTERVAL + 1
        bridge.sync_once()
        self.assertEqual(len(self.registry), registrations)
        self.assertEqual(sum(path == "/api/agent/register-tool" for _, path, _, _ in self.calls), 2 * registrations)
        self.assertEqual(self.session_count, 1)
        self.assertTrue(any(path == "/api/agent/tools" and kwargs.get("expect_list")
                            for _, path, _, kwargs in self.calls))
        bridge._LAST_REGISTRY_CHECK -= bridge.REGISTRY_INTERVAL + 1
        bridge.sync_once()
        self.assertEqual(sum(path == "/api/agent/register-tool" for _, path, _, _ in self.calls), 2 * registrations)

    def test_disable_rotates_token_and_stops_delivery_and_registration(self):
        self.enable()
        bridge.sync_once()
        activity.record_search("birds", "all", 2)
        old_token = activity.get_state()["token"]
        result = bridge.configure({"enabled": False})
        self.assertNotEqual(activity.get_state()["token"], old_token)
        self.assertEqual(result["registered_tools"], [])
        request_count = len(self.calls)
        bridge.sync_once(force=True)
        self.assertEqual(len(self.calls), request_count)
        self.assertEqual(len(activity.pending_events()), 1)

    def test_disable_during_delivery_stops_batch_and_preserves_retry_event(self):
        self.enable()
        bridge.sync_once()
        activity.record_search("birds", "all", 2, event_id="one")
        activity.record_search("rivers", "all", 2, event_id="two")

        def disable_before_ack(body):
            bridge.configure({"enabled": False})
            return {"session_id": body["session_id"], "content": "message received"}

        self.chat_handler = disable_before_ack
        result = bridge.sync_once()
        self.assertFalse(result["enabled"])
        self.assertEqual(len(self.messages()), 1)
        self.assertEqual(len(activity.pending_events()), 2)


class ConnectorRequestShapeTest(unittest.TestCase):
    def test_real_request_accepts_tool_registry_list_only_when_requested(self):
        client_class = httpx.Client
        transport = httpx.MockTransport(lambda request: httpx.Response(200, json=[{"name": "tool"}]))
        with patch.object(config, "RECOMMENDATION_CONNECTOR_URL", "http://connector.example:8301"), patch.object(
                connector_client.httpx, "Client", side_effect=lambda **kwargs: client_class(transport=transport, **kwargs)):
            result = connector_client.request("GET", "/api/agent/tools", expect_list=True)
            self.assertEqual(result, [{"name": "tool"}])
            with self.assertRaises(connector_client.ConnectorError) as caught:
                connector_client.request("GET", "/api/agent/tools")
            self.assertEqual(caught.exception.code, "invalid_response")


if __name__ == "__main__":
    unittest.main()
