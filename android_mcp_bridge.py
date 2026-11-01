"""Read-only Android MCP transport for a supervised Windows LHM Agent.

Executes a single operator-configured local MCP stdio server (from ggminh).
The AI cannot choose an executable, repo path or extra device serials.
No Android write actions, screenshots, OTPs, or raw UI labels are exposed.
"""
from __future__ import annotations

import asyncio
import json
import os
import re
from pathlib import Path
from typing import Any
from fastapi import HTTPException

DEVICE_RE = re.compile(r"^[A-Za-z0-9._:-]{1,128}$")
READ_TOOLS = {
    "android_mcp_devices": "android_list_devices",
    "android_mcp_inspect": "android_inspect_ui",
}
ALL_ROLES = {
    "director", "research", "strategy", "lead", "content", "ads",
    "crm", "sales", "quality", "analytics", "operations", "finance",
    "hr", "procurement", "support", "compliance",
}
MAX_OUTPUT_BYTES = 96_000
MAX_NODES = 120

def allowed_roles() -> set[str]:
    return {r.strip() for r in os.getenv("LHM_ANDROID_MCP_ALLOWED_ROLES", "").split(",")
            if r.strip() in ALL_ROLES}

def allowed_serials() -> set[str]:
    values = [s.strip() for s in os.getenv("LHM_ANDROID_MCP_ALLOWED_SERIALS", "").split(",")
              if s.strip()]
    if len(values) > 8 or any(not DEVICE_RE.fullmatch(v) for v in values):
        return set()
    return set(values)

def native_desktop() -> bool:
    return os.name == "nt" and os.getenv("LHM_DESKTOP_NATIVE") == "1"


def configured(role: str) -> bool:
    if not native_desktop():
        return False
    if os.getenv("LHM_ANDROID_MCP_ENABLED") != "1":
        return False
    if role not in allowed_roles() or not allowed_serials():
        return False
    entry = Path(os.getenv("LHM_ANDROID_MCP_ENTRY", ""))
    node = os.getenv("LHM_ANDROID_MCP_NODE", "")
    return bool(node and entry.name == "lhm-android-mcp.js" and
                entry.is_file() and not entry.is_symlink())

def validate(role: str, tool: str, args: dict[str, Any]) -> None:
    if tool not in READ_TOOLS:
        raise HTTPException(422, "Android MCP tool is not read-only/allowlisted")
    if not configured(role):
        raise HTTPException(403, "Android MCP chưa bật cho vai trò hoặc máy này")
    if not isinstance(args, dict):
        raise HTTPException(422, "Android MCP arguments must be an object")
    if tool == "android_mcp_devices":
        if args: raise HTTPException(422, "android_mcp_devices does not take arguments")
    else:
        if set(args) != {"serial"} or not isinstance(args.get("serial"), str):
            raise HTTPException(422, "Specify an allowed Android serial")
        if args["serial"] not in allowed_serials():
            raise HTTPException(403, "Android serial is not approved for this Agent")

def clean_result(tool: str, result: dict) -> dict:
    """Server-side double redaction; never trust output from the bridge."""
    if not isinstance(result, dict):
        raise HTTPException(502, "Android MCP returned an invalid result")
    if tool == "android_mcp_devices":
        out = []
        entries = result.get("devices", [])
        if not isinstance(entries, list): raise HTTPException(502, "Invalid devices")
        for d in entries[:8]:
            if not isinstance(d, dict) or d.get("serial") not in allowed_serials():
                continue
            out.append({
                "serial": d["serial"],
                "kind": d.get("kind") if d.get("kind") in ("emulator", "physical") else "unknown",
                "status": d.get("status") if d.get("status") in ("device","offline","unauthorized") else "unknown",
            })
        return {"devices": out, "read_only": True}
    elements = result.get("elements", [])
    if not isinstance(elements, list): raise HTTPException(502, "Invalid UI elements")
    clean = []
    hint_allow = {"back","home","search","menu","close","next"}
    roles = {"button","text-input","toggle","picker","slider","interactive-view"}
    for element in elements[:MAX_NODES]:
        if not isinstance(element, dict): continue
        bounds=element.get("bounds")
        if not isinstance(bounds, dict):continue
        coords={k:bounds.get(k) for k in ("x","y","width","height")}
        if any(type(v) is not int or v < 0 or v > 8192 for v in coords.values()):
            continue
        if coords["width"] == 0 or coords["height"] == 0:continue
        clean.append({
            "index":element.get("index") if type(element.get("index")) is int else 0,
            "role":element.get("role") if isinstance(element.get("role"),str) and element.get("role") in roles else "interactive-view",
            "hint":element.get("hint") if isinstance(element.get("hint"),str) and element.get("hint") in hint_allow else None,
            "bounds":coords
        })
    return {"privacy":"geometry-only","read_only":True,
            "totalNodes":min(max(result.get("totalNodes",0) if type(result.get("totalNodes")) is int else 0,0),3000),
            "elements":clean}

async def execute(role: str, tool: str, args: dict[str, Any]) -> dict:
    validate(role,tool,args)
    node=os.environ["LHM_ANDROID_MCP_NODE"]
    entry=os.environ["LHM_ANDROID_MCP_ENTRY"]
    env={key:os.environ[key] for key in (
        "SYSTEMROOT","WINDIR","PATH","PATHEXT","TEMP","TMP",
        "APPDATA","LOCALAPPDATA","USERPROFILE","ANDROID_SDK_ROOT","ANDROID_HOME"
    ) if key in os.environ}
    env["LHM_MCP_ALLOWED_SERIALS"]=",".join(sorted(allowed_serials()))
    env["NODE_OPTIONS"]="--no-deprecation"
    initialize={"jsonrpc":"2.0","id":1,"method":"initialize",
                "params":{"protocolVersion":"2025-06-18",
                          "capabilities":{},"clientInfo":{"name":"lhm-robot","version":"0.2"}}}
    call={"jsonrpc":"2.0","id":2,"method":"tools/call",
          "params":{"name":READ_TOOLS[tool],"arguments":args}}
    payload=("\n".join(json.dumps(row,separators=(",",":"))
          for row in (initialize,{"jsonrpc":"2.0","method":"notifications/initialized"},call))+"\n").encode()
    try:
        proc=await asyncio.create_subprocess_exec(
            node,entry,stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,stderr=asyncio.subprocess.DEVNULL,
            env=env
        )
        try:
            stdout,_=await asyncio.wait_for(proc.communicate(payload),timeout=27)
        except asyncio.TimeoutError:
            proc.kill()
            await proc.communicate()
            raise HTTPException(504,"Android MCP local read timed out")
    except HTTPException:raise
    except (OSError,ValueError):
        raise HTTPException(503,"Android MCP executable or source unavailable")
    if proc.returncode!=0 or len(stdout)>MAX_OUTPUT_BYTES:
        raise HTTPException(502,"Android MCP local process failed")
    try:
        replies=[json.loads(line) for line in stdout.decode("utf-8").splitlines() if line.strip()]
        init=next(x for x in replies if x.get("id")==1)
        response=next(x for x in replies if x.get("id")==2)
        if init.get("result",{}).get("protocolVersion")!="2025-06-18":
            raise ValueError("protocol mismatch")
        if response.get("result",{}).get("isError"):
            raise HTTPException(503,"Android MCP denied or could not inspect the device")
        blocks=response["result"]["content"]
        data=json.loads(blocks[0]["text"])
    except HTTPException:raise
    except (ValueError,KeyError,TypeError,IndexError,StopIteration,AttributeError):
        raise HTTPException(502,"Android MCP returned an invalid protocol response")
    return clean_result(tool,data)
