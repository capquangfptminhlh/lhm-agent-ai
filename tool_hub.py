"""LHM Tool Hub: local-pilot tool registry + reviewed external execution.

IMPORTANT: This module intentionally does not permit AI-generated tool invocations
without a separate explicit approval. It uses one environment-scoped connection
per tool and consequently restricts external actions to LHM_TOOLS_WORKSPACE.
For SaaS production replace with per-tenant OAuth credential vault + scoped RBAC.
"""
from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Literal
from urllib.parse import urlparse

import httpx
from fastapi import APIRouter, HTTPException
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field

from core import ROLES, audit, db, new_id, now, workspace

router = APIRouter(tags=["Tool Hub"])
CATALOG = json.loads((Path(__file__).parent / "templates" / "tool_catalog.json").read_text(encoding="utf-8"))
TOOL_ACTIONS = {
    "postiz.create_post": {"tool": "postiz", "writes_external": True, "approval_required": True},
    "wordpress.create_post": {"tool": "wordpress", "writes_external": True, "approval_required": True},
    "activepieces.trigger": {"tool": "activepieces", "writes_external": True, "approval_required": True},
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
        """)
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
    return False


def _sanitize_response(value: Any) -> Any:
    """Keep task receipts small, never echo external bearer credentials."""
    if isinstance(value, list):
        return [_sanitize_response(x) for x in value[:50]]
    if isinstance(value, dict):
        return {k: _sanitize_response(v) for k, v in list(value.items())[:70]
                if str(k).lower() not in {"token", "access_token", "refresh_token", "api_key", "password", "authorization"}}
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
    action: Literal["postiz.create_post", "wordpress.create_post", "activepieces.trigger"]
    payload: dict[str, Any]
    idempotency_key: str = Field(min_length=6, max_length=120)


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
    else:
        raise HTTPException(422, "Tool chưa được cấp phép")


def _job_view(row) -> dict[str, Any]:
    obj = dict(row)
    obj["payload"] = json.loads(obj.pop("payload_json"))
    raw = obj.pop("result_json")
    obj["receipt"] = json.loads(raw) if raw else None
    return obj


@router.post("/workspaces/{ws}/tool-jobs", status_code=201)
def create_tool_job(ws: str, payload: ToolJobIn):
    _pilot_ws(ws)
    _assert_payload(payload.action, payload.payload)
    source = payload.payload.get("source_task_id")
    if source is not None:
        if not isinstance(source, str):
            raise HTTPException(422, "source_task_id không hợp lệ")
        with db() as c:
            task = c.execute("SELECT id,status FROM tasks WHERE id=? AND workspace_id=?",(source,ws)).fetchone()
        if not task or task["status"] != "completed":
            raise HTTPException(422, "Chỉ được dùng kết quả AI thực sự hoàn thành trong workspace này")
    with db() as c:
        c.execute("BEGIN IMMEDIATE")
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
        c.execute("UPDATE tool_jobs SET status='approved',updated_at=? WHERE id=?",(now(),job_id))
        audit(c,r["workspace_id"],"tool_job.approved",f"Approved: {r['action']}",job_id)
    return {"id":job_id,"status":"approved"}


async def dispatch_tool(action: str, p: dict[str, Any]) -> dict[str, Any]:
    if action == "postiz.create_post":
        return await _dispatch_postiz(p)
    if action == "wordpress.create_post":
        return await _dispatch_wordpress(p)
    if action == "activepieces.trigger":
        return await _dispatch_activepieces(p)
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