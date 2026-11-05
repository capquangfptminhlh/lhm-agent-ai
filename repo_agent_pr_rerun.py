"""Explicit owner-requested re-run of FAILED GitHub Actions jobs on an owned Draft PR.

The same workflow/commit runs on GitHub's infrastructure. No arbitrary commands,
repo writes, workflow dispatch, secret exposure, auto retry, merge or deploy.
A rerun may execute the original workflow's side effects and secrets, so the
owner must explicitly select a run and authorize it with Actions-only token.
"""
from __future__ import annotations

from datetime import datetime, timezone
import os
import re
from typing import Any

import httpx
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from core import audit, db, new_id, now, workspace
from repo_agent_pr_ci import verify_repair_pr_ci
from repo_agent_repair import _agent, _api, _github_headers, _row, prepare_db as repair_prepare_db

router=APIRouter(tags=["Repository Agent Actions Rerun"])
_BANNED=re.compile(r"(?:deploy|publish|release|production|migration|migrate|"
                   r"secret|payment|billing|provision|destroy|rollback)",re.I)
_ALLOWED_EVENT={"push","pull_request"}
_MAX_AGE_SECONDS=30*24*3600


def prepare_db():
    repair_prepare_db()
    with db() as c:
        c.execute("""CREATE TABLE IF NOT EXISTS repo_agent_pr_reruns(
          id TEXT PRIMARY KEY,
          workspace_id TEXT NOT NULL REFERENCES workspaces(id),
          agent_id TEXT NOT NULL,repository TEXT NOT NULL,
          repair_id TEXT NOT NULL REFERENCES repo_agent_repairs(id),
          run_id INTEGER NOT NULL,run_attempt INTEGER NOT NULL,
          pr_number INTEGER NOT NULL,head_sha TEXT NOT NULL,
          workflow_name TEXT NOT NULL, status TEXT NOT NULL,
          error TEXT,created_at INTEGER NOT NULL,updated_at INTEGER NOT NULL,
          UNIQUE(workspace_id,repair_id,run_id)
        )""")
        c.execute("""CREATE INDEX IF NOT EXISTS ix_repo_agent_pr_reruns
                 ON repo_agent_pr_reruns(workspace_id,agent_id,repair_id,created_at)""")


class RerunRequest(BaseModel):
    run_id: int=Field(gt=0)


def _row_view(row):
    return {
        "id":row["id"],"workspace_id":row["workspace_id"],
        "agent_id":row["agent_id"],"repository":row["repository"],
        "repair_id":row["repair_id"],"run_id":row["run_id"],
        "run_attempt":row["run_attempt"],"pr_number":row["pr_number"],
        "head_sha":row["head_sha"],"workflow_name":row["workflow_name"],
        "status":row["status"],"error":row["error"],
        "created_at":row["created_at"],"updated_at":row["updated_at"],
        "run_url":"https://github.com/"+row["repository"]+
                   "/actions/runs/"+str(row["run_id"]),
        "github_request_submitted":row["status"]=="requested",
        "rerun_is_not_test_success":True,"owner_approval_required":True,
        "automatically_merged":False,"deployed_verified":False,
    }


async def _github_get(client,url,params=None):
    try:
        res=await client.get(url,headers=_github_headers(),params=params)
    except httpx.HTTPError:
        raise HTTPException(503,"Không thể đọc GitHub Actions; không được suy đoán")
    if res.status_code!=200:
        raise HTTPException(424 if res.status_code in (401,403,404) else 502,
                            "GitHub Actions read failed ("+str(res.status_code)+")")
    try:
        payload=res.json()
    except (ValueError,TypeError):
        payload=None
    if not isinstance(payload,dict):
        raise HTTPException(502,"GitHub Actions trả dữ liệu không hợp lệ")
    return payload


def _run_candidate(item,sha,branch):
    if not isinstance(item,dict):
        return None
    identifier=item.get("id")
    attempt=item.get("run_attempt")
    name=item.get("name")
    path=item.get("path")
    event=item.get("event")
    status=item.get("status")
    conclusion=item.get("conclusion")
    if (type(identifier) is not int or identifier<=0 or
        type(attempt) is not int or attempt<=0 or
        item.get("head_sha")!=sha or item.get("head_branch")!=branch or
        event not in _ALLOWED_EVENT or status!="completed" or
        conclusion not in ("failure","timed_out") or
        not isinstance(name,str) or not 1<=len(name)<=120 or
        not isinstance(path,str) or not path.startswith(".github/workflows/") or
        len(path)>240 or _BANNED.search(name+" "+path)):
        return None
    created=item.get("created_at")
    try:
        stamp=datetime.fromisoformat(created.replace("Z","+00:00"))
        if stamp.tzinfo is None:
            return None
        age=datetime.now(timezone.utc).timestamp()-stamp.timestamp()
        if age<0 or age>_MAX_AGE_SECONDS:
            return None
    except (TypeError,AttributeError,ValueError,OverflowError):
        return None
    return {"run_id":identifier,"run_attempt":attempt,
            "workflow_name":name,"event":event,
            "head_sha":sha,"head_branch":branch,
            "conclusion":conclusion,"status":status,
            "run_url":"https://github.com/"+item.get("_fixed_repo","")+
                      "/actions/runs/"+str(identifier)}


async def _discover(repo,snapshot):
    async with httpx.AsyncClient(timeout=15,follow_redirects=False,
                                 trust_env=False) as client:
        listing=await _github_get(client,_api(repo,"actions/runs"),
                   {"head_sha":snapshot["head_sha"],"per_page":50})
    runs=listing.get("workflow_runs")
    total=listing.get("total_count")
    if not isinstance(runs,list) or type(total) is not int or total<0:
        raise HTTPException(502,"GitHub Actions không trả danh sách workflow tin cậy")
    if total>len(runs) or len(runs)>50:
        # Never claim discovery is complete and act on a partial sample.
        raise HTTPException(409,"Danh sách workflow bị phân trang; xem GitHub để kiểm tra thủ công")
    candidates=[]
    for item in runs:
        if isinstance(item,dict):
            item={**item,"_fixed_repo":repo}
        acceptable=_run_candidate(item,snapshot["head_sha"],snapshot["head_branch"])
        if acceptable:
            candidates.append(acceptable)
    return sorted(candidates,key=lambda c:c["run_id"],reverse=True)[:10]


async def _authorized_snapshot(ws,agent_id,repair_id):
    repo=_agent(agent_id)
    prepare_db()
    with db() as c:
        if not workspace(c,ws):
            raise HTTPException(404,"Workspace không tồn tại")
        repair=_row(c,ws,agent_id,repair_id)
        if repair["repository"]!=repo or repair["status"]!="pr_created":
            raise HTTPException(409,"Chỉ có PR thuộc Agent này mới có thể yêu cầu CI chạy lại")
    snapshot=await verify_repair_pr_ci(ws,agent_id,repair_id)
    if snapshot["verdict"]!="failed_observed" or not snapshot["draft"]:
        raise HTTPException(409,"PR không còn là Draft có lỗi CI được xác minh")
    return repo,snapshot


@router.get("/workspaces/{ws}/repo-agents/{agent_id}/repairs/{repair_id}/rerunnable-runs")
async def rerunnable_runs(ws:str,agent_id:str,repair_id:str):
    repo,snapshot=await _authorized_snapshot(ws,agent_id,repair_id)
    candidates=await _discover(repo,snapshot)
    with db() as c:
        previous={r["run_id"] for r in c.execute("""
          SELECT run_id FROM repo_agent_pr_reruns
          WHERE workspace_id=? AND agent_id=? AND repair_id=?""",
          (ws,agent_id,repair_id)).fetchall()}
    return {
        "repository":repo,"pr_number":snapshot["pr_number"],
        "head_sha":snapshot["head_sha"],"runs":[
            {**item,"previous_request":item["run_id"] in previous}
            for item in candidates],
        "read_only":True,"github_actions_not_executed":True,
        "warning":("Rerun uses original workflow privileges and can execute "
                   "side effects. Choose ONLY a trusted CI/test workflow. "
                   "No deploy/release/migration workflows are allowed."),
    }


@router.post("/workspaces/{ws}/repo-agents/{agent_id}/repairs/{repair_id}/rerun-failed",
             status_code=201)
async def rerun_failed(ws:str,agent_id:str,repair_id:str,inp:RerunRequest):
    if not os.environ.get("LHM_ADMIN_TOKEN","").strip():
        raise HTTPException(403,"Bật LHM_ADMIN_TOKEN trước khi gọi GitHub Actions")
    token=os.environ.get("LHM_GITHUB_ACTIONS_TOKEN","").strip()
    if not token:
        raise HTTPException(424,"Thiếu LHM_GITHUB_ACTIONS_TOKEN dành riêng Actions:write")
    repo,snapshot=await _authorized_snapshot(ws,agent_id,repair_id)
    candidates=await _discover(repo,snapshot)
    run=next((x for x in candidates if x["run_id"]==inp.run_id),None)
    if not run:
        raise HTTPException(409,"Không tìm thấy workflow CI thất bại khớp đúng commit PR")
    # Re-read by ID just before reserving the local single-use request.
    async with httpx.AsyncClient(timeout=15,follow_redirects=False,
                                 trust_env=False) as client:
        fresh=await _github_get(client,_api(repo,"actions/runs/"+str(inp.run_id)))
    if not _run_candidate(fresh,snapshot["head_sha"],snapshot["head_branch"]) or (
            fresh["run_attempt"]!=run["run_attempt"] or
            fresh["name"]!=run["workflow_name"]):
        raise HTTPException(409,"Workflow run đã đổi commit/attempt hoặc tình trạng")
    with db() as c:
        c.execute("BEGIN IMMEDIATE")
        repair=_row(c,ws,agent_id,repair_id)
        if repair["status"]!="pr_created" or repair["pr_number"]!=snapshot["pr_number"]:
            raise HTTPException(409,"PR liên kết đã thay đổi")
        previous=c.execute("""SELECT * FROM repo_agent_pr_reruns WHERE
          workspace_id=? AND repair_id=? AND run_id=?""",
          (ws,repair_id,inp.run_id)).fetchone()
        if previous:
            return {**_row_view(previous),"deduplicated":True}
        record_id=new_id()
        c.execute("""INSERT INTO repo_agent_pr_reruns
          (id,workspace_id,agent_id,repository,repair_id,run_id,run_attempt,
           pr_number,head_sha,workflow_name,status,created_at,updated_at)
          VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)""",
          (record_id,ws,agent_id,repo,repair_id,inp.run_id,run["run_attempt"],
           snapshot["pr_number"],snapshot["head_sha"],run["workflow_name"],
           "requesting",now(),now()))
        audit(c,ws,"repo_agent.actions_rerun_owner_approved",
              "Owner explicitly requested failed jobs rerun on verified PR commit",record_id)
    try:
        async with httpx.AsyncClient(timeout=20,follow_redirects=False,
                                     trust_env=False) as client:
            response=await client.post(
                _api(repo,"actions/runs/"+str(inp.run_id)+"/rerun-failed-jobs"),
                headers={**_github_headers(),"Authorization":"Bearer "+token},
                json={"enable_debug_logging":False})
        if response.status_code!=201:
            raise HTTPException(424 if response.status_code in (401,403,404) else 502,
                                "GitHub không xác nhận yêu cầu chạy lại CI ("+
                                str(response.status_code)+")")
        with db() as c:
            c.execute("BEGIN IMMEDIATE")
            c.execute("""UPDATE repo_agent_pr_reruns SET status='requested',
               updated_at=? WHERE id=?""",(now(),record_id))
            audit(c,ws,"repo_agent.actions_rerun_confirmed",
                  "GitHub accepted rerun request; CI results not yet known",record_id)
            row=c.execute("SELECT * FROM repo_agent_pr_reruns WHERE id=?",
                          (record_id,)).fetchone()
            return {**_row_view(row),"deduplicated":False}
    except Exception:
        # A failed network response might still mean GitHub received request.
        # Never automatically retry the same run and potentially duplicate.
        with db() as c:
            c.execute("BEGIN IMMEDIATE")
            c.execute("""UPDATE repo_agent_pr_reruns SET status='needs_reconciliation',
               error=?,updated_at=? WHERE id=? AND status='requesting'""",
               ("GitHub outcome uncertain; inspect Actions run before any new request",
                now(),record_id))
            audit(c,ws,"repo_agent.actions_rerun_unknown",
                  "Unknown GitHub action; no automatic retry",record_id)
        raise


@router.get("/workspaces/{ws}/repo-agents/{agent_id}/repairs/{repair_id}/rerun-history")
def rerun_history(ws:str,agent_id:str,repair_id:str):
    repo=_agent(agent_id)
    prepare_db()
    with db() as c:
        if not workspace(c,ws):
            raise HTTPException(404,"Workspace không tồn tại")
        repair=_row(c,ws,agent_id,repair_id)
        if repair["repository"]!=repo:
            raise HTTPException(409,"Repository liên kết không hợp lệ")
        rows=c.execute("""SELECT * FROM repo_agent_pr_reruns WHERE
          workspace_id=? AND agent_id=? AND repair_id=?
          ORDER BY created_at DESC,rowid DESC LIMIT 20""",
          (ws,agent_id,repair_id)).fetchall()
        return {"history":[_row_view(item) for item in rows]}
