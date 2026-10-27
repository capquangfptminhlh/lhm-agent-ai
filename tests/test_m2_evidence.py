"""M2: evidence bytes must remain local, redacted and tamper-detectable."""
from __future__ import annotations

import asyncio
import base64
import hashlib
import json
import os
import stat
import struct
import tempfile
import unittest
import zlib
from pathlib import Path
from unittest.mock import AsyncMock, patch

from fastapi import HTTPException
from evidence_artifacts import store_redacted_png


def png():
    def chunk(kind, data):
        return (struct.pack(">I", len(data))+kind+data+
                struct.pack(">I", zlib.crc32(kind+data) & 0xffffffff))
    ihdr=struct.pack(">IIBBBBB",1,1,8,2,0,0,0)
    return (b"\x89PNG\r\n\x1a\n"+chunk(b"IHDR",ihdr)
            +chunk(b"IDAT",zlib.compress(b"\x00\x00\x00\x00"))
            +chunk(b"IEND",b""))


class EvidenceArtifactTests(unittest.TestCase):
    def test_redacted_file_and_checksum(self):
        img=png()
        with tempfile.TemporaryDirectory() as home:
            meta=store_redacted_png("session_abc","step_123","pre",
                {"redacted":True,"png_base64":base64.b64encode(img).decode()},
                db_path=str(Path(home)/"db.sqlite"))
            self.assertEqual(meta["sha256"],hashlib.sha256(img).hexdigest())
            self.assertEqual(meta["path"],"evidence/session_abc/step_123-pre.png")
            self.assertTrue(meta["redacted"])
            f=Path(home)/meta["path"]
            self.assertEqual(f.read_bytes(),img)
            if os.name != "nt":
                self.assertEqual(stat.S_IMODE(f.stat().st_mode),0o600)
                self.assertEqual(stat.S_IMODE(f.parent.stat().st_mode),0o700)
            with self.assertRaises(HTTPException) as e:
                store_redacted_png("session_abc","step_123","pre",
                    {"redacted":True,"png_base64":base64.b64encode(img).decode()},
                    db_path=str(Path(home)/"db.sqlite"))
            self.assertEqual(e.exception.status_code,409)

    def test_raw_or_forged_redaction_is_rejected(self):
        img=base64.b64encode(png()).decode()
        with tempfile.TemporaryDirectory() as home:
            for bad in [{"png_base64":img}, {"redacted":False,"png_base64":img},
                        {"redacted":"true","png_base64":img},
                        {"redacted":True,"png_base64":"not base64"}]:
                with self.subTest(bad=str(bad)[:40]):
                    with self.assertRaises(HTTPException):
                        store_redacted_png("session","step","post",bad,
                            db_path=str(Path(home)/"db.sqlite"))
            self.assertFalse((Path(home)/"evidence").exists())

    def test_invalid_id_and_png_are_rejected(self):
        with tempfile.TemporaryDirectory() as home:
            for rid,sid,phase in [("../escape","step","pre"),
                                   ("session","../../x","pre"),
                                   ("session","step","other")]:
                with self.assertRaises(HTTPException):
                    store_redacted_png(rid,sid,phase,{"redacted":True,
                        "png_base64":base64.b64encode(png()).decode()},
                        db_path=str(Path(home)/"db.sqlite"))
            with self.assertRaises(HTTPException):
                store_redacted_png("session","step","pre",
                    {"redacted":True,"png_base64":base64.b64encode(b"not-png").decode()},
                    db_path=str(Path(home)/"db.sqlite"))

    def test_evidence_directory_symlink_cannot_redirect_image(self):
        image={"redacted":True,"png_base64":base64.b64encode(png()).decode()}
        with tempfile.TemporaryDirectory() as home:
            base=Path(home)
            redirected=base/"redirected"
            redirected.mkdir()
            link=base/"evidence"
            try:
                link.symlink_to(redirected,target_is_directory=True)
            except (OSError,NotImplementedError):
                self.skipTest("Symlink creation not supported without privileges")
            with self.assertRaises(HTTPException) as exc:
                store_redacted_png("run","step","pre",image,
                                   db_path=str(base/"db.sqlite3"))
            self.assertEqual(exc.exception.status_code,403)
            self.assertFalse(list(redirected.rglob("*.png")))

    def test_agent_calls_only_redacted_runner_tool(self):
        import computer_agent as agent
        image={"redacted":True,"png_base64":base64.b64encode(png()).decode()}
        with tempfile.TemporaryDirectory() as home:
            with patch.dict(os.environ,{"LHM_DB_PATH":str(Path(home)/"db.sqlite")}):
                with patch.object(agent,"runner_call",new=AsyncMock(return_value=image)) as mock:
                    result=asyncio.run(agent._capture_redacted_evidence(
                        {"workspace_id":"demo","role":"research"},
                        "browser_click",{}, "run_1","step_1","pre"))
                    self.assertTrue(result["redacted"])
                    self.assertEqual(mock.await_args.args[1],"browser_evidence")
                    self.assertNotIn("png_base64",json.dumps(result))
                    win=asyncio.run(agent._capture_redacted_evidence(
                        {"workspace_id":"demo","role":"research"},
                        "windows_click",{"hwnd":123},"run_1","step_1","post"))
                    self.assertEqual(mock.await_args.args[1],"windows_evidence")
                    self.assertEqual(mock.await_args.args[2],{"hwnd":123})
                    self.assertTrue((Path(home)/win["path"]).exists())

    def test_browser_evidence_rejects_non_editable_secrets(self):
        import computer_executor as runner
        for leaked in ["Card number 4111 1111 1111 1111", 
                       "Mật khẩu trong trang",
                       "support@example.com",
                       "Số thẻ của khách", "CVV 123", "Bank account overview"]:
            with self.subTest(leaked=leaked):
                with self.assertRaises(HTTPException) as exc:
                    runner._check_public_browser_text_for_evidence(leaked)
                self.assertEqual(exc.exception.status_code,403)
        runner._check_public_browser_text_for_evidence(
            "Welcome to the demo product search. Name  Email  Submit")

    def test_evidence_runner_requires_opt_in_and_obeys_stop(self):
        from fastapi.testclient import TestClient
        import computer_executor as runner
        client=TestClient(runner.runner)
        headers={"Authorization":"Bearer private-runner-evidence-token"}
        payload={"workspace_id":"demo","role":"research",
                 "tool":"browser_evidence","args":{}}
        with patch.dict(os.environ,{"LHM_COMPUTER_RUNNER_TOKEN":
                                   "private-runner-evidence-token",
                                   "LHM_EVIDENCE_CAPTURE_ENABLED":"0"}):
            response=client.post("/v1/tool",json=payload,headers=headers)
            self.assertEqual(response.status_code,403,response.text)
        # This new tool must be in the emergency-stop mutation set. No
        # screenshot should be taken after stop, even in an already-open tab.
        self.assertIn("browser_evidence",runner.MUTATIONS)
        self.assertIn("windows_evidence",runner.MUTATIONS)

    def test_ungroundable_desktop_action_fails_closed(self):
        import computer_agent as agent
        with self.assertRaises(HTTPException) as err:
            asyncio.run(agent._capture_redacted_evidence(
                {"workspace_id":"demo","role":"research"},
                "windows_desktop_click",{"x":100,"y":100},
                "run_1","step_1","pre"))
        self.assertEqual(err.exception.status_code,403)


if __name__ == "__main__":
    unittest.main()
