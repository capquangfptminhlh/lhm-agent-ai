"""LHM Computer Runner: a separate non-root service for sandboxed agent tools.

Important: one shared runner is NOT an isolated VM per agent. The OS process
has access to its mounted volume. Use a dedicated runner/container per tenant
and per agent for strict isolation. Never mount host directories or Docker sock.
No GitHub/API/LLM tokens should be present inside this container.
"""
from __future__ import annotations
import asyncio
import base64
import os
import re
try:
    import resource  # Linux-only child process limits.
except ImportError:
    resource = None
import shutil
from pathlib import Path
from urllib.parse import urlsplit

from fastapi import FastAPI, Header, HTTPException
from pydantic import BaseModel, Field
from typing import Any, Literal

runner = FastAPI(title="LHM Computer Runner", docs_url=None, redoc_url=None, openapi_url=None)
ROOT = Path(os.getenv("LHM_COMPUTER_HOME", "/agent-data")).resolve()
ROLE_RE = re.compile(r"^[a-z][a-z0-9_]{1,39}$")
ID_RE = re.compile(r"^[a-z0-9][a-z0-9_-]{1,48}$")
ALLOWED_COMMANDS = {"python","python3","pytest","git","ls","cat","pwd","find",
                    "rg","grep","sed","wc","head","tail","mkdir","cp","mv"}
WINDOWS_TOOLS = {"windows_list","windows_snapshot","windows_screenshot",
                 "windows_activate","windows_click","windows_type","windows_scroll",
                 "windows_right_click","windows_double_click","windows_hotkey","windows_desktop_screenshot",
                 "windows_drag","windows_desktop_click","windows_desktop_drag","windows_desktop_scroll"}
ALLOWED_TOOLS = {"opencli_open","opencli_state","opencli_click","opencli_fill","agent_reach_search","list_files","read_file","write_file","edit_file","run_command","sandbox_run","browser_open",
                 "browser_snapshot","browser_click","browser_fill","browser_scroll","browser_screenshot"} | WINDOWS_TOOLS
PAUSED = False
MUTATIONS = {"opencli_click","opencli_fill","write_file","edit_file","run_command","sandbox_run","browser_click","browser_fill",
             "windows_activate","windows_click","windows_type","windows_scroll",
             "windows_right_click","windows_double_click","windows_hotkey","windows_desktop_screenshot",
             "windows_drag","windows_desktop_click","windows_desktop_drag","windows_desktop_scroll"}
MAX_FILE_BYTES = 96_000
MAX_OUTPUT_CHARS = 12_000
BROWSER_SESSIONS: dict[str,Any] = {}
BROWSER_LOCKS: dict[str,asyncio.Lock] = {}
_PLAYWRIGHT = None

class ToolRequest(BaseModel):
    workspace_id: str = Field(pattern=r"^[a-z0-9][a-z0-9_-]{1,48}$")
    role: str = Field(pattern=r"^[a-z][a-z0-9_]{1,39}$")
    tool: Literal["opencli_open","opencli_state","opencli_click","opencli_fill","agent_reach_search","list_files","read_file","write_file","edit_file","run_command","sandbox_run","browser_open",
                  "browser_snapshot","browser_click","browser_fill","browser_scroll","browser_screenshot",
                  "windows_list","windows_snapshot","windows_screenshot","windows_activate",
                  "windows_click","windows_type","windows_scroll",
                  "windows_right_click","windows_double_click","windows_hotkey","windows_desktop_screenshot",
                  "windows_drag","windows_desktop_click","windows_desktop_drag","windows_desktop_scroll"]
    args: dict[str,Any] = Field(default_factory=dict)

def _auth(authorization: str | None) -> None:
    import secrets
    token=os.getenv("LHM_COMPUTER_RUNNER_TOKEN","").strip()
    if not token:
        raise HTTPException(503,"Runner token not configured")
    if not authorization or not secrets.compare_digest(authorization,"Bearer "+token):
        raise HTTPException(401,"Runner authentication required")

def _home(ws: str, role: str) -> Path:
    if not ID_RE.fullmatch(ws) or not ROLE_RE.fullmatch(role):
        raise HTTPException(422,"Invalid workspace/agent identifier")
    root=ROOT/ws/role
    root.mkdir(parents=True,exist_ok=True)
    return root

def _files(ws: str, role: str) -> Path:
    target=_home(ws,role)/"files"
    target.mkdir(parents=True,exist_ok=True)
    return target

def _safe_path(ws: str,role: str,name: str) -> Path:
    if not isinstance(name,str) or not name or len(name)>360 or name.startswith("/"):
        raise HTTPException(422,"Invalid relative path")
    base=_files(ws,role).resolve()
    path=(base/name).resolve()
    if not path.is_relative_to(base):
        raise HTTPException(403,"Path outside Agent sandbox")
    return path

def _allowed_domain(url: str, role: str | None = None) -> bool:
    allow={x.lower().rstrip(".").strip() for x in
            os.getenv("LHM_COMPUTER_ALLOWED_HOSTS","").split(",") if x.strip()}
    # Extra network fence for specialist Chrome profiles. Both global and
    # role host lists must allow a target; an empty global list denies all.
    if role in ("content","sales","quality"):
        raw=os.getenv("LHM_COMPUTER_"+role.upper()+"_ALLOWED_HOSTS","")
        # Fail closed: an empty role allowlist cannot inherit the global list.
        own={x.lower().rstrip(".").strip() for x in raw.split(",") if x.strip()}
        allow &= own
    try:
        u=urlsplit(url)
        host=(u.hostname or "").lower().rstrip(".")
        return u.scheme=="https" and host in allow and bool(host) and (
            u.port in (None,443)) and not u.username and not u.password
    except ValueError:
        return False

def _child_limits() -> None:
    # POSIX runner on Linux only. Timeout and memory are defensive limits,
    # not a substitute for per-container / VM security boundaries.
    if resource is None:
        return
    resource.setrlimit(resource.RLIMIT_CPU,(15,16))
    resource.setrlimit(resource.RLIMIT_FSIZE,(12*1024*1024,12*1024*1024))
    resource.setrlimit(resource.RLIMIT_NOFILE,(128,128))

async def _command(ws: str,role: str,args: dict[str,Any]) -> dict:
    argv=args.get("argv")
    if not isinstance(argv,list) or not 1<=len(argv)<=24 or any(
         not isinstance(x,str) or len(x)>500 or "\x00" in x for x in argv):
        raise HTTPException(422,"Command must be a bounded argv list; no shell")
    binary=argv[0]
    if "/" in binary or binary not in ALLOWED_COMMANDS:
        raise HTTPException(403,"Command not in allowed sandbox toolset")
    exe=shutil.which(binary)
    if not exe:
        raise HTTPException(422,"Sandbox command not installed")
    cwd=_safe_path(ws,role,args.get("cwd","."))
    if not cwd.is_dir():
        raise HTTPException(422,"Working directory not found")
    # Do not pass the runner bearer token, service account env or secrets to
    # child processes. Commands run as non-root inside a restricted sidecar.
    env={"PATH":"/usr/local/bin:/usr/bin:/bin","HOME":str(_home(ws,role)),
         "LANG":"C.UTF-8","PYTHONDONTWRITEBYTECODE":"1"}
    # The host Windows runner is NOT a code sandbox. Unrestricted Python
    # would have the logged-in user's filesystem and network privileges.
    if os.name=="nt":
        raise HTTPException(403,"Host Windows terminal disabled. Use Docker/VM sandbox.")
    proc=await asyncio.create_subprocess_exec(exe,*argv[1:],cwd=str(cwd),
        env=env,stdout=asyncio.subprocess.PIPE,stderr=asyncio.subprocess.PIPE,
        preexec_fn=_child_limits,start_new_session=True)
    try:
        stdout,stderr=await asyncio.wait_for(proc.communicate(),timeout=18)
    except asyncio.TimeoutError:
        import signal
        os.killpg(proc.pid,signal.SIGKILL)
        await proc.communicate()
        return {"exit_code":-1,"timed_out":True,"stdout":"","stderr":"Command exceeded 18s"}
    return {"exit_code":proc.returncode,"timed_out":False,
            "stdout":stdout[:MAX_OUTPUT_CHARS].decode("utf-8","replace"),
            "stderr":stderr[:MAX_OUTPUT_CHARS].decode("utf-8","replace")}

async def _browser(ws: str,role: str,tool: str,args: dict[str,Any]) -> dict:
    global _PLAYWRIGHT
    try:
        from playwright.async_api import async_playwright
    except ImportError:
        raise HTTPException(503,"Playwright missing in Computer Runner")
    key=ws+"/"+role
    if key not in BROWSER_LOCKS:
        BROWSER_LOCKS[key]=asyncio.Lock()
    async with BROWSER_LOCKS[key]:
        if key not in BROWSER_SESSIONS:
            if _PLAYWRIGHT is None:
                _PLAYWRIGHT=await async_playwright().start()
            profile=_home(ws,role)/"browser"
            channel=os.environ.get("LHM_COMPUTER_BROWSER_CHANNEL","").strip()
            # Standard sidecar uses its bundled Chromium. CI may use the
            # system Chrome channel to exercise this real browser pathway.
            options={"channel":channel} if channel in ("chrome","chromium","msedge") else {}
            context=await _PLAYWRIGHT.chromium.launch_persistent_context(
                str(profile),headless=True,accept_downloads=False,
                args=["--no-sandbox","--disable-dev-shm-usage"],
                viewport={"width":1300,"height":850},**options)
            async def guard(route):
                url=route.request.url
                if url.startswith(("about:","data:","blob:")) or _allowed_domain(url, role):
                    await route.continue_()
                else:
                    await route.abort()
            await context.route("**/*",guard)
            BROWSER_SESSIONS[key]=context
        context=BROWSER_SESSIONS[key]
        page=context.pages[0] if context.pages else await context.new_page()
        page.set_default_timeout(8000)
        if tool=="browser_open":
            url=args.get("url","")
            if not isinstance(url,str) or not _allowed_domain(url, role):
                raise HTTPException(422,"Browser URL must match an admin-approved HTTPS host")
            await page.goto(url,wait_until="domcontentloaded",timeout=16000)
        elif tool=="browser_scroll":
            direction=args.get("direction","down")
            if direction not in ("up","down"):
                raise HTTPException(422,"Scroll direction up/down required")
            distance=args.get("pixels",450)
            if type(distance) is not int or not 1<=distance<=1100:
                raise HTTPException(422,"Scroll must be between 1 and 1100 pixels")
            await page.mouse.wheel(0,distance if direction=="down" else -distance)
        elif tool in ("browser_click","browser_fill"):
            selector=args.get("selector")
            if not isinstance(selector,str) or not 1<=len(selector)<=240:
                raise HTTPException(422,"Provide a bounded CSS selector")
            element=page.locator(selector).first
            if tool=="browser_click":
                await element.click(timeout=7000)
            else:
                value=args.get("text")
                if not isinstance(value,str) or len(value)>3000:
                    raise HTTPException(422,"Text must be <=3000 chars")
                # Password fields are never filled by the Agent in any
                # mode (operating decision 2026-10-27: secrets always stay
                # with the human). Fail closed before any fill happens.
                try:
                    field_type=await element.evaluate(
                        "e => ((e.getAttribute && e.getAttribute('type')) || '').toLowerCase()")
                except Exception:
                    field_type=""
                if field_type=="password":
                    raise HTTPException(403,"Agent never fills password fields; enter secrets manually")
                await element.fill(value,timeout=7000)
        if tool=="browser_screenshot":
            png=await page.screenshot(type="png",full_page=False,timeout=15000)
            if len(png)>2*1024*1024:
                raise HTTPException(413,"Screenshot larger than 2MB")
            return {"png_base64":base64.b64encode(png).decode("ascii"),"url":page.url}
        # Limit observation size to keep model context and cost bounded.
        heading=await page.title()
        text=(await page.locator("body").inner_text(timeout=7000))[:9000]
        links=await page.locator("a[href]").evaluate_all(
            "(xs)=>xs.slice(0,40).map(x=>({text:(x.innerText||'').slice(0,100),href:x.href}))")
        inputs=await page.locator("input,button,textarea,select").evaluate_all(
            "(xs)=>xs.slice(0,60).map(x=>({tag:x.tagName.toLowerCase(),"
            "selector:x.id?'#'+x.id:(x.getAttribute('name')?'[name='+JSON.stringify(x.getAttribute('name'))+']':x.tagName.toLowerCase()),"
            "text:(x.innerText||x.getAttribute('aria-label')||'').slice(0,80)}))")
        return {"url":page.url,"title":heading[:180],"text":text,
                "links":links,"controls":inputs}

def __native_tool(tool: str,args: dict):
    from windows_native import execute
    return execute(tool,args)


async def perform(req: ToolRequest) -> dict:
    ws,role,tool,args=req.workspace_id,req.role,req.tool,req.args
    if PAUSED and tool in MUTATIONS:
        raise HTTPException(423,"Emergency pause is active; no mutations allowed")
    if tool in WINDOWS_TOOLS:
        # Validate platform BEFORE touching the sandbox volume. A Linux
        # worker with an unmounted /agent-data must fail closed with 503.
        if os.name!="nt" or os.environ.get("LHM_DESKTOP_NATIVE")!="1":
            raise HTTPException(503,"Windows native control is not available")
        return __native_tool(tool,args)
    _home(ws,role)
    if tool.startswith("opencli_"):
        from opencli_windows_bridge import execute as opencli_execute
        return await opencli_execute(ws,role,tool,args)
    if tool=="agent_reach_search":
        from agent_reach_bridge import search as agent_reach_search
        return await agent_reach_search(ws,role,args,_home(ws,role))
    if tool=="sandbox_run":
        if os.name!="nt" or os.environ.get("LHM_DESKTOP_NATIVE")!="1":
            raise HTTPException(503,"Local Docker sandbox command is Windows Desktop-only")
        from robot_sandbox import run as sandbox_run
        return await sandbox_run(ws,role,args)
    if tool=="list_files":
        path=_safe_path(ws,role,args.get("path","."))
        if not path.is_dir():
            raise HTTPException(422,"Not a directory")
        entries=sorted(path.iterdir(),key=lambda x:x.name)[:100]
        return {"entries":[{"name":x.name,"type":"directory" if x.is_dir() else "file",
                            "bytes":x.stat().st_size if x.is_file() else None} for x in entries]}
    if tool=="read_file":
        path=_safe_path(ws,role,args.get("path",""))
        if not path.is_file() or path.stat().st_size>MAX_FILE_BYTES:
            raise HTTPException(422,"File unavailable or >96KB")
        return {"path":str(path.relative_to(_files(ws,role))),"content":path.read_text(encoding="utf-8")[:MAX_OUTPUT_CHARS]}
    if tool=="write_file":
        path=_safe_path(ws,role,args.get("path",""))
        data=args.get("content")
        if not isinstance(data,str) or len(data.encode("utf-8"))>MAX_FILE_BYTES:
            raise HTTPException(422,"File must be text <=96KB")
        path.parent.mkdir(parents=True,exist_ok=True)
        # Re-check after mkdir; symlinks and traversal must not escape root.
        path=_safe_path(ws,role,args["path"])
        path.write_text(data,encoding="utf-8")
        return {"path":str(path.relative_to(_files(ws,role))),"bytes":len(data.encode("utf-8"))}
    if tool=="edit_file":
        path=_safe_path(ws,role,args.get("path",""))
        old=args.get("old");new=args.get("new")
        if not path.is_file() or path.stat().st_size>MAX_FILE_BYTES:
            raise HTTPException(422,"Existing UTF-8 file <=96KB required")
        if not isinstance(old,str) or not old or not isinstance(new,str):
            raise HTTPException(422,"Exact non-empty old text and replacement text required")
        text=path.read_text(encoding="utf-8")
        if text.count(old)!=1:
            raise HTTPException(409,"Old text must match exactly once; reload file first")
        updated=text.replace(old,new,1)
        if len(updated.encode("utf-8"))>MAX_FILE_BYTES:
            raise HTTPException(413,"Result file too large")
        # Same-path tempfile and replace avoids partial files if process crashes.
        import tempfile
        with tempfile.NamedTemporaryFile(mode="w",encoding="utf-8",dir=path.parent,
                                         prefix=".lhm-",delete=False) as tmp:
            tmp.write(updated);name=tmp.name
        try:
            # Re-check symlink/traversal before replacing target.
            if _safe_path(ws,role,args["path"])!=path:
                raise HTTPException(403,"Target path changed")
            os.replace(name,path)
        finally:
            if os.path.exists(name):os.unlink(name)
        return {"path":str(path.relative_to(_files(ws,role))),"edited":True,
                "bytes":len(updated.encode("utf-8"))}
    if tool=="run_command":
        return await _command(ws,role,args)
    if tool.startswith("browser_"):
        return await _browser(ws,role,tool,args)
    raise HTTPException(422,"Unsupported computer tool")

@runner.get("/health")
async def health(authorization: str | None = Header(None)):
    _auth(authorization)
    return {"ok":True,
            "mode":"windows_native" if os.name=="nt" and os.environ.get("LHM_DESKTOP_NATIVE")=="1" else "separate_runner",
            "paused":PAUSED,
            "agent_isolation":"directories_not_vms",
            "agent_reach_cli_installed":bool(shutil.which("agent-reach")),
            "tools":sorted(ALLOWED_TOOLS)}

@runner.post("/v1/tool")
async def tool_endpoint(request: ToolRequest,authorization: str | None = Header(None)):
    _auth(authorization)
    try:
        return await perform(request)
    except HTTPException:
        raise
    except (OSError,ValueError,TimeoutError) as exc:
        raise HTTPException(422,f"Sandbox tool failed: {type(exc).__name__}") from exc
    except Exception as exc:
        # Do not leak browser cookies/tokens, page data or internal tracebacks.
        raise HTTPException(502,"Browser/runner action failed; inspect runner server logs") from exc

class PauseIn(BaseModel):
    paused: bool

@runner.post("/v1/pause")
async def emergency_pause(inp: PauseIn, authorization: str | None = Header(None)):
    _auth(authorization)
    global PAUSED
    PAUSED = inp.paused
    return {"paused":PAUSED,
            "notice":"In-flight Windows actions may have already completed; pause blocks NEW mutations."}
