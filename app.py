"""LHM Agent Operating System — runnable local FastAPI backend.

No fake connectors. Only verified AI-generated drafts and imported public B2B lead data.
Production requires genuine authentication/authorization, HTTPS and hardening.
"""
from __future__ import annotations

import asyncio
import json
import os
import sqlite3
from pathlib import Path
from typing import Any, Literal

import httpx
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse
from office_api import router as office_router
from tool_hub import router as tool_hub_router
from pydantic import BaseModel, Field
from publication_gate import (MAX_HANDOFF_CHARS, REVIEW_ROLES, record_review,
                              review_task_passed, scrub_output)

from core import (CATALOG, DATABASE, MODEL, ROLES, DEPARTMENTS, WORKFLOWS, TASK_RESERVE, audit, connect, db,
                  financials, new_id, now, recorded_cost, request_reservation,
                  role_prompt, sanitize, setup, store_usage, workspace, next_offpeak)

API_URL = os.getenv("DEEPSEEK_API_URL", "https://api.deepseek.com/chat/completions")
ACTIVE_WORKSPACE = os.getenv("LHM_ACTIVE_WORKSPACE", "demo")
app = FastAPI(title="LHM Agent Operating System", version="1.1.0")
app.include_router(office_router)
app.include_router(tool_hub_router)


@app.middleware("http")
async def local_only_or_auth(request: Request, call_next):
    """Local demo guard, not a production multi-tenant auth solution.

    Set LHM_ADMIN_TOKEN to require bearer token. With no token, refuse hosts other
    than loopback/testclient, including LAN and proxy traffic.
    """
    from fastapi.responses import JSONResponse
    import base64
    import binascii
    import secrets
    from urllib.parse import urlsplit
    token = os.getenv("LHM_ADMIN_TOKEN", "").strip()
    if token:
        presented = request.headers.get("Authorization", "")
        bearer_ok = secrets.compare_digest(presented, "Bearer " + token)
        basic_ok = False
        if presented.startswith("Basic "):
            try:
                decoded = base64.b64decode(presented[6:], validate=True).decode("utf-8")
                username, password = decoded.split(":", 1)
                basic_ok = secrets.compare_digest(username, "admin") and secrets.compare_digest(password, token)
            except (ValueError, UnicodeDecodeError, binascii.Error):
                pass
        if not (bearer_ok or basic_ok):
            return JSONResponse({"detail": "Authentication required"}, status_code=401,
                                headers={"WWW-Authenticate": 'Basic realm="LHM Agent OS"'})
        # Basic authentication is automatically attached by browsers. Reject cross-site
        # form/fetch writes, while bearer-token API clients remain unaffected.
        if basic_ok and request.method not in {"GET", "HEAD", "OPTIONS"}:
            if request.headers.get("sec-fetch-site", "").lower() == "cross-site":
                return JSONResponse({"detail": "Cross-site requests blocked"}, status_code=403)
            origin = request.headers.get("origin")
            if origin and urlsplit(origin).netloc.lower() != request.headers.get("host", "").lower():
                return JSONResponse({"detail": "Cross-origin writes blocked"}, status_code=403)
    elif (request.client.host if request.client else "") not in {"127.0.0.1", "::1", "localhost", "testclient"}:
        return JSONResponse({"detail": "Configure LHM_ADMIN_TOKEN; localhost access only"}, status_code=403)
    return await call_next(request)


class IndustryIn(BaseModel):
    id: str = Field(pattern=r"^[a-z][a-z0-9_]{2,40}$")
    label: str = Field(min_length=3,max_length=100)
    audience: str = Field(min_length=8,max_length=1000)
    proof: str = Field(min_length=8,max_length=1000)
    channels: list[str] = Field(min_length=1,max_length=12)


class BudgetIn(BaseModel):
    budget_usd: float = Field(ge=0.02, le=100000)


class WorkspaceIn(BaseModel):
    id: str = Field(pattern=r"^[a-z0-9][a-z0-9_-]{1,48}$")
    name: str = Field(min_length=2, max_length=100)
    industry: str
    business_context: str = Field(default="", max_length=4000)
    budget_usd: float = Field(default=5.0, ge=0.01, le=100000)


class TaskIn(BaseModel):
    role: str | None = None
    instruction: str = Field(min_length=5, max_length=8000)
    context: str = Field(default="", max_length=5000)
    requires_approval: bool = False
    complexity: Literal["normal", "high"] = "normal"
    not_before: int = 0
    offpeak_only: bool = False
    idempotency_key: str | None = Field(default=None, max_length=100)


class WorkflowIn(BaseModel):
    process: str
    objective: str = Field(min_length=8,max_length=3000)
    context: str = Field(default="",max_length=4000)
    requires_approval: bool = False
    offpeak_only: bool = False
    idempotency_key: str | None = Field(default=None,max_length=80)


class CampaignIn(BaseModel):
    objective: str = Field(min_length=8, max_length=3000)
    context: str = Field(default="", max_length=4000)
    mode: Literal["lean", "full"] = "lean"
    offpeak_only: bool = False
    requires_approval: bool = False
    idempotency_key: str | None = Field(default=None, max_length=80)


class LeadIn(BaseModel):
    business_name: str = Field(min_length=2, max_length=200)
    source_url: str = Field(min_length=8, max_length=1000, pattern=r"^https?://")
    contact_channel: str = Field(default="", max_length=200)
    fit: int = Field(ge=0, le=10)
    intent: int = Field(ge=0, le=10)
    reachable: int = Field(ge=0, le=10)
    notes: str = Field(default="", max_length=3000)
    data_origin_confirmed: bool = False


class Message(BaseModel):
    role: str
    content: Any


class ChatIn(BaseModel):
    role: str | None = None
    lane: str | None = None
    model: str | None = None
    conversation_id: str | None = None
    session_id: str | None = None
    stream: bool = False
    messages: list[Message] = Field(min_length=1)


def fetch_workspace_or_404(c: sqlite3.Connection, ws: str):
    item = workspace(c, ws)
    if not item:
        raise HTTPException(404, "Không tìm thấy doanh nghiệp")
    return item


def task_record(c, ws, role, instruction, context, approval=False, model=MODEL, not_before=0,
                idem=None, depends_on=None):
    if role not in ROLES:
        raise HTTPException(422, "Vai trò Agent không hợp lệ")
    if idem:
        old = c.execute("SELECT id,status FROM tasks WHERE workspace_id=? AND idempotency_key=?", (ws, idem)).fetchone()
        if old:
            return {"id": old["id"], "status": old["status"], "deduplicated": True}
    t = new_id()
    status = "awaiting_approval" if approval else "queued"
    c.execute("""INSERT INTO tasks(id,workspace_id,role,instruction,context,status,requires_approval,model,
             next_run_at,created_at,updated_at,idempotency_key,depends_on)
             VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)""",
             (t, ws, role, instruction, context, status, int(approval), model,
              max(0,not_before),now(),now(),idem,depends_on))
    audit(c, ws, "task.created", f"Created {role}: {status}", t)
    return {"id": t, "status": status, "role": role, "deduplicated": False}


def choose_role(instruction: str) -> str:
    """Zero-token deterministic router. Explicit role always takes precedence."""
    text = instruction.lower()
    rules = [
        ("hr", ["tuyển dụng", "nhân sự", "đào tạo nhân viên", "jd tuyển"]),
        ("finance", ["dòng tiền", "ngân sách kế toán", "báo cáo tài chính", "lãi lỗ"]),
        ("procurement", ["nhập hàng", "nhà cung cấp", "báo giá mua", "nguồn cung"]),
        ("operations", ["quy trình vận hành", "kho hàng", "sop", "giao việc"]),
        ("compliance", ["pháp lý", "tuân thủ", "bảo vệ dữ liệu"]),
        ("support", ["khiếu nại", "hỗ trợ khách hàng", "bảo hành"]),
        ("quality", ["kiểm duyệt", "kiểm chứng", "kiểm tra lỗi", "review"]),
        ("analytics", ["kpi", "báo cáo", "chuyển đổi", "hiệu quả", "doanh thu"]),
        ("crm", ["crm", "chăm sóc", "follow-up", "nhắc khách"]),
        ("lead", ["tìm khách", "lead", "khách tiềm năng", "sàng lọc"]),
        ("ads", ["quảng cáo", "google ads", "meta ads", "cpl", "roas"]),
        ("content", ["viết bài", "caption", "kịch bản", "seo", "nội dung", "video"]),
        ("research", ["nghiên cứu", "thị trường", "đối thủ", "phân khúc"]),
        ("sales", ["tư vấn", "báo giá", "chốt đơn", "sales"]),
    ]
    for role, needles in rules:
        if any(n in text for n in needles):
            return role
    return "strategy"


@app.get("/")
def home():
    return {"app": "LHM Agent OS", "dashboard": "/dashboard", "office": "/office", "api_docs": "/docs",
            "tool_hub": "/tool-hub", "note": "Local MVP; reviewed tool actions only, no unapproved external publishing"}


@app.get("/dashboard")
def dashboard():
    return FileResponse(Path(__file__).parent / "dashboard.html", media_type="text/html")


@app.get("/health")
def health():
    return {"ok": True, "status": "ready" if os.getenv("DEEPSEEK_API_KEY") else "waiting_for_key",
            "deepseek_configured": bool(os.getenv("DEEPSEEK_API_KEY")), "runtime": "LHM Universal Agent OS",
            "database": "sqlite", "mode": "local_mvp"}


@app.get("/catalog")
def catalog():
    return {"industries": CATALOG, "departments":DEPARTMENTS, "workflows":WORKFLOWS, "roles": {k: {"name": v[0], "description": v[1]} for k,v in ROLES.items()},
            "work": "Drafts and internal analysis only; external connectors not configured"}


@app.post("/industries", status_code=201)
def create_industry(inp: IndustryIn):
    # Industry-specific policy becomes a template, not a separate server or model.
    if inp.id in CATALOG:
        raise HTTPException(409, "Mẫu ngành này đã tồn tại")
    config={"label":inp.label,"audience":inp.audience,"proof":inp.proof,
            "channels":[c[:80] for c in inp.channels],"pipeline":["research","strategy","content","quality"]}
    with db() as c:
        try:
            c.execute("INSERT INTO industries VALUES(?,?,?)",(inp.id,json.dumps(config,ensure_ascii=False),now()))
        except sqlite3.IntegrityError:
            raise HTTPException(409, "Industry ID đã tồn tại")
        audit(c,None,"industry.created",inp.label)
    CATALOG[inp.id]=config
    return {"id":inp.id,"industry":config}


@app.get("/workspaces")
def workspaces():
    with db() as c:
        result = []
        for w in c.execute("SELECT * FROM workspaces ORDER BY created_at DESC").fetchall():
            result.append({**dict(w), "financials": financials(c,w["id"])})
    return {"workspaces": result}


@app.post("/workspaces", status_code=201)
def create_workspace(inp: WorkspaceIn):
    if inp.industry not in CATALOG:
        raise HTTPException(422, "Ngành chưa có trong catalog; hãy thêm template trước")
    try:
        with db() as c:
            c.execute("INSERT INTO workspaces VALUES(?,?,?,?,?,?)",
                      (inp.id, inp.name, inp.industry, inp.business_context, inp.budget_usd, now()))
            audit(c, inp.id, "workspace.created", inp.name)
    except sqlite3.IntegrityError:
        raise HTTPException(409, "Workspace ID đã tồn tại")
    return {"id": inp.id, "name": inp.name, "industry": inp.industry, "budget_usd": inp.budget_usd}


@app.patch("/workspaces/{ws}/budget")
def change_budget(ws: str, inp: BudgetIn):
    with db() as c:
        c.execute("BEGIN IMMEDIATE")
        old=fetch_workspace_or_404(c,ws)
        c.execute("UPDATE workspaces SET budget_usd=? WHERE id=?",(inp.budget_usd,ws))
        audit(c,ws,"budget.updated",f"Budget changed {old['budget_usd']} -> {inp.budget_usd} USD")
        f=financials(c,ws)
    return f


@app.get("/workspaces/{ws}")
def get_workspace(ws: str):
    with db() as c:
        w = fetch_workspace_or_404(c,ws)
        counts = {r["status"]:r["n"] for r in c.execute(
            "SELECT status,COUNT(*) n FROM tasks WHERE workspace_id=? GROUP BY status",(ws,)).fetchall()}
        leads = c.execute("SELECT COUNT(*) FROM leads WHERE workspace_id=?", (ws,)).fetchone()[0]
        return {**dict(w), "financials": financials(c, ws), "tasks": counts, "leads": leads}


@app.post("/workspaces/{ws}/tasks", status_code=201)
def create_task(ws: str, inp: TaskIn):
    role = inp.role or choose_role(inp.instruction)
    model = "deepseek-v4-pro" if inp.complexity == "high" else MODEL
    if inp.complexity == "high" and os.getenv("LHM_ALLOW_PRO", "0") != "1":
        raise HTTPException(422, "Model nâng cao chưa được chủ doanh nghiệp bật để tránh phát sinh chi phí")
    with db() as c:
        fetch_workspace_or_404(c,ws)
        return task_record(c, ws, role, inp.instruction, inp.context, inp.requires_approval,
                           model, max(inp.not_before, next_offpeak(now()) if inp.offpeak_only else 0), inp.idempotency_key)


@app.post("/workspaces/{ws}/campaigns", status_code=201)
def create_campaign(ws: str, inp: CampaignIn):
    stages = ["research", "content", "quality"] if inp.mode == "lean" else ["research", "strategy", "content", "quality"]
    outputs = []
    with db() as c:
        fetch_workspace_or_404(c,ws)
        depends = None
        for i, role in enumerate(stages):
            task_idem = f"{inp.idempotency_key}-{i}" if inp.idempotency_key else None
            info = task_record(
                c, ws, role,
                f"Giai đoạn {i+1}/{len(stages)} ({ROLES[role][0]}) của chiến dịch. Mục tiêu: {inp.objective}. "
                f"Chỉ dùng thông tin xác thực và đầu ra các bước trước. Xuất sản phẩm bàn giao để bước sau dùng.",
                inp.context, approval=inp.requires_approval and i==0,
                not_before=next_offpeak(now()) if inp.offpeak_only else 0,
                idem=task_idem, depends_on=depends
            )
            outputs.append(info)
            depends = info["id"]
    return {"mode": inp.mode, "pipeline": outputs, "note": "Worker handles stages sequentially, not in parallel"}


@app.post("/workspaces/{ws}/workflows", status_code=201)
def create_workflow(ws: str, inp: WorkflowIn):
    stages=WORKFLOWS.get(inp.process)
    if stages is None:
        raise HTTPException(422,"Loại quy trình chưa tồn tại")
    result=[]
    with db() as c:
        fetch_workspace_or_404(c,ws)
        depends=None
        for i, role in enumerate(stages):
            idem=f"workflow:{inp.idempotency_key}:{i}" if inp.idempotency_key else None
            info=task_record(c,ws,role,
                f"Quy trình {inp.process}, bước {i+1}/{len(stages)}. Mục tiêu: {inp.objective}. "
                f"Chỉ đưa ra sản phẩm bàn giao nội bộ, nêu dữ liệu thiếu và không tự thực hiện hành động bên ngoài.",
                inp.context,approval=inp.requires_approval and i==0,
                not_before=next_offpeak(now()) if inp.offpeak_only else 0,idem=idem,depends_on=depends)
            result.append(info)
            depends=info['id']
    return {"process":inp.process,"pipeline":result,"note":"Các Agent chạy tuần tự; không sinh 3 model riêng"}


@app.get("/workspaces/{ws}/tasks")
def list_tasks(ws: str, limit: int = 100):
    with db() as c:
        fetch_workspace_or_404(c,ws)
        rows = c.execute("SELECT * FROM tasks WHERE workspace_id=? ORDER BY created_at DESC LIMIT ?",
                         (ws, max(1,min(limit,200)))).fetchall()
        return {"tasks": [dict(r) for r in rows]}


@app.get("/tasks/{task_id}")
def get_task(task_id: str):
    with db() as c:
        task = c.execute("SELECT * FROM tasks WHERE id=?",(task_id,)).fetchone()
        if not task: raise HTTPException(404,"Không tìm thấy tác vụ")
        return dict(task)


@app.post("/tasks/{task_id}/approve")
def approve_task(task_id: str):
    with db() as c:
        c.execute("BEGIN IMMEDIATE")
        updated = c.execute("UPDATE tasks SET status='queued',updated_at=? WHERE id=? AND status='awaiting_approval'", (now(),task_id))
        row=c.execute("SELECT workspace_id,status FROM tasks WHERE id=?",(task_id,)).fetchone()
        if not row: raise HTTPException(404,"Không tìm thấy tác vụ")
        if not updated.rowcount: raise HTTPException(409,f"Không thể duyệt tác vụ trạng thái {row['status']}")
        audit(c,row["workspace_id"],"task.approved","Owner approved draft generation",task_id)
    return {"id":task_id,"status":"queued"}


@app.post("/tasks/{task_id}/cancel")
def cancel_task(task_id: str):
    with db() as c:
        c.execute("BEGIN IMMEDIATE")
        t=c.execute("SELECT workspace_id,status FROM tasks WHERE id=?",(task_id,)).fetchone()
        if not t: raise HTTPException(404,"Không tìm thấy tác vụ")
        if t['status'] not in ('queued','awaiting_approval','blocked_budget','blocked_dependency'):
            raise HTTPException(409,"Tác vụ không ở trạng thái có thể hủy")
        c.execute("UPDATE tasks SET status='cancelled',reserve_usd=0,updated_at=? WHERE id=?",(now(),task_id))
        audit(c,t['workspace_id'],"task.cancelled","Cancelled by operator",task_id)
    return {"id":task_id,"status":"cancelled"}


@app.post("/tasks/{task_id}/retry")
def retry_task(task_id: str):
    with db() as c:
        c.execute("BEGIN IMMEDIATE")
        t=c.execute("SELECT workspace_id,status,depends_on FROM tasks WHERE id=?",(task_id,)).fetchone()
        if not t: raise HTTPException(404,"Không tìm thấy tác vụ")
        if t['status'] not in ('failed','blocked_budget','blocked_dependency'):
            raise HTTPException(409,"Chỉ retry tác vụ lỗi/bị chặn sau khi đã kiểm tra")
        if t['depends_on']:
            prev=c.execute("SELECT id,role,status FROM tasks WHERE id=?",(t['depends_on'],)).fetchone()
            if not prev or prev['status']!='completed':
                raise HTTPException(409,"Phải xử lý công đoạn trước")
            if prev["role"] in REVIEW_ROLES and not review_task_passed(c,prev["id"]):
                raise HTTPException(409,"QA chưa pass; chưa thể chạy bước phụ thuộc")
        c.execute("UPDATE tasks SET status='queued',attempts=0, next_run_at=0,error=NULL,reserve_usd=0,updated_at=? WHERE id=?",(now(),task_id))
        audit(c,t['workspace_id'],"task.manual_retry","Manually retried after review",task_id)
    return {"id":task_id,"status":"queued"}


@app.get("/metrics")
def metrics(workspace_id: str = "demo"):
    with db() as c:
        fetch_workspace_or_404(c,workspace_id)
        counts = {r["status"]:r["n"] for r in c.execute(
            "SELECT status,COUNT(*) n FROM tasks WHERE workspace_id=? GROUP BY status",(workspace_id,)).fetchall()}
        aggregates = c.execute("SELECT COALESCE(SUM(input_hit),0) hit, COALESCE(SUM(input_miss),0) miss,COALESCE(SUM(output_tokens),0) output FROM usage WHERE workspace_id=?",(workspace_id,)).fetchone()
        return {"workspace_id":workspace_id,"registered_agent_roles":len(ROLES),"tasks":counts,
                "tokens":dict(aggregates),"financials":financials(c,workspace_id),"pricing_method":"upper_bound_peak_rate_not_invoice"}


@app.get("/events")
def events(workspace_id: str = "demo", limit: int = 60):
    with db() as c:
        fetch_workspace_or_404(c,workspace_id)
        rows = c.execute("SELECT * FROM events WHERE workspace_id=? ORDER BY id DESC LIMIT ?",
                         (workspace_id,max(1,min(limit,200)))).fetchall()
        return {"events":[dict(r) for r in rows]}


@app.post("/workspaces/{ws}/leads",status_code=201)
def add_lead(ws: str, inp: LeadIn):
    if not inp.data_origin_confirmed:
        raise HTTPException(422,"Cần xác nhận thông tin được thu thập/sử dụng hợp lệ")
    score = int(round(inp.fit*4.0 + inp.intent*4.0 + inp.reachable*2.0))
    with db() as c:
        fetch_workspace_or_404(c,ws)
        identifier=new_id()
        try:
            c.execute("INSERT INTO leads VALUES(?,?,?,?,?,?,?,?,?,?,?)",
                      (identifier,ws,inp.business_name,inp.source_url,inp.contact_channel,
                       inp.fit,inp.intent,inp.reachable,score,inp.notes,now()))
        except sqlite3.IntegrityError:
            raise HTTPException(409,"Khách này đã được nhập trước đó")
        audit(c,ws,"lead.imported",f"Source recorded; lead score {score}")
    return {"id":identifier,"score":score,"verified":False,"note":"Điểm nội bộ, chưa xác minh nhu cầu mua"}


@app.get("/workspaces/{ws}/leads")
def list_leads(ws:str):
    with db() as c:
        fetch_workspace_or_404(c,ws)
        rows=c.execute("SELECT * FROM leads WHERE workspace_id=? ORDER BY score DESC,created_at DESC", (ws,)).fetchall()
        return {"leads":[dict(r) for r in rows]}


# API compatibility with Hermes3D 'custom' provider. The active workspace may be
# switched by changing LHM_ACTIVE_WORKSPACE and restarting the process.
@app.get("/state")
def state():
    with db() as c:
        ws = workspace(c, ACTIVE_WORKSPACE)
        if ws is None:
            raise HTTPException(503,"LHM_ACTIVE_WORKSPACE chưa tồn tại")
        running = {r["role"] for r in c.execute("SELECT role FROM tasks WHERE workspace_id=? AND status='running'",(ACTIVE_WORKSPACE,)).fetchall()}
        finance = financials(c,ACTIVE_WORKSPACE)
    return {"profileName":f"LHM Agent OS — {ws['name']}",
            "runtime":{"name":"LHM Universal Agent OS","version":"1.0.0","vendor":"LHM",
                       "status":"ready" if os.getenv("DEEPSEEK_API_KEY") else "degraded", "active_model":MODEL},
            "identity":{"name":"AI Director","role":"director","lane":"director","model_id":MODEL},
            "active":{r:MODEL for r in ROLES},
            "agent_states":{r:"working" if r in running else "idle" for r in ROLES},
            "business":{"workspace_id":ACTIVE_WORKSPACE,"industry":ws["industry"],"budget":finance}}


@app.get("/registry")
def registry():
    return {"models":{MODEL:{"provider":"deepseek","label":"DeepSeek Flash"}},
            "roles":{k:{"name":v[0],"description":v[1]} for k,v in ROLES.items()},"industries":list(CATALOG.keys())}


async def deepseek_request(role: str, ws: sqlite3.Row, instruction: str, context: str, model: str) -> dict[str, Any]:
    api_key=os.getenv("DEEPSEEK_API_KEY", "")
    if not api_key:
        raise HTTPException(503,"Chưa cấu hình DEEPSEEK_API_KEY; không thực thi giả lập")
    system = role_prompt(role,ws["industry"],ws["business_context"])
    if len(context) > MAX_HANDOFF_CHARS:
        raise HTTPException(422, "Context quá dài; yêu cầu chia phần QA rõ ràng, không cắt âm thầm")
    user = instruction[:8000] + ("\n\nDỮ LIỆU ĐẦU VÀO (chưa kiểm chứng):\n" + context if context else "")
    try:
        async with httpx.AsyncClient(timeout=httpx.Timeout(90,connect=10)) as client:
            response=await client.post(API_URL,headers={"Authorization":f"Bearer {api_key}"},
                 json={"model":model,"messages":[{"role":"system","content":system},{"role":"user","content":user}],
                       "stream":False,"max_tokens":1100,
                       "thinking":{"type":"enabled" if model=="deepseek-v4-pro" else "disabled"},
                       **({"reasoning_effort":"low"} if model=="deepseek-v4-pro" else {})})
        response.raise_for_status()
        payload=response.json()
        _=payload["choices"][0]["message"]["content"]
        if not payload.get("usage") or not isinstance(_,str) or not _.strip():
            raise ValueError("Missing content or usage")
        return payload
    except httpx.HTTPStatusError as exc:
        code=exc.response.status_code
        # provider errors must not leak request/response bodies or token
        raise HTTPException(503 if code in (429,500,502,503,504) else 502,
                            f"DeepSeek lỗi HTTP {code}") from exc
    except httpx.RequestError as exc:
        raise HTTPException(503,"DeepSeek mất kết nối hoặc timeout") from exc
    except (ValueError, KeyError, IndexError, TypeError, json.JSONDecodeError) as exc:
        raise HTTPException(502,"DeepSeek trả phản hồi thiếu nội dung/usage; cần kiểm tra trước khi thử lại") from exc


def claim_task(task_id: str) -> dict[str,Any]:
    with db() as c:
        c.execute("BEGIN IMMEDIATE")
        t=c.execute("SELECT * FROM tasks WHERE id=?",(task_id,)).fetchone()
        if not t:
            raise HTTPException(404,"Không tìm thấy tác vụ")
        if t["status"] != "queued":
            raise HTTPException(409,f"Tác vụ chưa sẵn sàng: {t['status']}")
        if not os.getenv("DEEPSEEK_API_KEY"):
            raise HTTPException(503,"Chưa có DEEPSEEK_API_KEY; nhiệm vụ giữ nguyên")
        if t["next_run_at"]>now():
            raise HTTPException(409,"Tác vụ đã hẹn giờ; chưa đến lúc chạy")
        prev = t["depends_on"]
        if prev:
            predecessor=c.execute("SELECT id,role,status,result FROM tasks WHERE id=? AND workspace_id=?",(prev,t["workspace_id"])).fetchone()
            if not predecessor or predecessor["status"] != "completed":
                raise HTTPException(409,"Phụ thuộc chưa hoàn tất")
            if predecessor["role"] in REVIEW_ROLES and not review_task_passed(c, predecessor["id"]):
                message = "Bước QA chưa pass; nội dung phải sửa và kiểm tra lại"
                c.execute("UPDATE tasks SET status='blocked_dependency',error=?,updated_at=? WHERE id=?",
                          (message,now(),task_id))
                audit(c,t["workspace_id"],"task.qa_blocked",message,task_id)
                return {"dependency_blocked": True, "task_id": task_id, "reason": message}
            full = (t["context"] or "") + "\n\nĐẦU RA GIAI ĐOẠN TRƯỚC:\n" + (predecessor["result"] or "")
            if not predecessor["result"] or len(full) > MAX_HANDOFF_CHARS:
                message = "Bàn giao vượt giới hạn; phải chia tài liệu để QA bao phủ toàn bộ"
                c.execute("UPDATE tasks SET status='blocked_dependency',error=?,updated_at=? WHERE id=?",
                          (message,now(),task_id))
                audit(c,t["workspace_id"],"task.handoff_blocked",message,task_id)
                return {"dependency_blocked": True, "task_id": task_id, "reason": message}
        if not request_reservation(c,t["workspace_id"],TASK_RESERVE,task_id):
            c.execute("UPDATE tasks SET status='blocked_budget',updated_at=? WHERE id=?",(now(),task_id))
            audit(c,t["workspace_id"],"task.budget_blocked","Insufficient remaining budget",task_id)
            return {"budget_blocked":True,"task_id":task_id}
        c.execute("UPDATE tasks SET status='running',attempts=attempts+1,updated_at=? WHERE id=?",(now(),task_id))
        audit(c,t["workspace_id"],"task.started",f"AI role={t['role']}, model={t['model']}",task_id)
        out=dict(t)
        if prev:
            out["context"] = full
        return out


async def execute_task(task_id: str) -> dict[str,Any]:
    t=claim_task(task_id)
    if t.get("budget_blocked"):
        raise HTTPException(402,"Ngân sách không đủ; tác vụ chuyển blocked_budget")
    if t.get("dependency_blocked"):
        raise HTTPException(409,t.get("reason", "Bàn giao chưa đủ điều kiện"))
    with db() as c:
        ws=fetch_workspace_or_404(c,t["workspace_id"])
        ws_data=dict(ws)
    try:
        response=await deepseek_request(t["role"],ws_data,t["instruction"],t["context"],t["model"])
        content=response["choices"][0]["message"]["content"]
        safe_content, pii_rejected = scrub_output(t["role"], content)
        with db() as c:
            c.execute("BEGIN IMMEDIATE")
            upper=store_usage(c,t["workspace_id"],task_id,t["model"],response["usage"])
            if pii_rejected:
                c.execute("UPDATE tasks SET status='failed',result=?,reserve_usd=0,error=?,updated_at=? WHERE id=?",
                          (safe_content,"PII detected: general Agent output rejected; no raw PII stored",now(),task_id))
                audit(c,t["workspace_id"],"task.pii_blocked","PII redacted before persistence",task_id)
            else:
                c.execute("UPDATE tasks SET status='completed',result=?,reserve_usd=0,error=NULL,updated_at=? WHERE id=?",
                          (safe_content,now(),task_id))
                record_review(c,t,safe_content)
                audit(c,t["workspace_id"],"task.completed",f"Draft created; estimated upper cost ${upper:.6f}",task_id)
        return {"id":task_id,"status":"failed" if pii_rejected else "completed",
                "result":safe_content,"cost_upper_usd":upper}
    except Exception as exc:
        message=exc.detail if isinstance(exc,HTTPException) else "Internal execution error"
        transient=isinstance(exc,HTTPException) and exc.status_code==503
        with db() as c:
            c.execute("BEGIN IMMEDIATE")
            old=c.execute("SELECT attempts FROM tasks WHERE id=?",(task_id,)).fetchone()
            attempts=old["attempts"] if old else 3
            delay=min(120,2**attempts * 5)
            retry=transient and attempts < 3
            c.execute("UPDATE tasks SET status=?,error=?,reserve_usd=0,next_run_at=?,updated_at=? WHERE id=?",
                      ("queued" if retry else "failed",str(message)[:300],now()+delay if retry else 0,now(),task_id))
            audit(c,t["workspace_id"],"task.retry" if retry else "task.failed",str(message),task_id)
        if isinstance(exc,HTTPException):raise
        raise HTTPException(500,"Tác vụ thất bại; xem nhật ký") from exc


@app.post("/tasks/{task_id}/run")
async def run_task(task_id: str):
    return await execute_task(task_id)


@app.post("/v1/chat/completions")
async def custom_chat(inp:ChatIn):
    """Hermes3D custom runtime contract with spend cap; no pretend streaming."""
    if inp.stream:
        raise HTTPException(422,"Streaming chưa hỗ trợ")
    if not os.getenv("DEEPSEEK_API_KEY"):
        raise HTTPException(503,"Chưa cấu hình DEEPSEEK_API_KEY")
    role=inp.role or inp.lane or "director"
    if role not in ROLES: role="director"
    messages=[m for m in inp.messages if m.role in {"user","assistant"}]
    if not messages:
        raise HTTPException(422,"Thiếu tin nhắn hợp lệ")
    # Short bounded history avoids unbounded context and cost.
    instruction="\n".join(("USER: " if m.role=="user" else "ASSISTANT: ")+str(m.content)[:2500] for m in messages[-5:])[:8000]
    with db() as c:
        fetch_workspace_or_404(c,ACTIVE_WORKSPACE)
        task=task_record(c,ACTIVE_WORKSPACE,role,instruction,"Hermes3D interactive chat")
    result=await execute_task(task["id"])
    return {"id":result["id"],"object":"chat.completion","created":now(),"model":MODEL,
            "choices":[{"index":0,"message":{"role":"assistant","content":result["result"]},"finish_reason":"stop"}]}


if __name__=="__main__":
    import uvicorn
    uvicorn.run(app,host="127.0.0.1",port=7770)