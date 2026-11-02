"""Actual Chrome smoke for the 16-role Work Order -> Computer Pilot UI.

Uses a disposable SQLite database and NO API key / external provider calls.
The Chrome browser clicks real controls and the FastAPI app queues actual DB runs.
A queued Computer Run is NOT treated as completed work.
"""
from __future__ import annotations

import os
from pathlib import Path
import socket
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request

from playwright.sync_api import sync_playwright

ROOT=Path(__file__).resolve().parents[1]
SCREENSHOT=ROOT/"agent-work-computer-browser-smoke.png"

def free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1",0))
        return sock.getsockname()[1]

def main() -> None:
    port=free_port()
    base=f"http://127.0.0.1:{port}"
    with tempfile.TemporaryDirectory(prefix="lhm-agent-work-chrome-") as tmp:
        env=os.environ.copy()
        env.update({
            "LHM_DB_PATH":str(Path(tmp)/"work.sqlite3"),
            "LHM_ADMIN_TOKEN":"",
            "LHM_OPENROUTER_FREE_ONLY":"1",
            "OPENROUTER_API_KEY":"",
            "DEEPSEEK_API_KEY":"",
            "LHM_TOOLS_WORKSPACE":"demo",
        })
        with open(Path(tmp)/"server.log","w",encoding="utf-8") as log:
            process=subprocess.Popen(
                [sys.executable,"-m","uvicorn","app:app",
                 "--host","127.0.0.1","--port",str(port)],
                cwd=ROOT,env=env,stdout=log,stderr=subprocess.STDOUT)
            try:
                for _ in range(100):
                    if process.poll() is not None:
                        raise RuntimeError("Server exited before browser acceptance")
                    try:
                        with urllib.request.urlopen(base+"/health",timeout=1) as resp:
                            if resp.status==200:break
                    except (urllib.error.URLError,OSError):
                        time.sleep(.25)
                else:
                    raise TimeoutError("Agent Work server did not boot")
                with sync_playwright() as p:
                    browser=p.chromium.launch(channel="chrome",headless=True,
                        args=["--disable-dev-shm-usage","--no-sandbox"])
                    page=browser.new_page(viewport={"width":1440,"height":950})
                    errors=[]
                    page.on("pageerror",lambda e:errors.append(str(e)))
                    page.on("dialog",lambda dialog:dialog.accept())
                    try:
                        response=page.goto(base+"/agent-work",
                                           wait_until="domcontentloaded",timeout=30000)
                        assert response and response.status==200
                        page.wait_for_selector("#role option",timeout=15000)
                        assert page.locator("#role option").count()==16
                        page.locator("#role").select_option("director")
                        page.locator("#objective").fill(
                            "Phân tích kế hoạch chất lượng cửa hàng giả định, không xuất bản")
                        page.locator("#submit").click()
                        page.wait_for_function(
                            "() => document.querySelector('#report').textContent.includes('Trạng thái Worker: queued')",
                            timeout=15000)
                        before=page.locator("#computerStatus").inner_text()
                        assert "chưa tạo" in before, before
                        page.locator("#computerStart").click()
                        page.wait_for_function(
                            "() => document.querySelector('#computerStatus').textContent.includes('Computer Run: ') && !document.querySelector('#computerStatus').textContent.includes('chưa tạo')",
                            timeout=15000)
                        status=page.locator("#computerStatus").inner_text()
                        assert "Computer: queued" in status,status
                        assert "CHƯA" in status,status
                        state=page.evaluate("""async () => {
                            const list=await (await fetch('/workspaces/demo/computer/runs')).json();
                            return list;
                        }""")
                        runs=state.get("runs",[])
                        assert len(runs)==1,state
                        rid=runs[0]["id"]
                        # Repeating the explicit button is deduplicated on the server.
                        page.locator("#computerStart").click()
                        page.wait_for_timeout(300)
                        state=page.evaluate("""async () =>
                          (await (await fetch('/workspaces/demo/computer/runs')).json())""")
                        assert len(state.get("runs",[]))==1
                        page.locator("#computerRefresh").click()
                        page.wait_for_function(
                            "() => document.querySelector('#computerStatus').textContent.includes('Computer: queued')",
                            timeout=15000)
                        # Fleet click-through selects one of 16 actual Work Orders.
                        page.locator("#fleetStart").click()
                        page.wait_for_function(
                            "() => document.querySelectorAll('#fleetOrders button').length===16",
                            timeout=20000)
                        fleet=page.evaluate("""async () => {
                            const data=await (await fetch('/workspaces/demo/agent-fleets')).json();
                            return data.fleets[0];
                        }""")
                        target=fleet["orders"][0]["order_id"]
                        assert target and target!=rid
                        page.locator("#fleetOrders button").first.click()
                        page.wait_for_function(
                            "(id) => document.querySelector('#computerStatus').textContent.includes('Work Order: '+id)",
                            arg=target,timeout=15000)
                        assert "Computer Run: chưa tạo" in page.locator("#computerStatus").inner_text()
                        assert "Lỗi" not in page.locator("#computerStatus").inner_text()
                        assert not errors, "Chrome JS errors: "+str(errors)
                        page.screenshot(path=str(SCREENSHOT),full_page=True)
                        print("AGENT WORK COMPUTER CHROME PASS: real UI controls, 16 roles, "+
                              "1 queued Computer Run, server-side dedup, fleet drill-down, "+
                              "no AI key/provider and no external side effects")
                    except BaseException:
                        page.screenshot(path=str(SCREENSHOT),full_page=True)
                        raise
                    finally:
                        browser.close()
            finally:
                process.terminate()
                try:process.wait(timeout=7)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait(timeout=5)

if __name__=="__main__":
    main()
