"""Owner-reviewed, one-file GitHub draft-PR bridge for fixed repository agents.

An AI diagnostic is NEVER GitHub write authorization. The owner supplies and
reviews replacement text, then explicitly approves one bounded PR. No auto-merge
or auto-retry after possible GitHub side effects.
"""
from __future__ import annotations

import base64
import difflib
import json
import os
import re
from pathlib import Path
from typing import Any
from urllib.parse import quote

import httpx
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from core import audit, db, new_id, now, workspace
from repo_agent_bindings import PROJECT_AGENTS, prepare_db as prepare_diagnoses

router=APIRouter(tags=["Repository Agent Repairs"])
_SHA=re.compile(r"^[0-9a-f]{40}$")
_COMPONENT=re.compile(r"^[A-Za-z0-9_][A-Za-z0-9_.-]{0,119}$")
_ALLOWED_SUFFIXES=(".py",".js",".cjs",".mjs",".ts",".tsx",".jsx",
                   ".html",".css",".json",".md",".sql",".vue")
_BLOCKED_WORDS=("secret","token","credential","password","private_key",
                "license_key",".env","id_rsa")
_MAX_SOURCE=96_000


class RepairIn(BaseModel):
    workspace: str = "demo"
    diagnosis_id: str = Field(min_length=10,max_length=60)
    path: str = Field(min_length=4,max_length=230)
    new_content: str = Field(min_length=1,max_length=24_000)
    explanation: str = Field(min_length=12,max_length=800)
    idempotency_key: str = Field(min_length=8,max_length=80,
                                 pattern=r"^[A-Za-z0-9_-]+$")


def _path(value: str) -> str:
    if (not isinstance(value,str) or "\\" in value or
        value.startswith("/") or "//" in value or
        not value.endswith(_ALLOWED_SUFFIXES)):
        raise HTTPException(422,"Đường dẫn bản vá không hợp lệ")
    parts=value.split("/")
    if (len(parts)>10 or any(not _COMPONENT.fullmatch(part) or
                            part in (".","..") for part in parts) or
        parts[0].lower() in (".github",".git",".circleci") or
        any(term in value.lower() for term in _BLOCKED_WORDS)):
        raise HTTPException(422,"Chặn file hệ thống/bí mật và đường dẫn chưa cho phép")
    return "/".join(parts)


def _github_headers(write: bool=False) -> dict[str,str]:
    token=(os.environ.get("LHM_GITHUB_WRITE_TOKEN","").strip()
           if write else (os.environ.get("LHM_GITHUB_READ_TOKEN","").strip() or
                          os.environ.get("LHM_GITHUB_WRITE_TOKEN","").strip()))
    if write and not token:
        raise HTTPException(424,"Chưa cấu hình LHM_GITHUB_WRITE_TOKEN phía backend")
    out={"Accept":"application/vnd.github+json",
         "X-GitHub-Api-Version":"2022-11-28",
         "User-Agent":"LHM-Owner-Reviewed-Repair"}
    if token:
        out["Authorization"]="Bearer "+token
    return out


def _api(repo: str, subpath: str) -> str:
    if repo not in {v["repository"] for v in PROJECT_AGENTS.values()}:
        raise HTTPException(403,"Repository không nằm trong danh sách Agent được phép")
    return "https://api.github.com/repos/"+repo+"/"+subpath


async def _request(client: httpx.AsyncClient,method: str,url: str,
                   write: bool=False, **kwargs) -> dict[str,Any]:
    try:
        response=await client.request(method,url,headers=_github_headers(write),
                                      **kwargs)
    except httpx.HTTPError:
        raise HTTPException(503,"Không liên lạc được GitHub API; chưa xác minh kết quả")
    expected=(200,201) if write else (200,)
    if response.status_code not in expected:
        # Never leak upstream JSON: GitHub can echo sensitive content/tokens.
        status=424 if response.status_code in (401,403,404) else 502
        raise HTTPException(status,
                            "GitHub từ chối "+method+" (HTTP "+str(response.status_code)+")")
    try:
        payload=response.json()
        if not isinstance(payload,dict):
            raise ValueError("not JSON object")
        return payload
    except (ValueError,TypeError):
        raise HTTPException(502,"Phản hồi GitHub API không hợp lệ")


def prepare_db() -> None:
    prepare_diagnoses()
    with db() as c:
        c.execute("""CREATE TABLE IF NOT EXISTS repo_agent_repairs(
            id TEXT PRIMARY KEY,
            workspace_id TEXT NOT NULL REFERENCES workspaces(id),
            agent_id TEXT NOT NULL,
            repository TEXT NOT NULL,
            diagnosis_id TEXT NOT NULL REFERENCES repo_agent_diagnoses(id),
            path TEXT NOT NULL,
            base_head_sha TEXT NOT NULL,
            source_blob_sha TEXT NOT NULL,
            new_content TEXT NOT NULL,
            diff_preview TEXT NOT NULL,
            explanation TEXT NOT NULL,
            idempotency_key TEXT NOT NULL,
            status TEXT NOT NULL,
            branch TEXT,
            pr_number INTEGER,
            error TEXT,
            created_at INTEGER NOT NULL,
            updated_at INTEGER NOT NULL,
            UNIQUE(workspace_id,agent_id,idempotency_key)
        )""")
        c.execute("CREATE INDEX IF NOT EXISTS ix_repo_agent_repairs"
                  " ON repo_agent_repairs(workspace_id,agent_id,created_at)")


def _row(c,ws: str,agent_id: str,repair_id: str):
    row=c.execute("""SELECT * FROM repo_agent_repairs
                     WHERE id=? AND workspace_id=? AND agent_id=?""",
                  (repair_id,ws,agent_id)).fetchone()
    if not row:
        raise HTTPException(404,"Không tìm thấy bản vá trong Workspace/Agent")
    return row


def _view(row) -> dict[str,Any]:
    return {
        "id":row["id"],"workspace_id":row["workspace_id"],
        "agent_id":row["agent_id"],"repository":row["repository"],
        "diagnosis_id":row["diagnosis_id"],"path":row["path"],
        "explanation":row["explanation"],"diff_preview":row["diff_preview"],
        "new_content":row["new_content"],
        "base_head_sha":row["base_head_sha"],
        "source_blob_sha":row["source_blob_sha"],
        "status":row["status"],"created_at":row["created_at"],
        "updated_at":row["updated_at"],"branch":row["branch"],
        "pull_request_number":row["pr_number"],
        "pull_request_url":("https://github.com/"+row["repository"]+"/pull/"+
                            str(row["pr_number"])) if row["pr_number"] else None,
        "error":row["error"],"human_approval_required":True,
        "automatic_merge":False,
        "externally_written":row["status"] in ("pr_created","needs_reconciliation"),
    }


def _agent(agent_id: str) -> str:
    if agent_id not in PROJECT_AGENTS:
        raise HTTPException(404,"Agent dự án không tồn tại")
    return PROJECT_AGENTS[agent_id]["repository"]


@router.get("/workspaces/{ws}/repo-agents/{agent_id}/repairs")
def list_repairs(ws: str,agent_id: str):
    _agent(agent_id)
    prepare_db()
    with db() as c:
        if not workspace(c,ws):
            raise HTTPException(404,"Workspace không tồn tại")
        rows=c.execute("""SELECT * FROM repo_agent_repairs
            WHERE workspace_id=? AND agent_id=?
            ORDER BY created_at DESC,rowid DESC LIMIT 20""",(ws,agent_id)).fetchall()
        return {"repairs":[_view(row) for row in rows]}


@router.get("/workspaces/{ws}/repo-agents/{agent_id}/repairs/{repair_id}")
def get_repair(ws: str,agent_id: str,repair_id: str):
    _agent(agent_id)
    prepare_db()
    with db() as c:
        return _view(_row(c,ws,agent_id,repair_id))


@router.post("/workspaces/{ws}/repo-agents/{agent_id}/repairs",status_code=201)
async def propose_repair(ws: str,agent_id: str,inp: RepairIn):
    repo=_agent(agent_id)
    if inp.workspace!=ws:
        raise HTTPException(422,"Workspace không khớp")
    path=_path(inp.path)
    content=inp.new_content
    if ("\x00" in content or not content.strip() or
        len(content.encode("utf-8"))>48_000):
        raise HTTPException(422,"Bản vá trống hoặc vượt giới hạn UTF-8")
    prepare_db()
    with db() as c:
        if not workspace(c,ws):
            raise HTTPException(404,"Workspace không tồn tại")
        old=c.execute("""SELECT * FROM repo_agent_repairs
            WHERE workspace_id=? AND agent_id=? AND idempotency_key=?""",
            (ws,agent_id,inp.idempotency_key)).fetchone()
        if old:
            if (old["diagnosis_id"]!=inp.diagnosis_id or old["path"]!=path or
                old["new_content"]!=content or old["explanation"]!=inp.explanation):
                raise HTTPException(409,"Mã yêu cầu đã dùng với bản vá khác")
            return {**_view(old),"deduplicated":True}
        diag=c.execute("""SELECT d.repository,t.status,t.result
            FROM repo_agent_diagnoses d
            JOIN tasks t ON t.id=d.task_id AND t.workspace_id=d.workspace_id
            WHERE d.id=? AND d.workspace_id=? AND d.agent_id=?""",
            (inp.diagnosis_id,ws,agent_id)).fetchone()
        if not diag or diag["repository"]!=repo:
            raise HTTPException(404,"Chẩn đoán không thuộc Agent và Workspace")
        if diag["status"]!="completed" or not diag["result"]:
            raise HTTPException(409,"Chẩn đoán CI chưa hoàn tất; không thể đề xuất sửa mã")
    async with httpx.AsyncClient(timeout=12,follow_redirects=False,
                                 trust_env=False) as client:
        head=await _request(client,"GET",_api(repo,"branches/main"))
        headsha=head.get("commit",{}).get("sha")
        if not isinstance(headsha,str) or not _SHA.fullmatch(headsha):
            raise HTTPException(502,"GitHub không cung cấp SHA của main")
        url=_api(repo,"contents/"+"/".join(quote(x,safe="") for x in path.split("/")))
        original=await _request(client,"GET",url,params={"ref":headsha})
    if (original.get("type")!="file" or not _SHA.fullmatch(str(original.get("sha") or "")) or
        original.get("encoding")!="base64"):
        raise HTTPException(422,"Chỉ hỗ trợ thay nội dung file văn bản đang tồn tại")
    try:
        raw=base64.b64decode(str(original["content"]),validate=False)
        if len(raw)>_MAX_SOURCE or b"\x00" in raw:
            raise ValueError("not text")
        oldtext=raw.decode("utf-8")
    except (ValueError,UnicodeError,KeyError):
        raise HTTPException(422,"Không thể đọc file UTF-8 trong giới hạn")
    if oldtext==content:
        raise HTTPException(409,"Bản vá không thay đổi nội dung file")
    difference="".join(difflib.unified_diff(oldtext.splitlines(keepends=True),
                  content.splitlines(keepends=True),
                  fromfile="a/"+path,tofile="b/"+path,n=3))
    preview=difference[:13_000]+("\n[DIFF TRUNCATED — mở file để kiểm tra đầy đủ]"
                                  if len(difference)>13_000 else "")
    with db() as c:
        c.execute("BEGIN IMMEDIATE")
        # Recheck active diagnosis after slow GitHub network operation.
        diag=c.execute("SELECT id FROM repo_agent_diagnoses WHERE id=? AND workspace_id=? AND agent_id=?",
                       (inp.diagnosis_id,ws,agent_id)).fetchone()
        if not diag:
            raise HTTPException(409,"Chẩn đoán đã thay đổi khi chuẩn bị bản vá")
        old=c.execute("""SELECT * FROM repo_agent_repairs
            WHERE workspace_id=? AND agent_id=? AND idempotency_key=?""",
            (ws,agent_id,inp.idempotency_key)).fetchone()
        if old:
            if (old["diagnosis_id"]!=inp.diagnosis_id or old["path"]!=path or
                old["new_content"]!=content or old["explanation"]!=inp.explanation):
                raise HTTPException(409,"Mã yêu cầu đã dùng với bản vá khác")
            return {**_view(old),"deduplicated":True}
        repair_id=new_id()
        c.execute("""INSERT INTO repo_agent_repairs
          (id,workspace_id,agent_id,repository,diagnosis_id,path,
           base_head_sha,source_blob_sha,new_content,diff_preview,explanation,
           idempotency_key,status,created_at,updated_at)
          VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
          (repair_id,ws,agent_id,repo,inp.diagnosis_id,path,headsha,original["sha"],
           content,preview,inp.explanation,inp.idempotency_key,"awaiting_approval",now(),now()))
        audit(c,ws,"repo_agent.repair_proposed",
              agent_id+" proposed owner-reviewed replacement "+path,repair_id)
        record=c.execute("SELECT * FROM repo_agent_repairs WHERE id=?",
                         (repair_id,)).fetchone()
        return {**_view(record),"deduplicated":False}


@router.post("/workspaces/{ws}/repo-agents/{agent_id}/repairs/{repair_id}/reject")
def reject_repair(ws: str,agent_id: str,repair_id: str):
    _agent(agent_id)
    prepare_db()
    with db() as c:
        c.execute("BEGIN IMMEDIATE")
        record=_row(c,ws,agent_id,repair_id)
        if record["status"]!="awaiting_approval":
            raise HTTPException(409,"Bản vá đã được quyết định hoặc có tác động GitHub")
        c.execute("UPDATE repo_agent_repairs SET status='rejected',updated_at=? WHERE id=?",
                  (now(),repair_id))
        audit(c,ws,"repo_agent.repair_rejected",
              "Owner rejected draft patch",repair_id)
        return _view(_row(c,ws,agent_id,repair_id))


@router.post("/workspaces/{ws}/repo-agents/{agent_id}/repairs/{repair_id}/approve")
async def approve_repair(ws: str,agent_id: str,repair_id: str):
    """One owner click -> GitHub draft PR. Unknown outcomes require reconciliation."""
    repo=_agent(agent_id)
    # Fail before changing state if no WRITE token exists. READ token cannot publish.
    _github_headers(True)
    prepare_db()
    with db() as c:
        c.execute("BEGIN IMMEDIATE")
        row=_row(c,ws,agent_id,repair_id)
        if row["status"]!="awaiting_approval":
            raise HTTPException(409,"Bản vá không còn chờ duyệt; không được gửi trùng GitHub")
        branch="agent-repair/"+agent_id+"/"+repair_id.split("-")[0]
        c.execute("""UPDATE repo_agent_repairs
            SET status='publishing',branch=?,updated_at=? WHERE id=?""",
            (branch,now(),repair_id))
        audit(c,ws,"repo_agent.repair_approved",
              "Owner approved one-file GitHub draft PR; no merge",repair_id)
        params={key:row[key] for key in ("path","new_content","explanation",
                                        "base_head_sha","source_blob_sha","diagnosis_id")}
    try:
        async with httpx.AsyncClient(timeout=15,follow_redirects=False,
                                     trust_env=False) as client:
            head=await _request(client,"GET",_api(repo,"branches/main"))
            latest=head.get("commit",{}).get("sha")
            if latest!=params["base_head_sha"]:
                with db() as c:
                    c.execute("""UPDATE repo_agent_repairs
                        SET status='stale',error=?,updated_at=? WHERE id=?""",
                        ("Main đã thay đổi kể từ khi tạo đề xuất; tạo bản vá mới để kiểm tra",now(),repair_id))
                raise HTTPException(409,"main đã thay đổi; không gửi bản vá lỗi thời")
            path=params["path"]
            current=await _request(client,"GET",_api(repo,"contents/"+
                                   "/".join(quote(x,safe="") for x in path.split("/"))),
                                   params={"ref":latest})
            if current.get("sha")!=params["source_blob_sha"]:
                with db() as c:
                    c.execute("""UPDATE repo_agent_repairs
                        SET status='stale',error=?,updated_at=? WHERE id=?""",
                        ("File đã thay đổi kể từ khi tạo đề xuất",now(),repair_id))
                raise HTTPException(409,"File đã thay đổi; không gửi bản vá lỗi thời")
            created=await _request(client,"POST",_api(repo,"git/refs"),write=True,
                json={"ref":"refs/heads/"+branch,"sha":latest})
            if not isinstance(created.get("ref"),str) or created["ref"]!="refs/heads/"+branch:
                raise HTTPException(502,"GitHub không xác nhận branch đã tạo")
            content=base64.b64encode(params["new_content"].encode("utf-8")).decode("ascii")
            updated=await _request(client,"PUT",_api(repo,"contents/"+
                               "/".join(quote(x,safe="") for x in path.split("/"))),
                               write=True,json={
                "message":"fix: owner-reviewed CI repair for "+agent_id,
                "content":content,"sha":params["source_blob_sha"],
                "branch":branch})
            if not isinstance(updated.get("commit",{}).get("sha"),str):
                raise HTTPException(502,"GitHub chưa xác nhận commit file trên branch")
            pr=await _request(client,"POST",_api(repo,"pulls"),write=True,json={
                "title":"fix: owner-reviewed repair in "+path,
                "head":branch,"base":"main","draft":True,
                "body":("One-file replacement approved by local LHM owner.\n"
                        "Source diagnostic ID: "+params["diagnosis_id"]+"\n"
                        "Explanation: "+params["explanation"]+"\n"
                        "CI is required. NO AUTOMATIC MERGE.")})
            number=pr.get("number")
            if type(number) is not int or number<=0:
                raise HTTPException(502,"GitHub chưa xác nhận PR; cần đối soát")
        with db() as c:
            c.execute("BEGIN IMMEDIATE")
            c.execute("""UPDATE repo_agent_repairs
                SET status='pr_created',pr_number=?,updated_at=? WHERE id=?""",
                (number,now(),repair_id))
            audit(c,ws,"repo_agent.draft_pr_created",
                  "GitHub draft PR #"+str(number)+" created for "+repo,repair_id)
            return _view(_row(c,ws,agent_id,repair_id))
    except Exception as error:
        # Once approved, some remote steps may have succeeded. Never send
        # again blindly; the owner must inspect the branch/PR on GitHub.
        with db() as c:
            c.execute("BEGIN IMMEDIATE")
            existing=c.execute("SELECT status FROM repo_agent_repairs WHERE id=?",
                               (repair_id,)).fetchone()
            if existing and existing["status"]=="publishing":
                c.execute("""UPDATE repo_agent_repairs
                    SET status='needs_reconciliation',error=?,updated_at=? WHERE id=?""",
                    ("Không xác minh hoàn tất; kiểm tra branch/PR trên GitHub trước mọi thử lại",
                     now(),repair_id))
                audit(c,ws,"repo_agent.repair_reconciliation",
                      "GitHub write outcome uncertain; no automatic retry",repair_id)
        raise error
