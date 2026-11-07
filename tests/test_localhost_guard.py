"""Tokenless local mode must resist DNS rebinding and cross-site form posts."""
import os
import unittest
from unittest.mock import patch

from fastapi.testclient import TestClient

import app


class TokenlessLocalhostGuardTests(unittest.TestCase):
    def setUp(self):
        self.env = patch.dict(os.environ, {"LHM_ADMIN_TOKEN": ""})
        self.env.start()
        self.addCleanup(self.env.stop)
        self.client = TestClient(app.app)

    def test_rebound_domain_cannot_read_local_data(self):
        for host in ("evil.example", "evil.example:7770", "127.0.0.1.evil.example",
                     "localhost.evil.example:7770", ""):
            with self.subTest(host=host):
                r = self.client.get("/workspaces", headers={"host": host})
                self.assertEqual(r.status_code, 403, r.text)
                self.assertNotIn("demo", r.text)

    def test_loopback_hosts_still_work(self):
        for host in ("127.0.0.1:7770", "localhost:7770", "[::1]:7770", "LOCALHOST", "testserver"):
            with self.subTest(host=host):
                self.assertEqual(self.client.get("/workspaces", headers={"host": host}).status_code, 200)

    def test_cross_site_writes_are_blocked_even_without_body(self):
        for headers in ({"sec-fetch-site": "cross-site"},
                        {"origin": "https://evil.example"},
                        {"origin": "http://127.0.0.1:9999", "host": "127.0.0.1:7770"}):
            with self.subTest(headers=headers):
                r = self.client.post("/tasks/00000000-0000-0000-0000-000000000000/approve", headers=headers)
                self.assertEqual(r.status_code, 403, r.text)

    def test_same_origin_local_write_reaches_the_route(self):
        r = self.client.post("/tasks/00000000-0000-0000-0000-000000000000/approve",
                             headers={"host": "127.0.0.1:7770", "origin": "http://127.0.0.1:7770",
                                      "sec-fetch-site": "same-origin"})
        self.assertEqual(r.status_code, 404, r.text)


if __name__ == "__main__":
    unittest.main()
