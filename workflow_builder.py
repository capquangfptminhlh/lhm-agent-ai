"""LHM Workflow Builder — durable, validated sequential Agent pipelines.

The final optional publisher tool is *never* executed from this module.
After a matching QA pass it becomes a normal Tool Hub job that requires
separate explicit human approval. Reusing Tool Hub means existing source
digest, idempotency and reconciliation rules apply unchanged.
"""
from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from typing import Any, Literal

from fastapi import APIRouter, HTTPException
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field, model_validator

from core import MODEL, ROLES, audit, db, new_id, now, workspace
from publication_gate import review_task_passed
from tool_hub import FromAgentIn, ToolJobIn, create_tool_job, _pilot_ws

router = APIRouter(tags=["Workflow Builder"])
ROOT = Path(__file__).parent
PUBLISH_ACTIONS = {"postiz.create_post", "wordpress.create_post"}


class AgentNode(BaseModel):
    type: Literal["agent"] = "agent"
    role: str
    instruction: str = Field(min_length=8, max_length=2400)


class PublishNode(BaseModel):
    type: Literal["tool"] = "tool"
    action: Literal["postiz.create_post", "wordpress.create_post"]
    mode: Literal["draft", "now", "schedule"] = "draft"
    channel_ids: list[str] = Field(default_factory=list, max_length=10)
    scheduled_at: str | None = None
    title: str = Field(default="", max_length=240)
    status: Literal["draft", "publish"] = "draft"
    media: list[dict[str, Any]] = Field(default_factory=list, max_length=12)


class TemplateIn(BaseModel):
    name: str = Field(min_length=3, max_length=100)
    description: str = Field(default="", max_length=600)
    steps: list[AgentNode | PublishNode] = Field(min_length=2, max_length=9)

    @model_validator(mode="after")
    def validate_pipeline(self):
        nodes = self.steps
        agents = [s for s in nodes if s.type == "agent"]
        if len(agents) < 2 or len(agents) > 8:
            raise ValueError("Workflow yêu cầu 2–8 Agent")
        for step in agents:
            if step.role not in ROLES:
                raise ValueError("Vai trò Agent không có trong hệ thống: " + step.role)
        tool_positions = [i for i, step in enumerate(nodes) if step.type == "tool"]
        if tool_positions:
            if tool_positions != [len(nodes) - 1]:
                raise ValueError("Chỉ một node công cụ ở cuối quy trình")
            # QA must review the *immediately preceding* content task, as
            # record_review binds its digest to that direct dependency.
            if len(nodes) < 3 or nodes[-2].role != "quality" or nodes[-3].role != "content":
                raise ValueError("Trước khi xuất bản phải có Content → Quality → Tool")
            tool = nodes[-1]
            if tool.action == "postiz.create_post":
                if not (1 <= len(tool.channel_ids) <= 10) or len(set(tool.channel_ids)) != len(tool.channel_ids):
                    raise ValueError("Postiz phải chọn 1–10 kênh khác nhau")
                if tool.mode == "schedule" and not tool.scheduled_at:
                    raise ValueError("Lịch đăng Postiz yêu cầu thời gian có timezone")
        return self


class StartIn(BaseModel):
    objective: str = Field(min_length=8, max_length=3000)
    context: str = Field(default="", max_length=4000)
    requires_approval: bool = False
    idempotency_key: str | None = Field(default=None, min_length=6, max_length=100)


def prepare_db() -> None:
    with db() as c:
        c.executescript("""
        CREATE TABLE IF NOT EXISTS builder_templates (
          id TEXT PRIMARY KEY, workspace_id TEXT NOT NULL REFERENCES workspaces(id),
          name TEXT NOT NULL, description TEXT NOT NULL,
          definition_json TEXT NOT NULL, created_at INTEGER NOT NULL, updated_at INTEGER NOT NULL
        );
        CREATE TABLE IF NOT EXISTS builder_runs (
          id TEXT PRIMARY KEY, workspace_id TEXT NOT NULL REFERENCES workspaces(id),
          template_id TEXT NOT NULL, objective TEXT NOT NULL,
          definition_json TEXT NOT NULL, task_ids_json TEXT NOT NULL,
          status TEXT NOT NULL, tool_job_id TEXT, error TEXT,
          idempotency_key TEXT, created_at INTEGER NOT NULL, updated_at INTEGER NOT NULL,
          UNIQUE(workspace_id,idempotency_key)
        );
        CREATE INDEX IF NOT EXISTS ix_builder_runs_ready ON builder_runs(status,created_at);
        CREATE INDEX IF NOT EXISTS ix_builder_template_ws ON builder_templates(workspace_id,created_at);
        """)
prepare_db()


def _ws(c: sqlite3.Connection, ws: str) -> None:
    if not workspace(c, ws):
        raise HTTPException(404, "Không tìm thấy doanh nghiệp")


def _get_template(c: sqlite3.Connection, ws: str, template_id: str) -> sqlite3.Row:
    row = c.execute("SELECT * FROM builder_templates WHERE id=? AND workspace_id=?",
                    (template_id, ws)).fetchone()
    if not row:
        raise HTTPException(404, "Không tìm thấy mẫu Workflow")
    return row


def _template_view(row: sqlite3.Row) -> dict[str, Any]:
    d = dict(row)
    d["definition"] = json.loads(d.pop("definition_json"))
    return d


def _run_view(c: sqlite3.Connection, row: sqlite3.Row) -> dict[str, Any]:
    d = dict(row)
    d["definition"] = json.loads(d.pop("definition_json"))
    task_ids = json.loads(d.pop("task_ids_json"))
    d["tasks"] = []
    for tid in task_ids:
        task = c.execute("SELECT id,role,status,result,error,updated_at FROM tasks WHERE id=? AND workspace_id=?",
                         (tid, row["workspace_id"])).fetchone()
        if task:
            d["tasks"].append(dict(task))
    if row["tool_job_id"]:
        job = c.execute("SELECT id,action,status,error,result_json FROM tool_jobs WHERE id=? AND workspace_id=?",
                        (row["tool_job_id"], row["workspace_id"])).fetchone()
        if job:
            d["tool"] = {"id":job["id"],"action":job["action"],"status":job["status"],
                         "error":job["error"],"receipt":json.loads(job["result_json"]) if job["result_json"] else None}
    return d


@router.get("/workflow-builder")
def builder_page():
    return FileResponse(ROOT / "static" / "workflow_builder.html", media_type="text/html")


@router.get("/workspaces/{ws}/builder/templates")
def list_builder_templates(ws: str):
    with db() as c:
        _ws(c,ws)
        return {"templates":[_template_view(r) for r in c.execute(
            "SELECT * FROM builder_templates WHERE workspace_id=? ORDER BY created_at DESC,rowid DESC LIMIT 100",(ws,))]}


@router.post("/workspaces/{ws}/builder/templates", status_code=201)
def create_builder_template(ws: str, inp: TemplateIn):
    with db() as c:
        _ws(c, ws)
        ident = new_id()
        record = inp.model_dump()
        c.execute("""INSERT INTO builder_templates
            (id,workspace_id,name,description,definition_json,created_at,updated_at)
            VALUES (?,?,?,?,?,?,?)""",
            (ident,ws,inp.name,inp.description,json.dumps(record,ensure_ascii=False),now(),now()))
        audit(c,ws,"builder.template_created",f"Template: {inp.name}")
        row = _get_template(c,ws,ident)
        return _template_view(row)


@router.get("/workspaces/{ws}/builder/templates/{template_id}")
def get_builder_template(ws: str, template_id: str):
    with db() as c:
        return _template_view(_get_template(c,ws,template_id))


@router.put("/workspaces/{ws}/builder/templates/{template_id}")
def update_builder_template(ws: str, template_id: str, inp: TemplateIn):
    with db() as c:
        _get_template(c,ws,template_id)
        c.execute("UPDATE builder_templates SET name=?,description=?,definition_json=?,updated_at=? WHERE id=?",
                  (inp.name,inp.description,json.dumps(inp.model_dump(),ensure_ascii=False),now(),template_id))
        audit(c,ws,"builder.template_updated",f"Template: {inp.name}")
        return _template_view(_get_template(c,ws,template_id))


@router.post("/workspaces/{ws}/builder/templates/{template_id}/run", status_code=201)
def start_builder_run(ws: str, template_id: str, inp: StartIn):
    from app import task_record  # Delayed import prevents circular app/router import.
    with db() as c:
        c.execute("BEGIN IMMEDIATE")
        template = _get_template(c,ws,template_id)
        if inp.idempotency_key:
            old = c.execute("SELECT * FROM builder_runs WHERE workspace_id=? AND idempotency_key=?",
                            (ws,inp.idempotency_key)).fetchone()
            if old:
                if old["template_id"] != template_id or old["objective"] != inp.objective:
                    raise HTTPException(409,"Idempotency key dùng cho workflow khác")
                return {**_run_view(c,old),"deduplicated":True}
        definition = json.loads(template["definition_json"])
        steps = definition["steps"]
        if steps[-1]["type"] == "tool":
            # Never let an unrelated tenant reuse pilot social credentials.
            _pilot_ws(ws)
        run_id = new_id()
        ids = []
        depends = None
        for idx, step in enumerate(steps):
            if step["type"] == "tool":
                continue
            prompt = (f"Workflow '{template['name']}' bước {idx+1}. "
                      f"Mục tiêu chung: {inp.objective}.\n"
                      f"Công việc của Agent: {step['instruction']}")
            if step["role"] == "quality" and idx == len(steps)-2 and steps[-1]["type"]=="tool":
                prompt += ("\nCHỈ trả về một đối tượng JSON có verdict = pass, fail hoặc needs_revision, "
                           "reason và kiểm chứng cụ thể. Không đưa nội dung ngoài JSON. "
                           "Không pass nếu thiếu bằng chứng hoặc nội dung có thông tin bịa đặt.")
            info = task_record(c,ws,step["role"],prompt,inp.context,
                        approval=inp.requires_approval and idx == 0,
                        depends_on=depends,idem=f"builder:{run_id}:{idx}")
            depends = info["id"]
            ids.append(info["id"])
        c.execute("""INSERT INTO builder_runs
          (id,workspace_id,template_id,objective,definition_json,task_ids_json,
           status,tool_job_id,error,idempotency_key,created_at,updated_at)
          VALUES (?,?,?,?,?,?,?,?,?,?,?,?)""",
          (run_id,ws,template_id,inp.objective,json.dumps(definition,ensure_ascii=False),
           json.dumps(ids),"running",None,None,inp.idempotency_key,now(),now()))
        audit(c,ws,"builder.run_created",f"{template['name']}: {len(ids)} tasks",run_id)
        row=c.execute("SELECT * FROM builder_runs WHERE id=?",(run_id,)).fetchone()
        return {**_run_view(c,row),"deduplicated":False}


@router.get("/workspaces/{ws}/builder/runs")
def list_builder_runs(ws: str, limit: int = 50):
    with db() as c:
        _ws(c,ws)
        rows=c.execute("SELECT * FROM builder_runs WHERE workspace_id=? ORDER BY created_at DESC,rowid DESC LIMIT ?",
                       (ws,max(1,min(limit,100)))).fetchall()
        return {"runs":[_run_view(c,r) for r in rows]}


@router.get("/builder/runs/{run_id}")
def get_builder_run(run_id: str):
    with db() as c:
        row=c.execute("SELECT * FROM builder_runs WHERE id=?",(run_id,)).fetchone()
        if not row: raise HTTPException(404,"Không tìm thấy lần chạy Workflow")
        return _run_view(c,row)


def sync_builder_run(run_id: str) -> dict[str, Any]:
    """Materialize an approved-source posting *request* once; never publish."""
    with db() as c:
        row=c.execute("SELECT * FROM builder_runs WHERE id=?",(run_id,)).fetchone()
        if not row: raise HTTPException(404,"Không tìm thấy lần chạy Workflow")
        if row["status"] not in ("running","awaiting_tool_approval"):
            return _run_view(c,row)
        steps=json.loads(row["definition_json"])["steps"]
        task_ids=json.loads(row["task_ids_json"])
        records=[c.execute("SELECT id,status,role,result FROM tasks WHERE id=? AND workspace_id=?",
                           (tid,row["workspace_id"])).fetchone() for tid in task_ids]
        if any(t is None for t in records):
            status,detail="blocked","Một hoặc nhiều task đã mất"
        elif any(t["status"] in {"failed","cancelled","blocked_budget","blocked_dependency"} for t in records):
            status,detail="blocked","Một hoặc nhiều bước đã lỗi hoặc bị chặn"
        elif not all(t["status"]=="completed" for t in records):
            return _run_view(c,row)
        elif steps[-1]["type"] != "tool":
            status,detail="completed",None
        else:
            source_task=records[-2]
            quality_task=records[-1]
            if source_task["role"]!="content" or quality_task["role"]!="quality" or not review_task_passed(c,quality_task["id"]):
                status,detail="review_required","Quality Agent chưa trả verdict=pass; không tạo lệnh xuất bản"
            else:
                status,detail="ready_to_create_tool",None
        if status != "ready_to_create_tool":
            if row["status"] != status:
                c.execute("UPDATE builder_runs SET status=?,error=?,updated_at=? WHERE id=?",
                          (status,detail,now(),run_id))
                audit(c,row["workspace_id"],"builder.run_status",status,run_id)
            return _run_view(c,c.execute("SELECT * FROM builder_runs WHERE id=?",(run_id,)).fetchone())
        if row["tool_job_id"]:
            return _run_view(c,row)
        tool=steps[-1].copy()
        ws=row["workspace_id"]
        src=records[-2]["id"]
        idem="builder:"+run_id+":publish"
    # No database transaction remains open before invoking the existing tool
    # handler, which has its own BEGIN IMMEDIATE and source-digest QA validation.
    try:
        data=FromAgentIn(source_task_id=src,action=tool["action"],
                         mode=tool.get("mode","draft"),
                         channel_ids=tool.get("channel_ids",[]),
                         scheduled_at=tool.get("scheduled_at"),
                         title=tool.get("title",""),
                         status=tool.get("status","draft"),
                         media=tool.get("media",[]),
                         idempotency_key=idem)
        created=create_tool_job(ws,ToolJobIn(action=data.action,idempotency_key=idem,
              payload=_tool_payload(data,records[-2]["result"])))
    except HTTPException as exc:
        with db() as c:
            c.execute("UPDATE builder_runs SET status='review_required',error=?,updated_at=? WHERE id=?",
                      (str(exc.detail)[:250],now(),run_id))
            audit(c,ws,"builder.tool_not_ready","Publisher request rejected",run_id)
            return _run_view(c,c.execute("SELECT * FROM builder_runs WHERE id=?",(run_id,)).fetchone())
    with db() as c:
        c.execute("UPDATE builder_runs SET status='awaiting_tool_approval',tool_job_id=?,updated_at=? WHERE id=?",
                  (created["id"],now(),run_id))
        audit(c,ws,"builder.tool_request_created","Tool request created, human approval required",run_id)
        return _run_view(c,c.execute("SELECT * FROM builder_runs WHERE id=?",(run_id,)).fetchone())


def _tool_payload(node: FromAgentIn, content: str) -> dict[str, Any]:
    if node.action == "wordpress.create_post":
        return {"title":node.title or "Nội dung từ LHM Agent AI",
                "content":content,"status":node.status,
                "source_task_id":node.source_task_id}
    payload={"content":content,"channel_ids":node.channel_ids,
             "mode":node.mode,"media":node.media,
             "source_task_id":node.source_task_id}
    if node.mode=="schedule":
        payload["scheduled_at"]=node.scheduled_at
    return payload


@router.post("/builder/runs/{run_id}/sync")
def sync_builder_run_api(run_id: str):
    return sync_builder_run(run_id)


def sync_ready_builder_runs(limit: int = 10) -> int:
    """Worker polls completed pipelines without requiring a browser session."""
    with db() as c:
        ids=[r["id"] for r in c.execute(
            "SELECT id FROM builder_runs WHERE status='running' ORDER BY created_at LIMIT ?",
            (min(30,max(1,limit)),)).fetchall()]
    for ident in ids:
        sync_builder_run(ident)
    return len(ids)
