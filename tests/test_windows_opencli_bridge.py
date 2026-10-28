"""Windows OpenCLI Browser Bridge policy and subprocess contract (no real accounts)."""
from __future__ import annotations

import asyncio
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from fastapi import HTTPException
import computer_agent
import computer_executor
import opencli_windows_bridge as bridge
from agent_runtime_pilot import PILOT_TOOLS


class OpenCLIPolicyTests(unittest.TestCase):
    def setUp(self):
        self.env=patch.dict(os.environ,{
            "LHM_COMPUTER_ALLOWED_HOSTS":"example.com,example.org",
            "LHM_OPENCLI_ALLOWED_HOSTS":"example.com",
            "LHM_OPENCLI_ENABLED":"1",
            "LHM_OPENCLI_PROFILE":"lhm-work",
            "LHM_DESKTOP_NATIVE":"1",
        })
        self.env.start()
        self.addCleanup(self.env.stop)

    def test_exact_hosts_fail_closed_redirects_and_urls(self):
        self.assertTrue(bridge.allowed_url("https://example.com/articles"))
        for url in ("http://example.com","https://evil.example.com/",
                    "https://example.com.evil.test/","https://example.org/",
                    "https://example.com:8443/","https://user:secret@example.com/",
                    "https://127.0.0.1/","file:///etc/passwd",
                    "https://example.com/a#secret"):
            self.assertFalse(bridge.allowed_url(url),url)
        with patch.dict(os.environ,{"LHM_OPENCLI_ALLOWED_HOSTS":""}):
            self.assertFalse(bridge.allowed_url("https://example.com"))

    def test_tool_roles_and_exact_args(self):
        self.assertIn("opencli_click",PILOT_TOOLS["content"])
        self.assertNotIn("opencli_click",PILOT_TOOLS["sales"])
        self.assertIn("opencli_state",PILOT_TOOLS["quality"])
        for role in ("quality","sales","research","finance","director"):
            with self.assertRaises(HTTPException):
                bridge.validate(role,"opencli_click",{"ref":1,"url":"https://example.com/form","label":"Publish"})
        for args in ({"ref":True},{"ref":"1"},{"ref":0},{"ref":3001},
                     {"ref":1,"url":"https://example.com"}):
            with self.assertRaises(HTTPException):
                bridge.validate("content","opencli_click",{**args,"url":"https://example.com/form","label":"Publish"})
        bridge.validate("content","opencli_open",{"url":"https://example.com"})
        bridge.validate("content","opencli_fill",{"ref":3,"text":"Approved caption","url":"https://example.com/form","label":"Caption"})

    def test_mutations_always_wait_for_owner_even_in_trusted_mode(self):
        self.assertTrue(computer_agent._approval_required("opencli_click",
                       {"mode":"trusted"}))
        self.assertTrue(computer_agent._approval_required("opencli_fill",
                       {"mode":"trusted"}))
        self.assertIn("opencli_click",computer_executor.MUTATIONS)

    def test_model_hides_opencli_when_disabled_and_legacy_roles(self):
        with patch.dict(os.environ,{"LHM_OPENCLI_ENABLED":"0"}):
            self.assertNotIn("opencli_open",computer_agent._system_prompt("content"))
        self.assertNotIn("opencli_open",computer_agent._system_prompt("finance"))
        self.assertIn("opencli_open",computer_agent._system_prompt("content"))

    def test_detects_global_node_prefix_without_appdata_install(self):
        with tempfile.TemporaryDirectory(prefix="node-global-") as tmp:
            node=Path(tmp)/"node.exe"
            node.write_bytes(b"")
            pkg=Path(tmp)/"node_modules"/"@jackwener"/"opencli"
            entry=pkg/"dist"/"src"/"main.js"
            entry.parent.mkdir(parents=True)
            entry.write_text("console.log('test')",encoding="utf-8")
            (pkg/"package.json").write_text(json.dumps({
                "name":"@jackwener/opencli","version":"2.0.2"}),encoding="utf-8")
            with patch.object(bridge,"_windows",return_value=True):
                with patch.object(bridge.shutil,"which",return_value=str(node)):
                    with patch.dict(os.environ,{"APPDATA":str(Path(tmp)/"elsewhere")}):
                        result=bridge._configuration()
            self.assertEqual(result,(str(node),str(entry),"lhm-work"))
            (pkg/"package.json").write_text(json.dumps({
                "name":"attacker/fake-cli","version":"2.0.2"}),encoding="utf-8")
            with patch.object(bridge,"_windows",return_value=True):
                with patch.object(bridge.shutil,"which",return_value=str(node)):
                    with patch.dict(os.environ,{"APPDATA":str(Path(tmp)/"elsewhere")}):
                        with self.assertRaises(HTTPException):
                            bridge._configuration()

    def test_no_implicit_profile_or_missing_cli(self):
        with patch.object(bridge,"_windows",return_value=True):
            with patch.dict(os.environ,{"LHM_OPENCLI_PROFILE":""}):
                with self.assertRaises(HTTPException) as err:
                    bridge._configuration()
                self.assertEqual(err.exception.status_code,409)
            with patch.object(bridge.shutil,"which",return_value=None):
                with self.assertRaises(HTTPException) as err:
                    bridge._configuration()
                self.assertEqual(err.exception.status_code,503)


class OpenCLIExecutionTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.env=patch.dict(os.environ,{
            "LHM_COMPUTER_ALLOWED_HOSTS":"example.com",
            "LHM_OPENCLI_ALLOWED_HOSTS":"example.com",
            "LHM_OPENCLI_ENABLED":"1","LHM_OPENCLI_PROFILE":"lhm-work",
            "LHM_DESKTOP_NATIVE":"1",
            "OPENROUTER_API_KEY":"DO_NOT_PASS",
            "LHM_COMPUTER_RUNNER_TOKEN":"DO_NOT_PASS",
            "GH_TOKEN":"DO_NOT_PASS",
        })
        self.env.start()
        self.addCleanup(self.env.stop)

    async def test_exact_node_js_argv_avoids_cmd_scripts_and_env_secrets(self):
        class Fake:
            returncode=0
            async def communicate(self):
                return json.dumps({"ok":True,"data":{"url":"https://example.com","snapshot":"[1] button Approve"}}).encode(),b""
        calls=[]
        async def spawn(*argv,**kw):
            calls.append((argv,kw));return Fake()
        with patch.object(bridge.asyncio,"create_subprocess_exec",side_effect=spawn):
            response=await bridge._invoke("C:/node.exe","C:/npm/main.js","lhm-work",
                        "lhm_demo_content","state")
        self.assertTrue(response["ok"])
        argv,kwargs=calls[0]
        self.assertEqual(argv,("C:/node.exe","C:/npm/main.js","--profile",
                               "lhm-work","browser","state","--session","lhm_demo_content"))
        self.assertFalse(kwargs.get("shell",False))
        for key in ("GH_TOKEN","OPENROUTER_API_KEY","LHM_COMPUTER_RUNNER_TOKEN"):
            self.assertNotIn(key,kwargs["env"])

    async def test_click_checks_fresh_ref_and_url_before_and_after(self):
        calls=[]
        async def invoke(node,entry,profile,session,action,*args):
            calls.append((action,args))
            if action=="state":
                return {"ok":True,"data":{"url":"https://example.com/form",
                                            "snapshot":"[12] button Send"}}
            if action=="click":
                return {"ok":True,"data":{"clicked":True,"target":"12"}}
            raise AssertionError(action)
        with patch.object(bridge,"_configuration",return_value=("node","app.js","lhm-work")):
            with patch.object(bridge,"_invoke",side_effect=invoke):
                out=await bridge.execute("demo","content","opencli_click",{"ref":12,"url":"https://example.com/form","label":"Send"})
        self.assertEqual([x[0] for x in calls],["state","click","state"])
        self.assertEqual(out["url_after"],"https://example.com/form")
        self.assertFalse(out["external_effect_verified"])
        self.assertTrue(out["requires_owner_reconciliation"])

    async def test_approved_ref_url_and_label_must_match_current_snapshot(self):
        calls=[]
        async def invoke(node,entry,profile,session,action,*args):
            calls.append(action)
            return {"ok":True,"data":{
                "url":"https://example.com/changed",
                "snapshot":"[12] button Delete account"}}
        with patch.object(bridge,"_configuration",return_value=("node","app.js","lhm-work")):
            with patch.object(bridge,"_invoke",side_effect=invoke):
                with self.assertRaises(HTTPException) as exc:
                    await bridge.execute("demo","content","opencli_click",{
                        "ref":12,"url":"https://example.com/form","label":"Send"})
        self.assertEqual(exc.exception.status_code,409)
        self.assertEqual(calls,["state"])
        calls.clear()
        async def renamed(node,entry,profile,session,action,*args):
            calls.append(action)
            return {"ok":True,"data":{
                "url":"https://example.com/form",
                "snapshot":"[12] button Delete account"}}
        with patch.object(bridge,"_configuration",return_value=("node","app.js","lhm-work")):
            with patch.object(bridge,"_invoke",side_effect=renamed):
                with self.assertRaises(HTTPException):
                    await bridge.execute("demo","content","opencli_click",{
                        "ref":12,"url":"https://example.com/form","label":"Send"})
        self.assertEqual(calls,["state"])

    async def test_status_requires_native_token_and_cannot_expose_profile_details(self):
        from fastapi.testclient import TestClient
        import computer_executor
        import opencli_diagnostics
        with patch.dict(os.environ,{"LHM_COMPUTER_RUNNER_TOKEN":"test-token"}):
            api=TestClient(computer_executor.runner)
            self.assertEqual(api.get("/v1/opencli/status").status_code,401)
            with patch.object(opencli_diagnostics,"inspect",return_value={
                "enabled":True,"cli_installed":True,"bridge_running":True,
                "profile_connected":True,"ready":True,"profile_alias":"lhm-work",
                "detail":"Chrome profile connected"}):
                response=api.get("/v1/opencli/status",headers={"Authorization":"Bearer test-token"})
        self.assertEqual(response.status_code,200)
        self.assertTrue(response.json()["ready"])

    async def test_redirect_outside_allowlist_blocks_write(self):
        calls=[]
        async def invoke(node,entry,profile,session,action,*args):
            calls.append(action)
            return {"ok":True,"data":{"url":"https://evil.test/","snapshot":"[1] button"}}
        with patch.object(bridge,"_configuration",return_value=("node","app.js","lhm-work")):
            with patch.object(bridge,"_invoke",side_effect=invoke):
                with self.assertRaises(HTTPException) as e:
                    await bridge.execute("demo","content","opencli_fill",{"ref":1,"text":"hello","url":"https://example.com/form","label":"Message"})
        self.assertEqual(e.exception.status_code,409)
        self.assertEqual(calls,["state"])

    async def test_open_requires_real_on_host_navigation(self):
        async def invoke(node,entry,profile,session,action,*args):
            return {"ok":True,"data":{"url":"https://redirect.test/"}}
        with patch.object(bridge,"_configuration",return_value=("node","app.js","lhm-work")):
            with patch.object(bridge,"_invoke",side_effect=invoke):
                with self.assertRaises(HTTPException):
                    await bridge.execute("demo","content","opencli_open",
                                         {"url":"https://example.com/start"})


if __name__=="__main__":
    unittest.main()
