"""Real multi-step LHM Computer Agent acceptance on a Windows CI desktop.

The scripted planner replaces only the LLM. The FastAPI Computer session, owner
approval, authenticated Runner tool endpoint, real Win32/UI Automation click,
post-action verification, and SQLite evidence all run for real.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import time
from unittest.mock import patch

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from windows_uia_gui_e2e import SOURCE


def main() -> None:
    if sys.platform!="win32":
        raise SystemExit("Multi-step acceptance needs the Windows runner")

    windir=Path(os.environ.get("WINDIR",r"C:\Windows"))
    compilers=[
        windir/"Microsoft.NET"/"Framework64"/"v4.0.30319"/"csc.exe",
        windir/"Microsoft.NET"/"Framework"/"v4.0.30319"/"csc.exe"]
    csc=next((p for p in compilers if p.is_file()),None)
    if csc is None:
        raise AssertionError("Native WinForms test compiler missing")

    with tempfile.TemporaryDirectory(prefix="lhm-agent-real-e2e-") as tmp:
        base=Path(tmp)
        src=base/"LhmUiaFixture.cs"
        exe=base/"LhmUiaFixture.exe"
        src.write_text(SOURCE,encoding="utf-8")
        subprocess.run([str(csc),"/nologo","/target:winexe",
                        "/reference:System.Windows.Forms.dll",
                        "/reference:System.Drawing.dll",
                        "/out:"+str(exe),str(src)],
                       check=True,capture_output=True,text=True,timeout=45)
        env={"LHM_DESKTOP_NATIVE":"1",
             "LHM_DESKTOP_ALLOWED_APPS":"lhmuiafixture.exe",
             "LHM_ADMIN_TOKEN":"",
             "LHM_COMPUTER_RUNNER_TOKEN":"uia-local-ci-runner-token",
             "LHM_COMPUTER_URL":"http://127.0.0.1:7782",
             "LHM_COMPUTER_HOME":str(base/"sandbox"),
             "LHM_COMPUTER_TRUSTED_ENABLED":"0",
             "LHM_COMPUTER_AUTO_WRITE":"0"}
        with patch.dict(os.environ,env):
            process=subprocess.Popen([str(exe)],cwd=str(base))
            try:
                _accept(base)
            finally:
                process.terminate()
                try:
                    process.wait(timeout=8)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait(timeout=5)


def _accept(base: Path) -> None:
    import httpx
    from fastapi import HTTPException
    from fastapi.testclient import TestClient
    from unittest.mock import patch
    import core
    import computer_agent as agent
    import computer_executor as executor
    import app

    saved_db=core.DATABASE
    core.DATABASE=base/"e2e.sqlite3"
    executor.PAUSED=False
    try:
        core.setup()
        agent.prepare_db()
        api=TestClient(app.app)
        native_api=TestClient(executor.runner)
        token={"Authorization":"Bearer uia-local-ci-runner-token"}

        # The local runner enforces auth even for read-only UIA access.
        unauthorized=native_api.post("/v1/tool",json={
            "workspace_id":"demo","role":"research","tool":"windows_list","args":{}})
        assert unauthorized.status_code==401,unauthorized.text

        deadline=time.monotonic()+25
        found=False
        while time.monotonic()<deadline:
            windows=native_api.post("/v1/tool",headers=token,json={
                "workspace_id":"demo","role":"research","tool":"windows_list",
                "args":{}})
            assert windows.status_code==200,windows.text
            if any(w.get("process")=="lhmuiafixture.exe" and
                   w.get("title")=="LHM UIA BEFORE"
                   for w in windows.json().get("windows",[])):
                found=True
                break
            time.sleep(0.25)
        if not found:
            raise AssertionError("Real WinForms window was not observable through Runner")

        async def through_runner(run: dict,tool: str,args: dict) -> dict:
            # In-process ASGI transport runs the authenticated real Runner,
            # avoiding a second network listener; it does NOT mock actions.
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=executor.runner),
                                         base_url="http://lhm-local-runner") as client:
                response=await client.post("/v1/tool",headers=token,json={
                    "workspace_id":run["workspace_id"],"role":run["role"],
                    "run_id":run["id"],"tool":tool,"args":args})
            if response.status_code!=200:
                raise HTTPException(502,"Computer Runner rejected tool")
            return response.json()

        planned=[]
        async def scripted_planner(run: dict,steps: list[dict]):
            # Only model selection is scripted; every observation is from the
            # actual Windows UIA controls, not hardcoded hwnd/automation_id.
            if not steps:
                planned.append("windows_list")
                return "windows_list",{}
            if len(steps)==1:
                matches=[w for w in steps[0]["result"]["windows"]
                         if w.get("process")=="lhmuiafixture.exe" and
                         w.get("title")=="LHM UIA BEFORE"]
                assert len(matches)==1, matches
                planned.append("windows_snapshot")
                return "windows_snapshot",{"hwnd":matches[0]["hwnd"]}
            if len(steps)==2:
                snap=steps[1]["result"]
                buttons=[c for c in snap["controls"] if
                         c.get("type")=="Button" and
                         c.get("label")=="LHM CI APPLY" and
                         c.get("automation_id")]
                assert len(buttons)==1,buttons
                b=buttons[0]
                planned.append("windows_click_element")
                return "windows_click_element",{
                    "hwnd":snap["window"]["hwnd"],
                    "automation_id":b["automation_id"],
                    "control_type":b["type"],
                    "label":b["label"]}
            if len(steps)==3:
                planned.append("windows_snapshot")
                return "windows_snapshot",{"hwnd":steps[1]["result"]["window"]["hwnd"]}
            if len(steps)==4:
                after=steps[3]["result"]
                assert after["window"]["title"]=="LHM UIA AFTER",after["window"]
                planned.append("finish")
                return "finish",{"summary":"Native test window changed; owner verification remains required.",
                                 "evidence":"Real Windows UIA window title observed after approved click"}
            raise AssertionError("Unbounded Agent action loop")

        created=api.post("/workspaces/demo/computer/runs",json={
            "role":"research",
            "objective":"Mở cửa sổ test Windows, nhận diện nút và thao tác sau khi chủ duyệt",
            "mode":"supervised","max_steps":8,
            "idempotency_key":"real-windows-agent-e2e-20261029"})
        assert created.status_code==201,created.text
        rid=created.json()["id"]
        tick="/computer/runs/"+rid+"/tick"
        with patch.object(agent,"runner_call",new=through_runner),patch.object(
            agent,"_next_action",new=scripted_planner):
            first=api.post(tick)
            assert first.status_code==200 and first.json()["last_tool"]=="windows_list",first.text
            second=api.post(tick)
            assert second.status_code==200 and second.json()["last_tool"]=="windows_snapshot",second.text

            pending=api.post(tick)
            assert pending.status_code==200 and pending.json()["status"]=="awaiting_approval",pending.text
            sid=pending.json()["step_id"]
            current=api.get("/computer/runs/"+rid).json()
            assert current["steps"][-1]["status"]=="awaiting_approval"
            assert current["steps"][1]["result"]["window"]["title"]=="LHM UIA BEFORE"
            # No click until explicit owner approval. This also checks that
            # a rejected duplicate approval cannot execute a second click.
            approved=api.post(f"/computer/runs/{rid}/steps/{sid}/approve")
            assert approved.status_code==200,approved.text
            duplicate=api.post(f"/computer/runs/{rid}/steps/{sid}/approve")
            assert duplicate.status_code==409,duplicate.text

            done_click=api.post(tick)
            assert done_click.status_code==200 and done_click.json()["last_tool"]=="windows_click_element",done_click.text
            observed=api.post(tick)
            assert observed.status_code==200 and observed.json()["last_tool"]=="windows_snapshot",observed.text
            finished=api.post(tick)
            assert finished.status_code==200 and finished.json()["status"]=="completed_unverified",finished.text

        final=api.get("/computer/runs/"+rid).json()
        assert [step["tool"] for step in final["steps"]]==[
            "windows_list","windows_snapshot","windows_click_element","windows_snapshot"],final["steps"]
        receipt=final["steps"][2]["result"]
        assert receipt["clicked"] is True and receipt["verified_effect"] is False,receipt
        proof=receipt["_verification"]
        assert proof["method"]=="state_pair" and proof["observed_change"] is True,proof
        assert proof["ok"] is None,proof
        assert proof["pre"]["title"]=="LHM UIA BEFORE",proof
        assert proof["post"]["title"]=="LHM UIA AFTER",proof
        assert final["steps"][3]["result"]["window"]["title"]=="LHM UIA AFTER"
        assert planned==["windows_list","windows_snapshot","windows_click_element",
                         "windows_snapshot","finish"],planned
        with core.db() as connection:
            audit=connection.execute(
                "SELECT COUNT(*) FROM audit_log WHERE workspace_id=? AND entity_id=?",
                ("demo",rid)).fetchone()[0]
        assert audit>=1, "Run should have a persistent audit record"
        print("REAL WINDOWS COMPUTER AGENT E2E PASS: authenticated runner, 4 actual tool "
              "steps, explicit approval, unique UIA click, UIA before/after fingerprint, "
              "SQLite evidence and completed_unverified")
    finally:
        core.DATABASE=saved_db
        executor.PAUSED=False


if __name__=="__main__":
    main()
