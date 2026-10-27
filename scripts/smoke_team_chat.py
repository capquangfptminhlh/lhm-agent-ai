"""Browser smoke of the real Agent-to-Agent Team Chat UI and backend.

Uses a temporary SQLite database and no paid LLM credentials.
It validates session creation, actual queued task, and a human note
through Chrome/Playwright, then saves a screenshot for visual QA.
"""
from __future__ import annotations

import os
import socket
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request
from pathlib import Path

from playwright.sync_api import sync_playwright

REPO=Path(__file__).resolve().parents[1]
SCREENSHOT=REPO/"team-chat-browser-smoke.png"

def available_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1",0))
        return s.getsockname()[1]

def smoke():
    port=available_port()
    url=f"http://127.0.0.1:{port}"
    with tempfile.TemporaryDirectory(prefix="lhm-team-browser-") as tmp:
        env=os.environ.copy()
        env.update({
          "LHM_DB_PATH":str(Path(tmp)/"team.sqlite3"),
          "LHM_ADMIN_TOKEN":"",
          "OPENROUTER_API_KEY":"",
          "DEEPSEEK_API_KEY":"",
        })
        with open(Path(tmp)/"server.log","w",encoding="utf-8") as log:
            proc=subprocess.Popen([sys.executable,"-m","uvicorn","app:app",
                "--host","127.0.0.1","--port",str(port)],
                cwd=REPO,env=env,stdout=log,stderr=subprocess.STDOUT)
            try:
                for i in range(100):
                    if proc.poll() is not None:
                        raise RuntimeError("Server died before Team Chat browser smoke")
                    try:
                        with urllib.request.urlopen(url+"/health",timeout=1) as response:
                            if response.status==200: break
                    except (urllib.error.URLError,OSError):
                        time.sleep(.25)
                else: raise TimeoutError("Server did not start")
                with sync_playwright() as p:
                    browser=p.chromium.launch(channel="chrome",headless=True,
                      args=["--disable-dev-shm-usage","--no-sandbox"])
                    page=browser.new_page(viewport={"width":1440,"height":950},device_scale_factor=1)
                    js_errors=[]
                    page.on("pageerror",lambda exc:js_errors.append(str(exc)))
                    try:
                        res=page.goto(url+"/team-chat",wait_until="domcontentloaded",timeout=30000)
                        assert res and res.status==200, "Team Chat HTML unavailable"
                        page.wait_for_selector("#roles input",timeout=20000)
                        assert page.locator("#roles input:checked").count()>=2
                        page.locator("#objective").fill(
                           "Director phân công Research xác minh thị trường, Content soạn bài rồi Quality kiểm tra.")
                        page.locator("#start").click()
                        page.wait_for_function(
                            "() => document.querySelectorAll('#sessions .session').length===1",
                            timeout=25000)
                        page.wait_for_function(
                            "() => document.querySelectorAll('#timeline .msg').length===1",
                            timeout=15000)
                        assert "Đã giao nhiệm vụ" in page.locator("#feedback").inner_text()
                        assert "queued" in page.locator("#active").inner_text() or (
                            "đang xử lý" in page.locator("#active").inner_text().lower())
                        page.locator("#humanNote").fill("Tất cả nội dung cần có nguồn kiểm chứng.")
                        page.locator("#sendNote").click()
                        page.wait_for_function(
                            "() => document.querySelectorAll('#timeline .msg').length===2",
                            timeout=20000)
                        assert not js_errors, "Browser errors: "+str(js_errors)
                        page.screenshot(path=str(SCREENSHOT),full_page=True)
                        print("Team Chat Chrome smoke PASS: session, queued task, human note, live UI")
                    except BaseException:
                        page.screenshot(path=str(SCREENSHOT),full_page=True)
                        if js_errors:print("JavaScript errors",js_errors)
                        raise
                    finally:browser.close()
            finally:
                proc.terminate()
                try:proc.wait(timeout=7)
                except subprocess.TimeoutExpired:
                    proc.kill();proc.wait(timeout=5)

if __name__=="__main__":
    smoke()
