"""Fixed-role department tool runtime for 16 agents.

Every role has a dedicated Activepieces webhook slot in addition to the existing
specialist adapters. An operator installs/configures each workflow separately.
This module never downloads/executes third-party repositories, logs secrets,
assumes an env var proves connectivity, or treats a webhook ACK as completion.
"""
from __future__ import annotations

import os
import re
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

import httpx
from fastapi import APIRouter, HTTPException
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field

from core import ROLES, db, workspace
from agent_work_orders import PRIMARY_JOBS, JOBS, REPOS, prepare_db as prepare_work_orders

router = APIRouter(tags=["Department Tools"])

# Concrete actions already implemented in Tool Hub. The workflow bridge below
# is the fallback for roles whose domain service is not connected yet.
ROLE_ADAPTERS: dict[str, tuple[str,...]] = {
    "director": ("activepieces.trigger",),
    "research": ("crawl4ai.crawl",),
    "strategy": ("crawl4ai.crawl",),
    "lead": ("crawl4ai.crawl", "twenty.create_person"),
    "content": ("wordpress.create_post", "postiz.create_post", "comfyui.generate_image"),
    "ads": ("comfyui.generate_image", "activepieces.trigger"),
    "crm": ("twenty.create_person", "chatwoot.create_contact"),
    "sales": ("twenty.create_person", "activepieces.trigger"),
    "quality": ("crawl4ai.crawl",),
    "analytics": ("activepieces.trigger",),
    "operations": ("activepieces.trigger",),
    "finance": ("activepieces.trigger",),
    "hr": ("activepieces.trigger",),
    "procurement": ("crawl4ai.crawl", "activepieces.trigger"),
    "support": ("chatwoot.create_contact", "activepieces.trigger"),
    "compliance": ("crawl4ai.crawl", "activepieces.trigger"),
}

assert set(ROLE_ADAPTERS) == set(ROLES) == set(PRIMARY_JOBS)

# Fixed, non-user-selectable webhook destination per role. These are action
# identifiers, not a provider-side claim of actual work completion.
ACTIONS = {
    "department.run." + role: {
        "tool": "activepieces", "writes_external": True,
        "approval_required": True,
    } for role in ROLE_ADAPTERS
}
ROLE_RE = re.compile(r"^[a-z_]{2,40}$")


def env_name(role: str) -> str:
    if role not in ROLE_ADAPTERS:
        raise HTTPException(404, "Không có agent phòng ban này")
    return "LHM_DEPARTMENT_" + role.upper() + "_WEBHOOK_URL"


def _url(role: str) -> str:
    value = os.getenv(env_name(role), "").strip()
    if not value:
        raise ValueError("Chưa cấu hình Activepieces webhook dành riêng cho " + role)
    try:
        u = urlsplit(value)
        if not u.hostname or u.username or u.password or u.fragment or u.query:
            raise ValueError("Webhook URL không được có userinfo/query/fragment")
        if u.scheme != "https" and not (
                u.scheme == "http" and u.hostname.lower() in ("localhost", "127.0.0.1")):
            raise ValueError("Webhook bắt buộc HTTPS, trừ localhost")
        if u.port and (u.port < 1 or u.port > 65535):
            raise ValueError("Cổng webhook không hợp lệ")
        return value
    except ValueError as exc:
        raise ValueError("Webhook của agent " + role + " không hợp lệ: " + str(exc)) from exc


def configured(role: str) -> bool:
    try:
        _url(role)
        return True
    except ValueError:
        return False


def role_of_action(action: str) -> str:
    if action not in ACTIONS:
        raise HTTPException(422, "Tool phòng ban không được cấp phép")
    return action.removeprefix("department.run.")


def validate_payload(action: str, payload: dict[str,Any]) -> None:
    role = role_of_action(action)
    if not isinstance(payload,dict) or set(payload) != {
            "role", "work_order_id", "source_task_id", "content"}:
        raise HTTPException(422, "Payload workflow phải khớp chính xác contract công việc")
    if payload["role"] != role:
        raise HTTPException(403, "Tool không thuộc Agent được giao")
    for name in ("work_order_id", "source_task_id"):
        if not isinstance(payload[name],str) or not 8 <= len(payload[name]) <= 80:
            raise HTTPException(422, "ID nguồn công việc không hợp lệ")
    if not isinstance(payload["content"],str) or not 1 <= len(payload["content"]) <= 12000:
        raise HTTPException(422, "Nội dung Agent rỗng hoặc quá giới hạn 12.000 ký tự")


def assert_origin(c, ws: str, action: str, payload: dict[str,Any]) -> None:
    """Called again before approval AND immediately before any provider call."""
    validate_payload(action, payload)
    order = c.execute(
        "SELECT role,task_id FROM agent_work_orders WHERE id=? AND workspace_id=?",
        (payload["work_order_id"], ws)).fetchone()
    if not order or order["role"] != payload["role"] or order["task_id"] != payload["source_task_id"]:
        raise HTTPException(409, "Tool không thuộc đúng nhiệm vụ cố định của Agent")
    task = c.execute("SELECT role,status,result FROM tasks WHERE id=? AND workspace_id=?",
                     (order["task_id"], ws)).fetchone()
    if not task or task["role"] != order["role"] or task["status"] != "completed":
        raise HTTPException(409, "Nhiệm vụ Agent chưa hoàn tất")
    if task["result"] != payload["content"]:
        raise HTTPException(409, "Nguồn Agent đã thay đổi, cần tạo và duyệt lại")
    # Tool Hub's publication_gate.assert_source_approved independently checks
    # latest QA verdict and a SHA-256 digest of the exact published content.


async def dispatch(action: str, payload: dict[str,Any]) -> dict[str,Any]:
    validate_payload(action, payload)
    role = role_of_action(action)
    url = _url(role)
    # No caller-supplied headers/URL, no auth tokens in the AI-visible payload.
    async with httpx.AsyncClient(timeout=25, follow_redirects=False, trust_env=False) as client:
        response = await client.post(url, json={
            "source": "LHM-Agent-OS",
            "role": role,
            "work_order_id": payload["work_order_id"],
            "task_id": payload["source_task_id"],
            "content": payload["content"],
        },headers={"Content-Type": "application/json"})
        response.raise_for_status()
    return {
        "provider": "activepieces", "role": role,
        "response_status": response.status_code,
        "confirmation": "workflow_triggered_unverified",
        "note": "Webhook đã nhận yêu cầu, chưa chứng minh hành động đích hoàn tất. Xem log Flow và biên nhận nền tảng.",
    }


def role_toolbox(role: str) -> dict[str,Any]:
    if role not in ROLE_ADAPTERS:
        raise HTTPException(404,"Agent không tồn tại")
    from tool_hub import TOOL_ACTIONS, configured as hub_configured
    job = JOBS[PRIMARY_JOBS[role]]
    repo = REPOS[job["tool"]]
    adapters = []
    for action in ROLE_ADAPTERS[role]:
        meta = TOOL_ACTIONS[action]
        tool = meta["tool"]
        adapters.append({
            "action": action, "tool": tool,
            "source_repo": REPOS[tool]["repository"] if tool in REPOS else None,
            "adapter_built": True,
            "configured": hub_configured(tool),
            "connection_verified": False,
            "approval_required": True,
        })
    return {
        "role": role, "name": ROLES[role][0],
        "fixed_job": job["title"],
        "primary_tool": {"name": repo["name"], "source_repo": repo["repository"],
                         "catalog_status": repo["status"]},
        "existing_actions": adapters,
        "department_workflow": {
            "action": "department.run." + role,
            "provider_repo": "https://github.com/activepieces/activepieces",
            "configured": configured(role), "connection_verified": False,
            "requires_quality_review": True,
            "requires_human_approval": True,
            "receipt_type": "workflow_triggered_unverified",
        },
        "ready_for_external_work": configured(role),
        "note": "Configured chỉ là có endpoint hợp lệ, không phải đã test đăng/ghi thật",
    }


@router.get("/agents/departments")
def departments():
    return {"count": len(ROLE_ADAPTERS),
            "agents": [role_toolbox(r) for r in ROLES],
            "externally_verified": False,
            "mode": "approval_gated_role_specific_webhooks_plus_existing_tools"}


@router.get("/agents/departments/ui")
def department_ui():
    return FileResponse(Path(__file__).parent / "static" / "departments.html",
                        media_type="text/html")


class PrepareToolIn(BaseModel):
    work_order_id: str = Field(min_length=8, max_length=80)
    idempotency_key: str = Field(min_length=8,max_length=120)


@router.post("/workspaces/{ws}/agents/{role}/department-tool-jobs", status_code=201)
def prepare_department_tool(ws: str, role: str, inp: PrepareToolIn):
    """Bind a completed, QA-passed fixed mission to one immutable role Flow.

    This only creates a reviewable Tool Hub request, never sends the webhook.
    """
    if role not in ROLE_ADAPTERS:
        raise HTTPException(404,"Agent không tồn tại")
    from tool_hub import ToolJobIn, _pilot_ws, create_tool_job
    _pilot_ws(ws)
    prepare_work_orders()
    with db() as c:
        order = c.execute(
            "SELECT role,task_id FROM agent_work_orders WHERE id=? AND workspace_id=?",
            (inp.work_order_id,ws)).fetchone()
        if not order or order["role"] != role:
            raise HTTPException(404,"Không tìm thấy nhiệm vụ cố định của Agent")
        task = c.execute("SELECT status,result FROM tasks WHERE id=? AND workspace_id=?",
                         (order["task_id"],ws)).fetchone()
        if not task or task["status"] != "completed" or not task["result"]:
            raise HTTPException(409,"Agent chưa hoàn thành bản nháp")
        if len(task["result"]) > 12000:
            raise HTTPException(422,"Bản nháp vượt giới hạn của workflow")
        payload = {"role":role, "work_order_id":inp.work_order_id,
                   "source_task_id":order["task_id"],"content":task["result"]}
    # Tool Hub enforces QA for this source_task_id, then requires explicit
    # human approval before execution. It also binds approval to payload hash.
    record = create_tool_job(ws,ToolJobIn(
        action="department.run."+role, payload=payload,
        idempotency_key=inp.idempotency_key))
    return {**record, "provider_configured": configured(role),
            "external_action_completed": False}
