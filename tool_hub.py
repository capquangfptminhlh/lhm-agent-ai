"""LHM Tool Hub: local-pilot tool registry + reviewed external execution.

IMPORTANT: This module intentionally does not permit AI-generated tool invocations
without a separate explicit approval. It uses one environment-scoped connection
per tool and consequently restricts external actions to LHM_TOOLS_WORKSPACE.
For SaaS production replace with per-tenant OAuth credential vault + scoped RBAC.
"""
from __future__ import annotations

import json
import os
import hashlib
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Literal
from urllib.parse import urlparse

import httpx
from fastapi import APIRouter, HTTPException, UploadFile, File, Form
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field

from core import ROLES, audit, db, new_id, now, workspace
from publication_gate import approval_digest, assert_source_approved, prepare_reviews
from agent_tool_providers import (ACTIONS as SPECIALIST_ACTIONS, ROLE_ACTIONS as SPECIALIST_ROLE_ACTIONS,
    configured as specialist_configured, validate_payload as specialist_validate,
    dispatch as specialist_dispatch, comfyui_status as specialist_comfyui_status)
from department_tools import (ACTIONS as DEPARTMENT_ACTIONS,
    validate_payload as validate_department_payload,
    assert_origin as assert_department_origin, dispatch as dispatch_department)

router = APIRouter(tags=["Tool Hub"])
CATALOG = json.loads((Path(__file__).parent / "templates" / "tool_catalog.json").read_text(encoding="utf-8"))
TOOL_ACTIONS = {
    "postiz.create_post": {"tool": "postiz", "writes_external": True, "approval_required": True},
    "wordpress.create_post": {"tool": "wordpress", "writes_external": True, "approval_required": True},
    "activepieces.trigger": {"tool": "activepieces", "writes_external": True, "approval_required": True},
    **SPECIALIST_ACTIONS,
    **DEPARTMENT_ACTIONS,
}


def prepare_db() -> None:
    with db() as c:
        c.executescript("""
        CREATE TABLE IF NOT EXISTS tool_jobs (
          id TEXT PRIMARY KEY,
          workspace_id TEXT NOT NULL REFERENCES workspaces(id),
          action TEXT NOT NULL,
          payload_json TEXT NOT NULL,
          status TEXT NOT NULL,
          result_json TEXT,
          error TEXT,
          idempotency_key TEXT NOT NULL,
          created_at INTEGER NOT NULL,
          updated_at INTEGER NOT NULL,
          UNIQUE(workspace_id, idempotency_key)
        );
        CREATE INDEX IF NOT EXISTS ix_tool_jobs_workspace ON tool_jobs(workspace_id,created_at);
        CREATE TABLE IF NOT EXISTS specialist_handoffs (
          tool_job_id TEXT PRIMARY KEY REFERENCES tool_jobs(id),
          workspace_id TEXT NOT NULL, source_task_id TEXT NOT NULL,
          role TEXT NOT NULL, source_digest TEXT NOT NULL,
          created_at INTEGER NOT NULL
        );
        """)
        columns = {r["name"] for r in c.execute("PRAGMA table_info(tool_jobs)").fetchall()}
        if "approval_digest" not in columns:
            c.execute("ALTER TABLE tool_jobs ADD COLUMN approval_digest TEXT")
        prepare_reviews(c)
prepare_db()


def _pilot_ws(ws: str) -> None:
    # Never use a single provider credential across arbitrary customer workspaces.
    permitted = os.getenv("LHM_TOOLS_WORKSPACE", "demo")
    with db() as c:
        if not workspace(c, ws):
            raise HTTPException(404, "Doanh nghiệp không tồn tại")
    if ws != permitted:
        raise HTTPException(403, "Pilot chỉ hỗ trợ workspace đã gắn tài khoản tool; chưa có OAuth/Vault đa khách hàng")


def configured(tool: str) -> bool:
    if tool == "postiz":
        return bool(os.getenv("LHM_POSTIZ_API_KEY"))
    if tool == "wordpress":
        return all(os.getenv(k) for k in ("LHM_WP_URL", "LHM_WP_USER", "LHM_WP_APP_PASSWORD"))
    if tool == "activepieces":
        return bool(os.getenv("LHM_ACTIVEPIECES_WEBHOOK_URL"))
    return specialist_configured(tool)


def _sanitize_response(value: Any) -> Any:
    """Keep task receipts small, never echo external bearer credentials."""
    if isinstance(value, list):
        return [_sanitize_response(x) for x in value[:50]]
    if isinstance(value, dict):
        blocked = {"token", "access_token", "refresh_token", "api_key", "password", "authorization",
                   "client_secret", "secret", "private_key", "cookie", "set-cookie", "credential", "credentials"}
        return {k: _sanitize_response(v) for k, v in list(value.items())[:70]
                if str(k).lower().replace("-", "_") not in blocked}
    if isinstance(value, str):
        return value[:1800]
    return value


def _remote_error(err: Exception) -> str:
    # Avoid persisting upstream error bodies, which may expose tokens or private data.
    if isinstance(err, httpx.HTTPStatusError):
        return f"Provider HTTP {err.response.status_code}. Kiểm tra kết nối, quyền và nội dung trên hệ thống gốc."
    return f"{type(err).__name__}: Có lỗi kết nối; phải đối soát nhà cung cấp trước khi gửi lại."


def _postiz_base() -> str:
    base = os.getenv("LHM_POSTIZ_BASE_URL", "https://api.postiz.com/public/v1").rstrip("/")
    parsed = urlparse(base)
    if parsed.scheme != "https" and not (parsed.scheme == "http" and parsed.hostname in {"127.0.0.1", "localhost"}):
        raise ValueError("Postiz base URL phải dùng HTTPS hoặc localhost")
    return base


async def _postiz_request(method: str, endpoint: str, data: Any = None) -> Any:
    key = os.getenv("LHM_POSTIZ_API_KEY", "").strip()
    if not key:
        raise ValueError("Chưa cấu hình LHM_POSTIZ_API_KEY")
    # API auth and public/v1 paths validated with official Postiz documentation.
    async with httpx.AsyncClient(timeout=25, follow_redirects=False) as client:
        response = await client.request(method, _postiz_base() + endpoint,
                      headers={"Authorization": key, "Accept": "application/json"},
                      json=data if method in {"POST", "PUT"} else None)
        response.raise_for_status()
        return response.json()


MAX_POSTIZ_UPLOAD_BYTES = 25 * 1024 * 1024
POSTIZ_MEDIA = {
    "image/jpeg": {".jpg", ".jpeg"},
    "image/png": {".png"},
    "image/webp": {".webp"},
    "image/gif": {".gif"},
    "video/mp4": {".mp4"},
    "video/quicktime": {".mov"},
}


def _media_signature_valid(data: bytes, media_type: str) -> bool:
    if media_type == "image/jpeg":
        return data.startswith(b"\xff\xd8\xff")
    if media_type == "image/png":
        return data.startswith(b"\x89PNG\r\n\x1a\n")
    if media_type == "image/webp":
        return data[:4] == b"RIFF" and data[8:12] == b"WEBP"
    if media_type == "image/gif":
        return data.startswith((b"GIF87a", b"GIF89a"))
    if media_type in {"video/mp4", "video/quicktime"}:
        return len(data) >= 12 and data[4:8] == b"ftyp"
    return False


@router.post("/tools/postiz/media")
async def upload_postiz_media(file: UploadFile = File(...), workspace_id: str = Form("demo")):
    """Upload a local file directly to a configured Postiz pilot account.

    No arbitrary upload URL is accepted (to avoid SSRF); no media is published.
    The provider's ID and HTTPS path can later be attached to a reviewed post.
    """
    _pilot_ws(workspace_id)
    if not configured("postiz"):
        raise HTTPException(409, "Chưa kết nối Postiz API key")
    filename = Path(file.filename or "").name
    media_type = str(file.content_type or "").lower().split(";", 1)[0].strip()
    suffix = Path(filename).suffix.lower()
    if not filename or media_type not in POSTIZ_MEDIA or suffix not in POSTIZ_MEDIA[media_type]:
        raise HTTPException(422, "Chỉ hỗ trợ JPEG, PNG, WEBP, GIF, MP4, MOV đúng định dạng")
    data = await file.read(MAX_POSTIZ_UPLOAD_BYTES + 1)
    if len(data) == 0 or len(data) > MAX_POSTIZ_UPLOAD_BYTES:
        raise HTTPException(413, "Media rỗng hoặc quá 25 MB; hãy nén file trước")
    if not _media_signature_valid(data, media_type):
        raise HTTPException(422, "Dữ liệu file không khớp định dạng ảnh/video khai báo")
    try:
        async with httpx.AsyncClient(timeout=httpx.Timeout(120, connect=10), follow_redirects=False) as client:
            resp = await client.post(_postiz_base() + "/upload",
                headers={"Authorization": os.environ["LHM_POSTIZ_API_KEY"].strip()},
                files={"file": (filename, data, media_type)})
        resp.raise_for_status()
        result = resp.json()
        if not isinstance(result, dict) or not isinstance(result.get("id"), str) or not isinstance(result.get("path"), str):
            raise ValueError("Postiz không trả id/path")
        if urlparse(result["path"]).scheme != "https":
            raise ValueError("Postiz không trả HTTPS media URL")
        return {"id": result["id"], "path": result["path"],
                "bytes": len(data), "media_type":media_type,
                "confirmation":"uploaded_to_postiz_not_published"}
    except httpx.HTTPStatusError as err:
        code = err.response.status_code
        raise HTTPException(429 if code == 429 else 502,
                            f"Postiz upload lỗi HTTP {code}; không lưu media ID giả") from err
    except (httpx.RequestError, ValueError, TypeError, KeyError) as err:
        raise HTTPException(502, "Không xác minh được media trên Postiz; thử lại sau khi kiểm tra tài khoản") from err
    finally:
        await file.close()


@router.get("/agent-tools")
def specialist_tools_page():
    return FileResponse(Path(__file__).parent / "static" / "agent_tools.html",
                        media_type="text/html")


@router.get("/tool-hub")
def tool_hub_page():
    return FileResponse(Path(__file__).parent / "static" / "tool_hub.html", media_type="text/html")


@router.get("/tools/catalog")
def tool_catalog():
    repo_status = []
    for r in CATALOG["repositories"]:
        rr = {**r}
        rr["configured"] = configured(r["id"])
        rr["connection_verified"] = False  # Only live-read health can confirm.
        repo_status.append(rr)
    return {"roles": {k: v[0] for k, v in ROLES.items()},
            "jobs": CATALOG["jobs"], "repositories": repo_status,
            "actions": TOOL_ACTIONS, "pilot_workspace": os.getenv("LHM_TOOLS_WORKSPACE", "demo")}


@router.get("/workspaces/{ws}/tools/ready-tasks")
def ready_agent_drafts(ws: str):
    """Human chooses from actual completed AI tasks; no automatic posting."""
    _pilot_ws(ws)
    with db() as c:
        rows = c.execute("SELECT id,role,instruction,result,updated_at FROM tasks WHERE workspace_id=? AND status=\'completed\' AND result IS NOT NULL ORDER BY updated_at DESC LIMIT 60",(ws,)).fetchall()
    return {"drafts":[{"id":r["id"],"role":r["role"],"instruction":r["instruction"],"text":r["result"][:15000],"updated_at":r["updated_at"]} for r in rows]}


@router.get("/tools/specialists/readiness")
def specialist_readiness():
    """Configuration presence only; does not claim provider authentication passed."""
    return {"providers":[{"name":k,"configured":specialist_configured(k),
            "connection_verified":False,"actions":[a for a,v in SPECIALIST_ACTIONS.items() if v["tool"]==k]}
            for k in ("crawl4ai","twenty","chatwoot","comfyui")],
            "scope":"single_pilot_workspace_only",
            "role_actions":{role:sorted(actions) for role,actions in SPECIALIST_ROLE_ACTIONS.items()}}


@router.get("/tools/comfyui/jobs/{prompt_id}")
async def check_comfyui_image(prompt_id: str, workspace_id: str="demo"):
    _pilot_ws(workspace_id)
    # Only inspect jobs that were actually accepted for this pilot workspace.
    with db() as c:
        jobs=c.execute("SELECT result_json FROM tool_jobs WHERE workspace_id=? AND action='comfyui.generate_image' AND result_json IS NOT NULL",
                      (workspace_id,)).fetchall()
    known=False
    for job in jobs:
        try:
            receipt=json.loads(job["result_json"])
            if receipt.get("prompt_id")==prompt_id:
                known=True
                break
        except (json.JSONDecodeError,AttributeError):
            pass
    if not known:
        raise HTTPException(404,"ComfyUI prompt_id không thuộc dữ liệu đã ghi nhận")
    try:
        return await specialist_comfyui_status(prompt_id)
    except httpx.HTTPError as err:
        raise HTTPException(502,_remote_error(err)) from err


@router.get("/tools/postiz/channels")
async def postiz_channels(workspace_id: str = "demo"):
    _pilot_ws(workspace_id)
    try:
        raw = await _postiz_request("GET", "/integrations")
    except (httpx.HTTPError, ValueError) as ex:
        raise HTTPException(502 if configured("postiz") else 409, _remote_error(ex) if configured("postiz") else str(ex)) from ex
    if not isinstance(raw, list):
        raise HTTPException(502, "Postiz trả về danh sách kênh không hợp lệ")
    items = [{"id": x.get("id"), "name": x.get("name"), "platform": x.get("identifier"),
              "disabled": bool(x.get("disabled", False)), "profile": x.get("profile")}
             for x in raw if isinstance(x, dict) and x.get("id")]
    return {"channels": items, "connection_verified": True, "count": len(items)}


class ToolJobIn(BaseModel):
    action: str = Field(min_length=4, max_length=80, pattern=r"^[a-z][a-z0-9_.-]+$")
    payload: dict[str, Any]
    idempotency_key: str = Field(min_length=6, max_length=120)


class FromAgentIn(BaseModel):
    source_task_id: str = Field(min_length=10, max_length=80)
    action: Literal["postiz.create_post", "wordpress.create_post"]
    idempotency_key: str = Field(min_length=6, max_length=120)
    mode: Literal["draft", "now", "schedule"] = "draft"
    channel_ids: list[str] = Field(default_factory=list)
    scheduled_at: str | None = None
    title: str = ""
    status: Literal["draft", "publish"] = "draft"
    media: list[dict[str, Any]] = Field(default_factory=list)


@router.post("/workspaces/{ws}/tool-jobs/from-agent", status_code=201)
def create_from_agent(ws: str, inp: FromAgentIn):
    """Copy the exact completed Agent output into a reviewed external-action request.

    No AI text is executed without the separate approval state transition.
    """
    _pilot_ws(ws)
    with db() as c:
        task = c.execute("SELECT role,instruction,result,status FROM tasks WHERE id=? AND workspace_id=?",
                         (inp.source_task_id, ws)).fetchone()
    if not task or task["status"] != "completed" or not task["result"]:
        raise HTTPException(422, "Agent chưa hoàn thành hoặc đầu ra trống")
    if task['role'] not in {"content", "research", "strategy", "sales", "support"}:
        raise HTTPException(422, "Vai trò Agent này không tạo bản nháp để xuất bản")
    if inp.action == "postiz.create_post":
        payload = {"mode": inp.mode, "content": task["result"],
                   "channel_ids": inp.channel_ids, "media": inp.media,
                   "source_task_id": inp.source_task_id}
        if inp.mode == "schedule": payload["scheduled_at"] = inp.scheduled_at
    else:
        payload = {"title": inp.title.strip() or task["instruction"][:180],
                   "content": task["result"], "status": inp.status,
                   "source_task_id": inp.source_task_id}
    return create_tool_job(ws, ToolJobIn(action=inp.action, payload=payload,
                                          idempotency_key=inp.idempotency_key))


def _assert_payload(action: str, p: dict[str, Any]) -> None:
    if action == "postiz.create_post":
        if p.get("mode") not in ("now", "schedule", "draft"):
            raise HTTPException(422, "mode phải là now, schedule hoặc draft")
        if not isinstance(p.get("content"), str) or not (1 <= len(p["content"].strip()) <= 15000):
            raise HTTPException(422, "Nội dung rỗng hoặc quá dài")
        channels = p.get("channel_ids")
        if not isinstance(channels, list) or not (1 <= len(channels) <= 10) or any(not isinstance(c, str) or not c for c in channels) or len(channels) != len(set(channels)):
            raise HTTPException(422, "Chọn từ 1 đến 10 kênh riêng biệt")
        if p["mode"] == "schedule":
            try:
                t = datetime.fromisoformat(str(p.get("scheduled_at", "")).replace("Z", "+00:00"))
                if t.tzinfo is None or t.astimezone(timezone.utc).timestamp() < now() + 60:
                    raise ValueError("Must be future timezone-aware timestamp")
            except (ValueError, TypeError):
                raise HTTPException(422, "scheduled_at phải là giờ UTC có timezone, trong tương lai")
        media = p.get("media", [])
        if not isinstance(media, list) or len(media) > 12:
            raise HTTPException(422, "media phải là danh sách tối đa 12 file đã upload")
        for item in media:
            if not isinstance(item, dict) or not isinstance(item.get("id"), str) or not isinstance(item.get("path"), str) or not item["path"].startswith("https://"):
                raise HTTPException(422, "Media phải có id và đường dẫn HTTPS đã upload lên Postiz")
    elif action == "wordpress.create_post":
        if not isinstance(p.get("title"), str) or not p["title"].strip() or len(p["title"]) > 240:
            raise HTTPException(422, "Thiếu tiêu đề WordPress")
        if not isinstance(p.get("content"), str) or not p["content"].strip() or len(p["content"]) > 100000:
            raise HTTPException(422, "Thiếu nội dung WordPress")
        if p.get("status", "draft") not in ("draft", "publish"):
            raise HTTPException(422, "Chỉ cho phép draft hoặc publish")
    elif action == "activepieces.trigger":
        if not isinstance(p.get("data"), dict):
            raise HTTPException(422, "Thiếu data dict")
        if len(json.dumps(p["data"], ensure_ascii=False)) > 15000:
            raise HTTPException(422, "Payload workflow quá lớn")
    elif action in SPECIALIST_ACTIONS:
        specialist_validate(action,p)
    elif action in DEPARTMENT_ACTIONS:
        validate_department_payload(action,p)
    else:
        raise HTTPException(422, "Tool chưa được cấp phép")


def _assert_publish_agent_scope(c, ws: str, action: str, payload: dict[str,Any]) -> None:
    """Server-side RBAC for AI-origin publishing, rechecked at approval and dispatch.

    Owner-authored jobs without source_task_id are distinct and still require
    manual approval. No Sales/QA/Research AI result may impersonate Content.
    """
    if action not in {"postiz.create_post","wordpress.create_post"}:
        return
    source=payload.get("source_task_id")
    if source is None:
        return
    record=c.execute("SELECT role FROM tasks WHERE workspace_id=? AND id=?",
                     (ws,source)).fetchone()
    if not record or record["role"]!="content":
        raise HTTPException(403,"Chỉ Agent Content có quyền đưa nội dung AI sang công cụ xuất bản")
    if action=="postiz.create_post":
        channel_ids=payload.get("channel_ids",[])
        authorized={x.strip() for x in os.getenv("LHM_CONTENT_POSTIZ_ALLOWED_CHANNEL_IDS","").split(",")
                    if x.strip()}
        if not authorized or not isinstance(channel_ids,list) or set(channel_ids)-authorized:
            raise HTTPException(403,"Kênh Postiz không nằm trong whitelist đăng bài của Agent Content")


def _job_view(row) -> dict[str, Any]:
    obj = dict(row)
    obj["payload"] = json.loads(obj.pop("payload_json"))
    raw = obj.pop("result_json")
    obj["receipt"] = json.loads(raw) if raw else None
    return obj


class SpecialistHandoffIn(BaseModel):
    """Bind a completed role-specific Agent task to an approved specialist action."""
    source_task_id: str = Field(min_length=10, max_length=80)
    action: Literal["crawl4ai.crawl","twenty.create_person",
                    "chatwoot.create_contact","comfyui.generate_image"]
    payload: dict[str,Any]
    idempotency_key: str = Field(min_length=6, max_length=120)


def _assert_specialist_origin(c, job_id: str, workspace_id: str) -> None:
    handoff=c.execute("SELECT * FROM specialist_handoffs WHERE tool_job_id=? AND workspace_id=?",
                      (job_id,workspace_id)).fetchone()
    if handoff is None:
        return
    source=c.execute("SELECT role,status,result FROM tasks WHERE id=? AND workspace_id=?",
                     (handoff["source_task_id"],workspace_id)).fetchone()
    if not source or source["status"]!="completed" or source["role"]!=handoff["role"]:
        raise HTTPException(409,"Nhiệm vụ nguồn Agent không còn hợp lệ")
    expected=hashlib.sha256(str(source["result"] or "").encode("utf-8")).hexdigest()
    if expected!=handoff["source_digest"]:
        raise HTTPException(409,"Kết quả Agent thay đổi; cần phê duyệt lại yêu cầu tool")


@router.post("/workspaces/{ws}/tool-jobs/from-specialist-agent", status_code=201)
def create_specialist_from_agent(ws: str, inp: SpecialistHandoffIn):
    _pilot_ws(ws)
    with db() as c:
        source=c.execute("SELECT role,status,result FROM tasks WHERE id=? AND workspace_id=?",
                         (inp.source_task_id,ws)).fetchone()
        if not source or source["status"]!="completed" or not source["result"]:
            raise HTTPException(409,"Agent chưa hoàn thành nhiệm vụ có kết quả")
        if inp.action not in SPECIALIST_ROLE_ACTIONS.get(source["role"],set()):
            raise HTTPException(403,"Agent không được giao quyền sử dụng tool này")
        origin_digest=hashlib.sha256(str(source["result"]).encode("utf-8")).hexdigest()
    specialist_validate(inp.action,inp.payload)
    job=create_tool_job(ws,ToolJobIn(action=inp.action,payload=inp.payload,
                                       idempotency_key=inp.idempotency_key))
    with db() as c:
        c.execute("BEGIN IMMEDIATE")
        existing=c.execute("SELECT * FROM specialist_handoffs WHERE tool_job_id=?",
                           (job["id"],)).fetchone()
        if existing and (existing["source_task_id"]!=inp.source_task_id or
                         existing["source_digest"]!=origin_digest):
            raise HTTPException(409,"Idempotency key đã gắn với nhiệm vụ Agent khác")
        if not existing:
            c.execute("""INSERT INTO specialist_handoffs
              (tool_job_id,workspace_id,source_task_id,role,source_digest,created_at)
              VALUES (?,?,?,?,?,?)""",
              (job["id"],ws,inp.source_task_id,source["role"],origin_digest,now()))
            audit(c,ws,"specialist.agent_handoff",
                  f"{source['role']} → {inp.action}; pending human approval",job["id"])
    return {**job,"agent_source":{"task_id":inp.source_task_id,"role":source["role"]}}


@router.post("/workspaces/{ws}/tool-jobs", status_code=201)
def create_tool_job(ws: str, payload: ToolJobIn):
    _pilot_ws(ws)
    _assert_payload(payload.action, payload.payload)
    source = payload.payload.get("source_task_id")
    if source is not None:
        if not isinstance(source, str):
            raise HTTPException(422, "source_task_id không hợp lệ")
        with db() as c:
            _assert_publish_agent_scope(c,ws,payload.action,payload.payload)
            assert_source_approved(c, ws, payload.action, payload.payload)
    with db() as c:
        c.execute("BEGIN IMMEDIATE")
        _assert_publish_agent_scope(c,ws,payload.action,payload.payload)
        assert_source_approved(c, ws, payload.action, payload.payload)
        if payload.action in DEPARTMENT_ACTIONS:
            assert_department_origin(c, ws, payload.action, payload.payload)
        old = c.execute("SELECT * FROM tool_jobs WHERE workspace_id=? AND idempotency_key=?",(ws,payload.idempotency_key)).fetchone()
        if old:
            if old["action"] != payload.action or json.loads(old["payload_json"]) != payload.payload:
                raise HTTPException(409,"Idempotency key đã dùng cho nội dung khác")
            return {**_job_view(old), "deduplicated": True}
        ident = new_id()
        c.execute("INSERT INTO tool_jobs(id,workspace_id,action,payload_json,status,idempotency_key,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?)",
                  (ident,ws,payload.action,json.dumps(payload.payload,ensure_ascii=False),"awaiting_approval",payload.idempotency_key,now(),now()))
        audit(c,ws,"tool_job.created",f"Waiting approval: {payload.action}",ident)
        row = c.execute("SELECT * FROM tool_jobs WHERE id=?",(ident,)).fetchone()
    return {**_job_view(row), "deduplicated": False}


@router.get("/workspaces/{ws}/tool-jobs")
def list_tool_jobs(ws: str, limit: int = 100):
    _pilot_ws(ws)
    with db() as c:
        rows = c.execute("SELECT * FROM tool_jobs WHERE workspace_id=? ORDER BY created_at DESC,rowid DESC LIMIT ?",(ws,min(200,max(1,limit)))).fetchall()
    return {"jobs": [_job_view(x) for x in rows]}


@router.post("/tool-jobs/{job_id}/approve")
def approve_tool_job(job_id: str):
    with db() as c:
        c.execute("BEGIN IMMEDIATE")
        r = c.execute("SELECT * FROM tool_jobs WHERE id=?",(job_id,)).fetchone()
        if not r: raise HTTPException(404,"Không tìm thấy yêu cầu tool")
        _pilot_ws(r["workspace_id"])
        if r["status"] != "awaiting_approval": raise HTTPException(409,"Yêu cầu không ở trạng thái chờ duyệt")
        body = json.loads(r["payload_json"])
        _assert_publish_agent_scope(c,r["workspace_id"],r["action"],body)
        assert_source_approved(c, r["workspace_id"], r["action"], body)
        if r["action"] in DEPARTMENT_ACTIONS:
            assert_department_origin(c, r["workspace_id"], r["action"], body)
        _assert_specialist_origin(c,job_id,r["workspace_id"])
        bound_digest = approval_digest(r["action"], body)
        c.execute("UPDATE tool_jobs SET status='approved',approval_digest=?,updated_at=? WHERE id=?",
                  (bound_digest,now(),job_id))
        audit(c,r["workspace_id"],"tool_job.approved",f"Approved: {r['action']}",job_id)
    return {"id":job_id,"status":"approved"}


async def dispatch_tool(action: str, p: dict[str, Any]) -> dict[str, Any]:
    if action == "postiz.create_post":
        return await _dispatch_postiz(p)
    if action == "wordpress.create_post":
        return await _dispatch_wordpress(p)
    if action == "activepieces.trigger":
        return await _dispatch_activepieces(p)
    if action in SPECIALIST_ACTIONS:
        return await specialist_dispatch(action,p)
    if action in DEPARTMENT_ACTIONS:
        return await dispatch_department(action,p)
    raise ValueError("Unsupported action")


async def _dispatch_postiz(p: dict[str, Any]) -> dict[str, Any]:
    raw = await _postiz_request("GET", "/integrations")
    if not isinstance(raw,list): raise ValueError("Không lấy được danh sách kênh Postiz")
    channels = {str(x["id"]):x for x in raw if isinstance(x,dict) and x.get("id") and not x.get("disabled")}
    selected = p["channel_ids"]
    if any(k not in channels for k in selected):
        raise ValueError("Kênh không tồn tại hoặc đã bị vô hiệu hóa trong Postiz")
    media = p.get("media",[])
    custom = p.get("settings_by_channel",{})
    posts = []
    for id in selected:
        platform = channels[id].get("identifier")
        settings = dict(custom.get(id,{}) if isinstance(custom,dict) else {})
        if not isinstance(platform,str): raise ValueError("Kênh thiếu định danh nền tảng")
        # Server decides the platform type based on actual connected account.
        settings["__type"] = platform
        if platform in ("instagram","instagram-standalone"):
            if not media: raise ValueError("Instagram cần media; hãy upload ảnh/video vào Postiz trước")
            settings.setdefault("post_type", "post")
        elif platform in ("youtube","tiktok"):
            if not media: raise ValueError(f"{platform} cần video/media đã upload")
            required=("title","type") if platform=="youtube" else ("privacy_level","duet","stitch","comment","autoAddMusic","brand_content_toggle","brand_organic_toggle","content_posting_method")
            if any(k not in settings for k in required): raise ValueError(f"Thiếu trường cấu hình {platform}, kiểm tra tài liệu Postiz")
        elif platform == "reddit" and "subreddit" not in settings:
            raise ValueError("Reddit yêu cầu subreddit[]")
        posts.append({"integration":{"id":id},"value":[{"content":p["content"],"image":media}],"settings":settings})
    d = datetime.now(timezone.utc) if p["mode"] != "schedule" else datetime.fromisoformat(p["scheduled_at"].replace("Z","+00:00"))
    body = {"type": p["mode"],"date":d.astimezone(timezone.utc).isoformat().replace("+00:00","Z"),"shortLink":False,"tags":[],"posts":posts}
    receipt = await _postiz_request("POST", "/posts", body)
    return {"provider":"postiz","provider_receipt":_sanitize_response(receipt),
            "confirmation":"draft_created" if p["mode"]=="draft" else "scheduled_unverified" if p["mode"]=="schedule" else "submitted_unverified",
            "note":"Postiz nhận yêu cầu không đồng nghĩa bài đã công khai. Đối soát qua Postiz."}


async def _dispatch_wordpress(p: dict[str, Any]) -> dict[str, Any]:
    url = os.getenv("LHM_WP_URL", "").rstrip("/")
    user = os.getenv("LHM_WP_USER", "")
    password = os.getenv("LHM_WP_APP_PASSWORD", "")
    parsed = urlparse(url)
    if not url or not user or not password: raise ValueError("Chưa cấu hình WordPress connection")
    if parsed.scheme != "https" and not (parsed.scheme == "http" and parsed.hostname in ("127.0.0.1","localhost")):
        raise ValueError("WordPress phải sử dụng HTTPS hoặc localhost")
    async with httpx.AsyncClient(timeout=25,follow_redirects=False,auth=(user,password)) as client:
        resp = await client.post(f"{url}/wp-json/wp/v2/posts",json={"title":p["title"],"content":p["content"],"status":p.get("status","draft")})
        resp.raise_for_status()
        data = resp.json()
    return {"provider":"wordpress","post_id":data.get("id"),"link":data.get("link"),
            "provider_status":data.get("status"),"confirmation":"provider_record_created",
            "note":"Cần xác minh URL công khai/canonical nếu muốn xác nhận đã xuất bản."}


async def _dispatch_activepieces(p: dict[str, Any]) -> dict[str, Any]:
    url = os.getenv("LHM_ACTIVEPIECES_WEBHOOK_URL", "")
    parsed = urlparse(url)
    if not url: raise ValueError("Chưa cấu hình Activepieces Webhook")
    if parsed.scheme != "https" and not (parsed.scheme == "http" and parsed.hostname in ("127.0.0.1","localhost")):
        raise ValueError("Webhook URL phải HTTPS hoặc localhost")
    async with httpx.AsyncClient(timeout=25,follow_redirects=False) as client:
        res = await client.post(url,json={"source":"LHM Agent OS","data":p["data"]})
        res.raise_for_status()
    return {"provider":"activepieces","response_status":res.status_code,
            "confirmation":"workflow_triggered_unverified", "note":"Webhook nhận thành công; cần kết quả flow/webhook callback để xác minh tác vụ cuối."}


@router.post("/tool-jobs/{job_id}/execute")
async def execute_tool_job(job_id: str):
    with db() as c:
        c.execute("BEGIN IMMEDIATE")
        row = c.execute("SELECT * FROM tool_jobs WHERE id=?", (job_id,)).fetchone()
        if not row: raise HTTPException(404,"Không tìm thấy tool job")
        _pilot_ws(row["workspace_id"])
        if row["status"] != "approved": raise HTTPException(409,"Chỉ được thực thi yêu cầu đã duyệt và chưa gửi")
        body = json.loads(row["payload_json"])
        if not row["approval_digest"] or row["approval_digest"] != approval_digest(row["action"], body):
            raise HTTPException(409,"Payload hoặc đích xuất bản đã thay đổi; cần duyệt lại")
        _assert_publish_agent_scope(c,row["workspace_id"],row["action"],body)
        assert_source_approved(c, row["workspace_id"], row["action"], body)
        if row["action"] in DEPARTMENT_ACTIONS:
            assert_department_origin(c, row["workspace_id"], row["action"], body)
        _assert_specialist_origin(c,job_id,row["workspace_id"])
        c.execute("UPDATE tool_jobs SET status='executing',updated_at=? WHERE id=?",(now(),job_id))
        audit(c,row["workspace_id"],"tool_job.executing","Provider call started",job_id)
    try:
        receipt = await dispatch_tool(row["action"],json.loads(row["payload_json"]))
    except (httpx.HTTPError,ValueError,TypeError) as ex:
        # Network/timeouts/5xx are ambiguous: NEVER blindly retry posting.
        definitive = isinstance(ex,ValueError) or (isinstance(ex,httpx.HTTPStatusError) and 400 <= ex.response.status_code < 500)
        new_status = "failed_validation" if definitive else "needs_reconciliation"
        with db() as c:
            c.execute("UPDATE tool_jobs SET status=?,error=?,updated_at=? WHERE id=?",(new_status,_remote_error(ex) if isinstance(ex,httpx.HTTPError) else str(ex)[:250],now(),job_id))
            audit(c,row["workspace_id"],"tool_job.error",f"{new_status}: {row['action']}",job_id)
        raise HTTPException(502,_remote_error(ex) if isinstance(ex,httpx.HTTPError) else str(ex)[:250]) from ex
    except Exception as ex:
        with db() as c:
            c.execute("UPDATE tool_jobs SET status='needs_reconciliation',error=?,updated_at=? WHERE id=?",("Uncertain provider result",now(),job_id))
            audit(c,row["workspace_id"],"tool_job.error","Uncertain remote result",job_id)
        raise HTTPException(502,"Không xác định kết quả từ nhà cung cấp. Kiểm tra bên ngoài trước khi thử lại.") from ex
    with db() as c:
        c.execute("UPDATE tool_jobs SET status='accepted_unverified',result_json=?,updated_at=? WHERE id=?",
                  (json.dumps(receipt,ensure_ascii=False),now(),job_id))
        audit(c,row["workspace_id"],"tool_job.accepted",f"Provider accepted: {row['action']}",job_id)
    return {"id":job_id,"status":"accepted_unverified","receipt":receipt}

async def _verify_wordpress_public(receipt: dict[str, Any]) -> dict[str, Any]:
    """Read-only confirmation: WP status publish AND anonymous 200 on same-origin URL.

    Does not prove Google indexing or search ranking. Never follow cross-host redirects.
    """
    base = os.getenv("LHM_WP_URL", "").rstrip("/")
    user, password = os.getenv("LHM_WP_USER", ""), os.getenv("LHM_WP_APP_PASSWORD", "")
    origin = urlparse(base)
    if not user or not password or not base or not origin.hostname:
        raise ValueError("WordPress connection not configured")
    if origin.scheme != "https" and not (origin.scheme == "http" and origin.hostname in {"localhost", "127.0.0.1"}):
        raise ValueError("WordPress URL requires HTTPS or localhost")
    post_id = receipt.get("post_id")
    if type(post_id) is not int or post_id <= 0:
        raise ValueError("WordPress receipt has no valid post ID")
    async with httpx.AsyncClient(timeout=20, follow_redirects=False, auth=(user,password)) as client:
        response = await client.get(f"{base}/wp-json/wp/v2/posts/{post_id}")
        response.raise_for_status()
        post = response.json()
    if not isinstance(post, dict) or post.get("id") != post_id:
        raise ValueError("WordPress returned an unexpected post")
    status, link = post.get("status"), post.get("link")
    if status != "publish":
        return {"verified":False,"provider_status":status,"reason":"WordPress post is not published"}
    target = urlparse(link) if isinstance(link,str) else None
    if not target or target.scheme != origin.scheme or target.netloc.lower() != origin.netloc.lower():
        raise ValueError("Public link host does not match configured WordPress site")
    # The link is issued by the authenticated site, but may still be untrusted.
    # Never follow redirects to other hosts or internal services.
    async with httpx.AsyncClient(timeout=20, follow_redirects=False) as client:
        check = await client.get(link)
    return {"verified": check.status_code == 200,
            "provider_status": status,
            "public_url": link,
            "public_http_status":check.status_code,
            "reason":"HTTP 200 on public same-origin link" if check.status_code == 200 else "URL not accessible anonymously"}


@router.post("/tool-jobs/{job_id}/verify")
async def verify_tool_job(job_id: str):
    """Independent provider read-back: WordPress public URL or Postiz published state/URL."""
    with db() as c:
        job=c.execute("SELECT * FROM tool_jobs WHERE id=?",(job_id,)).fetchone()
        if not job: raise HTTPException(404,"Tool job not found")
        _pilot_ws(job["workspace_id"])
        if job["action"] not in {"wordpress.create_post","postiz.create_post"}:
            raise HTTPException(422,"Verification is supported only for WordPress and Postiz")
        if job["status"] not in {"accepted_unverified", "verified_published"}:
            raise HTTPException(409,"No accepted provider receipt available")
        receipt=json.loads(job["result_json"] or "{}")
    try:
        if job["action"]=="wordpress.create_post":
            result=await _verify_wordpress_public(receipt)
        else:
            from postiz_verifier import verify_postiz_receipt
            result=await verify_postiz_receipt(receipt,_postiz_request)
    except (httpx.HTTPError, ValueError, TypeError) as exc:
        # A failed readback must not mutate an already-submitted job to failed.
        raise HTTPException(502,_remote_error(exc) if isinstance(exc,httpx.HTTPError) else str(exc)[:250]) from exc
    if result["verified"]:
        with db() as c:
            c.execute("BEGIN IMMEDIATE")
            c.execute("UPDATE tool_jobs SET status='verified_published',updated_at=? WHERE id=? AND status IN ('accepted_unverified','verified_published')",(now(),job_id))
            audit(c,job["workspace_id"],"tool_job.verified",
                  "Verified provider readback for "+job["action"],job_id)
    return {"id":job_id,"status":"verified_published" if result["verified"] else "accepted_unverified", "verification": result}
