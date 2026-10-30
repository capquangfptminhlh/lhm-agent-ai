"""Read-only Agent Office bridge: no real Office installation or paid AI required."""
from __future__ import annotations

import os
import unittest
from unittest.mock import patch

import httpx
from fastapi.testclient import TestClient

import app
import agent_office_bridge as bridge


GOOD = {
    "floor": {"id": "floor-1", "name": "LHM Repo", "repo": "owner/example", "branch": "main"},
    "workers": [
        {"id": "worker-1", "name": "Coder", "provider": "codex",
         "model": "a-model", "kind": "agent", "desk": "Desk 2",
         "status": "working", "task": "Fix tests", "activity": "git status",
         "pr": {"number": 12, "url": "https://github.com/owner/example/pull/12"},
         "hookToken": "MUST_NOT_LEAK", "secret": "MUST_NOT_LEAK"},
        {"id": "worker-2", "name": "Reviewer", "kind": "agent",
         "status": "needs_input"},
    ],
}
CONFIG = {
    "LHM_AGENT_OFFICE_HOOK_URL": "http://127.0.0.1:5123",
    "LHM_AGENT_OFFICE_WORKER_ID": "worker-1",
    "LHM_AGENT_OFFICE_HOOK_TOKEN": "sensitive-only-in-process",
    "AGENT_OFFICE_HOOK_URL": "",
    "AGENT_OFFICE_WORKER_ID": "",
    "AGENT_OFFICE_HOOK_TOKEN": "",
    "LHM_ADMIN_TOKEN": "",
}


class FakeClient:
    def __init__(self, status=200, payload=None):
        self.status = status
        self.payload = payload if payload is not None else GOOD
        self.calls = []

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    async def get(self, url, **kwargs):
        self.calls.append((url, kwargs))
        return httpx.Response(self.status, json=self.payload,
                              request=httpx.Request("GET", url))


class AgentOfficeBridgeTests(unittest.TestCase):
    def setUp(self):
        self.client = TestClient(app.app)

    def test_no_credentials_does_not_attempt_network_and_never_claims_connected(self):
        with patch.dict(os.environ, {k: "" for k in CONFIG}):
            with patch.object(bridge.httpx, "AsyncClient", side_effect=AssertionError("network")):
                r = self.client.get("/api/agent-office/workers")
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.json()["state"], "not_configured")
        self.assertFalse(r.json()["external_execution_enabled"])
        self.assertEqual(r.json()["workers"], [])

    def test_rejects_ssrf_and_partial_configuration_without_io(self):
        malicious = [
            "http://localhost:5123", "http://example.com:5123",
            "https://127.0.0.1:5123", "http://127.0.0.1:123",
            "http://127.0.0.1:5123/evil", "http://127.0.0.1:5123/?x=1",
            "http://user:pass@127.0.0.1:5123", "http://127.0.0.1:5123#x",
            "http://127.0.0.1:5123@evil.com:5123",
        ]
        for address in malicious:
            with self.subTest(address=address), patch.dict(os.environ, {
                **CONFIG, "LHM_AGENT_OFFICE_HOOK_URL": address
            }):
                with patch.object(bridge.httpx, "AsyncClient", side_effect=AssertionError("network")):
                    r = self.client.get("/api/agent-office/workers")
                self.assertEqual(r.json()["state"], "invalid_configuration")
        with patch.dict(os.environ, {**CONFIG, "LHM_AGENT_OFFICE_HOOK_TOKEN": ""}):
            self.assertEqual(
                self.client.get("/api/agent-office/workers").json()["state"],
                "incomplete_configuration",
            )

    def test_reads_only_auth_scoped_floor_and_redacts_unknown_fields(self):
        fake = FakeClient()
        with patch.dict(os.environ, CONFIG):
            with patch.object(bridge.httpx, "AsyncClient", return_value=fake) as construct:
                r = self.client.get("/api/agent-office/workers")
        self.assertEqual(r.status_code, 200, r.text)
        body = r.json()
        self.assertEqual(body["state"], "connected")
        self.assertEqual(body["scope"], "one_authorized_floor")
        self.assertEqual(body["mode"], "read_only")
        self.assertEqual(len(body["workers"]), 2)
        self.assertEqual(body["workers"][0]["pull_request"], 12)
        self.assertEqual(body["workers"][1]["status"], "needs_input")
        self.assertNotIn("MUST_NOT_LEAK", r.text)
        self.assertNotIn(CONFIG["LHM_AGENT_OFFICE_HOOK_TOKEN"], r.text)
        self.assertFalse(body["external_execution_enabled"])
        self.assertEqual(fake.calls[0][0], "http://127.0.0.1:5123/office/workers")
        self.assertEqual(fake.calls[0][1]["params"], {"worker": "worker-1"})
        self.assertEqual(fake.calls[0][1]["headers"]["Authorization"],
                         "Bearer " + CONFIG["LHM_AGENT_OFFICE_HOOK_TOKEN"])
        self.assertEqual(construct.call_args.kwargs["follow_redirects"], False)
        self.assertEqual(construct.call_args.kwargs["trust_env"], False)

    def test_upstream_errors_and_unknown_shape_are_not_treated_as_idle(self):
        for status, payload, expected in [
            (401, {}, "authorization_failed"),
            (403, {}, "authorization_failed"),
            (302, {}, "unavailable"),
            (503, {}, "unavailable"),
            (200, {"workers": []}, "unavailable"),
        ]:
            with self.subTest(status=status, payload=payload), patch.dict(os.environ, CONFIG):
                with patch.object(bridge.httpx, "AsyncClient", return_value=FakeClient(status, payload)):
                    r = self.client.get("/api/agent-office/workers")
            self.assertEqual(r.json()["state"], expected)
            self.assertEqual(r.json()["workers"], [])

    def test_auth_and_ui_have_no_control_actions(self):
        with patch.dict(os.environ, {**CONFIG, "LHM_ADMIN_TOKEN": "owner-secret"}):
            self.assertEqual(self.client.get("/agent-office").status_code, 401)
            self.assertEqual(self.client.get("/api/agent-office/workers").status_code, 401)
            headers = {"Authorization": "Bearer owner-secret"}
            html = self.client.get("/agent-office", headers=headers)
            self.assertEqual(html.status_code, 200)
            self.assertIn("id=\"workers\"", html.text)
            self.assertIn("/api/agent-office/workers", html.text)
            self.assertNotIn("innerHTML", html.text)
            self.assertNotIn("eval(", html.text)
            self.assertEqual(
                self.client.post("/api/agent-office/workers", headers=headers).status_code, 405
            )


if __name__ == "__main__":
    unittest.main()
