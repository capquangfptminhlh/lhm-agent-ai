"""Local offline export of an LHM Computer session audit.

Exports only *explicitly marked, SHA256-verified redacted* PNG evidence,
step order/status and a static HTML replay index. Never exports objectives,
agent prompts, tool arguments, API keys, unredacted screenshots or tool logs.
No network endpoint: owner runs this command on their own machine:

    python audit_replay.py <run_id> <output.zip> --db <local-lhm.sqlite3>

The ZIP itself is sensitive and must not be published to GitHub or shared
without the owner's explicit review. This export is NOT a certification that
redaction is complete or that all steps have pre/post evidence.
"""
from __future__ import annotations

import argparse
import hashlib
import html
import json
import os
import re
import sqlite3
import stat
import zipfile
from pathlib import Path

from fastapi import HTTPException

_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]{0,79}$")
_HASH = re.compile(r"^[0-9a-f]{64}$")
_MAX_PNG_BYTES = 2 * 1024 * 1024
_MAX_STEPS = 500


def _check_ref(root: Path, run_id: str, step_id: str,
               phase: str, meta: object) -> tuple[str, bytes, dict] | None:
    if not isinstance(meta, dict) or meta.get("redacted") is not True:
        return None
    name=f"evidence/{run_id}/{step_id}-{phase}.png"
    if meta.get("path") != name or not _HASH.fullmatch(str(meta.get("sha256", ""))):
        return None
    path=root/name
    # Refuse symlinks, junctions and path redirection, even within an archive.
    # The validated run/step names themselves contain no separators or dots.
    if path.is_symlink() or not path.is_file() or not path.resolve().is_relative_to(root.resolve()):
        return None
    try:
        if not (32 <= path.stat().st_size <= _MAX_PNG_BYTES):
            return None
        image=path.read_bytes()
    except OSError:
        return None
    if image[:8] != b"\x89PNG\r\n\x1a\n" or image[12:16] != b"IHDR":
        return None
    digest=hashlib.sha256(image).hexdigest()
    if digest != meta["sha256"] or meta.get("bytes") != len(image):
        return None
    checked={"path":name, "sha256":digest, "bytes":len(image),
             "redacted":True}
    return name, image, checked


def _step_evidence(result: object) -> dict:
    if not isinstance(result,dict):
        return {}
    verify=result.get("_verification")
    if isinstance(verify,dict):
        evidence=verify.get("evidence")
        if isinstance(evidence,dict):
            return evidence
    record=result.get("_evidence")
    return record if isinstance(record,dict) else {}


def export_session(db_path: str | Path, run_id: str,
                   output_zip: str | Path) -> dict:
    """Create a sanitized replay archive or fail without claiming coverage."""
    if not isinstance(run_id,str) or not _ID.fullmatch(run_id):
        raise ValueError("Invalid run ID")
    db=Path(db_path).resolve()
    if not db.is_file():
        raise FileNotFoundError("LHM local database not found")
    output=Path(output_zip).resolve()
    if output.suffix.lower() != ".zip" or output==db:
        raise ValueError("Expected a distinct .zip output file")
    if output.exists():
        raise FileExistsError("Refusing to overwrite an existing evidence export")
    root=db.parent.resolve()
    with sqlite3.connect(db) as connection:
        connection.row_factory=sqlite3.Row
        session=connection.execute(
            "SELECT id,status,created_at,updated_at FROM computer_runs WHERE id=?",
            (run_id,)).fetchone()
        if session is None:
            raise ValueError("Computer run not found")
        rows=connection.execute(
            "SELECT id,number,tool,status,created_at,updated_at,result_json "
            "FROM computer_steps WHERE run_id=? ORDER BY number ASC",
            (run_id,)).fetchall()
        if len(rows)>_MAX_STEPS:
            raise ValueError("Evidence replay exceeds maximum step count")
        steps=[]
        images=[]
        for row in rows:
            sid=row["id"]
            if not isinstance(sid,str) or not _ID.fullmatch(sid):
                raise ValueError("Invalid step identifier in database")
            try:
                result=json.loads(row["result_json"]) if row["result_json"] else None
            except (TypeError, ValueError):
                result=None
            refs=_step_evidence(result)
            checked={}
            for phase in ("pre","post"):
                item=_check_ref(root,run_id,sid,phase,refs.get(phase))
                if item is not None:
                    name,content,meta=item
                    images.append((name,content))
                    checked[phase]=meta
            steps.append({
                "number":row["number"],"status":str(row["status"])[:32],
                "tool":str(row["tool"])[:64],
                "created_at":row["created_at"],"updated_at":row["updated_at"],
                "evidence":checked,
                "evidence_complete":len(checked)==2,
            })
    complete=sum(1 for step in steps if step["evidence_complete"])
    manifest={
        "format":"LHM-AUDIT-REPLAY-1",
        "run_id":run_id,
        "run_status":str(session["status"])[:40],
        "created_at":session["created_at"],
        "updated_at":session["updated_at"],
        "step_count":len(steps),
        "steps_with_both_images":complete,
        "evidence_complete":len(steps)>0 and complete==len(steps),
        "notice":"Only image redaction flags and SHA256 are verified; manually review before sharing.",
        "steps":steps,
    }
    parts=["<!doctype html><html lang='vi'><meta charset='utf-8'>",
           "<meta http-equiv='Content-Security-Policy' content=\"default-src 'none'; img-src 'self'; style-src 'unsafe-inline'\">",
           "<title>LHM — Phát lại bằng chứng cục bộ</title>",
           "<style>body{font:15px system-ui;max-width:920px;margin:30px auto;padding:0 14px;color:#203045}"
           "article{border:1px solid #ddd;border-radius:10px;padding:16px;margin:14px 0}"
           "img{display:block;max-width:100%;border:1px solid #ccc;margin:8px 0}"
           "small{color:#64748b}.bad{color:#a52a2a}</style>",
           "<h1>LHM — Bằng chứng phiên (offline)</h1>",
           "<p>Chỉ ảnh đã được đánh dấu che dữ liệu. Chưa đủ ảnh không đồng nghĩa hoàn thành.</p>"]
    for step in steps:
        label=html.escape(f"Bước {step['number']} · {step['tool']} · {step['status']}")
        parts.append("<article><h2>"+label+"</h2>")
        for phase in ("pre","post"):
            evidence=step["evidence"].get(phase)
            if evidence:
                path=html.escape(evidence["path"],quote=True)
                digest=html.escape(evidence["sha256"],quote=True)
                parts.append(f"<strong>{phase.upper()}</strong><img src='{path}' alt='{phase}'>"
                             f"<small>SHA-256: {digest}</small>")
            else:
                parts.append("<p class='bad'>Thiếu ảnh "+phase.upper()+" đã xác minh</p>")
        parts.append("</article>")
    parts.append("</html>")
    output.parent.mkdir(parents=True,exist_ok=True)
    fd=None
    try:
        fd=os.open(str(output),os.O_CREAT | os.O_EXCL | os.O_WRONLY,0o600)
        with os.fdopen(fd,"wb") as fh:
            fd=None
            with zipfile.ZipFile(fh,mode="w",compression=zipfile.ZIP_DEFLATED,
                                 compresslevel=6) as archive:
                archive.writestr("manifest.json",json.dumps(
                    manifest,ensure_ascii=False,indent=2))
                archive.writestr("index.html","".join(parts))
                for name,image in images:
                    archive.writestr(name,image)
        if os.name!="nt":
            os.chmod(output,stat.S_IRUSR|stat.S_IWUSR)
    except BaseException:
        if fd is not None:
            os.close(fd)
        output.unlink(missing_ok=True)
        raise
    return {"zip":str(output),"run_id":run_id,"step_count":len(steps),
            "complete_steps":complete,"evidence_complete":manifest["evidence_complete"]}


def main() -> None:
    parser=argparse.ArgumentParser(description="Export local redacted LHM session evidence")
    parser.add_argument("run_id")
    parser.add_argument("output_zip")
    parser.add_argument("--db",required=True,help="Path to this installation's lhm.sqlite3")
    args=parser.parse_args()
    result=export_session(args.db,args.run_id,args.output_zip)
    print(json.dumps(result,ensure_ascii=False))


if __name__=="__main__":
    main()
