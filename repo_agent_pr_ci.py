"""Read-only GitHub verification of Agent-created Draft PR checks.

Never infer a repaired, merged, deployed or production-ready application from
a draft or an AI report. The only trustworthy inputs here are a locally bound
repo/PR and current GitHub check/status APIs. No GitHub writes occur.
"""
from __future__ import annotations

import re
from typing import Any

import httpx
from fastapi import APIRouter, HTTPException

from core import db, workspace
from repo_agent_repair import _agent, _api, _github_headers, _row, prepare_db

router = APIRouter(tags=["Repository Agent PR CI"])
_SHA = re.compile(r"^[0-9a-f]{40}$")
_FAILURES = {"failure", "timed_out", "cancelled", "action_required",
             "startup_failure", "stale"}
_COMPLETE_PASS = {"success"}


async def _get(client: httpx.AsyncClient, url: str, **kwargs) -> dict[str,Any]:
    try:
        reply=await client.get(url,headers=_github_headers(),**kwargs)
    except httpx.HTTPError:
        raise HTTPException(503,"Không thể xác minh GitHub API; không suy diễn PR đã PASS")
    if reply.status_code in (401,403,404):
        raise HTTPException(424,"GitHub không cho phép kiểm tra PR hoặc check-runs")
    if reply.status_code!=200:
        raise HTTPException(502,"GitHub trả lỗi khi xác minh PR (HTTP "+str(reply.status_code)+")")
    try:
        data=reply.json()
    except (ValueError,TypeError):
        raise HTTPException(502,"GitHub trả nội dung JSON không hợp lệ")
    if not isinstance(data,dict):
        raise HTTPException(502,"GitHub trả đối tượng API không hợp lệ")
    return data


@router.get("/workspaces/{ws}/repo-agents/{agent_id}/repairs/{repair_id}/ci")
async def verify_repair_pr_ci(ws: str,agent_id: str,repair_id: str):
    repo=_agent(agent_id)
    prepare_db()
    with db() as c:
        if not workspace(c,ws):
            raise HTTPException(404,"Workspace không tồn tại")
        repair=_row(c,ws,agent_id,repair_id)
        if repair["status"]!="pr_created" or type(repair["pr_number"]) is not int:
            raise HTTPException(409,"Chưa có Draft PR đã xác minh để đọc CI")
        if repair["repository"]!=repo or not repair["branch"]:
            raise HTTPException(409,"Liên kết repo hoặc branch không hợp lệ")
        number=repair["pr_number"]
        owned_branch=repair["branch"]

    async with httpx.AsyncClient(timeout=15,follow_redirects=False,
                                 trust_env=False) as client:
        pull=await _get(client,_api(repo,"pulls/"+str(number)))
        head=pull.get("head")
        base=pull.get("base")
        head=head if isinstance(head,dict) else {}
        base=base if isinstance(base,dict) else {}
        sha=head.get("sha")
        head_repo=head.get("repo")
        head_repo=head_repo if isinstance(head_repo,dict) else {}
        base_repo=base.get("repo")
        base_repo=base_repo if isinstance(base_repo,dict) else {}
        if (type(pull.get("number")) is not int or pull["number"]!=number
            or head.get("ref")!=owned_branch or head_repo.get("full_name")!=repo
            or base.get("ref")!="main" or base_repo.get("full_name")!=repo
            or not isinstance(sha,str) or not _SHA.fullmatch(sha)):
            raise HTTPException(409,"GitHub PR đã đổi repo/nhánh hoặc trả dữ liệu không khớp")
        checks=await _get(client,_api(repo,"commits/"+sha+"/check-runs"),
                          params={"per_page":100})
        combined=await _get(client,_api(repo,"commits/"+sha+"/status"),
                            params={"per_page":100})
    check_runs=checks.get("check_runs")
    statuses=combined.get("statuses")
    total=checks.get("total_count")
    if (not isinstance(check_runs,list) or not isinstance(statuses,list)
        or type(total) is not int or total<0):
        raise HTTPException(502,"GitHub không cung cấp dữ liệu CI đầy đủ")
    # Only evaluate bounded well-formed records; bad fields never mean PASS.
    invalid=False
    observed=[]
    for check in check_runs[:100]:
        if not isinstance(check,dict) or not isinstance(check.get("name"),str):
            invalid=True
            continue
        status=check.get("status")
        conclusion=check.get("conclusion")
        if status not in ("completed","in_progress","queued","pending","requested",
                          "waiting") or (conclusion is not None and
                                         not isinstance(conclusion,str)):
            invalid=True
        observed.append({"name":check["name"][:140],
                         "status":str(status or "unknown")[:30],
                         "conclusion":conclusion[:30] if isinstance(conclusion,str) else None})
    commit_statuses=[]
    for item in statuses[:100]:
        if not isinstance(item,dict) or not isinstance(item.get("context"),str):
            invalid=True
            continue
        state=item.get("state")
        if state not in ("success","pending","failure","error"):
            invalid=True
        commit_statuses.append({"context":item["context"][:140],
                                "state":str(state or "unknown")[:30]})
    checks_complete=(total==len(check_runs) and len(check_runs)<100)
    statuses_complete=len(statuses)<100  # GitHub's REST pagination is bounded.
    has_failure=any(item["conclusion"] in _FAILURES for item in observed)
    has_failure=has_failure or any(item["state"] in ("failure","error")
                                  for item in commit_statuses)
    has_pending=any(item["status"]!="completed" for item in observed)
    has_pending=has_pending or any(item["state"]=="pending" for item in commit_statuses)
    all_pass=(bool(observed or commit_statuses)
              and all(item["status"]=="completed" and
                      item["conclusion"] in _COMPLETE_PASS for item in observed)
              and all(item["state"]=="success" for item in commit_statuses))
    coverage=checks_complete and statuses_complete and not invalid
    open_pr=pull.get("state")=="open" and pull.get("merged_at") is None
    if not open_pr:
        verdict="pr_not_open"
    elif has_failure:
        verdict="failed_observed"
    elif has_pending:
        verdict="checks_running"
    elif coverage and all_pass:
        verdict="observed_checks_green"
    else:
        verdict="unverified"
    return {
        "repair_id":repair_id,"agent_id":agent_id,"repository":repo,
        "pr_number":number,
        "pr_url":"https://github.com/"+repo+"/pull/"+str(number),
        "pr_state":str(pull.get("state") or "unknown")[:20],
        "draft":pull.get("draft") is True,
        "head_sha":sha,"base_branch":"main","head_branch":owned_branch,
        "check_runs":observed,"commit_statuses":commit_statuses,
        "check_runs_total":total,
        "check_runs_complete":checks_complete,
        "commit_statuses_complete":statuses_complete,
        "verdict":verdict,
        "checked_from_github":True,"read_only":True,
        "required_checks_verified":False,
        "review_approval_verified":False,
        "automatically_merged":False,"deployed_verified":False,
        "notice":("Observed checks only; branch protection, required checks, "
                  "human reviews and production deployment are NOT verified. "
                  "Do not merge until repository acceptance criteria are met."),
    }
