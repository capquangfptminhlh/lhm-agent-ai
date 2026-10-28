"""Windows-only, owner-opted-in OpenCLI Browser Bridge integration.

OpenCLI drives a *real connected Chrome profile*. This is not a sandbox:
only a dedicated, explicitly selected profile, exact HTTPS host allowlists,
scoped session names and supervised mutations are permitted. Never expose
arbitrary OpenCLI CLI commands, adapters, eval, network, tabs or downloads.
"""
from __future__ import annotations

import asyncio
import json
import os
import re
import shutil
import subprocess
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

from fastapi import HTTPException

READ_ROLES=frozenset({"content","quality","sales","research","lead"})
WRITE_ROLES=frozenset({"content"})
READ_TOOLS=frozenset({"opencli_open","opencli_state"})
WRITE_TOOLS=frozenset({"opencli_click","opencli_fill"})
ALL_TOOLS=READ_TOOLS|WRITE_TOOLS
_ID=re.compile(r"^[a-z0-9][a-z0-9_-]{1,48}$")
_PROFILE=re.compile(r"^[a-zA-Z0-9][a-zA-Z0-9_-]{0,79}$")
MAX_OUTPUT=40_000


def _windows() -> bool:
    return os.name=="nt"


def allowed_url(url: str) -> bool:
    if not isinstance(url,str) or len(url)>1500 or any(ord(c)<32 for c in url):
        return False
    try:
        u=urlsplit(url)
        host=(u.hostname or "").lower().rstrip(".")
        global_hosts={h.strip().lower().rstrip(".") for h in
                      os.getenv("LHM_COMPUTER_ALLOWED_HOSTS","").split(",") if h.strip()}
        tool_hosts={h.strip().lower().rstrip(".") for h in
                    os.getenv("LHM_OPENCLI_ALLOWED_HOSTS","").split(",") if h.strip()}
        return (u.scheme=="https" and host and host in global_hosts & tool_hosts
                and u.port in (None,443) and not u.username and not u.password
                and "\\" not in url and "#" not in url)
    except (ValueError,TypeError):
        return False


def validate(role: str,tool: str,args: dict[str,Any]) -> None:
    if role not in READ_ROLES or tool not in ALL_TOOLS:
        raise HTTPException(403,"Agent không có quyền OpenCLI")
    if tool in WRITE_TOOLS and role not in WRITE_ROLES:
        raise HTTPException(403,"Chỉ Content Agent có quyền đề xuất thao tác OpenCLI")
    if not isinstance(args,dict):
        raise HTTPException(422,"Invalid tool args")
    expected={"opencli_open":{"url"},"opencli_state":set(),
              "opencli_click":{"ref","url","label"},
              "opencli_fill":{"ref","url","label","text"}}[tool]
    if set(args)!=expected:
        raise HTTPException(422,"OpenCLI tool arguments do not match the allowlist")
    if tool=="opencli_open" and not allowed_url(args.get("url")):
        raise HTTPException(403,"OpenCLI URL không thuộc danh sách HTTPS được cấp quyền")
    if tool in WRITE_TOOLS:
        n=args.get("ref")
        if type(n) is not int or not 1<=n<=3000:
            raise HTTPException(422,"OpenCLI requires a numeric ref from a recent state")
        if not allowed_url(args.get("url")):
            raise HTTPException(403,"OpenCLI click/fill URL must be allowlisted")
        label=args.get("label")
        if (not isinstance(label,str) or not 2<=len(label.strip())<=120
                or any(ord(c)<32 for c in label)):
            raise HTTPException(422,"OpenCLI requires an observed element label")
    if tool=="opencli_fill":
        value=args.get("text")
        if (not isinstance(value,str) or not 1<=len(value)<=1200
                or "\x00" in value or any(ord(c)<32 and c not in "\n\t" for c in value)):
            raise HTTPException(422,"Invalid OpenCLI text")


def _toolchain() -> tuple[str,str]:
    if not _windows() or os.environ.get("LHM_DESKTOP_NATIVE")!="1":
        raise HTTPException(503,"OpenCLI requires Windows Desktop Native Runner")
    # The commercial Windows installer ships its own Node and OpenCLI
    # 2.0.2 package. Never require the customer to install npm globally.
    # No auto-updates, plugins or remote installers are triggered by an Agent.
    bundle=os.environ.get("LHM_OPENCLI_BUNDLE_DIR","").strip()
    if bundle:
        base=Path(bundle)
        if not base.is_absolute():
            raise HTTPException(503,"Invalid OpenCLI bundle path")
        node=base/"node.exe"
        package=base/"node_modules"/"@jackwener"/"opencli"
        entry=package/"dist"/"src"/"main.js"
        metadata=package/"package.json"
        if not node.is_file() or not entry.is_file() or not metadata.is_file():
            raise HTTPException(503,"Bundled OpenCLI files missing; reinstall LHM Robot")
        try:
            record=json.loads(metadata.read_text(encoding="utf-8"))
        except (OSError,ValueError) as exc:
            raise HTTPException(503,"Bundled OpenCLI package metadata corrupt") from exc
        if record.get("name")!="@jackwener/opencli" or record.get("version")!="2.0.2":
            raise HTTPException(503,"Bundled OpenCLI package version mismatch")
        return str(node),str(entry)
    # Development/source checkout only. Production always provides a bundle
    # directory; no arbitrary npm.cmd execution or shell fallback.
    node=shutil.which("node.exe") or shutil.which("node")
    if not node:
        raise HTTPException(503,"Node.js 20.18.1+ chưa có trên Windows")
    roots=[]
    appdata=os.environ.get("APPDATA","")
    if appdata:
        roots.append(Path(appdata)/"npm")
    roots.append(Path(node).resolve().parent)
    for root in roots:
        package=root/"node_modules"/"@jackwener"/"opencli"
        entry=package/"dist"/"src"/"main.js"
        metadata=package/"package.json"
        if not entry.is_file() or not metadata.is_file():
            continue
        try:
            record=json.loads(metadata.read_text(encoding="utf-8"))
        except (OSError,ValueError):
            continue
        if record.get("name")=="@jackwener/opencli" and record.get("version")=="2.0.2":
            return node,str(entry)
    raise HTTPException(503,"Cài OpenCLI 2.0.2 bằng npm -g trong Windows; Node global prefix chưa được nhận diện")


def _configuration() -> tuple[str,str,str]:
    if os.getenv("LHM_OPENCLI_ENABLED")!="1":
        raise HTTPException(403,"OpenCLI chưa bật trong Thiết lập Robot")
    profile=os.getenv("LHM_OPENCLI_PROFILE","").strip()
    if not _PROFILE.fullmatch(profile):
        raise HTTPException(409,"Chọn Chrome profile riêng đã kết nối OpenCLI")
    node,entry=_toolchain()
    return node,entry,profile

async def _invoke(node: str,entry: str,profile: str,session: str,
                  action: str,*argv: str) -> dict[str,Any]:
    # No .cmd/.bat launch or shell expansion; call npm package's JS directly.
    # No LLM API keys, bearer token, GitHub credential variables are forwarded.
    env={k:os.environ[k] for k in ("PATH","PATHEXT","SYSTEMROOT","WINDIR",
         "APPDATA","LOCALAPPDATA","USERPROFILE","TEMP","TMP","COMSPEC")
         if k in os.environ}
    env["OPENCLI_PROFILE"]=profile
    env["NO_COLOR"]="1"
    cmd=[node,entry,"--profile",profile,"browser",action,*argv,
         "--session",session]
    try:
        proc=await asyncio.create_subprocess_exec(
            *cmd,env=env,stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            creationflags=getattr(subprocess,"CREATE_NO_WINDOW",0))
        try:
            out,_=await asyncio.wait_for(proc.communicate(),timeout=35)
        except asyncio.TimeoutError as exc:
            proc.kill()
            await proc.communicate()
            raise HTTPException(504,"OpenCLI timeout; inspect browser before retry") from exc
    except OSError as exc:
        raise HTTPException(503,"OpenCLI could not be started") from exc
    if len(out)>MAX_OUTPUT:
        raise HTTPException(502,"OpenCLI output too large")
    try:
        value=json.loads(out.decode("utf-8"))
    except (UnicodeDecodeError,ValueError) as exc:
        raise HTTPException(502,"OpenCLI returned invalid JSON") from exc
    if not isinstance(value,dict) or value.get("ok") is not True or proc.returncode!=0:
        raise HTTPException(502,"OpenCLI command failed; inspect Chrome Bridge/profile")
    return value


def _url_from_response(response: dict[str,Any]) -> str:
    data=response.get("data")
    return data.get("url","") if isinstance(data,dict) else ""


def _state(response: dict[str,Any]) -> dict[str,Any]:
    data=response.get("data")
    url=_url_from_response(response)
    if not allowed_url(url):
        raise HTTPException(409,"OpenCLI page moved outside approved hosts; stop and inspect")
    snapshot=data.get("snapshot") if isinstance(data,dict) else None
    if not isinstance(snapshot,str):
        raise HTTPException(502,"OpenCLI state missing a text snapshot")
    return {"url":url,"snapshot":snapshot[:9000],
            "truncated":len(snapshot)>9000,"verified_navigation":True}


async def execute(ws: str,role: str,tool: str,args: dict[str,Any]) -> dict[str,Any]:
    validate(role,tool,args)
    if not _ID.fullmatch(ws) or not _ID.fullmatch(role):
        raise HTTPException(422,"Invalid workspace or role")
    node,entry,profile=_configuration()
    session="lhm_"+ws+"_"+role
    async def call(action:str,*rest:str):
        return await _invoke(node,entry,profile,session,action,*rest)
    if tool=="opencli_open":
        res=await call("open",args["url"])
        if not allowed_url(_url_from_response(res)):
            raise HTTPException(409,"OpenCLI navigation redirected outside approved hosts")
        return {"url":_url_from_response(res),"opened":True,"provider":"OpenCLI",
                "verified_navigation":True}
    before=_state(await call("state"))
    if tool=="opencli_state":
        return {**before,"provider":"OpenCLI","read_only":True}
    # Manual approval binds the URL and semantic label, not only an ordinal.
    # Ref [12] can identify another button after the page re-renders.
    ref=args["ref"]
    if before["url"]!=args["url"]:
        raise HTTPException(409,"OpenCLI page changed since approval; reapprove")
    marker="["+str(ref)+"]"
    line=next((line for line in before["snapshot"].splitlines()
               if marker in line),None)
    if line is None or args["label"].casefold() not in line.casefold():
        raise HTTPException(409,"OpenCLI element changed since approval; reapprove")
    if tool=="opencli_click":
        result=await call("click",str(ref))
        data=result.get("data")
        if not isinstance(data,dict) or data.get("clicked") is not True:
            raise HTTPException(409,"OpenCLI click not confirmed; do not auto retry")
    else:
        result=await call("fill",str(ref),args["text"])
        data=result.get("data")
        if not isinstance(data,dict) or data.get("filled") is not True or data.get("verified") is not True:
            raise HTTPException(409,"OpenCLI fill not confirmed; do not auto retry")
    # Postflight verifies current URL. This does not prove an external post,
    # purchase, or CRM mutation took place.
    after=_state(await call("state"))
    return {"provider":"OpenCLI","action":tool,
            "url_before":before["url"],"url_after":after["url"],
            "confirmed_by_cli":True,"external_effect_verified":False,
            "requires_owner_reconciliation":tool=="opencli_click"}
