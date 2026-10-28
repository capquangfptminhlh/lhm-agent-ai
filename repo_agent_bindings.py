"""Repository-bound CI agents. Real GitHub Actions reads; never imply a PR was created.

These project agents are deliberately separate from the 16 business specialists:
each project agent owns exactly one authorized repository and one CI-triage job.
A GitHub link or successful AI draft is not an authenticated GitHub write.
"""
from __future__ import annotations

import os
from pathlib import Path
from typing import Any

from core import audit, db, new_id, now, workspace

import httpx
from fastapi import APIRouter, HTTPException
from fastapi.responses import FileResponse
from pydantic import BaseModel

router = APIRouter(tags=["Repository Agents"])

# Fixed inventory: repositories verified to be owned by capquangfptminhlh.
# No caller-controlled URL, repo name, GitHub API host, or write operation.
PROJECT_AGENTS: dict[str, dict[str, str]] = {
    "agent-platform": {
        "repository": "capquangfptminhlh/lhm-agent-ai",
        "role": "operations", "job": "Kiểm tra CI và độ ổn định nền tảng Agent"},
    "agent-seo-crm": {
        "repository": "capquangfptminhlh/crmminh",
        "role": "content", "job": "Kiểm tra pipeline SEO LHMCRM"},
    "agent-autotax": {
        "repository": "capquangfptminhlh/autotax",
        "role": "quality", "job": "Kiểm tra các quality gate AutoTax POS"},
    "agent-browser": {
        "repository": "capquangfptminhlh/ggminh",
        "role": "operations", "job": "Kiểm tra build và thử nghiệm LHM Browser"},
    "agent-ads": {
        "repository": "capquangfptminhlh/lhm-ads-ai",
        "role": "ads", "job": "Kiểm tra CI ứng dụng LHM Ads AI"},
    "agent-pickle": {
        "repository": "capquangfptminhlh/pickle",
        "role": "quality", "job": "Kiểm tra CI ứng dụng Pickleup"},
    "agent-cnlg": {
        "repository": "capquangfptminhlh/cnlg",
        "role": "quality", "job": "Kiểm tra CI website CNLG"},
    "agent-english-game": {
        "repository": "capquangfptminhlh/tienganh",
        "role": "quality", "job": "Kiểm tra CI game tiếng Anh"},
    "agent-zalo-crm": {
        "repository": "capquangfptminhlh/zalo-crm-frontend",
        "role": "quality", "job": "Kiểm tra CI giao diện Zalo CRM"},
}

class RepoDiagnosisIn(BaseModel):
    workspace: str = "demo"


def prepare_db() -> None:
    """One immutable repository/run binding for every diagnosis task."""
    with db() as c:
        c.execute("""CREATE TABLE IF NOT EXISTS repo_agent_diagnoses (
            id TEXT PRIMARY KEY,
            workspace_id TEXT NOT NULL REFERENCES workspaces(id),
            agent_id TEXT NOT NULL,
            repository TEXT NOT NULL,
            run_id INTEGER NOT NULL,
            task_id TEXT NOT NULL UNIQUE REFERENCES tasks(id),
            created_at INTEGER NOT NULL,
            UNIQUE(workspace_id,agent_id,run_id)
        )""")
        c.execute("CREATE INDEX IF NOT EXISTS ix_repo_agent_diagnoses_ws"
                  " ON repo_agent_diagnoses(workspace_id,agent_id,created_at)")


def agent_profile(agent_id: str) -> dict[str, str]:
    agent = PROJECT_AGENTS.get(agent_id)
    if not agent:
        raise HTTPException(404, "Agent dự án không tồn tại")
    return agent


@router.get("/repo-agents/ui")
def repository_agent_ui():
    return FileResponse(Path(__file__).parent / "static" / "repo_agents.html",
                        media_type="text/html")


@router.get("/repo-agents")
def list_repository_agents() -> dict[str, Any]:
    return {
        "agents": [
            {"id": agent_id, **item,
             "repository_url": "https://github.com/" + item["repository"],
             "job_type": "github_actions_ci_triage",
             "live_check_url": "/repo-agents/" + agent_id + "/ci",
             "writes_enabled": False}
            for agent_id, item in PROJECT_AGENTS.items()],
        "status_note": ("Mỗi Agent chỉ chẩn đoán CI một repo cố định. "
                        "Không suy luận đã sửa code hay triển khai chỉ từ báo cáo AI."),
    }


def _github_headers() -> dict[str,str]:
    token=os.environ.get("LHM_GITHUB_READ_TOKEN","").strip()
    headers={"Accept":"application/vnd.github+json",
             "X-GitHub-Api-Version":"2022-11-28",
             "User-Agent":"LHM-Repo-Agent-CI"}
    if token:
        headers["Authorization"]="Bearer "+token
    return headers


async def check_ci(agent_id: str) -> dict[str, Any]:
    """Read actual workflow runs and, where permitted, failing job/step evidence.

    Repository and URL path are fixed by server-owned inventory, never supplied
    by the user or the LLM. Do not claim checks passed from missing job details.
    """
    agent=agent_profile(agent_id)
    repo=agent["repository"]
    url="https://api.github.com/repos/"+repo+"/actions/runs"
    try:
        async with httpx.AsyncClient(timeout=12.0,follow_redirects=False,
                                     trust_env=False) as client:
            response=await client.get(url,params={"per_page":10},
                                      headers=_github_headers())
            if response.status_code in (401,403,404):
                raise HTTPException(424,"Không đọc được CI: cần GitHub token read-only hoặc đã bị giới hạn API")
            if response.status_code!=200:
                raise HTTPException(502,"GitHub API trả về lỗi "+str(response.status_code))
            try:
                payload=response.json()
                runs=payload["workflow_runs"]
                if not isinstance(runs,list):
                    raise ValueError("workflow_runs is not a list")
            except (ValueError,TypeError,KeyError):
                raise HTTPException(502,"Phản hồi GitHub API không hợp lệ")
            results=[]
            for run in runs[:10]:
                if (not isinstance(run,dict) or type(run.get("id")) is not int
                        or run["id"]<=0):
                    continue
                branch=run.get("head_branch")
                results.append({
                    "id":run["id"],"name":str(run.get("name") or "")[:160],
                    "status":str(run.get("status") or "unknown")[:32],
                    "conclusion":str(run.get("conclusion") or "pending")[:32],
                    "head_branch":str(branch)[:120] if isinstance(branch,str) else None,
                    "head_sha":str(run.get("head_sha") or "")[:40],
                    "html_url":"https://github.com/"+repo+"/actions/runs/"+str(run["id"])})
            # Job summaries are a distinct GitHub API read. Failure to fetch
            # them is surfaced as unavailable, not passed or empty evidence.
            failures=[]
            details_verified=False
            checked_run=results[0] if results else None
            if checked_run:
                jobs_url=url+"/"+str(checked_run["id"])+"/jobs"
                try:
                    jobs_reply=await client.get(jobs_url,params={"per_page":100},
                                                headers=_github_headers())
                    if jobs_reply.status_code==200:
                        jobs_payload=jobs_reply.json()
                        jobs=jobs_payload["jobs"]
                        if not isinstance(jobs,list):
                            raise ValueError("jobs invalid")
                        details_verified=True
                        for job in jobs[:100]:
                            if not isinstance(job,dict):
                                continue
                            if job.get("conclusion") not in ("failure","timed_out",
                                                               "action_required","startup_failure"):
                                continue
                            steps=job.get("steps")
                            bad_steps=[str(step.get("name") or "")[:150]
                                       for step in (steps if isinstance(steps,list) else [])[:100]
                                       if isinstance(step,dict)
                                       and step.get("conclusion") in ("failure","timed_out")]
                            job_id=job.get("id")
                            failures.append({
                                "job":str(job.get("name") or "")[:150],
                                "conclusion":job["conclusion"],
                                "failed_steps":bad_steps[:8],
                                "html_url":("https://github.com/"+repo+
                                            "/actions/runs/"+str(checked_run["id"])+
                                            "/job/"+str(job_id))
                                            if type(job_id) is int and job_id>0 else None})
                except (httpx.HTTPError,ValueError,TypeError,KeyError):
                    details_verified=False
                    failures=[]
    except httpx.HTTPError:
        raise HTTPException(503,"Không kết nối được GitHub API; không thể xác minh CI")
    return {
        "agent_id":agent_id,"repository":repo,"job":agent["job"],
        "connection_verified":True,"read_only":True,"runs":results,
        "job_details_verified":details_verified,
        "failed_jobs":failures[:25],"checked_run_id":checked_run["id"] if checked_run else None,
        "can_create_pr":False,"code_changed":False,
        "next_step":"Chẩn đoán từ nguồn CI đã đọc; không coi đây là PR hay bản sửa code.",
    }


@router.get("/repo-agents/{agent_id}/ci")
async def get_repository_ci(agent_id: str):
    return await check_ci(agent_id)


def _view_diagnosis(c, row) -> dict[str,Any]:
    from openrouter_provider import model_configured
    task=c.execute("""SELECT status,model,result,error,attempts FROM tasks
                      WHERE id=? AND workspace_id=?""",
                   (row["task_id"],row["workspace_id"])).fetchone()
    if not task:
        raise HTTPException(409,"Tác vụ chẩn đoán đã mất; cần quản trị viên kiểm tra")
    hb=c.execute("SELECT updated_at FROM worker_heartbeats WHERE id='default'").fetchone()
    live=bool(hb and now()-int(hb["updated_at"])<45)
    model_ready=model_configured(task["model"])
    agent=agent_profile(row["agent_id"])
    return {
        "id":row["id"],"workspace_id":row["workspace_id"],
        "agent_id":row["agent_id"],"repository":row["repository"],
        "fixed_job":{"title":agent["job"],"type":"github_actions_ci_triage"},
        "run_id":row["run_id"],"role":agent["role"],"task_id":row["task_id"],
        "task_status":task["status"],"attempts":task["attempts"],
        "report":task["result"] if task["status"]=="completed" else None,
        "error":task["error"] if task["status"] in
                 ("failed","blocked_budget","blocked_dependency") else None,
        "readiness":{"api_key_configured":model_ready,"worker_recent_heartbeat":live},
        "created_at":row["created_at"],
        "code_changed":False,"pull_request_created":False,
        "verified_live":False,"report_is_draft":task["status"]=="completed",
    }


@router.get("/workspaces/{ws}/repo-agents/{agent_id}/diagnoses")
def list_repository_diagnoses(ws: str, agent_id: str):
    agent_profile(agent_id)
    prepare_db()
    with db() as c:
        if not workspace(c,ws):
            raise HTTPException(404,"Workspace không tồn tại")
        rows=c.execute("""SELECT * FROM repo_agent_diagnoses
            WHERE workspace_id=? AND agent_id=? ORDER BY created_at DESC,rowid DESC LIMIT 20""",
            (ws,agent_id)).fetchall()
        return {"diagnoses":[_view_diagnosis(c,row) for row in rows]}


@router.get("/workspaces/{ws}/repo-agents/{agent_id}/diagnoses/{diagnosis_id}")
def get_repository_diagnosis(ws: str,agent_id: str,diagnosis_id: str):
    agent_profile(agent_id)
    prepare_db()
    with db() as c:
        row=c.execute("""SELECT * FROM repo_agent_diagnoses
                         WHERE id=? AND workspace_id=? AND agent_id=?""",
                      (diagnosis_id,ws,agent_id)).fetchone()
        if not row:
            raise HTTPException(404,"Không tìm thấy báo cáo trong Agent và Workspace này")
        return _view_diagnosis(c,row)


@router.post("/repo-agents/{agent_id}/diagnose",status_code=201)
async def create_repository_diagnosis(agent_id: str,inp: RepoDiagnosisIn):
    """Live evidence -> fixed-purpose CI task -> worker -> observable report.

    This is NOT a generic Content/Quality work order. The repo-specific mission,
    source SHA and run are fixed and audited; no GitHub write tool is enabled.
    """
    ci=await check_ci(agent_id)
    if not ci["runs"]:
        raise HTTPException(409,"Repo chưa có GitHub Actions run để chẩn đoán")
    recent=ci["runs"][0]
    agent=agent_profile(agent_id)
    from app import task_record
    from core import MODEL
    from openrouter_provider import validate_model
    model=("openrouter/free" if os.environ.get("LHM_OPENROUTER_FREE_ONLY")=="1"
           else MODEL)
    validate_model(model)
    evidence=(
        "Repo (read-only): "+ci["repository"]+
        "\nWorkflow run ID: "+str(recent["id"])+
        "\nWorkflow: "+recent["name"]+
        "\nBranch: "+str(recent.get("head_branch") or "unknown")+
        "\nCI status: "+recent["status"]+
        "\nCI conclusion: "+recent["conclusion"]+
        "\nCommit SHA: "+recent["head_sha"]+
        "\nURL: "+recent["html_url"]+
        "\nJob details verified: "+str(ci.get("job_details_verified") is True)+
        "\nFailing jobs/steps (if independently verified): "+
            __import__("json").dumps(ci.get("failed_jobs",[]),ensure_ascii=False)[:1600]+
        "\nIMPORTANT: absence of job details is NOT successful testing; "
        "no full logs were fetched. No code or pull requests were changed."
    )[:2900]
    instruction=(
        "BẠN LÀ AGENT CHẨN ĐOÁN CI CỐ ĐỊNH của duy nhất repo "+
        agent["repository"]+". NHIỆM VỤ DUY NHẤT: "+agent["job"]+". "
        "Chỉ phân tích bằng chứng GitHub Actions đính kèm; không được bịa log, "
        "nội dung commit, lỗi hay tác động bên ngoài. "
        "Trả về báo cáo tiếng Việt gồm: (1) run/branch/SHA và tình trạng thực, "
        "(2) job/step lỗi có bằng chứng, phân biệt thiếu dữ liệu, "
        "(3) giả thuyết nguyên nhân kèm độ tin cậy, "
        "(4) đề xuất bản vá có giới hạn và các test cần chạy, "
        "(5) điều kiện nghiệm thu còn thiếu. "
        "Chỉ là báo cáo đề xuất; KHÔNG có quyền ghi repo, merge, release, "
        "truy cập token hoặc tự tuyên bố đã sửa lỗi."
    )
    prepare_db()
    with db() as c:
        c.execute("BEGIN IMMEDIATE")
        if not workspace(c,inp.workspace):
            raise HTTPException(404,"Workspace không tồn tại")
        old=c.execute("""SELECT * FROM repo_agent_diagnoses
            WHERE workspace_id=? AND agent_id=? AND run_id=?""",
            (inp.workspace,agent_id,recent["id"])).fetchone()
        if old:
            return {**_view_diagnosis(c,old),"deduplicated":True}
        task=task_record(c,inp.workspace,agent["role"],instruction,evidence,
                         model=model,idem="repo-ci:"+agent_id+":"+str(recent["id"]))
        record_id=new_id()
        c.execute("""INSERT INTO repo_agent_diagnoses
          (id,workspace_id,agent_id,repository,run_id,task_id,created_at)
          VALUES(?,?,?,?,?,?,?)""",
          (record_id,inp.workspace,agent_id,agent["repository"],
           recent["id"],task["id"],now()))
        audit(c,inp.workspace,"repo_agent.ci_triage_queued",
              agent_id+" run="+str(recent["id"])+" read-only CI triage",task["id"])
        row=c.execute("SELECT * FROM repo_agent_diagnoses WHERE id=?",
                      (record_id,)).fetchone()
        return {**_view_diagnosis(c,row),"deduplicated":False,
                "ci_evidence_url":recent["html_url"]}
