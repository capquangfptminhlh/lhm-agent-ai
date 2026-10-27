"""Postiz media uploader: offline tests only, zero external network calls."""
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import httpx
from fastapi.testclient import TestClient

import app
import core
import tool_hub

PNG = bytes([137,80,78,71,13,10,26,10]) + b"unit-test-bytes"
JPEG = bytes([255,216,255]) + b"unit-test-bytes"


class MediaUploadTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.old_db = core.DATABASE
        core.DATABASE = Path(self.temp.name) / "upload.sqlite3"
        core.setup()
        tool_hub.prepare_db()
        self.env = patch.dict(os.environ, {"LHM_ADMIN_TOKEN":"","LHM_TOOLS_WORKSPACE":"demo",
                                          "LHM_POSTIZ_API_KEY":""})
        self.env.start()
        self.client = TestClient(app.app)

    def tearDown(self):
        self.env.stop()
        core.DATABASE = self.old_db
        self.temp.cleanup()

    def upload(self, name="photo.png", content=PNG, mime="image/png", workspace="demo"):
        return self.client.post("/tools/postiz/media",data={"workspace_id":workspace},
                       files={"file":(name,content,mime)})

    def test_no_key_does_not_fake_upload(self):
        r=self.upload()
        self.assertEqual(r.status_code,409)
        self.assertNotIn("id",r.json())

    def test_rejects_disallowed_extension_and_mime(self):
        with patch.dict(os.environ,{"LHM_POSTIZ_API_KEY":"test"}):
            self.assertEqual(self.upload("notes.html",b"<html>hello</html>","text/html").status_code,422)
            self.assertEqual(self.upload("photo.png",b"not really a PNG","image/png").status_code,422)
            self.assertEqual(self.upload("image.svg",b"<svg/>","image/svg+xml").status_code,422)

    def test_rejects_empty_and_too_large(self):
        with patch.dict(os.environ,{"LHM_POSTIZ_API_KEY":"test"}):
            self.assertEqual(self.upload(content=b"").status_code,413)
            with patch.object(tool_hub,"MAX_POSTIZ_UPLOAD_BYTES",16):
                self.assertEqual(self.upload(content=PNG+b"more than sixteen bytes").status_code,413)

    def test_scope_prevents_cross_tenant_upload(self):
        with patch.dict(os.environ,{"LHM_POSTIZ_API_KEY":"test"}):
            x=self.client.post("/workspaces",json={"id":"tenant_other","name":"Tenant khác","industry":"general","budget_usd":3})
            self.assertEqual(x.status_code,201)
            result=self.upload(workspace="tenant_other")
            self.assertEqual(result.status_code,403)

    def test_valid_media_upload_returns_postiz_id_and_https_path_only(self):
        seen={}
        class Client:
            def __init__(self, *args, **kwargs):pass
            async def __aenter__(self):return self
            async def __aexit__(self,*args):return False
            async def post(self,url,headers=None,files=None):
                seen.update(url=url,headers=headers,files=files)
                return httpx.Response(200,json={"id":"img-2026","path":"https://uploads.postiz.com/photo.png",
                                                 "secret":"do-not-echo"},
                                      request=httpx.Request("POST",url))
        with patch.dict(os.environ,{"LHM_POSTIZ_API_KEY":"test-postiz-key"}):
            with patch.object(tool_hub.httpx,"AsyncClient",Client):
                r=self.upload()
        self.assertEqual(r.status_code,200,r.text)
        self.assertEqual(r.json()["id"],"img-2026")
        self.assertEqual(r.json()["path"],"https://uploads.postiz.com/photo.png")
        self.assertEqual(r.json()["confirmation"],"uploaded_to_postiz_not_published")
        self.assertNotIn("test-postiz-key",str(r.json()))
        self.assertNotIn("secret",str(r.json()))
        self.assertEqual(seen["url"],"https://api.postiz.com/public/v1/upload")
        self.assertEqual(seen["headers"]["Authorization"],"test-postiz-key")
        self.assertEqual(seen["files"]["file"][1],PNG)

    def test_provider_wrong_receipt_fails_closed(self):
        class Client:
            def __init__(self,*args,**kwargs):pass
            async def __aenter__(self):return self
            async def __aexit__(self,*args):return False
            async def post(self,url,**kwargs):
                return httpx.Response(200,json={"path":"http://insecure.local/path","id":"x"},
                                      request=httpx.Request("POST",url))
        with patch.dict(os.environ,{"LHM_POSTIZ_API_KEY":"test"}):
            with patch.object(tool_hub.httpx,"AsyncClient",Client):
                r=self.upload()
        self.assertEqual(r.status_code,502)
        self.assertNotIn("id",r.json())

    def test_frontend_has_upload_controls(self):
        html=self.client.get("/tool-hub")
        self.assertEqual(html.status_code,200)
        self.assertIn('id="uploadMedia"',html.text)
        self.assertIn("/tools/postiz/media",html.text)


if __name__=="__main__":
    unittest.main()
