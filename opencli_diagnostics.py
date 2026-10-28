"""Read-only Windows OpenCLI setup diagnostics; never opens or touches Chrome tabs."""
from __future__ import annotations

import asyncio
import json
import os
import subprocess
from typing import Any

from fastapi import HTTPException

from opencli_windows_bridge import _configuration, _windows, MAX_OUTPUT


async def inspect() -> dict[str,Any]:
    state={"enabled":os.environ.get("LHM_OPENCLI_ENABLED")=="1",
           "cli_installed":False,"bridge_running":False,
           "profile_connected":False,"ready":False,
           "profile_alias":os.environ.get("LHM_OPENCLI_PROFILE","")[:38],
           "detail":""}
    if not _windows() or os.environ.get("LHM_DESKTOP_NATIVE")!="1":
        return {**state,"detail":"Requires local Windows Desktop"}
    if not state["enabled"]:
        return {**state,"detail":"Enable OpenCLI in Robot Settings and restart"}
    try:
        node,entry,profile=_configuration()
    except HTTPException as exc:
        return {**state,"detail":str(exc.detail)[:160]}
    state["cli_installed"]=True
    env={k:os.environ[k] for k in ("PATH","PATHEXT","SYSTEMROOT",
         "WINDIR","APPDATA","LOCALAPPDATA","USERPROFILE","TEMP","TMP")
         if k in os.environ}
    env["NO_COLOR"]="1"
    try:
        proc=await asyncio.create_subprocess_exec(
            node,entry,"profile","list",env=env,
            stdout=asyncio.subprocess.PIPE,stderr=asyncio.subprocess.PIPE,
            creationflags=getattr(subprocess,"CREATE_NO_WINDOW",0))
        try:
            out,_=await asyncio.wait_for(proc.communicate(),timeout=9)
        except asyncio.TimeoutError:
            proc.kill()
            await proc.communicate()
            return {**state,"detail":"OpenCLI profile check timed out"}
        if proc.returncode!=0 or len(out)>MAX_OUTPUT:
            return {**state,"detail":"OpenCLI profile list failed"}
        envelope=json.loads(out.decode("utf-8"))
        data=envelope.get("data") if isinstance(envelope,dict) and envelope.get("ok") is True else None
        if not isinstance(data,dict):
            return {**state,"detail":"OpenCLI returned an invalid diagnostics response"}
        state["bridge_running"]=data.get("daemonRunning") is True and data.get("daemonStale") is not True
        profiles=data.get("profiles")
        state["profile_connected"]=isinstance(profiles,list) and any(
            isinstance(p,dict) and p.get("alias")==profile and p.get("connected") is True
            for p in profiles)
        state["ready"]=state["bridge_running"] and state["profile_connected"]
        state["detail"]="Chrome profile connected" if state["ready"] else (
            "Start Chrome with the OpenCLI extension on the selected dedicated profile")
        return state
    except (OSError,UnicodeDecodeError,ValueError):
        return {**state,"detail":"OpenCLI diagnostics unavailable"}
