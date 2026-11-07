"""Hosted-Windows P4/T07 precursor: real *headed* Edge Ctrl+F via Computer Agent.

A disposable Edge profile opens a local offline HTML fixture. Real native
Win32 HWND + Ctrl+F + Unicode SendInput + Escape operate through the
authenticated Computer Runner and supervised approvals. Independent
Playwright DOM checks confirm that find-in-page scrolled the target into view.
Only the planner and owner confirmations are scripted. No screenshot, search
text or browser profile is uploaded/stored in the DB or GitHub artifact.

This is NOT the owner's personal Edge, P4 OWNER_WINDOWS_PASS or live Free AI.
"""
from __future__ import annotations

import base64
import hashlib
import os
from pathlib import Path
import sys
import tempfile
import time
from unittest.mock import AsyncMock, patch

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))

TITLE="LHM-P4-HEADED-EDGE-FIND-2026"
NEEDLE="Privacy"
HTML="""<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<title>LHM-P4-HEADED-EDGE-FIND-2026</title>
<style>
body { margin: 0; font-family: Arial, sans-serif; background: #fff; }
header { padding: 36px; font-size: 24px; background: #eef2f9; }
.spacer { height: 2200px; }
#privacy { padding: 28px; background: #e5f4fa; font-size: 30px; }
.footer { height: 1100px; }
</style></head><body>
<header>Offline Windows Edge native find-in-page acceptance</header>
<div class="spacer"></div>
<section id="privacy"><h2 id="target">Privacy</h2>
<p>Unique below-the-fold target — CI fixture, no personal data.</p></section>
<div class="footer"></div></body></html>
"""
TOOLS=("windows_list","windows_activate","windows_hotkey",
       "windows_type","windows_hotkey","windows_screenshot")


def main() -> None:
    if sys.platform!="win32":
        raise SystemExit("Headed Edge native-CTRL+F acceptance requires Windows")
    import httpx
    from fastapi import HTTPException
    from fastapi.testclient import TestClient
    from playwright.sync_api import sync_playwright

    import app
    import computer_agent as agent
    import computer_executor as executor
    import core

    with tempfile.TemporaryDirectory(prefix="lhm-edge-find-private-") as tmp:
        base=Path(tmp)
        document=base/"find-offline.html"
        document.write_text(HTML,encoding="utf-8")
        profile=base/"isolated-ci-edge-profile"
        original_db=core.DATABASE
        core.DATABASE=base/"agent.sqlite3"
        options={
            "LHM_DESKTOP_NATIVE":"1",
            "LHM_DESKTOP_WORKSPACE":"demo",
            "LHM_DESKTOP_ALLOWED_APPS":"msedge.exe",
            "LHM_COMPUTER_RUNNER_TOKEN":"ci-edge-find-only",
            "LHM_COMPUTER_URL":"http://127.0.0.1:7782",
            "LHM_ADMIN_TOKEN":"",
            "LHM_COMPUTER_TRUSTED_ENABLED":"0",
            "LHM_OPENROUTER_FREE_ONLY":"0",
        }
        with patch.dict(os.environ,options):
            executor.PAUSED=False
            try:
                core.setup()
                agent.prepare_db()
                native=TestClient(executor.runner)
                api=TestClient(app.app)
                auth={"Authorization":"Bearer ci-edge-find-only"}

                def direct(tool: str,args: dict | None=None,headers=auth):
                    return native.post("/v1/tool",headers=headers,json={
                        "workspace_id":"demo","role":"research",
                        "tool":tool,"args":args if args is not None else {}
                    })

                unauth=direct("windows_list",headers={})
                assert unauth.status_code==401,unauth.text
                wrong=native.post("/v1/tool",headers=auth,json={
                    "workspace_id":"other","role":"research",
                    "tool":"windows_list","args":{}})
                assert wrong.status_code in (403,423),wrong.text

                before=direct("windows_list")
                assert before.status_code==200,before.text
                old_hwnds={w["hwnd"] for w in before.json()["windows"]}

                with sync_playwright() as playwright:
                    # Do NOT connect to an existing browser/session. This
                    # profile is unique, temporary and fully disposable.
                    context=playwright.chromium.launch_persistent_context(
                        user_data_dir=str(profile),channel="msedge",
                        headless=False,viewport={"width":1070,"height":720},
                        args=["--no-first-run","--no-default-browser-check",
                              "--disable-background-networking",
                              "--disable-component-update"]
                    )
                    try:
                        page=context.new_page()
                        page.goto(document.as_uri(),wait_until="load",timeout=15000)
                        page.bring_to_front()
                        for other in list(context.pages):
                            if other!=page:
                                other.close()
                        assert page.title()==TITLE
                        assert page.evaluate("window.scrollY")==0
                        assert page.locator("#target").count()==1
                        assert page.locator("#target").bounding_box()["y"]>1200

                        created=api.post("/workspaces/demo/computer/runs",json={
                            "role":"research",
                            "objective":"Trên Edge thật, dùng Ctrl+F tìm Privacy ở cuối trang và chụp màn hình",
                            "mode":"supervised","max_steps":12,
                            "idempotency_key":"p4-real-headed-edge-find-20261108",
                        })
                        assert created.status_code==201,created.text
                        rid=created.json()["id"]
                        target_hwnd=None
                        planned=[]

                        async def through_runner(run: dict,tool: str,args: dict):
                            async with httpx.AsyncClient(
                                transport=httpx.ASGITransport(app=executor.runner),
                                base_url="http://ci-runner",
                            ) as client:
                                response=await client.post(
                                    "/v1/tool",headers=auth,
                                    json={"workspace_id":run["workspace_id"],
                                          "role":run["role"],"run_id":run["id"],
                                          "tool":tool,"args":args}
                                )
                            if response.status_code!=200:
                                raise HTTPException(502,
                                    "Headed Edge native Runner rejected "+tool+
                                    ": HTTP "+str(response.status_code))
                            return response.json()

                        async def plan(run: dict,steps: list[dict]):
                            nonlocal target_hwnd
                            n=len(steps)
                            if n==0:
                                action=("windows_list",{})
                            else:
                                if target_hwnd is None:
                                    candidates=[w for w in steps[0]["result"]["windows"]
                                        if w.get("process")=="msedge.exe"
                                        and TITLE in str(w.get("title",""))
                                        and w.get("hwnd") not in old_hwnds]
                                    assert len(candidates)==1,(
                                        "No unique new headed Edge HWND with expected title: "+
                                        repr([(w.get("process"),w.get("title")) for w
                                              in steps[0]["result"]["windows"]]))
                                    target_hwnd=candidates[0]["hwnd"]
                                hwnd=target_hwnd
                                if n==1:
                                    action=("windows_activate",{"hwnd":hwnd})
                                elif n==2:
                                    action=("windows_hotkey",{"hwnd":hwnd,"keys":"CTRL+F"})
                                elif n==3:
                                    action=("windows_type",{"hwnd":hwnd,"text":NEEDLE})
                                elif n==4:
                                    action=("windows_hotkey",{"hwnd":hwnd,"keys":"ESC"})
                                elif n==5:
                                    action=("windows_screenshot",{"hwnd":hwnd})
                                elif n==6:
                                    action=("finish",{
                                        "summary":"Đã gửi Ctrl+F tới Edge thử nghiệm; cần đối chiếu kết quả độc lập.",
                                        "evidence":"Giao diện Windows thật và hậu kiểm DOM tách riêng."
                                    })
                                else:
                                    raise AssertionError("Unbounded CI planner")
                            planned.append(action[0])
                            return action

                        tick=f"/computer/runs/{rid}/tick"
                        approval_count=0
                        with patch.object(agent,"runner_call",new=through_runner), \
                             patch.object(agent,"_next_action",new=plan):
                            for expected in TOOLS:
                                response=api.post(tick)
                                assert response.status_code==200,response.text
                                out=response.json()
                                if out["status"]=="awaiting_approval":
                                    # CI simulates explicit operator clicks, not
                                    # production auto-approval. No native action
                                    # is dispatched prior to this API decision.
                                    sid=out["step_id"]
                                    approve=api.post(
                                        f"/computer/runs/{rid}/steps/{sid}/approve")
                                    assert approve.status_code==200,approve.text
                                    approval_count+=1
                                    response=api.post(tick)
                                    assert response.status_code==200,response.text
                                    out=response.json()
                                assert out.get("last_tool")==expected,(expected,out)
                                assert out["status"]=="queued",(expected,out)

                                if expected=="windows_type":
                                    # Verify real Edge native Find-in-page has
                                    # visibly scrolled DOM; no OCR or screenshot
                                    # of potential private content is needed.
                                    page.wait_for_function(
                                        "window.scrollY > 900",timeout=7000)
                                    rect=page.locator("#target").bounding_box()
                                    assert rect and 0<=rect["y"]<=720,rect
                                    assert page.locator("#target").inner_text()==NEEDLE
                            end=api.post(tick)
                            assert end.status_code==200,end.text
                            assert end.json()["status"]=="completed_unverified",end.text

                        assert approval_count>=3,approval_count
                        assert planned==list(TOOLS)+["finish"],planned
                        saved=api.get(f"/computer/runs/{rid}").json()
                        assert [s["tool"] for s in saved["steps"]]==list(TOOLS)
                        assert all(s["status"]=="completed" for s in saved["steps"])
                        # Screenshot bytes are NOT persisted to SQLite.
                        assert saved["steps"][-1]["result"]["screenshot_available"] is True
                        assert "png_base64" not in str(saved)

                        screenshot=direct("windows_screenshot",{"hwnd":target_hwnd})
                        assert screenshot.status_code==200,screenshot.text[:150]
                        image=base64.b64decode(screenshot.json()["png_base64"])
                        assert image.startswith(b"\x89PNG\r\n\x1a\n")
                        assert 1000<len(image)<2*1024*1024

                        timeline=api.get(
                            f"/workspaces/demo/computer/runs/{rid}/replay")
                        assert timeline.status_code==200,timeline.text
                        log=timeline.json()
                        assert log["actions_reexecuted"] is False
                        assert [x["tool"] for x in log["steps"]]==list(TOOLS)
                        approvals=[x for x in log["steps"] if x["decision"]=="approved"]
                        assert len(approvals)==approval_count,approvals
                        assert all(x["verification"]["level"]!="deterministic_pass"
                                   for x in log["steps"])
                        assert "png_base64" not in str(log)
                        assert NEEDLE not in str(log)
                        print("HOSTED WINDOWS HEADED EDGE Ctrl+F PASS: "
                              "isolated profile, real msedge HWND, native Win32 "
                              "Ctrl+F and Unicode typing, target scrolled into view, "
                              "PNG read in memory, supervised approvals and redacted replay; "
                              "image_sha256_prefix="+hashlib.sha256(image).hexdigest()[:12]+
                              "; NOT owner P4 T07 or live model acceptance")
                    finally:
                        context.close()
            finally:
                executor.PAUSED=False
                core.DATABASE=original_db


if __name__=="__main__":
    main()
