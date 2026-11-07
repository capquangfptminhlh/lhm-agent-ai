"""Regression: P4 test@example.com must not trip the Free privacy gate.

All tests run offline without keys or outbound API calls.
"""
from __future__ import annotations

import asyncio
import os
import unittest
from unittest.mock import patch

from fastapi import HTTPException

import openrouter_provider as provider


class ReservedTestContactTests(unittest.TestCase):
    def test_reserved_test_addresses_are_not_real_contacts(self):
        for value in (
            "Điền form bằng test@example.com",
            "a@EXAMPLE.NET",
            "qa@foo.example.org",
            "agent@shop.test",
            "dev@unit.invalid",
            "example@localhost.localhost",
            "robot@sample.example",
            "fake@foo.example.com",
        ):
            with self.subTest(value=value):
                self.assertFalse(provider.has_real_contact(value))

    def test_personal_emails_and_phone_numbers_remain_blocked(self):
        for value in (
            "khach.hang@gmail.com",
            "abc@business.vn",
            "test@example.com.vn",
            "a@reallyexample.com",
            "test@example.com và manager@business.vn",
            "test@example.com và 0912 345 678",
            "+84 912 345 678",
        ):
            with self.subTest(value=value):
                self.assertTrue(provider.has_real_contact(value))

    def test_reserved_email_reaches_transport_without_real_network(self):
        # A dummy email is permitted past the Free privacy check. We abort
        # deliberately at transport, before any network request is created.
        env={"OPENROUTER_API_KEY":"dummy-offline",
             "LHM_OPENROUTER_ALLOW_CONTACT_DATA":"0",
             "LHM_OPENROUTER_FREE_ONLY":"0",
             "LHM_OPENROUTER_FREE_AUTO_SELECT":"0"}
        with patch.dict(os.environ,env), \
             patch.object(provider,"role_prompt",return_value="Test role"), \
             patch.object(provider.httpx,"AsyncClient",
                          side_effect=RuntimeError("offline transport sentinel")) as client:
            with self.assertRaisesRegex(RuntimeError,"offline transport sentinel"):
                asyncio.run(provider.call_free_model(
                    "research",{"industry":"general","business_context":""},
                    "Nhập test@example.com vào form thử nghiệm","", "openrouter/free"))
            client.assert_called_once()

    def test_real_mailbox_rejected_before_network(self):
        env={"OPENROUTER_API_KEY":"dummy-offline",
             "LHM_OPENROUTER_ALLOW_CONTACT_DATA":"0",
             "LHM_OPENROUTER_FREE_ONLY":"0",
             "LHM_OPENROUTER_FREE_AUTO_SELECT":"0"}
        with patch.dict(os.environ,env), \
             patch.object(provider.httpx,"AsyncClient") as transport:
            with self.assertRaises(HTTPException) as err:
                asyncio.run(provider.call_free_model(
                    "research",{"industry":"general","business_context":""},
                    "Nhập manager@company.vn vào form","", "openrouter/free"))
            self.assertEqual(err.exception.status_code,422)
            transport.assert_not_called()


if __name__=="__main__":
    unittest.main()
