"""Read-only Windows OpenCLI setup diagnostics; never opens or touches Chrome tabs."""
from __future__ import annotations

import asyncio
import json
import os
import subprocess
from typing import Any

from fastapi import HTTPException

from opencli_windows_bridge import _configuration, _toolchain, _windows, _PROFILE, MAX_OUTPUT


async def inspect() -> dict[str,Any]:
    state={"enabled":os.environ.get("LHM_OPENCLI_ENABLED")=="1",
           "cli_installed":False,"bridge_running":False,
           "profile_connected":False,"ready":False,
           "profile_alias":os.environ.get("LHM_OPENCLI_PROFILE","")[:80],
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
            isinstance(p,dict) and profile in (p.get("alias"),p.get("contextId")) and p.get("connected") is True
            for p in profiles)
        state["ready"]=state["bridge_running"] and state["profile_connected"]
        state["detail"]="Chrome profile connected" if state["ready"] else (
            "Start Chrome with the OpenCLI extension on the selected dedicated profile")
        return state
    except (OSError,UnicodeDecodeError,ValueError):
        return {**state,"detail":"OpenCLI diagnostics unavailable"}


_PROFILE_PICKER_LOCK=asyncio.Lock()


async def _read_owner_cli(node: str,entry: str,env: dict[str,str],
                          *args: str,timeout: int=9) -> dict[str,Any]:
    """Fixed argv-only CLI call; no user input, secrets, shell or Chrome tabs."""
    proc=await asyncio.create_subprocess_exec(
        node,entry,*args,env=env,stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
        creationflags=getattr(subprocess,"CREATE_NO_WINDOW",0))
    try:
        out,_=await asyncio.wait_for(proc.communicate(),timeout=timeout)
    except asyncio.TimeoutError:
        proc.kill()
        await proc.communicate()
        raise ValueError("Local OpenCLI timeout")
    if proc.returncode!=0 or len(out)>MAX_OUTPUT:
        raise ValueError("OpenCLI command did not succeed")
    envelope=json.loads(out.decode("utf-8"))
    data=envelope.get("data") if isinstance(envelope,dict) and envelope.get("ok") is True else None
    if not isinstance(data,dict):
        raise ValueError("OpenCLI returned invalid status")
    return data


async def connected_profiles() -> dict[str,Any]:
    """On owner's explicit click, start stopped local daemon; list connected
    profile identities, never attach/login/touch Chrome tabs."""
    if not _windows() or os.environ.get("LHM_DESKTOP_NATIVE")!="1":
        return {"cli_installed":False,"profiles":[],"detail":"Only on Windows Desktop"}
    try:
        node,entry=_toolchain()
    except HTTPException as exc:
        return {"cli_installed":False,"profiles":[],"detail":str(exc.detail)[:140]}
    env={k:os.environ[k] for k in ("PATH","PATHEXT","SYSTEMROOT","WINDIR",
        "APPDATA","LOCALAPPDATA","USERPROFILE","TEMP","TMP") if k in os.environ}
    env["NO_COLOR"]="1"
    async with _PROFILE_PICKER_LOCK:
        try:
            payload=await _read_owner_cli(node,entry,env,"profile","list")
            started=False
            # Profile-list is read-only and does not spawn the daemon.
            # A first-install profile picker otherwise stays empty forever.
            # This starts the *local* OpenCLI daemon only after the user
            # explicitly clicks "Start Bridge & find Chrome profiles".
            if payload.get("daemonRunning") is False:
                startup=await _read_owner_cli(node,entry,env,"daemon","restart",timeout=14)
                if startup.get("action") not in ("started","restarted"):
                    raise ValueError("Unable to launch local Browser Bridge")
                started=True
                payload=await _read_owner_cli(node,entry,env,"profile","list")
            records=payload.get("profiles",[])
            identities=[]
            if isinstance(records,list):
                for p in records[:30]:
                    if not isinstance(p,dict) or p.get("connected") is not True:
                        continue
                    cid=p.get("contextId")
                    if not isinstance(cid,str) or not _PROFILE.fullmatch(cid):
                        continue
                    alias=p.get("alias")
                    display=alias if isinstance(alias,str) and _PROFILE.fullmatch(alias) else cid
                    identities.append({"id":cid,"label":display[:80]})
            return {"cli_installed":True,"profiles":identities[:12],
                    "daemon_started":started,
                    "detail":"Choose the dedicated Chrome profile" if identities else
                    "Local Bridge started. Open Chrome with OpenCLI extension in the dedicated profile, then try again"}
        except (OSError,UnicodeDecodeError,ValueError,TypeError):
            return {"cli_installed":True,"profiles":[],"detail":"Could not start/list local Browser Bridge"}
