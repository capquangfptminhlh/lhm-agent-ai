"""Frozen Python entrypoint for LHM Robot Desktop on Windows.

backend = FastAPI on 127.0.0.1:7770 (Electron UI only)
worker  = existing Agent work queue
native  = authenticated Windows controller on 127.0.0.1:7782
The Electron main process generates per-launch tokens and starts each mode
as a separate process. No cloud control service is required.
"""
from __future__ import annotations
import argparse
import asyncio
import os
import sys
from pathlib import Path

# python desktop/runtime.py normally sets sys.path[0] to desktop/; include the
# repository root in source mode. Frozen mode uses PyInstaller's collected app.
if not getattr(sys,"frozen",False):
    sys.path.insert(0,str(Path(__file__).resolve().parents[1]))

def main(argv=None):
    cli=argparse.ArgumentParser(description="LHM Robot Desktop trusted local runtime")
    cli.add_argument("mode",choices=["api","worker","native","self-test"])
    mode=cli.parse_args(argv).mode
    if mode=="self-test":
        import app
        import team_collaboration
        import computer_agent
        import computer_executor
        import agent_runtime_pilot
        import postiz_verifier
        import windows_native
        import robot_vision
        import robot_sandbox
        assert callable(app.app) and callable(computer_executor.runner)
        assert "windows_click" in computer_agent.TOOLS
        assert "windows_drag" in computer_agent.TOOLS
        assert "windows_desktop_click" in computer_agent.TOOLS
        assert "vision_observe" in computer_agent.TOOLS
        assert "vision_locate" in computer_agent.TOOLS
        assert "sandbox_run" in computer_agent.TOOLS
        print("LHM Windows runtime imports PASS",flush=True)
        return
    if mode=="api":
        import uvicorn
        from app import app as api
        uvicorn.run(api,host="127.0.0.1",port=7770,log_level="info",
                    workers=1,access_log=False)
        return
    if mode=="worker":
        import worker
        asyncio.run(worker.main())
        return
    if mode=="native":
        if os.name!="nt":
            raise SystemExit("Windows native mode requires Windows")
        if not os.getenv("LHM_COMPUTER_RUNNER_TOKEN"):
            raise SystemExit("Native runner secret missing")
        import uvicorn
        from computer_executor import runner
        uvicorn.run(runner,host="127.0.0.1",port=7782,log_level="warning",
                    workers=1,access_log=False)
        return

if __name__=="__main__":
    main()
