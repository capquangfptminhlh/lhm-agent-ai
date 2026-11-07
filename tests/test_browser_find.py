"""Browser find is observation-only; real scrolling is tested in Chromium smoke."""
import unittest

from fastapi import HTTPException

import computer_agent as agent
import computer_executor as executor


class BrowserFindContractTests(unittest.TestCase):
    def test_safe_read_only_registry(self):
        self.assertIn("browser_find",agent.TOOLS)
        self.assertIn("browser_find",executor.ALLOWED_TOOLS)
        allowed=executor.ToolRequest.model_fields["tool"].annotation.__args__
        self.assertIn("browser_find",allowed)
        self.assertNotIn("browser_find",executor.MUTATIONS)
        self.assertFalse(agent._approval_required(
            "browser_find",{"mode":"supervised"}))
        self.assertFalse(agent._approval_required(
            "browser_find",{"mode":"trusted"}))

    def test_plain_text_only_with_length_limit(self):
        for needle in ("Privacy","domain","Bảo mật", "keyword 123"):
            self.assertEqual(executor._validate_find_text(needle),needle)
        for bad in (None, "", "   ", "a\nb", "\tword", "x\x00y",
                    "x"*121, 12, {"text":"Privacy"}):
            with self.subTest(value=repr(bad)),self.assertRaises(HTTPException) as exc:
                executor._validate_find_text(bad)
            self.assertEqual(exc.exception.status_code,422)

    def test_no_tool_can_submit_via_find_contract(self):
        # This tool never takes selectors or a button/keyboard argument.
        # The actual Runner _browser uses page.get_by_text and only calls
        # scroll_into_view_if_needed on the observed text.
        self.assertEqual(
            executor._validate_find_text("Enter"),"Enter")
        self.assertIn("without clicking",agent.TOOLS["browser_find"])


if __name__=="__main__":
    unittest.main()
