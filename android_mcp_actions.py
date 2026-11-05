"""Single-use, human-approved tap guard for dedicated Android test emulators.

This module never exposes ADB shell or coordinates as a model tool.
Only Computer Agent's approval-gated android_mcp_tap can call it.
"""
from __future__ import annotations

import asyncio
import json
import os
import re
import secrets
import threading
import time
from pathlib import Path
from typing import Any, Awaitable, Callable
from fastapi import HTTPException

EMULATOR = re.compile(r"^emulator-[0-9]{4,5}$")
UUID = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$")
MAX_AGE_SECONDS = 300
MAX_OBSERVATIONS = 24
# Native Windows runner memory only. Restart invalidates all observations.
_OBSERVATIONS: dict[str, dict[str, Any]] = {}
# A shared Windows Runner can execute two approved Computer runs concurrently.
# Serialize tap + before/after inspection per emulator so both cannot act on
# a stale screen at the same time. This lock is process-local (not an ADB lock).
_TAP_MUTEX = threading.Lock()
_ACTIVE_TAPS: set[str] = set()


def _reserve(serial: str) -> None:
    with _TAP_MUTEX:
        if serial in _ACTIVE_TAPS:
            raise HTTPException(409,"Android emulator has another active tap; inspect again")
        _ACTIVE_TAPS.add(serial)


def _release(serial: str) -> None:
    with _TAP_MUTEX:
        _ACTIVE_TAPS.discard(serial)

def _valid_run(run_id: str | None) -> bool:
    return isinstance(run_id,str) and bool(UUID.fullmatch(run_id))

def tap_enabled() -> bool:
    return os.environ.get("LHM_ANDROID_MCP_TAP_ENABLED") == "1"

def prepare(role: str,run_id: str | None,serial: str,observed: dict) -> str | None:
    """Bind the actual MCP observation to this run/role/serial, not AI text."""
    if not tap_enabled() or not _valid_run(run_id) or not EMULATOR.fullmatch(serial):
        return None
    if not isinstance(observed,dict) or observed.get("privacy")!="geometry-only":
        return None
    now=time.monotonic()
    for key,value in list(_OBSERVATIONS.items()):
        if now-value["time"]>MAX_AGE_SECONDS or (
            value["run_id"]==run_id and value["role"]==role and value["serial"]==serial
        ):
            _OBSERVATIONS.pop(key,None)
    while len(_OBSERVATIONS)>=MAX_OBSERVATIONS:
        _OBSERVATIONS.pop(next(iter(_OBSERVATIONS)))
    token=secrets.token_hex(16)
    _OBSERVATIONS[token]={
        "run_id":run_id,"role":role,"serial":serial,
        "time":now,"snapshot":json.dumps(observed,sort_keys=True,separators=(",",":"))
    }
    return token

def _claim(role: str,run_id: str | None,args: dict) -> dict:
    if not tap_enabled() or not _valid_run(run_id):
        raise HTTPException(403,"Android tap requires operator opt-in and a bound Computer run")
    if not isinstance(args,dict) or set(args)!={"serial","observation_id","index"}:
        raise HTTPException(422,"Use exact serial, observed token and numeric widget index")
    serial=args["serial"]
    token=args["observation_id"]
    index=args["index"]
    if not isinstance(serial,str) or not EMULATOR.fullmatch(serial):
        raise HTTPException(403,"Only dedicated Android emulator serials can be tapped")
    if not isinstance(token,str) or not re.fullmatch(r"[0-9a-f]{32}",token):
        raise HTTPException(422,"Invalid Android observation token")
    if type(index) is not int or not 1<=index<=120:
        raise HTTPException(422,"Android UI index must be between 1 and 120")
    # Remove token before the first await. Retries/replays fail closed.
    record=_OBSERVATIONS.pop(token,None)
    if not record or record["run_id"]!=run_id or record["role"]!=role or record["serial"]!=serial:
        raise HTTPException(409,"Android target is missing, consumed or belongs to another run")
    if time.monotonic()-record["time"]>MAX_AGE_SECONDS:
        raise HTTPException(409,"Android observation expired. Inspect UI again")
    return {"serial":serial,"index":index,"snapshot":record["snapshot"]}

def _adb_binary() -> str:
    """Find one real SDK ADB binary, never a model-supplied command/PATH hit."""
    candidates=[os.environ.get("ANDROID_SDK_ROOT"),os.environ.get("ANDROID_HOME")]
    local=os.environ.get("LOCALAPPDATA")
    if local:
        candidates.append(str(Path(local)/"Android"/"Sdk"))
    for sdk in candidates:
        if not sdk or not Path(sdk).is_absolute():
            continue
        binary=Path(sdk)/"platform-tools"/"adb.exe"
        if binary.is_file() and not binary.is_symlink():
            return str(binary)
    raise HTTPException(503,"Android SDK adb.exe not found in configured or standard Windows SDK locations")

async def _send_tap(serial: str,x: int,y: int) -> None:
    executable=_adb_binary()
    try:
        process=await asyncio.create_subprocess_exec(
            executable,"-s",serial,"shell","input","tap",str(x),str(y),
            stdin=asyncio.subprocess.DEVNULL,
            stdout=asyncio.subprocess.DEVNULL,
            stderr=asyncio.subprocess.DEVNULL
        )
        try:
            await asyncio.wait_for(process.wait(),timeout=12)
        except asyncio.TimeoutError:
            process.kill()
            await process.wait()
            # The touch may already have occurred: NEVER retry automatically.
            raise HTTPException(504,"Android tap outcome unknown; inspect device manually")
        if process.returncode!=0:
            raise HTTPException(502,"Android tap outcome unknown; inspect device manually")
    except HTTPException:
        raise
    except OSError:
        raise HTTPException(503,"Android ADB tap transport unavailable")

async def perform(role: str,run_id: str | None,args: dict,
                  inspect_fn: Callable[[str],Awaitable[dict]]) -> dict:
    # _claim consumes the observation before any await, even when the
    # emulator is already busy. An approved run must inspect again to retry.
    expected=_claim(role,run_id,args)
    serial=expected["serial"]
    _reserve(serial)
    try:
        return await _perform_reserved(expected,inspect_fn)
    finally:
        _release(serial)


async def _perform_reserved(expected: dict,
                            inspect_fn: Callable[[str],Awaitable[dict]]) -> dict:
    # Single-use consumed before asynchronous work and never returned.
    serial=expected["serial"]
    try:
        fresh=await inspect_fn(serial)
    except Exception:
        raise HTTPException(409,"Android UI could not be re-inspected. No tap sent")
    if json.dumps(fresh,sort_keys=True,separators=(",",":")) != expected["snapshot"]:
        raise HTTPException(409,"Android UI changed since observation. No tap sent")
    node=next((n for n in fresh.get("elements",[])
               if n.get("index")==expected["index"]),None)
    if node is None:
        raise HTTPException(409,"Android target no longer exists. No tap sent")
    coords=node.get("bounds",{})
    x,y=coords.get("x"),coords.get("y")
    if type(x) is not int or type(y) is not int or not 0<=x<=8192 or not 0<=y<=8192:
        raise HTTPException(422,"Target coordinates are invalid. No tap sent")
    await _send_tap(serial,x,y)
    # Do not leak screen contents, and do not equate UI change with a successful
    # transaction. Require owner reconciliation after EVERY tap.
    try:
        after=await inspect_fn(serial)
        changed=after!=fresh
        observation="changed" if changed else "unchanged"
    except Exception:
        observation="unavailable"
    return {
        "state":"tap_sent","serial":serial,"index":expected["index"],
        "ui_after":observation,"confirmed_by_cli":True,
        "requires_owner_reconciliation":True,
        "external_effect_verified":False
    }

__all__=["prepare","perform","tap_enabled","_OBSERVATIONS","_claim","_send_tap"]
