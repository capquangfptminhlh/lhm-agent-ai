"""Real-browser WebGL smoke test of LHM's native Three.js office.

CI uses the preinstalled Google Chrome and downloads no mock source code.
It checks that an actual canvas is rendered, UI roster comes from SQLite,
and the end-user task dialog can open. No AI or social API calls are made.
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


def free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1",0))
        return sock.getsockname()[1]


def smoke() -> None:
    from playwright.sync_api import sync_playwright
    repo = Path(__file__).resolve().parents[1]
    port = free_port()
    url = f"http://127.0.0.1:{port}"
    out=repo / "office3d-browser-smoke.png"
    with tempfile.TemporaryDirectory(prefix="lhm_office3d_browser_") as data:
        env = os.environ.copy()
        env.update({
            "LHM_DB_PATH":str(Path(data)/"browser.sqlite3"),
            "LHM_ADMIN_TOKEN":"",
            "DEEPSEEK_API_KEY":"",
            "OPENROUTER_API_KEY":"",
            "LHM_TOOLS_WORKSPACE":"demo",
        })
        with open(Path(data)/"server.log","w",encoding="utf-8") as output:
            proc=subprocess.Popen([sys.executable,"-m","uvicorn","app:app",
                 "--host","127.0.0.1","--port",str(port)],
                 cwd=repo,env=env,stdout=output,stderr=subprocess.STDOUT)
            try:
                for attempt in range(120):
                    if proc.poll() is not None:
                        raise RuntimeError("FastAPI server exited before browser smoke")
                    try:
                        with urllib.request.urlopen(url+"/health",timeout=1) as response:
                            if response.status==200:
                                break
                    except (urllib.error.URLError, OSError):
                        time.sleep(.25)
                else:
                    raise TimeoutError("FastAPI did not start in 30 seconds")

                with sync_playwright() as p:
                    browser=p.chromium.launch(channel="chrome",headless=True,args=[
                        "--enable-webgl","--enable-unsafe-swiftshader",
                        "--use-gl=angle","--use-angle=swiftshader",
                        "--disable-dev-shm-usage","--no-sandbox"
                    ])
                    page=browser.new_page(viewport={"width":1500,"height":950},device_scale_factor=1)
                    errors=[]
                    page.on("pageerror",lambda err: errors.append(str(err)))
                    try:
                        response=page.goto(url+"/office3d",wait_until="domcontentloaded",timeout=45000)
                        assert response and response.status==200, "3D HTML route unavailable"
                        page.wait_for_selector("#roster .roster-btn",timeout=30000)
                        page.wait_for_function(
                            "() => document.querySelectorAll('#roster .roster-btn').length === 16",
                            timeout=30000
                        )
                        page.wait_for_selector("#canvasHost canvas",timeout=60000)
                        page.wait_for_function(
                            "() => document.getElementById('sceneLoading').hidden",
                            timeout=12000
                        )
                        assert not page.locator("#sceneFallback").is_visible(), "WebGL/CDN fallback was triggered"
                        metrics=page.evaluate("""() => ({
                          canvas:document.querySelectorAll('#canvasHost canvas').length,
                          labels:document.querySelectorAll('#actorLabels .actor-label').length,
                          width:document.querySelector('#canvasHost canvas').width,
                          height:document.querySelector('#canvasHost canvas').height
                        })""")
                        assert metrics["canvas"] == 1 and metrics["labels"] == 16, metrics
                        assert metrics["width"]>200 and metrics["height"]>200, metrics
                        assert not errors, "Browser JavaScript errors: "+str(errors)
                        page.locator('#roster button[data-role="content"]').click()
                        page.locator("#assignAgent").click()
                        assert page.locator("#taskDialog").evaluate("(el)=>el.open")
                        page.locator("#cancelDialog").click()
                        page.screenshot(path=str(out),full_page=True)
                        print("3D browser smoke PASS",metrics,"screenshot",out,flush=True)
                    except BaseException:
                        page.screenshot(path=str(out),full_page=True)
                        print("3D browser smoke FAIL; screenshot:",out,flush=True)
                        if errors: print("JS errors:",errors,flush=True)
                        raise
                    finally:
                        browser.close()
            finally:
                proc.terminate()
                try:
                    proc.wait(timeout=8)
                except subprocess.TimeoutExpired:
                    proc.kill()
                    proc.wait(timeout=5)


if __name__ == "__main__":
    smoke()
