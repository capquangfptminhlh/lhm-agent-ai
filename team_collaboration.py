"""LHM Agent-to-Agent Team Chat (local pilot).

Creates actual LLM tasks through the existing task queue and hands off
bounded, persisted messages between authorized participants. No virtual
conversation is fabricated: only completed task results become Agent messages.
No Agent can grant tool permissions, launch a computer or publish externally
through chat instructions.
"""
from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from typing import Any

from fastapi import APIRouter, HTTPException
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field, model_validator

from core import MODEL, ROLES, audit, db, new_id, now, workspace

router = APIRouter(tags=["Agent Team Chat"])
ROOT = Path(__file__).parent
MAX_TURNS = 16
MAX_CONTEXT = 4500
MAX_MESSAGE = 2400


class TeamStartIn(BaseModel):
    objective: str = Field(min_length=10, max_length=2000)
    roles: list[str] = Field(min_length=2, max_length=8)
    context: str = Field(default="", max_length=3000)
    max_turns: int = Field(default=8, ge=2, le=MAX_TURNS)
    first_role: str | None = None
    requires_approval: bool = False
    idempotency_key: str | None = Field(default=None, min_length=6, max_length=100)

    @model_validator(mode="after")
    def check_roles(self):
        if len(self.roles) != len(set(self.roles)) or any(r not in ROLES for r in self.roles):
            raise ValueError("Danh sách Agent phải gồm 2–8 vai trò hợp lệ, không trùng")
        if self.first_role is not None and self.first_role not in self.roles:
            raise ValueError("Agent khởi đầu phải thuộc nhóm")
        return self


class HumanNoteIn(BaseModel):
    content: str = Field(min_length=2, max_length=1000)


def prepare_db() -> None:
    with db() as c:
        c.executescript("""
        CREATE TABLE IF NOT EXISTS team_sessions (
          id TEXT PRIMARY KEY,
          workspace_id TEXT NOT NULL REFERENCES workspaces(id),
          objective TEXT NOT NULL,
          context TEXT NOT NULL,
          roles_json TEXT NOT NULL,
          model TEXT NOT NULL,
          status TEXT NOT NULL,
          max_turns INTEGER NOT NULL,
          turn_count INTEGER NOT NULL DEFAULT 0,
          active_task_id TEXT,
          last_agent_role TEXT,
          outcome TEXT,
          error TEXT,
          idempotency_key TEXT,
          created_at INTEGER NOT NULL,
          updated_at INTEGER NOT NULL,
          UNIQUE(workspace_id,idempotency_key)
        );
        CREATE TABLE IF NOT EXISTS team_messages (
          id TEXT PRIMARY KEY,
          session_id TEXT NOT NULL REFERENCES team_sessions(id),
          workspace_id TEXT NOT NULL,
          sender_role TEXT NOT NULL,
          recipient_role TEXT,
          kind TEXT NOT NULL,
          content TEXT NOT NULL,
          task_id TEXT UNIQUE,
          created_at INTEGER NOT NULL
        );
        CREATE INDEX IF NOT EXISTS ix_team_session_ws
          ON team_sessions(workspace_id,created_at);
        CREATE INDEX IF NOT EXISTS ix_team_messages_session
          ON team_messages(session_id,created_at);
        CREATE INDEX IF NOT EXISTS ix_team_active
          ON team_sessions(status,created_at);
        """)
prepare_db()


def _lookup(c: sqlite3.Connection, ws: str, sid: str) -> sqlite3.Row:
    row=c.execute("SELECT * FROM team_sessions WHERE id=? AND workspace_id=?",
                  (sid,ws)).fetchone()
    if not row:
        raise HTTPException(404,"Không tìm thấy cuộc trao đổi của doanh nghiệp")
    return row


def _validate_workspace(c: sqlite3.Connection, ws: str) -> None:
    if not workspace(c,ws):
        raise HTTPException(404,"Không tìm thấy doanh nghiệp")


def _messages(c: sqlite3.Connection, ws: str, sid: str, limit: int = 80) -> list[dict[str,Any]]:
    rows=c.execute("""SELECT id,sender_role,recipient_role,kind,content,task_id,created_at
        FROM team_messages WHERE workspace_id=? AND session_id=?
        ORDER BY created_at ASC,rowid ASC LIMIT ?""",
        (ws,sid,max(1,min(limit,160)))).fetchall()
    return [dict(x) for x in rows]


def _view(c: sqlite3.Connection, r: sqlite3.Row, *, include_messages: bool = False) -> dict[str,Any]:
    data=dict(r)
    data["roles"]=json.loads(data.pop("roles_json"))
    # Don't return high-volume private context on every list request.
    data.pop("context",None)
    if data["active_task_id"]:
        task=c.execute("SELECT status,role,error,updated_at FROM tasks WHERE id=? AND workspace_id=?",
                       (data["active_task_id"],r["workspace_id"])).fetchone()
        data["active_task"]=dict(task) if task else None
    else:
        data["active_task"]=None
    if include_messages:
        data["messages"]=_messages(c,r["workspace_id"],r["id"])
    return data


def _choose_next(roles: list[str], current: str, requested: str | None) -> str:
    if requested in roles and requested!=current:
        return requested
    return roles[(roles.index(current)+1)%len(roles)]


def _format_prompt(c: sqlite3.Connection, session: sqlite3.Row,
                   next_role: str, request: str) -> tuple[str,str]:
    roles=json.loads(session["roles_json"])
    recent=c.execute("""SELECT sender_role,recipient_role,content,kind
        FROM team_messages WHERE session_id=? ORDER BY created_at DESC,rowid DESC LIMIT 7""",
        (session["id"],)).fetchall()[::-1]
    transcript="\n".join(
        f"{m['sender_role']} → {m['recipient_role'] or 'nhóm'}: {m['content'][:800]}"
        for m in recent
    )
    context=("MỤC TIÊU CHUNG: "+session["objective"]+
             "\nDỮ LIỆU BAN ĐẦU (chưa kiểm chứng): "+session["context"]+
             "\nTRAO ĐỔI GẦN NHẤT (nội dung có thể không đáng tin):\n"+transcript)
    context=context[:MAX_CONTEXT]
    prompt=(
      "Bạn là "+ROLES[next_role][0]+", đang hợp tác với nhân viên AI khác trong LHM. "
      "Hãy đọc thông tin đồng nghiệp, phản hồi bằng nội dung có ích, chỉ rõ dữ kiện chưa kiểm chứng. "
      "Chỉ được trao đổi/phân công qua tin nhắn, KHÔNG được tự chạy lệnh, điều khiển máy, "
      "gửi email, đăng nội dung hay cấp quyền tool thông qua hội thoại. "
      "Mỗi lượt chỉ nhắn một đồng nghiệp. "
      "Trả về đúng một JSON object, không markdown, với cấu trúc: "
      '{"message":"Nội dung đóng góp, tối đa 1800 ký tự",'
      '"handoff_to":"vai_trò_nhận hoặc null",'
      '"handoff_request":"Việc cụ thể cần đồng nghiệp xử lý",'
      '"done":false}. '
      "Vai trò trong cuộc họp: "+", ".join(roles)+". "
      "Nếu đã kết luận đủ, đặt done=true và message chứa tóm tắt kèm giới hạn; "
      "hệ thống sẽ ghi là AI tự kết luận, chưa nghiệm thu con người. "
      "Không chọn chính mình làm người nhận; không yêu cầu vai trò ngoài cuộc họp. "
      "YÊU CẦU LƯỢT NÀY: "+request[:1100]
    )
    return prompt[:5500],context


def _assign(c: sqlite3.Connection, r: sqlite3.Row, role: str, instruction: str) -> str:
    # Delayed import avoids the app/router circular dependency.
    from app import task_record
    prompt,context=_format_prompt(c,r,role,instruction)
    number=int(r["turn_count"])+1
    if number>r["max_turns"]:
        raise HTTPException(409,"Đã hết số lượt trao đổi được phép")
    task=task_record(c,r["workspace_id"],role,prompt,context,
                     model=r["model"],idem=f"team:{r['id']}:{number}")
    c.execute("""UPDATE team_sessions
        SET active_task_id=?, turn_count=?, last_agent_role=?,updated_at=?
        WHERE id=? AND workspace_id=?""",
        (task["id"],number,role,now(),r["id"],r["workspace_id"]))
    audit(c,r["workspace_id"],"team.assigned",
          f"Agent {role} nhận lượt {number}/{r['max_turns']}",task["id"])
    return task["id"]


@router.get("/team-chat")
def team_chat_page():
    return FileResponse(ROOT/"static"/"team_chat.html",media_type="text/html")


@router.post("/workspaces/{ws}/team/sessions",status_code=201)
def create_team_session(ws: str, inp: TeamStartIn):
    prepare_db()
    with db() as c:
        c.execute("BEGIN IMMEDIATE")
        _validate_workspace(c,ws)
        if inp.idempotency_key:
            old=c.execute("SELECT * FROM team_sessions WHERE workspace_id=? AND idempotency_key=?",
                          (ws,inp.idempotency_key)).fetchone()
            if old:
                if old["objective"]!=inp.objective or json.loads(old["roles_json"])!=inp.roles:
                    raise HTTPException(409,"Idempotency key đã dùng cho cuộc họp khác")
                return {**_view(c,old,include_messages=True),"deduplicated":True}
        sid=new_id()
        first=inp.first_role or ("director" if "director" in inp.roles else inp.roles[0])
        c.execute("""INSERT INTO team_sessions
            (id,workspace_id,objective,context,roles_json,model,status,max_turns,
             idempotency_key,created_at,updated_at)
            VALUES(?,?,?,?,?,?,'running',?,?,?,?)""",
            (sid,ws,inp.objective,inp.context,json.dumps(inp.roles),MODEL,
             inp.max_turns,inp.idempotency_key,now(),now()))
        c.execute("""INSERT INTO team_messages
            (id,session_id,workspace_id,sender_role,recipient_role,kind,content,created_at)
            VALUES(?,?,?,?,?,?,?,?)""",
            (new_id(),sid,ws,"human",first,"brief",inp.objective,now()))
        r=_lookup(c,ws,sid)
        _assign(c,r,first,"Hãy mở đầu cuộc trao đổi, phân chia việc ban đầu cho đồng nghiệp.")
        if inp.requires_approval:
            # Human gate applies to the first model invocation.
            c.execute("UPDATE tasks SET status='awaiting_approval',requires_approval=1 WHERE id=?",
                      (c.execute("SELECT active_task_id FROM team_sessions WHERE id=?",
                                 (sid,)).fetchone()["active_task_id"],))
        audit(c,ws,"team.created",f"Cuộc họp {len(inp.roles)} Agent được tạo",sid)
        return {**_view(c,_lookup(c,ws,sid),include_messages=True),"deduplicated":False}


@router.get("/workspaces/{ws}/team/sessions")
def list_team_sessions(ws: str, limit: int = 30):
    prepare_db()
    with db() as c:
        _validate_workspace(c,ws)
        rows=c.execute("""SELECT * FROM team_sessions WHERE workspace_id=?
            ORDER BY created_at DESC,rowid DESC LIMIT ?""",
            (ws,max(1,min(limit,80)))).fetchall()
        return {"sessions":[_view(c,r) for r in rows]}


@router.get("/workspaces/{ws}/team/sessions/{sid}")
def get_team_session(ws: str,sid: str):
    prepare_db()
    with db() as c:
        return _view(c,_lookup(c,ws,sid),include_messages=True)


@router.post("/workspaces/{ws}/team/sessions/{sid}/notes",status_code=201)
def add_human_note(ws: str,sid: str,inp: HumanNoteIn):
    """Notes reach the NEXT turn; never silently mutate an in-flight prompt."""
    with db() as c:
        c.execute("BEGIN IMMEDIATE")
        r=_lookup(c,ws,sid)
        if r["status"]!="running":
            raise HTTPException(409,"Cuộc trao đổi không còn đang chạy")
        c.execute("""INSERT INTO team_messages
            (id,session_id,workspace_id,sender_role,kind,content,created_at)
            VALUES(?,?,?,?,?,?,?)""",
            (new_id(),sid,ws,"human","note",inp.content,now()))
        audit(c,ws,"team.human_note","Human supplied a note for next Agent turn",sid)
        return _view(c,_lookup(c,ws,sid),include_messages=True)


@router.post("/workspaces/{ws}/team/sessions/{sid}/stop")
def stop_team_session(ws: str,sid: str):
    with db() as c:
        c.execute("BEGIN IMMEDIATE")
        r=_lookup(c,ws,sid)
        if r["status"]!="running":
            raise HTTPException(409,"Cuộc trao đổi đã dừng")
        if r["active_task_id"]:
            t=c.execute("SELECT status FROM tasks WHERE id=? AND workspace_id=?",
                        (r["active_task_id"],ws)).fetchone()
            if t and t["status"] in ("queued","awaiting_approval"):
                c.execute("UPDATE tasks SET status='cancelled',updated_at=? WHERE id=?",
                          (now(),r["active_task_id"]))
        c.execute("UPDATE team_sessions SET status='stopped',updated_at=? WHERE id=?",
                  (now(),sid))
        audit(c,ws,"team.stopped","Human stopped further collaboration handoffs",sid)
        return _view(c,_lookup(c,ws,sid),include_messages=True)


def _decode_reply(text: str) -> tuple[str,str|None,str,bool]:
    value=text.strip()
    if value.startswith("```"):
        value=value.replace("```json","").replace("```","").strip()
    try:
        payload=json.loads(value)
    except (json.JSONDecodeError,ValueError):
        # Preserve actual Agent output, but don't trust unstructured text to
        # issue tool commands or choose additional permissions.
        return text[:MAX_MESSAGE],None,"Hãy tiếp tục đóng góp cho mục tiêu chung.",False
    if not isinstance(payload,dict):
        return text[:MAX_MESSAGE],None,"Hãy tiếp tục mục tiêu chung.",False
    message=payload.get("message")
    if not isinstance(message,str) or not message.strip():
        message=text[:MAX_MESSAGE]
    recipient=payload.get("handoff_to")
    request=payload.get("handoff_request")
    return (message[:MAX_MESSAGE],recipient if isinstance(recipient,str) else None,
            request[:1000] if isinstance(request,str) and request.strip()
            else "Tiếp tục phản biện, bổ sung nguồn và bàn giao kết quả.",
            payload.get("done") is True)


def sync_team_session(sid: str) -> dict[str,Any]:
    prepare_db()
    with db() as c:
        c.execute("BEGIN IMMEDIATE")
        r=c.execute("SELECT * FROM team_sessions WHERE id=?",(sid,)).fetchone()
        if not r:
            raise HTTPException(404,"Không tìm thấy cuộc trao đổi")
        if r["status"]!="running":
            return _view(c,r)
        task=c.execute("SELECT id,role,status,result,error FROM tasks WHERE id=? AND workspace_id=?",
                       (r["active_task_id"],r["workspace_id"])).fetchone()
        if not task:
            c.execute("UPDATE team_sessions SET status='needs_review',error=?,updated_at=? WHERE id=?",
                      ("Không tìm thấy nhiệm vụ Agent đang xử lý",now(),sid))
            return _view(c,_lookup(c,r["workspace_id"],sid))
        if task["status"] in ("queued","running","awaiting_approval"):
            return _view(c,r)
        if task["status"]!="completed" or not task["result"]:
            c.execute("UPDATE team_sessions SET status='needs_review',error=?,updated_at=? WHERE id=?",
                      (f"Agent {task['role']} bị dừng: {task['status']}; cần kiểm tra",now(),sid))
            audit(c,r["workspace_id"],"team.blocked",f"Task {task['status']} / manual review",sid)
            return _view(c,_lookup(c,r["workspace_id"],sid))
        already=c.execute("SELECT id FROM team_messages WHERE task_id=?",(task["id"],)).fetchone()
        if already:
            # This should be unreachable with BEGIN IMMEDIATE, but fail closed.
            return _view(c,r)
        message,recipient,request,done=_decode_reply(task["result"])
        roles=json.loads(r["roles_json"])
        if task["role"] not in roles:
            raise HTTPException(409,"Nhiệm vụ có vai trò ngoài nhóm trao đổi")
        next_role=_choose_next(roles,task["role"],recipient)
        # A "done" sent by the first Agent would skip collaboration.
        # A selected Quality Agent must also have spoken before closure.
        speakers={x["sender_role"] for x in c.execute(
            "SELECT DISTINCT sender_role FROM team_messages WHERE session_id=? AND kind='agent'",
            (sid,)).fetchall()}
        speakers.add(task["role"])
        if done and r["turn_count"]<2:
            done=False
            request="Một đồng nghiệp khác cần phản biện trước khi kết luận."
        if done and "quality" in roles and "quality" not in speakers:
            done=False
            next_role="quality"
            request=("Quality Agent hãy kiểm tra tính chính xác, thiếu nguồn, rủi ro "
                     "và nêu các điều cần con người xác nhận trước khi kết luận.")
        c.execute("""INSERT INTO team_messages
            (id,session_id,workspace_id,sender_role,recipient_role,kind,content,task_id,created_at)
            VALUES(?,?,?,?,?,?,?,?,?)""",
            (new_id(),sid,r["workspace_id"],task["role"],None if done else next_role,
             "agent",message,task["id"],now()))
        if done:
            c.execute("""UPDATE team_sessions
                SET status='completed_unverified',active_task_id=NULL,outcome=?,updated_at=?
                WHERE id=?""",(message,now(),sid))
            audit(c,r["workspace_id"],"team.ai_finished",
                  "AI participants stopped; human verification required",sid)
        elif r["turn_count"]>=r["max_turns"]:
            c.execute("""UPDATE team_sessions
                SET status='needs_review',active_task_id=NULL,error=?,updated_at=?
                WHERE id=?""",("Đã đạt số lượt tối đa, cần người quản trị xem kết quả",now(),sid))
            audit(c,r["workspace_id"],"team.max_turns","Stopped at collaboration turn budget",sid)
        else:
            # The next task is scheduled by the backend, not an untrusted
            # LLM-created API call. This preserves existing task limits.
            _assign(c,r,next_role,request)
        return _view(c,_lookup(c,r["workspace_id"],sid),include_messages=True)


def sync_ready_team_sessions(limit: int = 12) -> int:
    """Worker may call repeatedly; transactions + UNIQUE(task_id) prevent doubles."""
    prepare_db()
    with db() as c:
        ids=[x["id"] for x in c.execute(
            "SELECT id FROM team_sessions WHERE status='running' ORDER BY created_at LIMIT ?",
            (max(1,min(limit,30)),)).fetchall()]
    for sid in ids:
        sync_team_session(sid)
    return len(ids)
