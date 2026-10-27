"""Offline replay ZIP must not leak prompts, tool arguments, raw log or fake PNGs."""
from __future__ import annotations
import base64
import json
import sqlite3
import struct
import tempfile
import unittest
import zipfile
import zlib
from pathlib import Path

from audit_replay import export_session
from evidence_artifacts import store_redacted_png


def png():
    def chunk(kind,body):
        return (struct.pack(">I",len(body))+kind+body+
                struct.pack(">I",zlib.crc32(kind+body)&0xffffffff))
    return (b"\x89PNG\r\n\x1a\n"+
            chunk(b"IHDR",struct.pack(">IIBBBBB",1,1,8,2,0,0,0))+
            chunk(b"IDAT",zlib.compress(b"\x00\x00\x00\x00"))+
            chunk(b"IEND",b""))


class AuditReplayTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root=Path(self.tmp.name)
        self.db=self.root/"local.sqlite3"
        self.output=self.root/"session.zip"
        with sqlite3.connect(self.db) as c:
            c.execute("CREATE TABLE computer_runs (id TEXT,status TEXT,created_at INTEGER,updated_at INTEGER,objective TEXT)")
            c.execute("CREATE TABLE computer_steps (id TEXT,run_id TEXT,number INTEGER,tool TEXT,status TEXT,created_at INTEGER,updated_at INTEGER,result_json TEXT,args_json TEXT)")
            c.execute("INSERT INTO computer_runs VALUES(?,?,?,?,?)",
                      ("run_1","needs_review",100,200,
                       "PRIVATE OBJECTIVE do-not-export@example.com"))

    def step(self,number=1,result=None):
        with sqlite3.connect(self.db) as c:
            c.execute("INSERT INTO computer_steps VALUES(?,?,?,?,?,?,?,?,?)",
                (f"step_{number}","run_1",number,"browser_click","completed",110,150,
                 json.dumps(result or {},ensure_ascii=False),
                 '{"selector":"#private","password":"PRIVATE-PIN-123"}'))

    def saved(self,phase,step="step_1"):
        return store_redacted_png("run_1",step,phase,{"redacted":True,
            "png_base64":base64.b64encode(png()).decode()},
            db_path=str(self.db))

    def test_complete_replay_with_verified_images_and_static_index(self):
        pre=self.saved("pre")
        post=self.saved("post")
        self.step(result={"_verification":{"method":"redacted_png_pair",
                          "evidence":{"pre":pre,"post":post}},
                          "danger":"SECRET-SERVER-LOG"})
        result=export_session(self.db,"run_1",self.output)
        self.assertEqual(result["complete_steps"],1)
        self.assertTrue(result["evidence_complete"])
        with zipfile.ZipFile(self.output) as z:
            names=z.namelist()
            self.assertEqual(names,["manifest.json","index.html",pre["path"],post["path"]])
            data=z.read("manifest.json").decode("utf-8")
            self.assertNotIn("PRIVATE OBJECTIVE",data)
            self.assertNotIn("PRIVATE-PIN",data)
            self.assertNotIn("SECRET-SERVER-LOG",data)
            self.assertIn("SHA-256",z.read("index.html").decode("utf-8"))
            self.assertNotIn("https://",z.read("index.html").decode("utf-8"))
            obj=json.loads(data)
            self.assertEqual(obj["steps_with_both_images"],1)
            self.assertEqual(z.read(pre["path"]),png())

    def test_missing_image_is_honestly_incomplete(self):
        pre=self.saved("pre")
        self.step(result={"_evidence":{"pre":pre}})
        report=export_session(self.db,"run_1",self.output)
        self.assertFalse(report["evidence_complete"])
        self.assertEqual(report["complete_steps"],0)
        with zipfile.ZipFile(self.output) as z:
            manifest=json.loads(z.read("manifest.json"))
            self.assertIn("pre",manifest["steps"][0]["evidence"])
            self.assertNotIn("post",manifest["steps"][0]["evidence"])

    def test_tampered_file_fails_sha256_without_exporting_bytes(self):
        pre=self.saved("pre")
        post=self.saved("post")
        self.step(result={"_verification":{"evidence":{"pre":pre,"post":post}}})
        (self.root/pre["path"]).write_bytes(png()+b"tampered")
        report=export_session(self.db,"run_1",self.output)
        self.assertFalse(report["evidence_complete"])
        with zipfile.ZipFile(self.output) as z:
            self.assertNotIn(pre["path"],z.namelist())
            self.assertIn(post["path"],z.namelist())

    def test_unredacted_and_unsafe_path_never_enter_export(self):
        good=self.saved("pre")
        forged={"path":"../../secret.png","sha256":good["sha256"],
                "redacted":True,"bytes":good["bytes"]}
        self.step(result={"_verification":{"evidence":{
            "pre":{**good,"redacted":False},"post":forged}}})
        report=export_session(self.db,"run_1",self.output)
        self.assertFalse(report["evidence_complete"])
        with zipfile.ZipFile(self.output) as z:
            self.assertEqual(z.namelist(),["manifest.json","index.html"])

    def test_no_overwrite_and_run_validation(self):
        self.step()
        export_session(self.db,"run_1",self.output)
        with self.assertRaises(FileExistsError):
            export_session(self.db,"run_1",self.output)
        for invalid in ("../run_1","other/run","", "run.1"):
            with self.subTest(invalid=invalid):
                with self.assertRaises(ValueError):
                    export_session(self.db,invalid,self.root/"other.zip")

    def test_missing_session_and_nonexistent_db(self):
        with self.assertRaises(ValueError):
            export_session(self.db,"not_here",self.output)
        with self.assertRaises(FileNotFoundError):
            export_session(self.root/"missing.sqlite3","run_1",self.output)


if __name__=="__main__":
    unittest.main()
