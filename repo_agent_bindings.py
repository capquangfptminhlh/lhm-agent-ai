"""Repository-bound CI agents. Real GitHub Actions reads; never imply a PR was created.

These project agents are deliberately separate from the 16 business specialists:
each project agent owns exactly one authorized repository and one CI-triage job.
A GitHub link or successful AI draft is not an authenticated GitHub write.
"""
from __future__ import annotations

import os
from pathlib import Path
from typing import Any

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


def agent_profile(agent_id: str) -> dict[str, str]:
    agent = PROJECT_AGENTS.get(agent_id)
    if not agent:
        raise HTTPException(404, "Agent dự án không tồn tại")
    return agent


@router.get("/repo-agents/ui")
def repository_agent_ui():
    return FileResponse(Path(__file__).parent / "static" / "repo_agents.html", media_type="text/html")


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
        "status_note": "Liên kết repo không chứng minh đã đăng nhập. CI được đọc live; thay đổi code/PR chưa được tự động hóa.",
    }


async def check_ci(agent_id: str) -> dict[str, Any]:
    agent = agent_profile(agent_id)
    repo = agent["repository"]
    token = os.environ.get("LHM_GITHUB_READ_TOKEN", "").strip()
    headers = {"Accept": "application/vnd.github+json",
               "X-GitHub-Api-Version": "2022-11-28",
               "User-Agent": "LHM-Repo-Agent-CI"}
    if token:
        headers["Authorization"] = "Bearer " + token
    url = "https://api.github.com/repos/" + repo + "/actions/runs"
    try:
        async with httpx.AsyncClient(timeout=10.0, follow_redirects=False) as client:
            response = await client.get(url, params={"per_page": 5}, headers=headers)
    except httpx.HTTPError:
        raise HTTPException(503, "Không kết nối được GitHub API; không thể xác minh CI")
    if response.status_code in (401, 403, 404):
        raise HTTPException(424, "Không đọc được CI: cần GitHub token read-only hoặc đã bị giới hạn API")
    if response.status_code != 200:
        raise HTTPException(502, "GitHub API trả về lỗi " + str(response.status_code))
    try:
        payload = response.json()
        runs = payload["workflow_runs"]
        if not isinstance(runs, list):
            raise ValueError("workflow_runs is not a list")
    except (ValueError, TypeError, KeyError):
        raise HTTPException(502, "Phản hồi GitHub API không hợp lệ")
    results = []
    for run in runs[:5]:
        if not isinstance(run, dict) or not isinstance(run.get("id"), int):
            continue
        # Derive the URL from the verified repository and numeric run ID.
        # Never reflect untrusted GitHub-provided URLs into downstream tools.
        results.append({
            "id": run["id"], "name": str(run.get("name") or "")[:160],
            "status": str(run.get("status") or "unknown")[:32],
            "conclusion": str(run.get("conclusion") or "pending")[:32],
            "head_sha": str(run.get("head_sha") or "")[:40],
            "html_url": "https://github.com/" + repo + "/actions/runs/" + str(run["id"])})
    return {
        "agent_id": agent_id, "repository": repo, "job": agent["job"],
        "connection_verified": True, "read_only": True,
        "runs": results, "can_create_pr": False,
        "next_step": "Tạo báo cáo chẩn đoán có nguồn, chưa tự sửa code hay tạo PR",
    }


@router.get("/repo-agents/{agent_id}/ci")
async def get_repository_ci(agent_id: str):
    return await check_ci(agent_id)


@router.post("/repo-agents/{agent_id}/diagnose", status_code=201)
async def create_repository_diagnosis(agent_id: str, inp: RepoDiagnosisIn):
    """Perform a live CI read then enqueue one fixed-purpose AI triage task.

    Queueing is real (SQLite + worker), but outputs are drafts and do not
    grant the model GitHub write permissions or execute suggested fixes.
    """
    ci = await check_ci(agent_id)
    if not ci["runs"]:
        raise HTTPException(409, "Repo chưa có GitHub Actions run để chẩn đoán")
    recent = ci["runs"][0]
    agent = agent_profile(agent_id)
    from agent_work_orders import WorkOrderIn, create_order
    from core import workspace, db
    with db() as c:
        if not workspace(c, inp.workspace):
            raise HTTPException(404, "Workspace không tồn tại")
    objective = ("Chẩn đoán CI theo nghề cố định: " + agent["job"] +
                 ". Chỉ dùng bằng chứng, phân biệt lỗi thực tế và giả thuyết; "
                 "đưa ra phương án sửa và test để người phụ trách duyệt.")
    evidence = (
        "Repository sở hữu (read-only): " + ci["repository"] +
        "\\nCI run id: " + str(recent["id"]) +
        "\\nCI status: " + recent["status"] +
        "\\nCI conclusion: " + recent["conclusion"] +
        "\\nSHA: " + recent["head_sha"] +
        "\\nURL kiểm chứng: " + recent["html_url"] +
        "\\nKhông tuyên bố đã đọc log nếu chưa lấy log; không tuyên bố đã sửa code.")
    request = WorkOrderIn(objective=objective, context=evidence,
                          idempotency_key="repo-ci-" + agent_id + "-" + str(recent["id"]))
    result = create_order(inp.workspace, agent["role"], request)
    return {**result, "repository": ci["repository"],
            "ci_evidence_url": recent["html_url"],
            "code_changed": False, "pull_request_created": False}
