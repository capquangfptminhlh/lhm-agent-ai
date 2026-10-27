"""Native Windows UI automation for the installed LHM Electron Robot.

Only the locally installed Computer Runner may import this module. On a
non-Windows host every action fails closed. The native controller deliberately
has NO shell executor and NEVER installs hooks, drivers or system services.

All desktop controls target a visible top-level HWND belonging to an exact
administrator-allowlisted executable (LHM_DESKTOP_ALLOWED_APPS). Every
mutation also requires a separately approved Computer step in the API.
"""
from __future__ import annotations

import base64
import ctypes
import io
import os
import re
from ctypes import wintypes
from fastapi import HTTPException

MAX_TEXT = 750
HWND_LIMIT = 80
NAME_LIMIT = 180

MOUSE_LEFT_DOWN=0x0002
MOUSE_LEFT_UP=0x0004
MOUSE_WHEEL=0x0800

def _require_windows():
    if os.name != "nt" or os.environ.get("LHM_DESKTOP_NATIVE") != "1":
        raise HTTPException(503,"Windows controller is not enabled on this machine")

def _allowlist():
    return {s.strip().lower() for s in os.environ.get("LHM_DESKTOP_ALLOWED_APPS","").split(",")
            if re.fullmatch(r"[a-zA-Z0-9._-]{3,70}\.exe",s.strip())}

def _user32():
    _require_windows()
    u=ctypes.windll.user32
    # Explicit 64-bit HWND prototypes (ctypes defaults to c_int, which
    # truncates real handles on 64-bit Windows).
    u.GetForegroundWindow.restype=wintypes.HWND
    u.IsWindow.argtypes=[wintypes.HWND]
    u.IsWindowVisible.argtypes=[wintypes.HWND]
    u.GetWindowTextLengthW.argtypes=[wintypes.HWND]
    u.GetWindowTextW.argtypes=[wintypes.HWND,wintypes.LPWSTR,ctypes.c_int]
    u.GetWindowRect.argtypes=[wintypes.HWND,ctypes.POINTER(wintypes.RECT)]
    u.GetWindowThreadProcessId.argtypes=[wintypes.HWND,ctypes.POINTER(wintypes.DWORD)]
    u.SetForegroundWindow.argtypes=[wintypes.HWND]
    u.ShowWindow.argtypes=[wintypes.HWND,ctypes.c_int]
    u.SetCursorPos.argtypes=[ctypes.c_int,ctypes.c_int]
    u.SendInput.argtypes=[wintypes.UINT,ctypes.c_void_p,ctypes.c_int]
    return u

def _window_info(hwnd):
    """Inspect a top-level window, never bypass the executable allowlist."""
    u=_user32()
    if type(hwnd) is not int or not 0 < hwnd < 2**64:
        raise HTTPException(422,"HWND must be a valid integer")
    if not u.IsWindow(hwnd) or not u.IsWindowVisible(hwnd):
        raise HTTPException(404,"Window is not visible")
    pid=wintypes.DWORD(0)
    u.GetWindowThreadProcessId(wintypes.HWND(hwnd),ctypes.byref(pid))
    if not pid.value:
        raise HTTPException(404,"Window process unavailable")
    try:
        import psutil
        process=psutil.Process(pid.value)
        name=process.name().lower()
    except (ImportError,Exception) as exc:
        raise HTTPException(403,"Cannot validate owning process") from exc
    if name not in _allowlist():
        raise HTTPException(403,"Window executable is not on the desktop allowlist")
    size=min(500,max(1,u.GetWindowTextLengthW(hwnd)+1))
    title=ctypes.create_unicode_buffer(size)
    u.GetWindowTextW(wintypes.HWND(hwnd),title,size)
    rect=wintypes.RECT()
    if not u.GetWindowRect(wintypes.HWND(hwnd),ctypes.byref(rect)):
        raise HTTPException(404,"Window rectangle unavailable")
    w,h=rect.right-rect.left,rect.bottom-rect.top
    if w<2 or h<2:
        raise HTTPException(422,"Window has no usable bounds")
    return {"hwnd":hwnd,"pid":pid.value,"process":name,"title":title.value[:NAME_LIMIT],
            "x":rect.left,"y":rect.top,"width":w,"height":h}

def _enumerate():
    _require_windows()
    if not _allowlist():
        return []
    u=_user32()
    handles=[]
    callback_type=ctypes.WINFUNCTYPE(wintypes.BOOL,wintypes.HWND,wintypes.LPARAM)
    @callback_type
    def visitor(hwnd,_):
        if len(handles)<300: handles.append(int(hwnd))
        return True
    u.EnumWindows(visitor,0)
    result=[]
    for handle in handles:
        if len(result)>=HWND_LIMIT:break
        try:result.append(_window_info(handle))
        except HTTPException:continue
    return result

def _focus(hwnd):
    i=_window_info(hwnd)
    u=_user32()
    u.ShowWindow(wintypes.HWND(hwnd),9)
    u.SetForegroundWindow(wintypes.HWND(hwnd))
    if u.GetForegroundWindow()!=hwnd:
        raise HTTPException(409,"Windows denied foreground focus. Click the app manually and retry.")
    return i

def _foreground(hwnd):
    i=_window_info(hwnd)
    if _user32().GetForegroundWindow()!=hwnd:
        raise HTTPException(409,"Target window is no longer foreground")
    return i

def _coordinates(i,args):
    x=args.get("x");y=args.get("y")
    if type(x) is not int or type(y) is not int:
        raise HTTPException(422,"Local x/y must be integers")
    # Avoid borders/title bar and accidental interaction with adjacent windows.
    if not 8 <= x < i["width"]-8 or not 8 <= y < i["height"]-8:
        raise HTTPException(422,"Click must lie inside the allowed window")
    return i["x"]+x,i["y"]+y

def _type_unicode(text):
    u=_user32()
    # Windows SendInput with KEYEVENTF_UNICODE types real UTF-16 characters,
    # including Vietnamese, without changing the user's clipboard.
    class MOUSEINPUT(ctypes.Structure):
        _fields_=[("dx",wintypes.LONG),("dy",wintypes.LONG),
                  ("mouseData",wintypes.DWORD),("dwFlags",wintypes.DWORD),
                  ("time",wintypes.DWORD),("dwExtraInfo",ctypes.c_size_t)]
    class KEYBDINPUT(ctypes.Structure):
        _fields_=[("wVk",wintypes.WORD),("wScan",wintypes.WORD),
                  ("dwFlags",wintypes.DWORD),("time",wintypes.DWORD),
                  ("dwExtraInfo",ctypes.c_size_t)]
    class HARDWAREINPUT(ctypes.Structure):
        _fields_=[("uMsg",wintypes.DWORD),("wParamL",wintypes.WORD),
                  ("wParamH",wintypes.WORD)]
    class UNION(ctypes.Union):
        _fields_=[("mi",MOUSEINPUT),("ki",KEYBDINPUT),("hi",HARDWAREINPUT)]
    class INPUT(ctypes.Structure):
        _fields_=[("type",wintypes.DWORD),("union",UNION)]
    key_units=[int.from_bytes(text.encode("utf-16-le")[i:i+2],"little")
               for i in range(0,len(text.encode("utf-16-le")),2)]
    if len(key_units)>MAX_TEXT*2:
        raise HTTPException(422,"Too many typed characters")
    seq=[]
    for k in key_units:
        for flags in (0x0004,0x0004|0x0002):
            entry=INPUT();entry.type=1
            entry.union.ki=KEYBDINPUT(0,k,flags,0,0)
            seq.append(entry)
    if seq:
        array=(INPUT*len(seq))(*seq)
        count=u.SendInput(len(seq),array,ctypes.sizeof(INPUT))
        if count!=len(seq):
            raise HTTPException(502,"Windows rejected injected key events")

def _snap(hwnd):
    i=_window_info(hwnd)
    try:
        from PIL import ImageGrab
        image=ImageGrab.grab(bbox=(i["x"],i["y"],i["x"]+i["width"],i["y"]+i["height"]))
        image.thumbnail((1400,1000))
        b=io.BytesIO()
        image.save(b,format="PNG",optimize=True)
        raw=b.getvalue()
    except Exception as exc:
        raise HTTPException(502,"Unable to capture selected Windows application") from exc
    if len(raw)>2*1024*1024:
        raise HTTPException(413,"Windows screenshot is too large")
    return {"png_base64":base64.b64encode(raw).decode("ascii"),
            "hwnd":hwnd,"title":i["title"],"process":i["process"],
            "width":image.width,"height":image.height}

def execute(tool: str,args: dict):
    """All native actions are invoked from the authenticated Runner process."""
    _require_windows()
    if not isinstance(args,dict):
        raise HTTPException(422,"Args must be an object")
    if tool=="windows_list":
        return {"windows":_enumerate(),"allowlist":sorted(_allowlist())}
    hwnd=args.get("hwnd")
    if type(hwnd) is not int:
        raise HTTPException(422,"A selected HWND is required")
    if tool=="windows_snapshot":
        i=_window_info(hwnd)
        try:
            from pywinauto import Desktop
            items=[]
            target=Desktop(backend="uia").window(handle=hwnd)
            for child in target.descendants()[:60]:
                try:
                    kind=child.element_info.control_type
                    name=child.window_text()
                    # Never read contents or values of edit/password fields.
                    items.append({"type":str(kind)[:70],
                                  "label":str(name)[:100] if kind not in ("Edit","Document") else ""})
                except Exception:continue
            return {"window":i,"controls":items}
        except Exception:
            return {"window":i,"controls":[],"note":"UI Automation controls unavailable"}
    if tool=="windows_screenshot":
        return _snap(hwnd)
    if tool=="windows_activate":
        i=_focus(hwnd)
        return {"focused":True,"window":i}
    if tool=="windows_click":
        i=_foreground(hwnd)
        x,y=_coordinates(i,args)
        u=_user32()
        if not u.SetCursorPos(x,y):
            raise HTTPException(502,"Unable to move mouse")
        u.mouse_event(MOUSE_LEFT_DOWN,0,0,0,0)
        u.mouse_event(MOUSE_LEFT_UP,0,0,0,0)
        return {"clicked":True,"hwnd":hwnd,"x":args["x"],"y":args["y"]}
    if tool=="windows_type":
        _foreground(hwnd)
        text=args.get("text")
        if not isinstance(text,str) or not text or len(text)>MAX_TEXT or any(
            ord(ch)<32 and ch not in ("\n","\t") for ch in text):
            raise HTTPException(422,"Text must be <=750 printable characters")
        _type_unicode(text)
        # Do not echo typed text; it could be confidential.
        return {"typed":True,"characters":len(text),"hwnd":hwnd}
    if tool=="windows_scroll":
        i=_foreground(hwnd)
        amount=args.get("steps",3)
        if type(amount) is not int or not -8<=amount<=8 or amount==0:
            raise HTTPException(422,"Scroll steps must be -8..8 excluding zero")
        u=_user32()
        u.SetCursorPos(i["x"]+i["width"]//2,i["y"]+i["height"]//2)
        u.mouse_event(MOUSE_WHEEL,0,0,amount*120,0)
        return {"scrolled":True,"steps":amount,"hwnd":hwnd}
    raise HTTPException(422,"Unsupported Windows tool")
