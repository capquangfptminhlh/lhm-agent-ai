"""Owner-only native Playwright cleanup; no access to personal Chrome sessions."""
from __future__ import annotations

import os
import unittest
from unittest.mock import AsyncMock, Mock, patch

from fastapi.testclient import TestClient

import computer_executor as executor


class BrowserShutdownTests(unittest.TestCase):
    def setUp(self):
        self.runner_key=patch.dict(os.environ,{"LHM_COMPUTER_RUNNER_TOKEN":"owner-test-token"})
        self.runner_key.start()
        self.client=TestClient(executor.runner)
        self.session=Mock(close=AsyncMock())
        self.playwright=Mock(stop=AsyncMock())
        executor.BROWSER_SESSIONS.clear()
        executor.BROWSER_LOCKS.clear()
        executor.BROWSER_SESSIONS["ci/quality"]=self.session
        executor._PLAYWRIGHT=self.playwright

    def tearDown(self):
        executor.BROWSER_SESSIONS.clear()
        executor.BROWSER_LOCKS.clear()
        executor._PLAYWRIGHT=None
        self.runner_key.stop()

    def test_only_owner_can_close_and_close_is_idempotent(self):
        self.assertEqual(self.client.post("/v1/browser/close").status_code,401)
        self.session.close.assert_not_awaited()
        auth={"Authorization":"Bearer owner-test-token"}
        response=self.client.post("/v1/browser/close",headers=auth)
        self.assertEqual(response.status_code,200,response.text)
        self.assertEqual(response.json()["contexts_closed"],1)
        self.session.close.assert_awaited_once()
        self.playwright.stop.assert_awaited_once()
        self.assertFalse(executor.BROWSER_SESSIONS)
        self.assertIsNone(executor._PLAYWRIGHT)
        response=self.client.post("/v1/browser/close",headers=auth)
        self.assertEqual(response.json()["contexts_closed"],0)
        self.session.close.assert_awaited_once()

    def test_unresponsive_context_does_not_block_playwright_stop(self):
        self.session.close=AsyncMock(side_effect=OSError("Edge no longer running"))
        response=self.client.post("/v1/browser/close",
            headers={"Authorization":"Bearer owner-test-token"})
        self.assertEqual(response.status_code,200,response.text)
        self.assertEqual(response.json()["contexts_closed"],0)
        self.playwright.stop.assert_awaited_once()
        self.assertEqual(executor.BROWSER_SESSIONS,{})


if __name__=="__main__":
    unittest.main()
