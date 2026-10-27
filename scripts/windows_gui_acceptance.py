"""Real-Windows GUI acceptance for the LHM computer-control tools.

Runs ONLY on a Windows runner with an interactive desktop (GitHub
windows-latest qualifies). It starts the source native runner with a
test-only configuration, then drives REAL applications through the
runner's authenticated HTTP tool API — the same surface the Agent uses:

  1. Notepad: activate, click into the editor, type a unique marker,
     Ctrl+S, complete the Save dialog by typing a path, press Enter.
     Verified by reading the saved file from disk.
  2. Alt+Tab between Notepad and Explorer; verified by asking Win32
     which window is foreground before/after.
  3. Drag-select the typed line in Notepad, Ctrl+C, End, Enter, Ctrl+V,
     Ctrl+S; verified by the marker appearing twice in the saved file.
  4. Desktop coordinates: full-desktop screenshot (consent env on) and
     an absolute-coordinate click inside the known Notepad window rect.

vision_locate is intentionally NOT exercised here: this workflow has
no OpenRouter key. Results print one line per task; exit code is
non-zero if any task FAILs. Nothing here is mocked.
"""
from __future__ import annotations

import ctypes
import json
import os
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

ROOT = Path(__file__).resolve().parents[1]
PORT = 7791
BASE = f"http://127.0.0.1:{PORT}"
TOKEN = "gui-acceptance-token"
MARKER = "LHM-GH-ACCEPT-2027"

RESULTS: list[tuple[str, bool, str]] = []


def report(name: str, ok: bool, detail: str = "") -> None:
    RESULTS.append((name, ok, detail))
    print(f"[{'PASS' if ok else 'FAIL'}] {name} {detail}", flush=True)


def call(tool: str, args: dict, timeout: float = 20) -> dict:
    body = json.dumps({"workspace_id": "demo", "role": "research",
                       "tool": tool, "args": args}).encode()
    req = Request(BASE + "/v1/tool", data=body, method="POST",
                  headers={"Authorization": "Bearer " + TOKEN,
                           "Content-Type": "application/json"})
    try:
        with urlopen(req, timeout=timeout) as resp:
            return json.loads(resp.read().decode("utf-8", "replace"))
    except HTTPError as exc:
        detail = exc.read().decode("utf-8", "replace")[:300]
        raise RuntimeError(f"{tool} -> HTTP {exc.code}: {detail}") from exc


def wait_for(predicate, seconds: float, label: str):
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        value = predicate()
        if value:
            return value
        time.sleep(0.5)
    raise TimeoutError(f"Timed out waiting for {label}")


def find_window(process: str, title_part: str = "") -> dict | None:
    try:
        data = call("windows_list", {})
    except Exception:
        return None
    for win in data.get("windows", []):
        if win.get("process") == process and title_part.lower() in (win.get("title") or "").lower():
            return win
    return None


def foreground_hwnd() -> int:
    return ctypes.windll.user32.GetForegroundWindow()


def main() -> int:
    if os.name != "nt":
        print("SKIP: windows_gui_acceptance requires Windows", flush=True)
        return 0
    workdir = Path(tempfile.mkdtemp(prefix="lhm-gui-accept-"))
    target = workdir / "lhm-gh-test.txt"
    env = os.environ.copy()
    env.update({
        "LHM_COMPUTER_RUNNER_TOKEN": TOKEN,
        "LHM_COMPUTER_HOME": str(workdir / "agent-home"),
        "LHM_DESKTOP_NATIVE": "1",
        "LHM_DESKTOP_ALLOWED_APPS": "notepad.exe,explorer.exe",
        "LHM_DESKTOP_FULL_SCREEN_ENABLED": "1",
        "LHM_COMPUTER_BROWSER_CHANNEL": "",
    })
    runner_log = open(workdir / "runner.log", "wb")
    runner = subprocess.Popen(
        [sys.executable, "-m", "uvicorn", "computer_executor:runner",
         "--host", "127.0.0.1", "--port", str(PORT)],
        cwd=str(ROOT), env=env, stdout=runner_log, stderr=subprocess.STDOUT)
    apps: list[subprocess.Popen] = []
    try:
        def health() -> bool:
            try:
                req = Request(BASE + "/health",
                              headers={"Authorization": "Bearer " + TOKEN})
                with urlopen(req, timeout=2) as resp:
                    return resp.status == 200
            except (URLError, OSError):
                return False
        wait_for(health, 45, "native runner health")

        def activate(hwnd: int) -> None:
            for attempt in range(3):
                try:
                    call("windows_activate", {"hwnd": hwnd})
                    return
                except RuntimeError:
                    if attempt == 2:
                        raise
                    time.sleep(1.5)

        # --- Task 1: type into Notepad and save through the real dialog.
        apps.append(subprocess.Popen(["notepad.exe"]))
        notepad = wait_for(lambda: find_window("notepad.exe"), 30, "Notepad window")
        hwnd = notepad["hwnd"]
        activate(hwnd)
        call("windows_click", {"hwnd": hwnd, "x": 200, "y": 130})
        call("windows_type", {"hwnd": hwnd, "text": MARKER})
        call("windows_hotkey", {"hwnd": hwnd, "keys": "CTRL+S"})
        dialog = wait_for(
            lambda: find_window("notepad.exe", "Save") or find_window("notepad.exe", "Lưu"),
            20, "Save dialog")
        activate(dialog["hwnd"])
        call("windows_type", {"hwnd": dialog["hwnd"], "text": str(target)})
        call("windows_hotkey", {"hwnd": dialog["hwnd"], "keys": "ENTER"})
        wait_for(lambda: target.is_file(), 20, "saved file on disk")
        content = target.read_text(encoding="utf-8", errors="replace")
        report("T1 type + save via real Notepad GUI", MARKER in content,
               f"file={target.name} bytes={target.stat().st_size}")
        notepad = find_window("notepad.exe") or notepad
        hwnd = notepad["hwnd"]

        # --- Task 2: Alt+Tab to Explorer and back (foreground verified).
        apps.append(subprocess.Popen(["explorer.exe", str(workdir)]))
        explorer = wait_for(lambda: find_window("explorer.exe"), 30, "Explorer window")
        activate(hwnd)
        before = wait_for(lambda: foreground_hwnd() == hwnd, 10, "Notepad foreground")
        call("windows_hotkey", {"hwnd": hwnd, "keys": "ALT+TAB"})
        changed = wait_for(lambda: foreground_hwnd() != hwnd, 10,
                           "foreground change after Alt+Tab")
        report("T2 ALT+TAB switches foreground window", bool(before and changed),
               f"explorer_hwnd={explorer['hwnd']}")

        # --- Task 3: drag-select the line, copy, paste a duplicate, save.
        activate(hwnd)
        time.sleep(0.4)
        call("windows_drag", {"hwnd": hwnd, "x": 40, "y": 100, "to_x": 260, "to_y": 100})
        call("windows_hotkey", {"hwnd": hwnd, "keys": "CTRL+C"})
        call("windows_hotkey", {"hwnd": hwnd, "keys": "END"})
        call("windows_hotkey", {"hwnd": hwnd, "keys": "ENTER"})
        call("windows_hotkey", {"hwnd": hwnd, "keys": "CTRL+V"})
        call("windows_hotkey", {"hwnd": hwnd, "keys": "CTRL+S"})
        def duplicated() -> bool:
            try:
                return target.read_text(encoding="utf-8", errors="replace").count(MARKER) >= 2
            except OSError:
                return False
        ok = False
        try:
            ok = bool(wait_for(duplicated, 15, "duplicated marker in file"))
        except TimeoutError:
            ok = False
        report("T3 drag-select + copy/paste duplicates the line", ok)

        # --- Task 4: desktop capture + absolute-coordinate click.
        shot = call("windows_desktop_screenshot", {})
        png = shot.get("png_base64") or ""
        rect = (find_window("notepad.exe") or notepad)
        click = call("windows_desktop_click",
                     {"x": int(rect["x"]) + 120, "y": int(rect["y"]) + 130})
        report("T4 desktop screenshot + absolute click",
               len(png) > 10_000 and click.get("clicked") is True,
               f"png_b64={len(png)}")
    except Exception as exc:
        report("SUITE", False, f"{type(exc).__name__}: {exc}")
        # Surface the cause as a check annotation (job logs need sign-in;
        # annotations are how this failure stays diagnosable from outside).
        detail = f"{type(exc).__name__}: {exc}".replace("\n", " ")[:400]
        print(f"::error ::GUI-ACCEPTANCE {detail}", flush=True)
        try:
            runner_log.flush()
            tail = (workdir / "runner.log").read_text(
                encoding="utf-8", errors="replace")[-2000:]
            print("--- runner log tail ---\n" + tail, flush=True)
        except OSError:
            pass
    finally:
        for proc in apps:
            subprocess.run(["taskkill", "/PID", str(proc.pid), "/T", "/F"],
                           capture_output=True, timeout=10, check=False)
        subprocess.run(["taskkill", "/IM", "notepad.exe", "/F"],
                       capture_output=True, timeout=10, check=False)
        if runner.poll() is None:
            runner.terminate()
            try:
                runner.wait(timeout=8)
            except subprocess.TimeoutExpired:
                runner.kill()
        runner_log.close()
    failed = [name for name, ok, _ in RESULTS if not ok]
    print(f"GUI ACCEPTANCE: {len(RESULTS) - len(failed)}/{len(RESULTS)} PASS", flush=True)
    for name, ok, detail in RESULTS:
        if not ok:
            safe = f"{name} :: {detail}".replace("\n", " ")[:400]
            print(f"::error ::GUI-TASK-FAIL {safe}", flush=True)
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
