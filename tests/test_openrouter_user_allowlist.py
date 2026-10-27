"""No API key required: owner screenshot allowlist regression tests."""
import sys
import unittest
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from openrouter_user_allowlist import filter_approved_models

class ScreenshotModelTests(unittest.TestCase):
    def test_accepts_only_named_free_zero_cost(self):
        data={"data":[
            {"id":"google/gemma-4-31b:free","name":"Google: Gemma 4 31B (free)","pricing":{"prompt":"0","completion":"0"}},
            {"id":"nvidia/nemotron-3.5-super-vl:free","name":"NVIDIA: Nemotron 3.5 Super VL (free)","pricing":{"prompt":"0","completion":"0"}},
            {"id":"nvidia/nemotron-3.5-super-vl","name":"NVIDIA: Nemotron 3.5 Super VL","pricing":{"prompt":"0","completion":"0"}},
            {"id":"openai/gpt-4:free","name":"Google: Gemma 4 31B (free)","pricing":{"prompt":"0","completion":"0"}},
            {"id":"cohere/north-mini-code:free","name":"Cohere: North Mini Code (free)","pricing":{"prompt":"0.001","completion":"0"}},
            {"id":"nvidia/other:free","name":"NVIDIA: Other (free)","pricing":{"prompt":"0","completion":"0"}}
        ]}
        self.assertEqual(filter_approved_models(data),[
            "google/gemma-4-31b:free","nvidia/nemotron-3.5-super-vl:free"
        ])
    def test_unknown_price_rejected(self):
        self.assertEqual(filter_approved_models({"data":[
            {"id":"recursive/flint:free","name":"Recursive: Flint (free)","pricing":{"prompt":"0"}}
        ]}),[])
if __name__=="__main__": unittest.main()
