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
import time
from ctypes import wintypes
from fastapi import HTTPException

MAX_TEXT = 750
HWND_LIMIT = 80
NAME_LIMIT = 180

MOUSE_LEFT_DOWN=0x0002
MOUSE_LEFT_UP=0x0004
MOUSE_RIGHT_DOWN=0x0008
MOUSE_RIGHT_UP=0x0010
MOUSE_WHEEL=0x0800
BLOCKED_APPS={"cmd.exe","powershell.exe","pwsh.exe","wt.exe",
              "regedit.exe","mmc.exe","taskmgr.exe","conhost.exe",
              "wscript.exe","cscript.exe","mshta.exe","rundll32.exe",
              "python.exe","pythonw.exe","node.exe","sdelete.exe"}

def _require_windows():
    if os.name != "nt" or os.environ.get("LHM_DESKTOP_NATIVE") != "1":
        raise HTTPException(503,"Windows controller is not enabled on this machine")

def _allowlist():
    return {s.strip().lower() for s in os.environ.get("LHM_DESKTOP_ALLOWED_APPS","").split(",")
            if re.fullmatch(r"[a-zA-Z0-9._-]{3,70}\.exe",s.strip()) and s.strip().lower() not in BLOCKED_APPS}

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
    u.keybd_event.argtypes=[wintypes.BYTE,wintypes.BYTE,wintypes.DWORD,ctypes.c_size_t]
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
        # Foreground-lock recovery (standard Win32 technique): Windows
        # grants foreground rights to a process that has just sent input,
        # and AttachThreadInput lets this thread act on the current
        # foreground window's input queue during the switch. Without
        # this, activation is denied whenever another process holds the
        # foreground lock (services, CI sessions, freshly spawned apps).
        try:
            u.keybd_event(0x12,0,0,0)  # ALT down: this process sent input
            u.keybd_event(0x12,0,2,0)  # ALT up
        except Exception:
            pass
        try:
            fg=u.GetForegroundWindow()
            kernel32=ctypes.windll.kernel32
            current_tid=kernel32.GetCurrentThreadId()
            fg_tid=u.GetWindowThreadProcessId(fg,None) if fg else 0
            attached=False
            if fg_tid and fg_tid!=current_tid:
                attached=bool(u.AttachThreadInput(current_tid,fg_tid,True))
            try:
                u.BringWindowToTop(wintypes.HWND(hwnd))
                u.SetForegroundWindow(wintypes.HWND(hwnd))
            finally:
                if attached:
                    u.AttachThreadInput(current_tid,fg_tid,False)
        except Exception:
            pass
    if u.GetForegroundWindow()!=hwnd:
        raise HTTPException(409,"Windows denied foreground focus. Click the app manually and retry.")
    return i

def _foreground(hwnd):
    i=_window_info(hwnd)
    if _user32().GetForegroundWindow()!=hwnd:
        raise HTTPException(409,"Target window is no longer foreground")
    return i

def _foreground_or_refocus(hwnd):
    """Foreground check for interaction tools, with ONE bounded re-focus.

    Focus gets stolen in real sessions (notifications, task switcher
    settling, another app finishing launch). The step already targets
    this exact allowlisted window, so a single re-focus attempt of the
    same window is inside the tool's contract; if Windows still refuses,
    the original 409 stands.
    """
    try:
        return _foreground(hwnd)
    except HTTPException as exc:
        if exc.status_code!=409:
            raise
        _focus(hwnd)
        return _foreground(hwnd)

def _coordinates(i,args):
    x=args.get("x");y=args.get("y")
    if type(x) is not int or type(y) is not int:
        raise HTTPException(422,"Local x/y must be integers")
    # Avoid borders/title bar and accidental interaction with adjacent windows.
    if not 8 <= x < i["width"]-8 or not 8 <= y < i["height"]-8:
        raise HTTPException(422,"Click must lie inside the allowed window")
    return i["x"]+x,i["y"]+y

def _drag_points(i,args):
    """Pure validation for a window-local drag: both ends inside the window.

    Kept free of Win32 calls so the bounds logic is unit-testable anywhere.
    Returns absolute screen coordinates (sx, sy, ex, ey)."""
    sx,sy=_coordinates(i,args)
    ex,ey=_coordinates(i,{"x":args.get("to_x"),"y":args.get("to_y")})
    if (sx,sy)==(ex,ey):
        raise HTTPException(422,"Drag start and end must differ")
    return sx,sy,ex,ey


def _require_desktop_consent():
    # Same explicit Electron opt-in that enables full-desktop capture also
    # enables absolute desktop coordinates. Without it these tools do not
    # exist in practice: every call fails closed with 403.
    if os.environ.get("LHM_DESKTOP_FULL_SCREEN_ENABLED") != "1":
        raise HTTPException(403,"Desktop coordinates require explicit screen consent")


def _desktop_point(args,metrics):
    """Pure validation for an absolute desktop point.

    metrics = (origin_x, origin_y, width, height) of the virtual screen.
    Kept Win32-free for unit tests."""
    x=args.get("x");y=args.get("y")
    if type(x) is not int or type(y) is not int:
        raise HTTPException(422,"Desktop x/y must be integers")
    ox,oy,w,h=metrics
    if not (ox <= x < ox+w and oy <= y < oy+h):
        raise HTTPException(422,"Point lies outside the virtual desktop")
    return x,y


def _virtual_screen_metrics():
    u=_user32()
    get=u.GetSystemMetrics
    return (get(76),get(77),get(78),get(79))  # SM_X/Y/CX/CYVIRTUALSCREEN


def _mouse_button(u,button,double=False):
    downs={ "left":(MOUSE_LEFT_DOWN,MOUSE_LEFT_UP),
            "right":(MOUSE_RIGHT_DOWN,MOUSE_RIGHT_UP)}
    if button not in downs:
        raise HTTPException(422,"Button must be left or right")
    down,up=downs[button]
    import time as _time
    for n in range(2 if double else 1):
        if n:_time.sleep(0.075)
        u.mouse_event(down,0,0,0,0)
        u.mouse_event(up,0,0,0,0)


def _drag_absolute(u,sx,sy,ex,ey):
    import time as _time
    if not u.SetCursorPos(sx,sy):
        raise HTTPException(502,"Unable to move mouse")
    u.mouse_event(MOUSE_LEFT_DOWN,0,0,0,0)
    try:
        steps=14
        for n in range(1,steps+1):
            u.SetCursorPos(round(sx+(ex-sx)*n/steps),round(sy+(ey-sy)*n/steps))
            _time.sleep(0.016)
    finally:
        u.mouse_event(MOUSE_LEFT_UP,0,0,0,0)


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
        count=u.SendInput(len(seq),ctypes.cast(array,ctypes.c_void_p),ctypes.sizeof(INPUT))
        if count!=len(seq):
            raise HTTPException(502,"Windows rejected injected key events")


# Deliberately narrow keyboard allowlist. OS shortcuts, Run dialogs and shell
# launchers are excluded. Every call must be explicitly approved by a human.
KEYS = {
    "ENTER": 0x0D, "ESC": 0x1B, "TAB": 0x09, "BACKSPACE": 0x08,
    "DELETE": 0x2E, "LEFT": 0x25, "UP": 0x26,
    "RIGHT": 0x27, "DOWN": 0x28, "HOME": 0x24, "END": 0x23,
    "PAGEUP": 0x21, "PAGEDOWN": 0x22
}
APPROVED_SHORTCUTS = {"CTRL+A","CTRL+C","CTRL+V","CTRL+S","CTRL+F",
                      "CTRL+Z","CTRL+Y","CTRL+SHIFT+S",
                      # Window switching is required for real multi-app work
                      # (Parity acceptance task 5). Only the two Alt+Tab
                      # forms are allowed; no other OS-level shortcut is.
                      "ALT+TAB","ALT+SHIFT+TAB"}


def _validate_hotkey(value):
    if not isinstance(value, str) or len(value) > 20:
        raise HTTPException(422, "Invalid keyboard shortcut")
    normalized=value.strip().upper()
    if normalized not in APPROVED_SHORTCUTS and normalized not in KEYS:
        raise HTTPException(422, "Shortcut is not in the safe supervised allowlist")
    return normalized


def _press_hotkey(value):
    name=_validate_hotkey(value)
    seq=name.split("+")
    vk={"CTRL":0x11,"SHIFT":0x10,"ALT":0x12,
        **{chr(k):k for k in range(ord("A"),ord("Z")+1)},
        **KEYS}
    codes=[vk[v] for v in seq]
    u=_user32()
    pushed=[]
    try:
        for code in codes:
            u.keybd_event(code,0,0,0)
            pushed.append(code)
    finally:
        for code in reversed(pushed):
            u.keybd_event(code,0,0x0002,0)


def _desktop_capture():
    _require_windows()
    # Global screen viewing is OFF by default. A separate approved Computer
    # step + this explicit Electron opt-in are both required.
    if os.environ.get("LHM_DESKTOP_FULL_SCREEN_ENABLED") != "1":
        raise HTTPException(403, "Desktop capture requires explicit screen consent")
    try:
        from PIL import ImageGrab
        image=ImageGrab.grab(all_screens=True)
        image.thumbnail((1600,1000))
        b=io.BytesIO()
        image.save(b,format="PNG",optimize=True)
        raw=b.getvalue()
    except Exception as exc:
        raise HTTPException(502, "Unable to capture Windows desktop") from exc
    if len(raw)>2*1024*1024:
        raise HTTPException(413, "Desktop capture exceeds safe bound")
    # Never save a screenshot on disk, in logs, or in the computer audit log.
    return {"png_base64":base64.b64encode(raw).decode("ascii"),
            "scope":"desktop","width":image.width,"height":image.height}

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

# Only controls that are ordinarily activated with a mouse click are eligible.
# No Edit/Document/password controls, shell commands, arbitrary selectors or XPath.
UIA_CLICK_TYPES=frozenset({"Button","CheckBox","RadioButton","Hyperlink",
                           "MenuItem","TabItem","ListItem","TreeItem"})

def _click_uia_control(hwnd: int, args: dict) -> dict:
    """Click one fresh, unique UIA target in an allowlisted foreground HWND.

    The model must provide all three fields taken from windows_snapshot.
    We re-resolve at execution time (after owner approval) rather than trusting
    cached coordinates. No substring/first-match fallback is permitted.
    """
    automation_id=args.get("automation_id")
    kind=args.get("control_type")
    label=args.get("label")
    if (not isinstance(automation_id,str) or not 1<=len(automation_id)<=100
            or not isinstance(label,str) or not 1<=len(label)<=100
            or kind not in UIA_CLICK_TYPES):
        raise HTTPException(422,"Use exact observed automation_id, control_type and nonempty label")
    window=_foreground_or_refocus(hwnd)
    try:
        from pywinauto import Desktop
        root=Desktop(backend="uia").window(handle=hwnd)
        matches=[]
        for child in root.descendants()[:60]:
            try:
                meta=child.element_info
                if (str(meta.control_type or "")!=kind
                        or str(meta.automation_id or "")!=automation_id
                        or bool(getattr(meta,"is_password",False))
                        or str(child.window_text() or "")!=label):
                    continue
                matches.append(child)
            except (AttributeError,TypeError,ValueError,OverflowError):
                continue
    except Exception as exc:
        raise HTTPException(503,"UI Automation unavailable; no click performed") from exc
    if len(matches)!=1:
        raise HTTPException(409,"UI Automation target missing or ambiguous; refresh snapshot")
    target=matches[0]
    try:
        rect=target.element_info.rectangle
        current=_foreground(hwnd)  # Revalidate allowlist and foreground on this window.
        bounds=(int(rect.left)-current["x"],int(rect.top)-current["y"],
                int(rect.right)-current["x"],int(rect.bottom)-current["y"])
        left,top,right,bottom=bounds
        if not (0<=left<right<=current["width"] and
                0<=top<bottom<=current["height"]):
            raise HTTPException(409,"UI Automation target outside the selected window")
        if not target.is_enabled() or not target.is_visible():
            raise HTTPException(409,"UI Automation target disabled or invisible")
        # Click this freshly resolved control, never an LLM-invented x/y.
        target.click_input()
    except HTTPException:
        raise
    except Exception as exc:
        # A click could have occurred before an automation error: never auto retry.
        raise HTTPException(502,"UIA interaction uncertain; manual review before retry") from exc
    return {"clicked":True,"hwnd":hwnd,"automation_id":automation_id,
            "control_type":kind,"label":label,"verified_effect":False,
            "detail":"UIA target clicked; application outcome not yet verified"}


def execute(tool: str,args: dict):
    """All native actions are invoked from the authenticated Runner process."""
    _require_windows()
    if not isinstance(args,dict):
        raise HTTPException(422,"Args must be an object")
    if tool=="windows_list":
        return {"windows":_enumerate(),"allowlist":sorted(_allowlist())}
    if tool=="windows_desktop_screenshot":
        if args:
            raise HTTPException(422,"Desktop screenshot accepts no parameters")
        return _desktop_capture()
    if tool in ("windows_desktop_click","windows_desktop_drag","windows_desktop_scroll"):
        # Absolute desktop control is a separate, stronger capability than
        # in-window control: it needs the owner's full-screen consent flag
        # AND (enforced in computer_agent) a human-approved step.
        _require_desktop_consent()
        metrics=_virtual_screen_metrics()
        u=_user32()
        if tool=="windows_desktop_scroll":
            x,y=_desktop_point(args,metrics)
            amount=args.get("steps",3)
            if type(amount) is not int or not -8<=amount<=8 or amount==0:
                raise HTTPException(422,"Scroll steps must be -8..8 excluding zero")
            if not u.SetCursorPos(x,y):
                raise HTTPException(502,"Unable to move mouse")
            u.mouse_event(MOUSE_WHEEL,0,0,amount*120,0)
            return {"scrolled":True,"steps":amount,"x":x,"y":y,"scope":"desktop"}
        x,y=_desktop_point(args,metrics)
        if tool=="windows_desktop_drag":
            ex,ey=_desktop_point({"x":args.get("to_x"),"y":args.get("to_y")},metrics)
            if (x,y)==(ex,ey):
                raise HTTPException(422,"Drag start and end must differ")
            _drag_absolute(u,x,y,ex,ey)
            return {"dragged":True,"from":{"x":x,"y":y},"to":{"x":ex,"y":ey},"scope":"desktop"}
        button=args.get("button","left")
        if not u.SetCursorPos(x,y):
            raise HTTPException(502,"Unable to move mouse")
        _mouse_button(u,button,double=args.get("double") is True)
        return {"clicked":True,"button":button,"double":args.get("double") is True,
                "x":x,"y":y,"scope":"desktop"}
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
                    meta=child.element_info
                    kind=str(meta.control_type or "")[:70]
                    # Never inspect values or read text from editable/document/password fields.
                    secret=kind.lower() in ("edit","document","password") or bool(
                        getattr(meta,"is_password",False))
                    name="" if secret else str(child.window_text() or "")[:100]
                    item={"type":kind,"label":name,
                          "automation_id":str(getattr(meta,"automation_id","") or "")[:100]}
                    rect=getattr(meta,"rectangle",None)
                    if rect is not None:
                        # Return coordinates in the SAME top-level HWND coordinate
                        # space as windows_click. Discard off-window UIA rectangles.
                        left=int(rect.left)-i["x"]
                        top=int(rect.top)-i["y"]
                        right=int(rect.right)-i["x"]
                        bottom=int(rect.bottom)-i["y"]
                        if 0<=left<right<=i["width"] and 0<=top<bottom<=i["height"]:
                            item["center"]={"x":(left+right)//2,"y":(top+bottom)//2}
                    items.append(item)
                except (AttributeError,TypeError,ValueError,OverflowError):
                    continue
            return {"window":i,"controls":items}
        except Exception:
            return {"window":i,"controls":[],"note":"UI Automation controls unavailable"}
    if tool=="windows_screenshot":
        return _snap(hwnd)
    if tool=="windows_activate":
        i=_focus(hwnd)
        return {"focused":True,"window":i}
    if tool=="windows_click_element":
        return _click_uia_control(hwnd,args)
    if tool=="windows_click":
        i=_foreground_or_refocus(hwnd)
        x,y=_coordinates(i,args)
        u=_user32()
        if not u.SetCursorPos(x,y):
            raise HTTPException(502,"Unable to move mouse")
        u.mouse_event(MOUSE_LEFT_DOWN,0,0,0,0)
        u.mouse_event(MOUSE_LEFT_UP,0,0,0,0)
        return {"clicked":True,"hwnd":hwnd,"x":args["x"],"y":args["y"]}
    if tool in ("windows_right_click", "windows_double_click"):
        i=_foreground_or_refocus(hwnd)
        x,y=_coordinates(i,args)
        u=_user32()
        if not u.SetCursorPos(x,y):
            raise HTTPException(502, "Unable to position mouse")
        if tool=="windows_right_click":
            u.mouse_event(MOUSE_RIGHT_DOWN,0,0,0,0)
            u.mouse_event(MOUSE_RIGHT_UP,0,0,0,0)
        else:
            for number in range(2):
                if number:time.sleep(0.075)
                u.mouse_event(MOUSE_LEFT_DOWN,0,0,0,0)
                u.mouse_event(MOUSE_LEFT_UP,0,0,0,0)
        return {"action":"right" if tool=="windows_right_click" else "double",
                "hwnd":hwnd,"x":args["x"],"y":args["y"]}
    if tool=="windows_drag":
        i=_foreground_or_refocus(hwnd)
        sx,sy,ex,ey=_drag_points(i,args)
        _drag_absolute(_user32(),sx,sy,ex,ey)
        return {"dragged":True,"hwnd":hwnd,
                "from":{"x":args["x"],"y":args["y"]},
                "to":{"x":args["to_x"],"y":args["to_y"]}}
    if tool=="windows_hotkey":
        _foreground_or_refocus(hwnd)
        combo=_validate_hotkey(args.get("keys"))
        _press_hotkey(combo)
        return {"pressed":combo,"hwnd":hwnd}
    if tool=="windows_type":
        _foreground_or_refocus(hwnd)
        text=args.get("text")
        if not isinstance(text,str) or not text or len(text)>MAX_TEXT or any(
            ord(ch)<32 and ch not in ("\n","\t") for ch in text):
            raise HTTPException(422,"Text must be <=750 printable characters")
        _type_unicode(text)
        # Do not echo typed text; it could be confidential.
        return {"typed":True,"characters":len(text),"hwnd":hwnd}
    if tool=="windows_scroll":
        i=_foreground_or_refocus(hwnd)
        amount=args.get("steps",3)
        if type(amount) is not int or not -8<=amount<=8 or amount==0:
            raise HTTPException(422,"Scroll steps must be -8..8 excluding zero")
        u=_user32()
        u.SetCursorPos(i["x"]+i["width"]//2,i["y"]+i["height"]//2)
        u.mouse_event(MOUSE_WHEEL,0,0,amount*120,0)
        return {"scrolled":True,"steps":amount,"hwnd":hwnd}
    raise HTTPException(422,"Unsupported Windows tool")
