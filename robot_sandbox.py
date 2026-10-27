"""Ephemeral, opt-in local Docker sandbox for untrusted Agent code.

The Windows native process NEVER runs model-created Python on the host.
Only an already-installed, pre-pulled, operator-approved Linux Docker image
can run bounded argv in a disposable container. No network, no host home,
no Docker socket, no cloud credentials. The sole writable bind is this
Agent's own files directory. Docker Desktop is optional for GUI use.
"""
from __future__ import annotations

import asyncio
import os
import re
import shutil
import uuid
from pathlib import Path
from typing import Any

from fastapi import HTTPException

PERMITTED = {"python","python3","pytest","git","ls","cat","pwd","rg","grep","wc","head","tail"}
SANDBOX_MAX_OUTPUT = 10000
SANDBOX_MAX_ARGS = 24
MAX_SECONDS = 25
_SANDBOX_SLOTS=asyncio.Semaphore(2)
IMAGE = "python:3.12-slim"

def configured() -> bool:
    return os.environ.get("LHM_SANDBOX_ENABLED") == "1"


def validate(args: dict[str,Any]) -> list[str]:
    if not isinstance(args,dict) or set(args)!={"argv"}:
        raise HTTPException(422,"Sandbox requires only argv")
    argv=args.get("argv")
    if not isinstance(argv,list) or not 1<=len(argv)<=SANDBOX_MAX_ARGS or any(
        not isinstance(x,str) or not x or len(x)>450 or "\x00" in x for x in argv):
        raise HTTPException(422,"Sandbox argv is invalid")
    if argv[0] not in PERMITTED:
        raise HTTPException(403,"Sandbox command is not approved")
    return argv


def docker_binary() -> str:
    exe=shutil.which("docker")
    if not exe:
        raise HTTPException(503,"Docker Desktop not found; code sandbox remains disabled")
    return exe


def _clean_env() -> dict[str,str]:
    # DO NOT inherit the runner's credentials, user profile paths or app env.
    env={"PATH":os.environ.get("PATH",""),
         "SYSTEMROOT":os.environ.get("SYSTEMROOT",""),
         "WINDIR":os.environ.get("WINDIR","")}
    return {key:value for key,value in env.items() if value}


async def _exec(argv: list[str],timeout: float=MAX_SECONDS) -> tuple[int,bytes,bytes]:
    proc=await asyncio.create_subprocess_exec(*argv,
        env=_clean_env(),stdin=asyncio.subprocess.DEVNULL,
        stdout=asyncio.subprocess.PIPE,stderr=asyncio.subprocess.PIPE,
        start_new_session=(os.name!="nt"))
    # Drain pipes continuously while retaining only 10 KB each. Calling
    # communicate() can consume unbounded host RAM if untrusted code floods
    # stdout/stderr, despite slicing the final result afterwards.
    async def drain(stream):
        captured=bytearray()
        while True:
            chunk=await stream.read(4096)
            if not chunk:
                break
            if len(captured)<SANDBOX_MAX_OUTPUT:
                captured.extend(chunk[:SANDBOX_MAX_OUTPUT-len(captured)])
        return bytes(captured)
    out_task=asyncio.create_task(drain(proc.stdout))
    err_task=asyncio.create_task(drain(proc.stderr))
    try:
        code=await asyncio.wait_for(proc.wait(),timeout=timeout)
        stdout,stderr=await asyncio.gather(out_task,err_task)
        return code,stdout,stderr
    except asyncio.TimeoutError:
        proc.kill()
        await proc.wait()
        await asyncio.gather(out_task,err_task,return_exceptions=True)
        raise


async def run(ws: str,role: str,args: dict[str,Any]) -> dict[str,Any]:
    if not configured():
        raise HTTPException(403,"Docker execution sandbox is disabled by the owner")
    argv=validate(args)
    docker=docker_binary()
    # Identity and allowed workspace paths are managed by the runner, not AI.
    from computer_executor import _files
    folder=_files(ws,role).resolve()
    if not folder.is_dir() or "," in str(folder) or "\n" in str(folder):
        raise HTTPException(422,"Unsupported Docker workspace mount path")
    # Operator must pre-pull this image. No auto-pull, no Docker build, no
    # arbitrary model-supplied image, privileged flags, credentials or mounts.
    name="lhm-"+uuid.uuid4().hex[:20]
    cli=[docker,"run","--rm","--pull","never","--name",name,
         "--network","none","--read-only","--cap-drop","ALL",
         "--security-opt","no-new-privileges","--pids-limit","64",
         "--memory","512m","--cpus","1","--user","65534:65534",
         "--tmpfs","/tmp:rw,noexec,nosuid,size=64m",
         "--mount",f"type=bind,src={folder},dst=/workspace",
         "--workdir","/workspace",IMAGE,*argv]
    async with _SANDBOX_SLOTS:
        try:
            code,out,err=await _exec(cli,timeout=MAX_SECONDS)
        except asyncio.TimeoutError:
            # Killing docker.exe may leave its container running, so explicitly
            # ask Docker to force-remove this one named container.
            try:
                await _exec([docker,"rm","--force",name],timeout=6)
            except (asyncio.TimeoutError,OSError):
                pass
            return {"exit_code":-1,"timed_out":True,"stdout":"",
                    "stderr":"Sandbox timed out; check Docker for stale lhm- containers",
                    "isolation":"docker_no_network_not_vm"}
        except OSError as exc:
            raise HTTPException(503,"Docker sandbox could not start") from exc
    return {"exit_code":code,"timed_out":False,
            "stdout":out.decode("utf-8","replace"),
            "stderr":err.decode("utf-8","replace"),
            "isolation":"docker_no_network_not_vm"}
