"""Read-only Agent-Reach connector for the isolated LHM Computer Runner.

This is a bounded *operator-installed CLI* bridge, not a second unrestricted
shell tool. Only public-channel search is in the pilot. No cookie cloning,
untrusted URLs, arbitrary plugin setup, installs or writes are exposed to AI.
"""
from __future__ import annotations

import asyncio
import json
import os
import re
import shutil
from pathlib import Path
from typing import Any

from fastapi import HTTPException

# Agent Reach v1.6 uses 'agent-reach <channel> search <query>'. Other actions,
# experimental/login-walled channels, setup/update and arbitrary URLs are NOT
# available to model calls.
SEARCH_CHANNELS = frozenset({"github", "youtube", "bilibili"})
SEARCH_ROLES = frozenset({"research", "strategy", "lead", "content",
                          "ads", "quality", "analytics", "compliance"})
MAX_QUERY = 180
MAX_JSON = 36_000


def validate(role: str, args: dict[str,Any]) -> tuple[str,str]:
    if role not in SEARCH_ROLES:
        raise HTTPException(403, "Agent không có quyền tìm kiếm bằng Agent-Reach")
    if not isinstance(args,dict) or set(args)!={"channel","query"}:
        raise HTTPException(422, "Agent-Reach yêu cầu đúng channel và query")
    channel,query=args["channel"],args["query"]
    if not isinstance(channel,str) or channel not in SEARCH_CHANNELS:
        raise HTTPException(403,"Kênh Agent-Reach chưa được kiểm chứng hoặc cần đăng nhập")
    if not isinstance(query,str) or not 2<=len(query.strip())<=MAX_QUERY:
        raise HTTPException(422,"Từ khóa tìm kiếm phải có độ dài 2–180 ký tự")
    query=query.strip()
    if query.startswith("-") or "://" in query or any(
            ord(x)<32 or ord(x)==127 for x in query):
        raise HTTPException(422,"Không chấp nhận tham số, URL hoặc ký tự điều khiển")
    return channel,query


async def search(ws: str,role: str,args: dict[str,Any], home: Path) -> dict[str,Any]:
    channel,query=validate(role,args)
    if os.name=="nt":
        # Never spawn third-party CLIs with access to the owner's logged-in
        # Windows session. Use a dedicated container/VM runner for this tool.
        raise HTTPException(503,"Agent-Reach chỉ chạy trong Linux Computer Runner riêng")
    exe=shutil.which("agent-reach")
    if not exe:
        raise HTTPException(503,"Agent-Reach CLI chưa được cài trong Computer Runner")
    # An isolated home for each workspace/role. Operator installs and tests
    # the read-only CLI itself; the backend never runs setup/install/update.
    base=(home/"reach-home").resolve()
    base.mkdir(parents=True,exist_ok=True)
    config=(base/".config").resolve()
    cache=(base/".cache").resolve()
    config.mkdir(exist_ok=True)
    cache.mkdir(exist_ok=True)
    safe_env={
        "PATH":os.environ.get("PATH","/usr/local/bin:/usr/bin:/bin"),
        "HOME":str(base),"XDG_CONFIG_HOME":str(config),
        "XDG_CACHE_HOME":str(cache),
        "PYTHONIOENCODING":"utf-8","PYTHONDONTWRITEBYTECODE":"1",
        "LANG":"C.UTF-8","NO_COLOR":"1",
    }
    # One immutable provider command; shell=False, no user-provided flags.
    process=await asyncio.create_subprocess_exec(
        exe,channel,"search",query,
        cwd=str(base),env=safe_env,
        stdout=asyncio.subprocess.PIPE,stderr=asyncio.subprocess.PIPE,
        start_new_session=True)
    try:
        stdout,stderr=await asyncio.wait_for(process.communicate(),timeout=25)
    except asyncio.TimeoutError as ex:
        import signal
        try:
            os.killpg(process.pid,signal.SIGKILL)
        except ProcessLookupError:
            pass
        await process.communicate()
        raise HTTPException(504,"Agent-Reach tìm kiếm quá thời gian cho phép") from ex
    if len(stdout)>MAX_JSON:
        raise HTTPException(502,"Agent-Reach trả dữ liệu vượt giới hạn an toàn")
    try:
        payload=json.loads(stdout.decode("utf-8"))
    except (UnicodeDecodeError,ValueError) as ex:
        raise HTTPException(502,"Agent-Reach trả dữ liệu không phải JSON hợp lệ") from ex
    if process.returncode!=0 or not isinstance(payload,dict) or payload.get("ok") is not True:
        # Avoid inserting raw CLI error traces or browser data in the model.
        raise HTTPException(502,"Agent-Reach không thực hiện được truy vấn")
    # The tool's content is untrusted third-party web data, not instructions.
    return {"provider":"Agent-Reach","mode":"read_only_search",
            "channel":channel,"query":query,
            "data":payload.get("result"),
            "verified":False,
            "note":"Đây là kết quả tìm kiếm bên ngoài; kiểm tra nguồn trước khi sử dụng."}
