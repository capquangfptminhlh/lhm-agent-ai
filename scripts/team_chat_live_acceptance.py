"""Manual-only real two-turn Free Team Chat acceptance; never logs raw responses."""
import asyncio
import json
import os
from pathlib import Path
import sys
import tempfile
from fastapi import HTTPException

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))

def main():
    key=os.environ.get("OPENROUTER_TEST_API_KEY","").strip()
    if not key:
        raise SystemExit("LIVE NOT RUN: OPENROUTER_TEST_API_KEY secret missing")
    with tempfile.TemporaryDirectory(prefix="lhm-free-team-") as tmp:
        # Set before importing app/core to avoid production DB or paid model.
        os.environ.update({
            "LHM_DB_PATH":str(Path(tmp)/"team.sqlite3"),
            "LHM_OPENROUTER_FREE_ONLY":"1",
            "OPENROUTER_API_KEY":key,
            "LHM_ADMIN_TOKEN":"",
            "LHM_TOOLS_WORKSPACE":"demo",
        })
        import core
        import app
        import team_collaboration as team
        import tool_hub
        import computer_agent
        from fastapi.testclient import TestClient
        core.setup()
        team.prepare_db()
        tool_hub.prepare_db()
        computer_agent.prepare_db()
        api=TestClient(app.app)
        response=api.post("/workspaces/demo/team/sessions",json={
            "objective":"Director lập kế hoạch cho cửa hàng giả định; Quality kiểm tra rủi ro và giới hạn",
            "context":"Chỉ dữ liệu tổng hợp giả định, không có thông tin khách hàng thật",
            "roles":["director","quality"],"first_role":"director",
            "max_turns":2,"idempotency_key":"free-team-live-two-turns",
        })
        if response.status_code!=201 or response.json().get("model")!="openrouter/free":
            raise AssertionError("Free-only team session could not be created")
        sid=response.json()["id"]
        checked=[]
        for role in ("director","quality"):
            state=api.get("/workspaces/demo/team/sessions/"+sid).json()
            tid=state["active_task_id"]
            if not tid or state["active_task"]["role"]!=role:
                raise AssertionError("Expected handoff role was not queued")
            with core.db() as c:
                task=c.execute("SELECT model,status FROM tasks WHERE id=?",(tid,)).fetchone()
                if not task or task["model"]!="openrouter/free" or task["status"]!="queued":
                    raise AssertionError("Task has a wrong Free-only model or state")
            # Exactly one completion call per role. Never retry, publish or pay.
            result=asyncio.run(app.execute_task(tid))
            if result["status"]!="completed":
                raise AssertionError("Model did not complete its Agent turn")
            try:
                answer=json.loads(result["result"].strip())
            except (ValueError,TypeError) as exc:
                raise AssertionError("Model output was not structured Team JSON") from exc
            if not isinstance(answer,dict) or not isinstance(answer.get("message"),str) \
                    or not answer["message"].strip():
                raise AssertionError("Model failed the Team response contract")
            team.sync_team_session(sid)
            checked.append(role)
        state=api.get("/workspaces/demo/team/sessions/"+sid).json()
        messages=[m for m in state["messages"] if m["kind"]=="agent"]
        if [m["sender_role"] for m in messages]!=checked or \
                state["status"] not in ("completed_unverified","needs_review"):
            raise AssertionError("Persisted two-role handoff failed")
        with core.db() as c:
            usage=c.execute("SELECT COUNT(*),COALESCE(SUM(upper_cost_usd),0) FROM usage").fetchone()
            tool_jobs=c.execute("SELECT COUNT(*) FROM tool_jobs").fetchone()[0]
            computer_steps=c.execute("SELECT COUNT(*) FROM computer_steps").fetchone()[0]
        if usage[0]!=2 or float(usage[1])!=0 or tool_jobs or computer_steps:
            raise AssertionError("Unexpected cost, usage or external side effects")
        print("TEAM LIVE FREE PASS: "+json.dumps({
            "model_turns":2,"persisted_agent_messages":len(messages),
            "reported_upper_cost_usd":0,"tool_jobs":0,
            "computer_steps":0,"final_state":state["status"],
            "external_completion_verified":False}))

if __name__=="__main__":
    try:
        main()
    except HTTPException as exc:
        print("TEAM LIVE FREE FAIL: HTTP "+str(exc.status_code),file=sys.stderr)
        raise SystemExit(1)
    except (AssertionError,RuntimeError,ValueError) as exc:
        print("TEAM LIVE FREE FAIL: "+type(exc).__name__,file=sys.stderr)
        raise SystemExit(1)
