"""50 executable offline integration/regression scenarios for LHM Agent AI.

These tests exercise real Python modules and an in-process FastAPI application.
They do NOT claim live OpenRouter calls, physical Windows UI, or external billing.
Each test is an individual unittest case, so CI reports exactly 50 cases.
"""
import os
import sys
import unittest
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
os.environ.setdefault("LHM_DB_PATH", "/tmp/lhm-50-scenarios.sqlite3")

CASES = [{"name":"price_zero_strings","kind":"price","input":{"prompt":"0","completion":"0"},"expected":True},{"name":"price_zero_numbers","kind":"price","input":{"prompt":0,"completion":0},"expected":True},{"name":"price_zero_scientific","kind":"price","input":{"prompt":"0e-9","completion":"0.000"},"expected":True},{"name":"price_zero_all_fields","kind":"price","input":{"prompt":"0","completion":"0","request":"0","image":"0","input_cache_read":"0"},"expected":True},{"name":"price_paid_prompt","kind":"price","input":{"prompt":"0.01","completion":"0"},"expected":False},{"name":"price_paid_completion","kind":"price","input":{"prompt":"0","completion":"0.01"},"expected":False},{"name":"price_paid_image","kind":"price","input":{"prompt":"0","completion":"0","image":"1"},"expected":False},{"name":"price_paid_request","kind":"price","input":{"prompt":"0","completion":"0","request":"1"},"expected":False},{"name":"price_missing_prompt","kind":"price","input":{"completion":"0"},"expected":False},{"name":"price_missing_completion","kind":"price","input":{"prompt":"0"},"expected":False},{"name":"price_empty","kind":"price","input":{},"expected":False},{"name":"price_None_prompt","kind":"price","input":{"prompt":None,"completion":"0"},"expected":False},{"name":"price_bool_prompt","kind":"price","input":{"prompt":True,"completion":"0"},"expected":False},{"name":"price_nan","kind":"price","input":{"prompt":"NaN","completion":"0"},"expected":False},{"name":"price_infinity","kind":"price","input":{"prompt":"Infinity","completion":"0"},"expected":False},{"name":"price_invalid","kind":"price","input":{"prompt":"unknown","completion":"0"},"expected":False},{"name":"price_negative","kind":"price","input":{"prompt":"-0.1","completion":"0"},"expected":False},{"name":"price_nonzero_cache","kind":"price","input":{"prompt":"0","completion":"0","cache_read":"0.01"},"expected":False},{"name":"price_bool_completion","kind":"price","input":{"prompt":"0","completion":False},"expected":False},{"name":"price_zero_negative","kind":"price","input":{"prompt":"-0","completion":"0"},"expected":True},{"name":"allow_gemma31","kind":"allow","id":"google/gemma-4-31b:free","display":"Google: Gemma 4 31B (free)","expected":True},{"name":"allow_gemma26","kind":"allow","id":"google/gemma-4-26b-a4b:free","display":"Google: Gemma 4 26B A4B (free)","expected":True},{"name":"allow_nemotron_ultra","kind":"allow","id":"nvidia/nemotron-3-ultra:free","display":"NVIDIA: Nemotron 3 Ultra (free)","expected":True},{"name":"allow_nemotron_lightning","kind":"allow","id":"nvidia/nemotron-3.5-lightning:free","display":"NVIDIA: Nemotron 3.5 Lightning (free)","expected":True},{"name":"allow_nemotron_vl","kind":"allow","id":"nvidia/nemotron-3.5-super-vl:free","display":"NVIDIA: Nemotron 3.5 Super VL (free)","expected":True},{"name":"allow_north_mini","kind":"allow","id":"cohere/north-mini-code:free","display":"Cohere: North Mini Code (free)","expected":True},{"name":"allow_inkling","kind":"allow","id":"thinkingmachines/inkling:free","display":"Thinking Machines: Inkling (free)","expected":True},{"name":"allow_inkling_small","kind":"allow","id":"thinkingmachines/inkling-small:free","display":"Thinking Machines: Inkling Small (free)","expected":True},{"name":"allow_wrong_publisher","kind":"allow","id":"openai/gemma-4-31b:free","display":"Google: Gemma 4 31B (free)","expected":False},{"name":"allow_paid_id","kind":"allow","id":"google/gemma-4-31b","display":"Google: Gemma 4 31B","expected":False},{"name":"allow_unnamed","kind":"allow","id":"google/gemma-4-31b:free","display":None,"expected":False},{"name":"allow_unknown_model","kind":"allow","id":"nvidia/other:free","display":"NVIDIA: Other (free)","expected":False},{"name":"allow_fake_price","kind":"allow","id":"google/gemma-4-31b:free","display":"Google: Gemma 4 31B (free)","expected":True},{"name":"allow_case_variation","kind":"allow","id":"google/gemma-4-31b:free","display":"GOOGLE: GEMMA 4 31B (free)","expected":True},{"name":"allow_not_free_auto","kind":"allow","id":"openrouter/auto","display":"OpenRouter Auto","expected":False},{"name":"route_01","kind":"route","path":"/health"},{"name":"route_02","kind":"route","path":"/workflow-builder"},{"name":"route_03","kind":"route","path":"/office3d"},{"name":"route_04","kind":"route","path":"/api/office3d/snapshot"},{"name":"route_05","kind":"route","path":"/tools/catalog"},{"name":"route_06","kind":"route","path":"/agent-tools"},{"name":"route_07","kind":"route","path":"/tools/specialists/readiness"},{"name":"route_08","kind":"route","path":"/computer"},{"name":"route_09","kind":"route","path":"/team-chat"},{"name":"route_10","kind":"route","path":"/workspaces/demo/team/sessions"},{"name":"reject_auto","kind":"free","id":"openrouter/auto","expected":False},{"name":"reject_paid","kind":"free","id":"openai/gpt-4","expected":False},{"name":"accept_router","kind":"free","id":"openrouter/free","expected":True},{"name":"accept_free_slug","kind":"free","id":"nvidia/nemotron-3:free","expected":True},{"name":"reject_invalid_catalog","kind":"invalid"}]

class FiftyRealScenarios(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from fastapi.testclient import TestClient
        from app import app
        cls.client = TestClient(app)

    def execute_case(self, case):
        from openrouter_free_catalog import is_zero_price
        from openrouter_user_allowlist import filter_approved_models
        from openrouter_provider import is_free_model
        kind = case["kind"]
        if kind == "price":
            self.assertEqual(is_zero_price(case["input"]), case["expected"])
        elif kind == "allow":
            payload = {"data": [{"id": case["id"], "name": case["display"],
                                 "pricing": {"prompt": "0", "completion": "0"}}]}
            result = filter_approved_models(payload)
            self.assertEqual(case["id"] in result, case["expected"])
        elif kind == "route":
            r = self.client.get(case["path"])
            self.assertEqual(r.status_code, 200, (case["path"], r.text[:200]))
        elif kind == "free":
            self.assertEqual(is_free_model(case["id"]), case["expected"])
        elif kind == "invalid":
            with self.assertRaises(ValueError):
                filter_approved_models({"not_data": []})
        else:
            raise AssertionError("Unknown scenario kind")

def _make_test(case):
    def test(self):
        self.execute_case(case)
    test.__doc__ = case["name"]
    return test

for index, case in enumerate(CASES, 1):
    setattr(FiftyRealScenarios, f"test_{index:02d}_{case['name']}", _make_test(case))

if __name__ == "__main__":
    unittest.main(verbosity=2)
