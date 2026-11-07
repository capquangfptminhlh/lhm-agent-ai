"""Real hosted-Windows smoke: an authenticated owner-approved allowlisted launch.

No external account, network action or arbitrary executable is started.
A visible Notepad HWND must appear; launcher receipt alone is not proof.
"""
from __future__ import annotations

import os
import sys
import time
from pathlib import Path
from unittest.mock import patch

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))


def main() -> None:
    if sys.platform!="win32":
        raise SystemExit("Windows launcher acceptance requires a real Windows runner")
    import psutil
    from fastapi.testclient import TestClient
    import computer_executor as runner

    env={"LHM_DESKTOP_NATIVE":"1",
         "LHM_DESKTOP_WORKSPACE":"demo",
         "LHM_DESKTOP_ALLOWED_APPS":"notepad.exe",
         "LHM_COMPUTER_RUNNER_TOKEN":"windows-ci-launch-token"}
    with patch.dict(os.environ,env):
        client=TestClient(runner.runner)
        token={"Authorization":"Bearer windows-ci-launch-token"}

        def call(tool,args=None,auth=token):
            return client.post("/v1/tool",headers=auth,json={
                "workspace_id":"demo","role":"research","tool":tool,
                "args":{} if args is None else args})

        no_auth=call("windows_list",auth={})
        assert no_auth.status_code==401,no_auth.text

        runner.PAUSED=True
        try:
            paused=call("windows_launch",{"app":"notepad.exe"})
            assert paused.status_code==423,paused.text
        finally:
            runner.PAUSED=False

        original=call("windows_list")
        assert original.status_code==200,original.text
        before={entry["hwnd"]:entry["pid"] for entry in
                original.json().get("windows",[])
                if entry.get("process")=="notepad.exe"}

        rejected=call("windows_launch",{"app":"cmd.exe"})
        assert rejected.status_code==403,rejected.text

        started=call("windows_launch",{"app":"notepad.exe"})
        assert started.status_code==200,started.text
        receipt=started.json()
        assert receipt.get("launched")=="notepad.exe",receipt
        assert receipt.get("verified_visible") is False,receipt

        new_pids=set()
        try:
            deadline=time.monotonic()+35
            new_window=None
            while time.monotonic()<deadline:
                state=call("windows_list")
                assert state.status_code==200,state.text
                candidates=[w for w in state.json().get("windows",[])
                            if w.get("process")=="notepad.exe"
                            and w.get("hwnd") not in before]
                if candidates:
                    new_window=candidates[0]
                    new_pids.update(int(w["pid"]) for w in candidates
                                    if int(w["pid"]) not in before.values())
                    break
                time.sleep(0.35)
            assert new_window, "Approved Notepad launch did not create a visible allowlisted HWND"
            assert int(new_window["width"])>100 and int(new_window["height"])>50,new_window
            assert new_window["process"]=="notepad.exe",new_window
            print("REAL WINDOWS LAUNCH PASS: auth, global pause, denied cmd, approved "
                  "Notepad launch and new visible HWND confirmed; no external action")
        finally:
            # Only terminate newly observed Notepad PIDs, never a pre-existing
            # user/editor process. This CI test runs on an ephemeral desktop.
            for pid in new_pids:
                try:
                    p=psutil.Process(pid)
                    if p.name().lower()=="notepad.exe":
                        p.terminate()
                except (psutil.NoSuchProcess,psutil.AccessDenied):
                    pass
            runner.PAUSED=False


if __name__=="__main__":
    main()
