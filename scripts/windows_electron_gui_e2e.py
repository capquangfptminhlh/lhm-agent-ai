"""Packaged Electron GUI acceptance on Windows CI using Chrome DevTools Protocol.

Launches win-unpacked/LHM Robot Desktop.exe, with bundled PyInstaller runtime.
Inspects actual Chromium renderer DOM, navigates key screens, checks for blank
pages and captures a screenshot. No OS click or real provider API is claimed.
"""
from __future__ import annotations
import os
import subprocess
import tempfile
import time
from pathlib import Path
from urllib.request import urlopen
from urllib.error import URLError

from playwright.sync_api import sync_playwright

ROOT = Path(__file__).resolve().parents[1]
DESKTOP = ROOT / "desktop" / "dist" / "win-unpacked" / "LHM Robot Desktop.exe"
PORT = 19283
CDP = f"http://127.0.0.1:{PORT}"
DEST = ROOT / "windows-electron-gui-smoke.png"


def main():
    assert os.name == "nt", "Electron GUI acceptance requires Windows"
    assert DESKTOP.exists(), f"Real packaged desktop app missing: {DESKTOP}"
    with tempfile.TemporaryDirectory(prefix="lhm-electron-real-gui-", ignore_cleanup_errors=True) as folder:
        profile = Path(folder)
        env = os.environ.copy()
        env["ELECTRON_DISABLE_SECURITY_WARNINGS"] = "1"
        # Point the app's writable Windows Roaming profile to an isolated CI dir.
        env["APPDATA"] = str(profile / "AppData" / "Roaming")
        env["LOCALAPPDATA"] = str(profile / "AppData" / "Local")
        Path(env["APPDATA"]).mkdir(parents=True, exist_ok=True)
        Path(env["LOCALAPPDATA"]).mkdir(parents=True, exist_ok=True)
        env["OPENROUTER_API_KEY"] = ""
        env["DEEPSEEK_API_KEY"] = ""
        env.pop("ELECTRON_RUN_AS_NODE", None)
        with (profile / "electron.log").open("wb") as logfile:
            proc = subprocess.Popen([
                str(DESKTOP),
                f"--remote-debugging-port={PORT}",
                f"--user-data-dir={profile / 'user-data'}",
            ], cwd=str(profile), stdout=logfile,
                stderr=subprocess.STDOUT, env=env)
            try:
                deadline = time.monotonic() + 105
                last = "CDP not started"
                while time.monotonic() < deadline:
                    if proc.poll() is not None:
                        raise RuntimeError(f"Packaged Electron exited: {proc.returncode}")
                    try:
                        with urlopen(CDP + "/json/version", timeout=2) as response:
                            if response.status == 200:
                                break
                    except (URLError, TimeoutError, OSError) as exc:
                        last = str(exc)
                    time.sleep(0.7)
                else:
                    raise RuntimeError("Electron Chromium CDP not ready: " + last)

                with sync_playwright() as playwright:
                    browser = playwright.chromium.connect_over_cdp(CDP, timeout=15000)
                    contexts = browser.contexts
                    assert contexts, "Electron has no Chromium browser context"
                    context = contexts[0]
                    # Playwright sync mode processes CDP events while executing its
                    # own wait methods. time.sleep() prevents navigation events
                    # from updating context.pages/page.url and causes false FAIL.
                    assert context.pages, "No Electron Chromium page available"
                    # First-run now creates TWO native Chromium windows.
                    # CDP enumeration order is undefined: choose the main
                    # splash/localhost page, NEVER the settings.html window.
                    print("Electron window URLs: "+
                          repr([p.url for p in context.pages]),flush=True)
                    main_candidates=[
                        p for p in context.pages
                        if "splash.html" in p.url or
                           p.url.startswith("http://127.0.0.1:7770/")
                    ]
                    assert len(main_candidates)==1, (
                        "Cannot identify main Electron window; URLs="+
                        repr([p.url for p in context.pages]))
                    page=main_candidates[0]
                    page.wait_for_url("http://127.0.0.1:7770/**",
                                      wait_until="domcontentloaded", timeout=80000)
                    assert page.url.startswith("http://127.0.0.1:7770/"), (
                        "Packaged Electron did not reach local backend: " + page.url)
                    console_errors = []
                    page.on("pageerror", lambda err: console_errors.append(str(err)[:180]))
                    paths = ["/office3d", "/team-chat", "/computer", "/workflow-builder",
                             "/agent-tools", "/dashboard"]
                    timings = []
                    for path in paths:
                        began = time.monotonic()
                        page.goto("http://127.0.0.1:7770" + path,
                                  wait_until="domcontentloaded", timeout=20000)
                        page.locator("body").wait_for(state="visible", timeout=12000)
                        body = page.locator("body").inner_text(timeout=8000)
                        assert len(body.strip()) > 80, f"Blank/empty Electron page: {path}"
                        assert page.url.startswith("http://127.0.0.1:7770" + path)
                        timings.append((path, round((time.monotonic()-began)*1000)))
                        if path == "/office3d":
                            page.screenshot(path=str(DEST), full_page=False, timeout=20000)
                    # New-install acceptance: real Electron opens a local settings
                    # window without requiring user to know .exe or website lists.
                    settings=[p for p in context.pages if "settings.html" in p.url]
                    assert settings, "First-run API key setup did not open"
                    setup=settings[0]
                    setup.locator("#quickStart").wait_for(state="visible",timeout=12000)
                    setup.locator("#refreshReadiness").click()
                    setup.locator("#readiness").filter(has_text="Bộ não / Backend").wait_for(
                        state="visible",timeout=12000)
                    assert "Chưa nhập API key" in setup.locator("#readiness").inner_text(), (
                        "First-run readiness must flag missing OpenRouter credential")
                    assert setup.locator("#advanced").get_attribute("open") is None, (
                        "Advanced settings should be collapsed")
                    print("REAL ELECTRON FIRST-RUN ONBOARDING PASS: "
                          "API key prompt, diagnostic panel, advanced collapsed",flush=True)
                    assert not console_errors, f"Electron runtime JS exceptions: {console_errors[:3]}"
                    print("REAL PACKAGED ELECTRON GUI PASS: "
                          + ", ".join(f"{p}={ms}ms" for p,ms in timings), flush=True)
                    browser.close()
            except BaseException:
                logfile.flush()
                # Electron's app.getName() can resolve to the package's slug,
                # not builder's display name. Inspect all local app logs.
                for base in [profile / "AppData" / "Roaming",
                             Path(os.environ.get("APPDATA", ""))]:
                    if base.is_dir():
                        for path in sorted(base.glob("*/logs/*.log")):
                            print(f"--- {path} tail ---\n"
                                  + path.read_text(encoding="utf-8", errors="replace")[-3200:],
                                  flush=True)
                for path in sorted(profile.rglob("logs/*.log"))[:12]:
                    print(f"--- app userData: {path} ---\\n"
                          + path.read_text(encoding="utf-8", errors="replace")[-3200:],
                          flush=True)
                for port in (7770, 7782):
                    import socket
                    with socket.socket() as sock:
                        sock.settimeout(1)
                        print(f"Port {port} connect_ex={sock.connect_ex(('127.0.0.1', port))}",
                              flush=True)
                print(subprocess.run(["tasklist", "/FI", "IMAGENAME eq lhm-runtime.exe"],
                                     text=True, capture_output=True, timeout=10).stdout[-3000:],
                      flush=True)
                print("--- Electron stdout/stderr tail ---\n"
                      + (profile / "electron.log").read_text(encoding="utf-8",
                                                             errors="replace")[-3500:],
                      flush=True)
                raise
            finally:
                subprocess.run(["taskkill", "/PID", str(proc.pid), "/T", "/F"],
                               capture_output=True, timeout=20, check=False)
                try:
                    proc.wait(timeout=15)
                except subprocess.TimeoutExpired:
                    proc.kill()


if __name__ == "__main__":
    main()
