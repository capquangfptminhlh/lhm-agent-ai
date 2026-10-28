"""Shared Computer Agent pilot: immutable Work Order bindings, role RBAC, and API-first social handoff.

Only Content, Sales and Quality are admitted to this pilot. The existing
Computer runner stays shared; this is logical role scoping, NOT a security
boundary between tenants or a proven live social/CRM deployment.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

from fastapi import APIRouter, HTTPException
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field

from core import audit, db, now
from agent_work_orders import prepare_db as prepare_orders
from computer_agent import ComputerRunIn, create_computer_run

router = APIRouter(tags=["Agent Runtime Pilot"])

PILOT_TOOLS = {
    # Browser writes are still always manually approved by Computer Agent.
    "content": frozenset({"list_files","read_file","browser_open","browser_snapshot",
                           "browser_scroll","browser_screenshot","browser_click","browser_fill"}),
    "sales": frozenset({"list_files","read_file","browser_open","browser_snapshot",
                         "browser_scroll","browser_screenshot"}),
    "quality": frozenset({"list_files","read_file","browser_open","browser_snapshot",
                           "browser_scroll","browser_screenshot"}),
}
PILOT_HOST_ENV = {role: "LHM_COMPUTER_"+role.upper()+"_ALLOWED_HOSTS" for role in PILOT_TOOLS}
PILOT_RETRIES = 2  # read-only observation retries only; NEVER resend a post on ambiguity


def prepare_db() -> None:
    prepare_orders()
    with db() as c:
        c.execute("""CREATE TABLE IF NOT EXISTS pilot_computer_runs(
            work_order_id TEXT PRIMARY KEY REFERENCES agent_work_orders(id),
            computer_run_id TEXT NOT NULL UNIQUE REFERENCES computer_runs(id),
            workspace_id TEXT NOT NULL, role TEXT NOT NULL, created_at INTEGER NOT NULL
        )""")
        c.execute("CREATE INDEX IF NOT EXISTS ix_pilot_runs_workspace ON pilot_computer_runs(workspace_id)")


def allowed_hosts(role: str) -> set[str]:
    if role not in PILOT_TOOLS:
        return set()
    return {x.strip().lower().rstrip(".") for x in os.getenv(PILOT_HOST_ENV[role],"").split(",")
            if x.strip()}


def _origin_allowed(role: str, url: str) -> bool:
    try:
        u=urlsplit(url)
        return (u.scheme=="https" and u.hostname is not None
                and u.hostname.rstrip(".").lower() in allowed_hosts(role)
                and u.port in (None,443) and not u.username and not u.password)
    except (ValueError,TypeError):
        return False


def profile(role: str) -> dict[str,Any]:
    if role not in PILOT_TOOLS:
        raise HTTPException(404,"Agent chưa tham gia thử nghiệm")
    return {"role":role,"tools":sorted(PILOT_TOOLS[role]),
            "memory_scope":"workspace/role","browser_profile_scope":"workspace/role",
            "browser_host_allowlist_configured":bool(allowed_hosts(role)),
            "external_credential_access":False,
            "api_actions_via_reviewed_tool_hub":True,
            "computer_mutations_need_approval":True,
            "observations_retry_limit":PILOT_RETRIES,
            "verified_live":False}


@router.get("/agents/runtime-pilot/ui")
def runtime_pilot_page():
    return FileResponse(Path(__file__).parent/"static"/"agent_pilot.html",media_type="text/html")


@router.get("/agents/runtime-pilot")
def runtime_pilot_profiles():
    return {"roles":[profile(x) for x in PILOT_TOOLS],
            "shared_computer_runtime":True,
            "tenant_isolation_proven":False,
            "verified_live":False,
            "priority":["approved_api","supervised_browser","supervised_desktop"]}


class StartComputerIn(BaseModel):
    max_steps: int = Field(default=10,ge=1,le=15)


@router.post("/workspaces/{ws}/agent-work-orders/{order_id}/computer-pilot",status_code=201)
def start_computer_pilot(ws: str,order_id: str,inp: StartComputerIn):
    """Inject one immutable Work Order into the shared Computer with a role tool policy."""
    prepare_db()
    from tool_hub import _pilot_ws
    _pilot_ws(ws)
    with db() as c:
        order=c.execute("SELECT * FROM agent_work_orders WHERE workspace_id=? AND id=?",
                        (ws,order_id)).fetchone()
        if not order:raise HTTPException(404,"Work Order không tồn tại")
        role=order["role"]
        if role not in PILOT_TOOLS:raise HTTPException(403,"Role này chưa được mở Computer Pilot")
        if not allowed_hosts(role):
            raise HTTPException(409,"Phải cấu hình whitelist host riêng cho "+role+" trước khi dùng browser")
        existing=c.execute("SELECT computer_run_id FROM pilot_computer_runs WHERE work_order_id=?",
                           (order_id,)).fetchone()
        if existing:
            from computer_agent import _view, _get
            return {**_view(c,_get(c,existing["computer_run_id"])),
                    "work_order_id":order_id,"deduplicated":True}
        task=c.execute("SELECT status,result FROM tasks WHERE id=? AND workspace_id=?",
                       (order["task_id"],ws)).fetchone()
        if task["status"] not in ("completed","queued","awaiting_approval"):
            raise HTTPException(409,"Công việc đang ở trạng thái không thể mở Computer")
    # Computer plan contains only work-order objective and policy, never account credentials.
    objective=("Work Order "+order_id+" | Agent "+role+
      ". Làm nhiệm vụ trong phạm vi chuyên môn: "+order["objective"]+
      ". Quan sát/thao tác trên đúng website được cấp quyền; không đăng, gửi tin,"
      " sửa dữ liệu hoặc thanh toán trước khi người quản trị phê duyệt."
      " Chỉ báo xong khi có kết quả công cụ thực, nêu rõ điều chưa xác minh.")
    data=create_computer_run(ws,ComputerRunIn(
        role=role,objective=objective[:2500],max_steps=inp.max_steps,mode="supervised",
        idempotency_key="fixed-computer:"+order_id))
    with db() as c:
        c.execute("BEGIN IMMEDIATE")
        old=c.execute("SELECT computer_run_id FROM pilot_computer_runs WHERE work_order_id=?",
                      (order_id,)).fetchone()
        if old and old["computer_run_id"] != data["id"]:
            raise HTTPException(409,"Work Order đã có Computer Run khác; cần đối soát")
        if not old:
            c.execute("""INSERT INTO pilot_computer_runs
              (work_order_id,computer_run_id,workspace_id,role,created_at)
              VALUES (?,?,?,?,?)""",(order_id,data["id"],ws,role,now()))
            audit(c,ws,"pilot.computer_bound","Fixed Work Order → shared Computer "+role,data["id"])
    return {**data,"work_order_id":order_id}


def pilot_role_for_run(run_id: str) -> str | None:
    """Never trust caller-supplied role if a Computer Run is bound to Work Order."""
    prepare_db()
    with db() as c:
        row=c.execute("SELECT role FROM pilot_computer_runs WHERE computer_run_id=?",
                      (run_id,)).fetchone()
    return row["role"] if row else None


def assert_pilot_action(run: dict, tool: str, args: dict[str,Any],
                        previous: list[dict[str,Any]] | None = None) -> None:
    role=pilot_role_for_run(run["id"])
    if role is None:
        return  # legacy Computer runs retain their existing supervision gates
    if run["role"]!=role or tool not in PILOT_TOOLS[role]:
        raise HTTPException(403,"Computer tool nằm ngoài quyền của Agent "+role)
    if tool=="browser_open" and not _origin_allowed(role,args.get("url","")):
        raise HTTPException(403,"URL nằm ngoài whitelist host riêng của Agent "+role)
    if tool.startswith("windows_") or tool in ("run_command","sandbox_run","write_file","edit_file"):
        raise HTTPException(403,"Computer Pilot không cho thao tác máy chủ hoặc ghi mã")
    # One-shot browser writes: same click/fill must not be silently repeated.
    # Read-only failures get at most two matching retries, then escalate.
    if previous is not None:
        matching=[step for step in previous
                  if step.get("tool")==tool and step.get("args")==args]
        if tool in {"browser_click","browser_fill"} and any(
                step.get("status") in {"completed","failed","needs_review"} for step in matching):
            raise HTTPException(409,"Trùng thao tác browser có thể đã gửi dữ liệu; cần đối soát")
        read_failures=sum(step.get("status") in {"failed","needs_review"}
                          for step in matching)
        if read_failures>=PILOT_RETRIES:
            raise HTTPException(409,"Vượt giới hạn retry quan sát; cần người xử lý")


class SocialHandoffIn(BaseModel):
    channel_ids: list[str] = Field(min_length=1,max_length=10)
    mode: str = Field(default="draft",pattern=r"^(draft|now|schedule)$")
    scheduled_at: str | None = None
    media: list[dict[str,Any]] = Field(default_factory=list,max_length=12)
    idempotency_key: str = Field(min_length=8,max_length=80)


@router.post("/workspaces/{ws}/agent-work-orders/{order_id}/social-handoff",status_code=201)
def social_handoff(ws: str,order_id: str,inp: SocialHandoffIn):
    """API-first. Content draft must pass independent QA, then OWNER approves Tool Hub job."""
    from tool_hub import _pilot_ws, create_from_agent, FromAgentIn
    _pilot_ws(ws)
    prepare_db()
    permitted={s.strip() for s in os.getenv("LHM_CONTENT_POSTIZ_ALLOWED_CHANNEL_IDS","").split(",")
               if s.strip()}
    if not permitted:
        raise HTTPException(409,"Chưa cấu hình whitelist kênh Postiz cho Content")
    if len(set(inp.channel_ids))!=len(inp.channel_ids) or set(inp.channel_ids)-permitted:
        raise HTTPException(403,"Content không được đăng trên một hoặc nhiều kênh này")
    with db() as c:
        order=c.execute("SELECT role,task_id FROM agent_work_orders WHERE workspace_id=? AND id=?",
                        (ws,order_id)).fetchone()
        if not order or order["role"]!="content":
            raise HTTPException(404,"Không có Work Order Content hợp lệ")
    result=create_from_agent(ws,FromAgentIn(
        source_task_id=order["task_id"],action="postiz.create_post",
        mode=inp.mode,channel_ids=inp.channel_ids,scheduled_at=inp.scheduled_at,
        media=inp.media,idempotency_key="social:"+inp.idempotency_key))
    return {**result,"work_order_id":order_id,"owner_approval_required":True,
            "provider_published_verified":False}


@router.get("/workspaces/{ws}/agent-work-orders/{order_id}/execution")
def execution_state(ws: str,order_id: str):
    """Read current state from DB; never report 'done' from a model's assertion."""
    from tool_hub import _pilot_ws
    _pilot_ws(ws)
    prepare_db()
    with db() as c:
        order=c.execute("SELECT * FROM agent_work_orders WHERE workspace_id=? AND id=?",
                        (ws,order_id)).fetchone()
        if not order:raise HTTPException(404,"Không tìm thấy Work Order")
        task=c.execute("SELECT status,result FROM tasks WHERE id=? AND workspace_id=?",
                       (order["task_id"],ws)).fetchone()
        qa=c.execute("SELECT verdict,source_digest FROM qa_reviews WHERE source_task_id=? AND workspace_id=? ORDER BY rowid DESC LIMIT 1",
                     (order["task_id"],ws)).fetchone()
        from publication_gate import digest
        if qa and (not task or not isinstance(task["result"],str)
                   or qa["source_digest"]!=digest(task["result"])):
            qa=None
        pilot=c.execute("SELECT computer_run_id FROM pilot_computer_runs WHERE work_order_id=?",
                        (order_id,)).fetchone()
        comp=c.execute("SELECT status,error FROM computer_runs WHERE id=?",
                       (pilot["computer_run_id"],)).fetchone() if pilot else None
        # Never use AI text to infer delivery. Only reviewed tool jobs bound to
        # this exact completed source task contribute to the external state.
        rows=c.execute("""SELECT id,action,status,error FROM tool_jobs
            WHERE workspace_id=? AND json_extract(payload_json,'$.source_task_id')=?
            ORDER BY rowid DESC LIMIT 25""",(ws,order["task_id"])).fetchall()
    jobs=[dict(x) for x in rows]
    statuses={j["status"] for j in jobs}
    if jobs and all(j["status"]=="verified_published" for j in jobs):stage="completed"
    elif "needs_reconciliation" in statuses:stage="needs_human"
    elif "executing" in statuses or "approved" in statuses:stage="executing"
    elif "accepted_unverified" in statuses:stage="verifying"
    elif "awaiting_approval" in statuses:stage="needs_human"
    elif qa and qa["verdict"]=="pass":stage="planning"
    elif task and task["status"] in ("failed","blocked_budget","blocked_dependency"):stage="failed"
    elif task and task["status"]=="completed":stage="verifying"
    elif comp and comp["status"]=="awaiting_approval":stage="needs_human"
    elif comp and comp["status"]=="needs_review":stage="needs_human"
    elif comp and comp["status"] in ("queued","processing"):stage="executing"
    else:stage="queued"
    return {"work_order_id":order_id,"role":order["role"],"stage":stage,
            "source_task_status":task["status"] if task else "missing",
            "qa_verdict":qa["verdict"] if qa else None,
            "computer_run_id":pilot["computer_run_id"] if pilot else None,
            "computer_status":comp["status"] if comp else None,
            "tool_jobs":jobs,
            "verified_external_completion":stage=="completed",
            "note":"Chỉ kết luận hoàn thành khi verifier đọc bằng chứng từ nhà cung cấp"}


@router.get("/agents/runtime-pilot/metrics")
def pilot_metrics(workspace_id: str = "demo"):
    """Real database observations, never synthetic AI success/cost counters.

    Cost only covers Work Order + QA task usage; Computer LLM calls currently
    book against workspace without run ID and cannot be attributed precisely.
    """
    from tool_hub import _pilot_ws
    _pilot_ws(workspace_id)
    prepare_db()
    with db() as c:
        rows=c.execute("""SELECT id,role,task_id,qa_task_id FROM agent_work_orders
            WHERE workspace_id=? AND role IN ('content','sales','quality')
            ORDER BY rowid DESC LIMIT 400""",(workspace_id,)).fetchall()
        task_ids=[task for row in rows for task in (row["task_id"],row["qa_task_id"]) if task]
        cost=0.0
        if task_ids:
            placeholders=",".join("?" for _ in task_ids)
            cost=float(c.execute("SELECT COALESCE(SUM(upper_cost_usd),0) FROM usage"
                      " WHERE workspace_id=? AND task_id IN ("+placeholders+")",
                      (workspace_id,*task_ids)).fetchone()[0])
    observed=[execution_state(workspace_id,row["id"]) for row in rows]
    total=len(observed)
    verified=sum(item["verified_external_completion"] for item in observed)
    intervention=sum(item["stage"]=="needs_human" for item in observed)
    failures=sum(item["stage"]=="failed" for item in observed)
    return {
        "workspace_id":workspace_id,"sample_size":total,
        "external_verified_orders":verified,
        "external_verified_rate":round(verified/total,4) if total else None,
        "needs_human_orders":intervention,
        "needs_human_rate":round(intervention/total,4) if total else None,
        "failed_orders":failures,
        "source_and_qa_upper_cost_usd":round(cost,7),
        "computer_model_cost_included":False,
        "observation_limit":400,
        "note":"Only provider-verified publication counts as external completion. Computer token costs are not yet attributable per run."
    }
