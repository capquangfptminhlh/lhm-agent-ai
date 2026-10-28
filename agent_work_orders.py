"""Fixed jobs for 16 LHM agents, linked to actual tool adapters and source repos.

A repository is a reference to an integration's code, NOT a connected service.
LLM output is only an internal draft; side effects must go through Tool Hub's
separate explicit human approval and provider receipt.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from fastapi import APIRouter, HTTPException
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field

from core import MODEL, ROLES, audit, db, new_id, now, workspace

router = APIRouter(tags=["Agent Work Orders"])
ROOT = Path(__file__).parent
CATALOG = json.loads((ROOT/"templates"/"tool_catalog.json").read_text(encoding="utf-8"))
JOBS = {j["id"]: j for j in CATALOG["jobs"]}
REPOS = {r["id"]: r for r in CATALOG["repositories"]}

# One stable professional mission per role. More jobs will need an explicit
# future product decision and own acceptance test, not ad-hoc LLM routing.
PRIMARY_JOBS = {
    "director":"director.01",
    "research":"research.01",
    "strategy":"strategy.01",
    "lead":"lead.01",
    "content":"content.01",
    "ads":"ads.01",
    "crm":"crm.01",
    "sales":"sales.01",
    "quality":"quality.01",
    "analytics":"analytics.02",
    "operations":"operations.01",
    "finance":"finance.01",
    "hr":"hr.01",
    "procurement":"procurement.01",
    "support":"support.03",
    "compliance":"compliance.02",
}
assert set(PRIMARY_JOBS)==set(ROLES)
assert all(JOBS[jid]["role"]==role for role,jid in PRIMARY_JOBS.items())


class WorkOrderIn(BaseModel):
    objective: str = Field(min_length=10,max_length=1200)
    context: str = Field(default="",max_length=3000)
    idempotency_key: str = Field(min_length=8,max_length=100)


class FleetIn(BaseModel):
    """One bounded owner-authorized mission; 16 independent fixed-role drafts."""
    mission: str = Field(min_length=10,max_length=1200)
    context: str = Field(default="",max_length=3000)
    idempotency_key: str = Field(min_length=8,max_length=80,
                                  pattern=r"^[A-Za-z0-9_-]+$")


def prepare_db() -> None:
    with db() as c:
        c.execute("""CREATE TABLE IF NOT EXISTS agent_work_orders(
          id TEXT PRIMARY KEY,
          workspace_id TEXT NOT NULL REFERENCES workspaces(id),
          role TEXT NOT NULL, job_id TEXT NOT NULL,
          objective TEXT NOT NULL,
          task_id TEXT NOT NULL UNIQUE REFERENCES tasks(id),
          idempotency_key TEXT NOT NULL,
          qa_task_id TEXT,
          created_at INTEGER NOT NULL,
          UNIQUE(workspace_id, role, idempotency_key))""")
        cols={x["name"] for x in c.execute("PRAGMA table_info(agent_work_orders)").fetchall()}
        if "qa_task_id" not in cols:
            c.execute("ALTER TABLE agent_work_orders ADD COLUMN qa_task_id TEXT")
        c.execute("CREATE INDEX IF NOT EXISTS ix_agent_work_orders_workspace ON agent_work_orders(workspace_id,created_at)")
        c.execute("""CREATE TABLE IF NOT EXISTS agent_fleets(
            id TEXT PRIMARY KEY,
            workspace_id TEXT NOT NULL REFERENCES workspaces(id),
            mission TEXT NOT NULL, context TEXT NOT NULL,
            idempotency_key TEXT NOT NULL, created_at INTEGER NOT NULL,
            UNIQUE(workspace_id,idempotency_key)
        )""")
        c.execute("""CREATE TABLE IF NOT EXISTS agent_fleet_orders(
            fleet_id TEXT NOT NULL REFERENCES agent_fleets(id),
            role TEXT NOT NULL, work_order_id TEXT NOT NULL UNIQUE
                REFERENCES agent_work_orders(id),
            PRIMARY KEY(fleet_id,role)
        )""")


def role_profile(role: str) -> dict[str,Any]:
    if role not in PRIMARY_JOBS:
        raise HTTPException(404,"Không có Agent chuyên môn này")
    job=JOBS[PRIMARY_JOBS[role]]
    repo=REPOS[job["tool"]]
    # Do not confuse a configured credential with real live connector health.
    from tool_hub import configured
    from tool_hub import TOOL_ACTIONS
    actions=[k for k,v in TOOL_ACTIONS.items() if v["tool"]==job["tool"]]
    available=bool(actions)
    configured_here=available and configured(job["tool"])
    return {
      "role":role,"name":ROLES[role][0],
      "fixed_job":{"id":job["id"],"title":job["title"],
                   "acceptance":job["acceptance"]},
      "integration":{"name":repo["name"],"id":repo["id"],"source_repo":repo["repository"],
                     "catalog_status":repo["status"],"adapter_available":available,
                     "configured":configured_here,"connection_verified":False,
                     "actions":sorted(actions),
                     "scope":"reference_only_until_provider_verified"},
      "outputs":"internal_draft_not_external_execution",
      "external_actions_require_approval":True,
    }


def work_status(task_status: str | None) -> str:
    if task_status=="completed": return "draft_ready_not_verified"
    if task_status in ("failed","blocked_budget","blocked_dependency","cancelled"):
        return "failed_or_blocked"
    if task_status=="awaiting_approval": return "awaiting_owner"
    if task_status=="running": return "working"
    return "queued"


def _view(c, row) -> dict[str,Any]:
    task=c.execute("SELECT status,role,model,result,error,attempts FROM tasks WHERE id=? AND workspace_id=?",
                   (row["task_id"],row["workspace_id"])).fetchone()
    if not task:
        raise HTTPException(409,"Work order bị mất tác vụ; cần kiểm tra dữ liệu")
    qa_id=row["qa_task_id"]
    qa_row=c.execute("SELECT status FROM tasks WHERE id=? AND workspace_id=?",(qa_id,row["workspace_id"])).fetchone() if qa_id else None
    qa_verdict=None
    if qa_id and task["result"]:
        from publication_gate import digest
        review=c.execute("""SELECT verdict,source_digest FROM qa_reviews
              WHERE qa_task_id=? AND workspace_id=? AND source_task_id=?""",
              (qa_id,row["workspace_id"],row["task_id"])).fetchone()
        if review and review["source_digest"]==digest(task["result"]):
            qa_verdict=review["verdict"]
    qa_passed=qa_verdict=="pass" and bool(qa_row) and qa_row["status"]=="completed"
    from openrouter_provider import model_configured
    key_ready=model_configured(task["model"])
    hb=c.execute("SELECT updated_at FROM worker_heartbeats WHERE id='default'").fetchone()
    worker_online=bool(hb and (now()-int(hb["updated_at"]))<45)
    blockers=[]
    if not key_ready:
        blockers.append("Chưa có API key phù hợp để Agent suy luận")
    if not worker_online:
        blockers.append("Worker chưa chạy hoặc không có heartbeat trong 45 giây")
    return {
      "id":row["id"],"workspace_id":row["workspace_id"],
      "role":row["role"],"fixed_job":role_profile(row["role"])["fixed_job"],
      "integration":role_profile(row["role"])["integration"],
      "task_id":row["task_id"],"task_status":task["status"],
      "work_status":work_status(task["status"]),
      "draft":task["result"] if task["status"]=="completed" else None,
      "error":task["error"] if task["status"] in ("failed","blocked_budget","blocked_dependency") else None,
      "attempts":task["attempts"],"created_at":row["created_at"],
      "qa_task_id":qa_id,"qa_status":qa_row["status"] if qa_row else "not_requested",
      "qa_verdict":qa_verdict,"qa_passed":qa_passed,
      "readiness":{"api_key_configured":key_ready,"worker_recent_heartbeat":worker_online,
                   "blockers":blockers},
      "can_submit_wordpress":row["role"]=="content" and qa_passed,
      "external_action_completed":False,
      "external_receipt":None,
      "next_step":"Duyệt bản nháp, rồi dùng Tool Hub và xác minh biên nhận trước khi công bố" if task["status"]=="completed"
                  else "Kiểm tra trạng thái Worker/API key và nguyên nhân chặn",
    }


@router.get("/agent-work")
def work_page():
    return FileResponse(ROOT/"static"/"agent_work.html",media_type="text/html")


@router.get("/agents/fixed-jobs")
def fixed_jobs():
    return {"agents":[role_profile(role) for role in ROLES],
            "guarantee":"One fixed mission per role; repository link alone does not install/connect any tool.",
            "usage":"POST /workspaces/{ws}/agents/{role}/work-orders"}


@router.get("/workspaces/{ws}/agent-work-orders")
def list_orders(ws: str, limit: int = 50):
    prepare_db()
    with db() as c:
        if not workspace(c,ws):
            raise HTTPException(404,"Không tìm thấy doanh nghiệp")
        rows=c.execute("SELECT * FROM agent_work_orders WHERE workspace_id=? ORDER BY created_at DESC,rowid DESC LIMIT ?",
                       (ws,max(1,min(100,limit)))).fetchall()
        return {"orders":[_view(c,r) for r in rows]}


@router.get("/workspaces/{ws}/agent-work-orders/{order_id}")
def get_order(ws: str, order_id: str):
    prepare_db()
    with db() as c:
        row=c.execute("SELECT * FROM agent_work_orders WHERE id=? AND workspace_id=?",
                      (order_id,ws)).fetchone()
        if not row:
            raise HTTPException(404,"Không tìm thấy đơn việc")
        return _view(c,row)


@router.post("/workspaces/{ws}/agent-work-orders/{order_id}/quality-review",status_code=201)
def request_quality_review(ws: str,order_id: str):
    """Queue an actual Quality Agent review; never forge the passing verdict."""
    prepare_db()
    with db() as c:
        c.execute("BEGIN IMMEDIATE")
        row=c.execute("SELECT * FROM agent_work_orders WHERE workspace_id=? AND id=?",
                      (ws,order_id)).fetchone()
        if not row:
            raise HTTPException(404,"Không tìm thấy đơn việc")
        task=c.execute("SELECT id,status,result FROM tasks WHERE id=? AND workspace_id=?",
                       (row["task_id"],ws)).fetchone()
        if not task or task["status"]!="completed" or not task["result"]:
            raise HTTPException(409,"Agent chưa có bản nháp hoàn tất để Quality kiểm tra")
        if row["qa_task_id"]:
            return {"qa_task_id":row["qa_task_id"],"deduplicated":True}
        from app import task_record
        qa=task_record(
            c,ws,"quality",
            "ĐỌC TOÀN BỘ đầu ra Agent được giao bên dưới. Kiểm tra sự thật, rủi ro và "
            "khả năng công bố. Chỉ trả về đúng một đối tượng JSON có khóa "
            '\"verdict\" (pass/fail/needs_revision), \"reason\" và \"missing_evidence\". '
            "Nếu chưa đủ bằng chứng, verdict phải là needs_revision; không tự bịa nguồn. "
            "Không đăng nội dung hoặc gửi tin.",
            "",depends_on=row["task_id"],idem="fixed-review:"+order_id)
        c.execute("UPDATE agent_work_orders SET qa_task_id=? WHERE id=?",
                  (qa["id"],order_id))
        audit(c,ws,"agent.quality_review_queued",
              f"Quality review of fixed job {row['job_id']}",qa["id"])
        return {"qa_task_id":qa["id"],"status":qa["status"],"deduplicated":False}


def _create_order_locked(c, ws: str, role: str, inp: WorkOrderIn,
                         model: str) -> dict[str,Any]:
    """Create one fixed-role task inside caller's existing IMMEDIATE transaction.

    Shared with fleet creation so 16 tasks + membership are all-or-nothing;
    neither path invokes a provider merely by inserting queue records.
    """
    job=JOBS[PRIMARY_JOBS[role]]
    old=c.execute("""SELECT * FROM agent_work_orders
        WHERE workspace_id=? AND role=? AND idempotency_key=?""",
        (ws,role,inp.idempotency_key)).fetchone()
    if old:
        if old["objective"]!=inp.objective:
            raise HTTPException(409,"Mã yêu cầu đã dùng với mục tiêu khác")
        return {**_view(c,old),"deduplicated":True}
    from app import task_record
    instruction=(
        f"NGHỀ CỐ ĐỊNH: {job['title']}. Chỉ thực hiện phạm vi chuyên môn này. "
        f"MỤC TIÊU CỤ THỂ: {inp.objective}\n"
        f"TIÊU CHÍ BÀN GIAO: {job['acceptance']}.\n"
        "Xuất rõ 4 phần: 1. Công việc đã phân tích; 2. Sản phẩm bàn giao; "
        "3. Nguồn hoặc dữ liệu cần xác minh; 4. Việc còn chờ kết nối/duyệt. "
        "Không được khẳng định đã thao tác phần mềm, đăng bài, gửi tin, thay đổi dữ liệu "
        "hoặc thực hiện giao dịch nếu không có công cụ và biên nhận thực tế."
    )
    task=task_record(c,ws,role,instruction,inp.context,
                     model=model,idem="fixed:"+role+":"+inp.idempotency_key)
    order_id=new_id()
    c.execute("""INSERT INTO agent_work_orders
      (id,workspace_id,role,job_id,objective,task_id,idempotency_key,created_at)
      VALUES(?,?,?,?,?,?,?,?)""",
      (order_id,ws,role,job["id"],inp.objective,task["id"],inp.idempotency_key,now()))
    audit(c,ws,"agent.work_order_created",
          f"{role}: fixed mission {job['id']} queued",task["id"])
    row=c.execute("SELECT * FROM agent_work_orders WHERE id=?",(order_id,)).fetchone()
    return {**_view(c,row),"deduplicated":False}


def _selected_model() -> str:
    import os
    from openrouter_provider import validate_model
    model="openrouter/free" if os.environ.get("LHM_OPENROUTER_FREE_ONLY")=="1" else MODEL
    validate_model(model)
    return model


@router.post("/workspaces/{ws}/agents/{role}/work-orders",status_code=201)
def create_order(ws: str,role: str,inp: WorkOrderIn):
    prepare_db()
    role_profile(role)
    model=_selected_model()
    with db() as c:
        c.execute("BEGIN IMMEDIATE")
        if not workspace(c,ws):
            raise HTTPException(404,"Không tìm thấy doanh nghiệp")
        return _create_order_locked(c,ws,role,inp,model)


def _fleet_view(c,row) -> dict[str,Any]:
    from openrouter_provider import model_configured
    orders=c.execute("""SELECT f.role,f.work_order_id,o.task_id,t.status,t.error
        FROM agent_fleet_orders f
        JOIN agent_work_orders o ON o.id=f.work_order_id
        JOIN tasks t ON t.id=o.task_id AND t.workspace_id=?
        WHERE f.fleet_id=?
        ORDER BY f.rowid""",(row["workspace_id"],row["id"])).fetchall()
    statuses=[r["status"] for r in orders]
    errors={"failed","blocked_budget","blocked_dependency","cancelled"}
    if any(status in errors for status in statuses):
        phase="needs_review"
    elif len(statuses)==len(PRIMARY_JOBS) and all(s=="completed" for s in statuses):
        phase="drafts_ready_unverified"
    elif any(s=="running" for s in statuses):
        phase="working"
    else:
        phase="queued"
    hb=c.execute("SELECT updated_at FROM worker_heartbeats WHERE id='default'").fetchone()
    from core import MODEL
    model=_selected_model()
    return {
        "id":row["id"],"workspace_id":row["workspace_id"],
        "mission":row["mission"],"created_at":row["created_at"],
        "status":phase,"role_count":len(orders),
        "drafts_ready":sum(s=="completed" for s in statuses),
        "queued":sum(s=="queued" for s in statuses),
        "working":sum(s=="running" for s in statuses),
        "needs_review":sum(s in errors for s in statuses),
        "orders":[{"role":r["role"],"order_id":r["work_order_id"],
                   "task_id":r["task_id"],"task_status":r["status"],
                   "error":r["error"] if r["status"] in errors else None}
                  for r in orders],
        "readiness":{"api_key_configured":model_configured(model),
                     "worker_recent_heartbeat":bool(hb and now()-int(hb["updated_at"])<45)},
        "external_actions_performed":False,
        "external_verification":False,
        "notice":"16 Agent chỉ tạo bản nháp theo nghề. Mọi tác động ngoài hệ thống cần Tool Hub được duyệt và biên nhận riêng.",
    }


@router.post("/workspaces/{ws}/agent-fleets",status_code=201)
def create_agent_fleet(ws: str,inp: FleetIn):
    """Atomically queue one job for each of the 16 unique specialists.

    Idempotency is per workspace+owner intent. Quota and model reservations
    occur in the worker one task at a time, not as an unbounded batch burst.
    """
    prepare_db()
    model=_selected_model()
    with db() as c:
        c.execute("BEGIN IMMEDIATE")
        if not workspace(c,ws):
            raise HTTPException(404,"Không tìm thấy doanh nghiệp")
        old=c.execute("SELECT * FROM agent_fleets WHERE workspace_id=? AND idempotency_key=?",
                      (ws,inp.idempotency_key)).fetchone()
        if old:
            if old["mission"]!=inp.mission or old["context"]!=inp.context:
                raise HTTPException(409,"Mã yêu cầu đã dùng với nhiệm vụ hoặc dữ liệu khác")
            return {**_fleet_view(c,old),"deduplicated":True}
        fid=new_id()
        c.execute("""INSERT INTO agent_fleets(id,workspace_id,mission,context,idempotency_key,created_at)
                     VALUES(?,?,?,?,?,?)""",
                  (fid,ws,inp.mission,inp.context,inp.idempotency_key,now()))
        for role in PRIMARY_JOBS:
            order=_create_order_locked(c,ws,role,WorkOrderIn(
                objective=inp.mission,context=inp.context,
                idempotency_key="fleet_"+fid+"_"+role),model)
            c.execute("""INSERT INTO agent_fleet_orders(fleet_id,role,work_order_id)
                         VALUES(?,?,?)""",(fid,role,order["id"]))
        audit(c,ws,"agent.fleet_queued",
              "16 fixed-role draft tasks queued atomically; no external actions",fid)
        record=c.execute("SELECT * FROM agent_fleets WHERE id=?",(fid,)).fetchone()
        return {**_fleet_view(c,record),"deduplicated":False}


@router.get("/workspaces/{ws}/agent-fleets")
def list_agent_fleets(ws: str):
    prepare_db()
    with db() as c:
        if not workspace(c,ws):raise HTTPException(404,"Không tìm thấy doanh nghiệp")
        rows=c.execute("""SELECT * FROM agent_fleets WHERE workspace_id=?
            ORDER BY created_at DESC,rowid DESC LIMIT 20""",(ws,)).fetchall()
        return {"fleets":[_fleet_view(c,row) for row in rows]}


@router.get("/workspaces/{ws}/agent-fleets/{fid}")
def get_agent_fleet(ws: str,fid: str):
    prepare_db()
    with db() as c:
        row=c.execute("SELECT * FROM agent_fleets WHERE id=? AND workspace_id=?",
                      (fid,ws)).fetchone()
        if not row:raise HTTPException(404,"Không tìm thấy chiến dịch Agent")
        return _fleet_view(c,row)
