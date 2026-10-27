"""ONE-SHOT GitHub Actions, real OpenRouter Free multi-agent acceptance.

DO NOT run in regular CI. Runs only in the isolated opt-in workflow with
OPENROUTER_TEST_API_KEY. The repository secret never enters a git file or log.
No external action/desktop tool, no paid fallback. At most 4 provider requests.
Uses the REAL production queue/worker dispatcher, session coordinator,
task persistence and HTTP routes; only the workspace data is synthetic.
"""
from __future__ import annotations

import asyncio
import json
import os
import sys
import tempfile
import time
from pathlib import Path

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))

# Required before importing core/app because they bind MODEL/DATABASE on import.
home=tempfile.TemporaryDirectory(prefix="lhm-team-live-free-")
os.environ.update({
    "LHM_DB_PATH":str(Path(home.name)/"team-live.sqlite3"),
    "LHM_LLM_PROVIDER":"openrouter",
    "OPENROUTER_MODEL":"openrouter/free",
    "LHM_OPENROUTER_FREE_ONLY":"1",
    "LHM_OPENROUTER_FREE_AUTO_SELECT":"0",
    "LHM_OPENROUTER_24H_LIMIT":"4",
    "LHM_OPENROUTER_1M_LIMIT":"4",
    "LHM_ALLOW_PRO":"0",
    "LHM_ADMIN_TOKEN":"",
    "DEEPSEEK_API_KEY":"",
    "OPENROUTER_API_KEY":os.environ.get("OPENROUTER_TEST_API_KEY",""),
})
if not os.environ["OPENROUTER_API_KEY"]:
    raise SystemExit("LIVE NOT RUN: GitHub secret OPENROUTER_TEST_API_KEY missing")

from fastapi.testclient import TestClient
import app
import core
import team_collaboration as team
import worker
from openrouter_provider import is_free_model

CAP = 4
observed=[]
real_gateway=app.call_free_model

async def guarded_real_gateway(role, ws, instruction, context, model):
    if len(observed)>=CAP:
        raise RuntimeError("Hard cap: >4 live OpenRouter requests forbidden")
    if model!="openrouter/free" or not is_free_model(model):
        raise RuntimeError("Paid/non-free model was requested; no network call")
    # Reserve the slot BEFORE network I/O: transport failure counts as an attempt.
    entry={"role":role,"requested_model":model,"api_called":True,
           "cost_reported":False}
    observed.append(entry)
    reply=await real_gateway(role,ws,instruction,context,model)
    actual=reply.get("model")
    usage=reply.get("usage") or {}
    if not isinstance(actual,str) or not actual.endswith(":free"):
        raise RuntimeError("Free router returned unverified/billable model; stopping")
    for source in (usage.get("cost"),reply.get("cost")):
        if source is not None:
            try:
                value=float(source)
            except (TypeError,ValueError):
                raise RuntimeError("Invalid provider cost metadata")
            if value!=0:
                raise RuntimeError("OpenRouter returned nonzero cost; stopping")
            entry["cost_reported"]=True
    entry.update(actual_model=actual,
                 input_tokens=usage.get("prompt_tokens"),
                 output_tokens=usage.get("completion_tokens"),
                 reported_cost_usd=0 if entry["cost_reported"] else None)
    if type(entry["input_tokens"]) is not int or type(entry["output_tokens"]) is not int:
        raise RuntimeError("No real usage metrics")
    return reply

async def run():
    core.setup()
    assert core.MODEL=="openrouter/free", core.MODEL
    assert len(core.ROLES)==16, "Expected 16 registered specialist Agent roles"
    assert worker.agent_parallelism() in range(1,5)
    assert not os.environ.get("DEEPSEEK_API_KEY")
    app.call_free_model=guarded_real_gateway
    client=TestClient(app.app)
    catalogue=client.get("/catalog")
    assert catalogue.status_code==200
    assert len(catalogue.json()["roles"])==16

    ws="agent_team_live_ci"
    w=client.post("/workspaces",json={
        "id":ws,"name":"Isolated Agent Collaboration Live Test",
        "industry":"general","business_context":"Synthetic only. No personal contacts.",
        "budget_usd":1.0,
    })
    assert w.status_code==201,(w.status_code,w.text[:160])
    start=client.post(f"/workspaces/{ws}/team/sessions",json={
        "objective":"Thử nghiệm phối hợp nội bộ: đề xuất ba cải tiến trải nghiệm cho phần mềm quản lý công việc; nêu hạn chế, không công bố.",
        "context":"Tình huống giả định, không dùng dữ liệu khách hàng. Không tạo email, link hoặc số điện thoại.",
        "roles":["director","research","content","quality"],
        "first_role":"director","max_turns":CAP,
        "requires_approval":False,
        "idempotency_key":"one-shot-free-team-20261027",
    })
    assert start.status_code==201,(start.status_code,start.text[:200])
    sid=start.json()["id"]

    began=time.monotonic()
    dispatches=[]
    for number in range(CAP):
        state=client.get(f"/workspaces/{ws}/team/sessions/{sid}").json()
        if state["status"]!="running":
            break
        task_id=state["active_task_id"]
        assert task_id, "Missing active task while session is running"
        with core.db() as c:
            pending=c.execute(
                "SELECT id,workspace_id,role,model FROM tasks WHERE id=?",
                (task_id,)).fetchone()
        assert pending is not None and pending["model"]=="openrouter/free"
        dispatched=await worker.dispatch_agent_batch([pending])
        assert dispatched==1,"Worker failed to dispatch current Agent task"
        with core.db() as c:
            outcome=c.execute(
                "SELECT status,error,attempts FROM tasks WHERE id=?",
                (task_id,)).fetchone()
        assert outcome is not None and outcome["status"]=="completed",(
            number+1,outcome["status"] if outcome else None,
            (outcome["error"] or "")[:120] if outcome else "missing")
        processed=team.sync_ready_team_sessions()
        assert processed>=1
        next_state=client.get(f"/workspaces/{ws}/team/sessions/{sid}").json()
        dispatches.append({"turn":number+1,"agent":pending["role"],
                           "task_completed":True,"session_state":next_state["status"]})
        print("LIVE_TEAM_TURN "+json.dumps(dispatches[-1]),flush=True)
        # Duplicate sync must not enqueue a second task/message for same turn.
        team.sync_team_session(sid)

    ended=client.get(f"/workspaces/{ws}/team/sessions/{sid}")
    assert ended.status_code==200
    data=ended.json()
    agent_msgs=[msg for msg in data["messages"] if msg["kind"]=="agent"]
    roles={msg["sender_role"] for msg in agent_msgs}
    ids=[msg["task_id"] for msg in agent_msgs]
    assert len(agent_msgs)>=2 and len(roles)>=2, (
        "Two distinct Agent handoffs were not observed",len(agent_msgs),roles)
    assert len(ids)==len(set(ids)), "Duplicate persisted handoff"
    assert all(msg["sender_role"] in ["director","research","content","quality"]
               for msg in agent_msgs), "Unauthorized role escaped"
    assert data["status"] in ("completed_unverified","needs_review"),(
        "Session may not pretend human acceptance",data["status"])
    if data["status"]=="completed_unverified":
        assert "quality" in roles, "Completed before Quality participation"
    assert len(observed)<=CAP and len(observed)==len(dispatches)
    stats={
        "mode":"LIVE_PROVIDER_AND_REAL_WORKER",
        "registered_roles":16,
        "selected_team_roles":["director","research","content","quality"],
        "calls_to_real_openrouter":len(observed),
        "max_authorized_calls":CAP,
        "successful_worker_turns":len(dispatches),
        "unique_agents_who_replied":sorted(roles),
        "persisted_agent_messages":len(agent_msgs),
        "final_session_status":data["status"],
        "elapsed_seconds":round(time.monotonic()-began,2),
        "all_actual_models_explicitly_free":all(
            str(x.get("actual_model","")).endswith(":free") for x in observed),
        "all_costs_reported_zero":all(
            x["cost_reported"] and x["reported_cost_usd"]==0 for x in observed),
        "total_input_tokens":sum(int(x.get("input_tokens") or 0) for x in observed),
        "total_output_tokens":sum(int(x.get("output_tokens") or 0) for x in observed),
        "calls":observed,
        "live_scope":"Only selected roles; NOT a certification of all 16 roles or Windows GUI",
    }
    Path("team-agents-live-free-result.json").write_text(
        json.dumps(stats,ensure_ascii=False,indent=2),encoding="utf-8")
    print("LIVE_TEAM_SUMMARY "+json.dumps(
        {k:v for k,v in stats.items() if k!="calls"},ensure_ascii=False),flush=True)
    assert stats["all_actual_models_explicitly_free"], "Free model verification failed"
    # A missing provider cost field does NOT authorize asserting zero dollars.
    assert stats["all_costs_reported_zero"], "Provider cost was not explicitly verified as USD 0"
    print("LIVE_TEAM_PASS: true worker + 2+ agents + verified free provider + cost 0",flush=True)

if __name__=="__main__":
    try:
        asyncio.run(run())
    finally:
        os.environ.pop("OPENROUTER_API_KEY",None)
        home.cleanup()
