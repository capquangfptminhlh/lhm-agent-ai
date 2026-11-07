"""P4 T01 precursor: real Microsoft Notepad GUI text + Save As on hosted Windows.

Uses the production Agent's supervised state machine and authenticated Runner.
Only the AI planner is scripted, the Windows application is NOT mocked.
No user documents are opened or overwritten: test writes only inside a unique
temporary directory; script is CI-only. It is not owner Windows T01 parity.
"""
from __future__ import annotations

import os
from pathlib import Path
import sys
import tempfile
import time
from unittest.mock import patch

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))

TEXT=("Xin chào, đây là bài nghiệm thu số một. "
      "Tôi đang kiểm tra khả năng gõ tiếng Việt có dấu và lưu tệp.")
STEPS=("windows_launch","windows_list","windows_activate","windows_type",
       "windows_hotkey","windows_list","windows_snapshot","windows_click",
       "windows_hotkey","windows_type","windows_hotkey")


def _file_name_edit(controls: list[dict]) -> dict:
    """No blind focus fallback: require one grounded File Name edit rectangle."""
    editors=[c for c in controls if c.get("type")=="Edit"
             and isinstance(c.get("center"),dict)]
    names=[c for c in editors if str(c.get("automation_id","")).casefold()
           in ("1001","filenamecontrolhost","filename") or "filename" in
           str(c.get("automation_id","")).casefold()]
    if len(names)==1:
        return names[0]
    if len(editors)==1:
        return editors[0]
    raise AssertionError("Save dialog filename Edit missing or ambiguous: "+
                         repr([(c.get("type"),c.get("automation_id")) for c in controls[:45]]))


def _new_notepad(before: dict, listed: dict) -> dict:
    candidates=[w for w in listed.get("windows",[])
                if w.get("process")=="notepad.exe"
                and w.get("hwnd") not in before]
    assert len(candidates)==1,("Must select exactly one NEW Notepad HWND, found "+
                               repr([(w["hwnd"],w["title"]) for w in candidates]))
    return candidates[0]


def _save_dialog(main_hwnd: int, listed: dict) -> dict:
    candidates=[w for w in listed.get("windows",[])
                if w.get("process")=="notepad.exe"
                and w.get("dialog") is True
                and w.get("hwnd")!=main_hwnd]
    assert len(candidates)==1,("Cannot ground one genuine native Save As dialog; "+
                              repr([(w.get("hwnd"),w.get("title"),w.get("dialog"))
                                    for w in listed.get("windows",[])]))
    return candidates[0]


def main() -> None:
    if sys.platform!="win32":
        raise SystemExit("Requires GitHub-hosted Windows desktop")
    import httpx
    import psutil
    from fastapi import HTTPException
    from fastapi.testclient import TestClient
    import app
    import core
    import computer_agent as agent
    import computer_executor as executor

    with tempfile.TemporaryDirectory(prefix="lhm-notepad-real-save-") as tmp:
        base=Path(tmp)
        target=base/"p4-notepad-vietnamese.txt"
        assert not target.exists()
        original_db=core.DATABASE
        core.DATABASE=base/"test.sqlite3"
        env={
            "LHM_DESKTOP_NATIVE":"1",
            "LHM_DESKTOP_WORKSPACE":"demo",
            "LHM_DESKTOP_ALLOWED_APPS":"notepad.exe",
            "LHM_ADMIN_TOKEN":"",
            "LHM_COMPUTER_RUNNER_TOKEN":"notepad-ci-only-token",
            "LHM_COMPUTER_URL":"http://127.0.0.1:7782",
            "LHM_COMPUTER_HOME":str(base/"sandbox"),
            "LHM_COMPUTER_TRUSTED_ENABLED":"0",
            "LHM_COMPUTER_AUTO_WRITE":"0",
        }
        with patch.dict(os.environ,env):
            executor.PAUSED=False
            try:
                core.setup()
                agent.prepare_db()
                api=TestClient(app.app)
                native=TestClient(executor.runner)
                auth={"Authorization":"Bearer notepad-ci-only-token"}

                def direct(tool: str,args: dict | None = None):
                    response=native.post("/v1/tool",headers=auth,json={
                        "workspace_id":"demo","role":"research",
                        "tool":tool,"args":args or {}})
                    assert response.status_code==200,(tool,response.text)
                    return response.json()

                before={w["hwnd"]:w["pid"] for w in
                        direct("windows_list")["windows"]
                        if w.get("process")=="notepad.exe"}
                launched_pids=set()

                async def through_runner(run: dict,tool: str,args: dict):
                    async with httpx.AsyncClient(
                        transport=httpx.ASGITransport(app=executor.runner),
                        base_url="http://local-runner") as client:
                        response=await client.post("/v1/tool",headers=auth,json={
                            "workspace_id":run["workspace_id"],
                            "role":run["role"],"run_id":run["id"],
                            "tool":tool,"args":args})
                    if response.status_code!=200:
                        raise HTTPException(502,"Runner rejected "+tool+
                                            ": HTTP "+str(response.status_code))
                    return response.json()

                async def plan(run: dict,steps: list[dict]):
                    index=len(steps)
                    assert index<len(STEPS)+1, "Unbounded Notepad test"
                    if index==0:
                        return "windows_launch",{"app":"notepad.exe"}
                    if index==1:
                        return "windows_list",{}
                    found=_new_notepad(before,steps[1]["result"])
                    hwnd=found["hwnd"]
                    if index==2:
                        return "windows_activate",{"hwnd":hwnd}
                    if index==3:
                        return "windows_type",{"hwnd":hwnd,"text":TEXT}
                    if index==4:
                        return "windows_hotkey",{"hwnd":hwnd,"keys":"CTRL+S"}
                    if index==5:
                        return "windows_list",{}
                    dialog=_save_dialog(hwnd,steps[5]["result"])
                    dialog_hwnd=dialog["hwnd"]
                    if index==6:
                        return "windows_snapshot",{"hwnd":dialog_hwnd}
                    edit=_file_name_edit(steps[6]["result"]["controls"])
                    if index==7:
                        return "windows_click",{"hwnd":dialog_hwnd,**edit["center"]}
                    if index==8:
                        return "windows_hotkey",{"hwnd":dialog_hwnd,"keys":"CTRL+A"}
                    if index==9:
                        return "windows_type",{"hwnd":dialog_hwnd,"text":str(target)}
                    if index==10:
                        return "windows_hotkey",{"hwnd":dialog_hwnd,"keys":"ENTER"}
                    raise AssertionError("Notepad Save As completed without file evidence")

                created=api.post("/workspaces/demo/computer/runs",json={
                    "role":"research",
                    "objective":"Thử gõ văn bản tiếng Việt và lưu bằng Notepad GUI",
                    "mode":"supervised","max_steps":15,
                    "idempotency_key":"real-notepad-gui-save-ci-20261107"})
                assert created.status_code==201,created.text
                rid=created.json()["id"]
                tick="/computer/runs/"+rid+"/tick"
                try:
                    with patch.object(agent,"runner_call",new=through_runner), \
                         patch.object(agent,"_next_action",new=plan):
                        for number, expected in enumerate(STEPS):
                            # Notepad may take a moment to create its main HWND
                            # or Save dialog; do not pre-guess another target.
                            if number in (1,5):
                                time.sleep(1.3)
                            response=api.post(tick)
                            assert response.status_code==200,(number,response.text)
                            payload=response.json()
                            if payload["status"]=="awaiting_approval":
                                if expected=="windows_hotkey" and number==10:
                                    assert not target.exists(), "Save sent before final approval"
                                sid=payload["step_id"]
                                approval=api.post(
                                    f"/computer/runs/{rid}/steps/{sid}/approve")
                                assert approval.status_code==200,approval.text
                                response=api.post(tick)
                                assert response.status_code==200,response.text
                                payload=response.json()
                            assert payload.get("last_tool")==expected,(number,expected,payload)
                            if number<10:
                                assert payload["status"]=="queued",(number,expected,payload)
                            else:
                                # Save As dialog may disappear immediately on
                                # Enter. PR #81 must then stop for reconciliation
                                # rather than assume side effects succeeded.
                                assert payload["status"] in ("queued","awaiting_reconciliation"),payload
                    deadline=time.monotonic()+7
                    while not target.exists() and time.monotonic()<deadline:
                        time.sleep(0.15)
                    assert target.is_file(),"Notepad Save As did not create the file via GUI"
                    data=target.read_bytes()
                    value=data.decode("utf-8-sig")
                    assert value==TEXT,(
                        "Real Notepad saved wrong Unicode text: "
                        f"wanted {len(TEXT)} chars, found {len(value)}")
                    assert len(data)>=len(TEXT.encode("utf-8"))
                    view=api.get("/computer/runs/"+rid).json()
                    assert len(view["steps"])==len(STEPS),view["steps"]
                    assert [step["tool"] for step in view["steps"]]==list(STEPS)
                    assert view["steps"][3]["result"]["typed"] is True
                    assert all(step["status"]=="completed" for step in view["steps"][:-1])
                    if view["steps"][-1]["status"]=="needs_review":
                        assert view["status"]=="awaiting_reconciliation"
                    with core.db() as con:
                        count=con.execute(
                            "SELECT COUNT(*) FROM events WHERE workspace_id=? AND task_id=?",
                            ("demo",rid)).fetchone()[0]
                    assert count>=4,"Notepad GUI run must have audit events"
                    print("REAL HOSTED WINDOWS NOTEPAD SAVE PASS: native Notepad GUI, "
                          "owner-style step approvals, real Win32 Unicode input, "
                          "Save As dialog grounded by UIA, exact UTF-8 file readback, "
                          "SQLite audit; scripted planner, NOT OWNER_WINDOWS_PASS")
                finally:
                    # Never terminate a Notepad that existed before the test.
                    for w in direct("windows_list")["windows"]:
                        if w.get("process")=="notepad.exe" and w["hwnd"] not in before:
                            launched_pids.add(w["pid"])
                    for pid in launched_pids:
                        if pid not in before.values():
                            try:
                                proc=psutil.Process(pid)
                                if proc.name().lower()=="notepad.exe":
                                    proc.terminate()
                            except (psutil.NoSuchProcess,psutil.AccessDenied):
                                pass
            finally:
                executor.PAUSED=False
                core.DATABASE=original_db


if __name__=="__main__":
    main()
