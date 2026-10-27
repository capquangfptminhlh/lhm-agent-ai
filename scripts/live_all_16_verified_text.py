"""Real isolated OpenRouter Free acceptance for ALL 16 LHM Agent roles.

One model request per registered role (hard upper bound: 16), no paid
fallback, no actual customer data, no desktop or external publishing.
Uses real production worker.dispatch_agent_batch and SQLite task records.
Evidence is strictly metadata-only: do not log prompts, results or keys.
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
home=tempfile.TemporaryDirectory(prefix="lhm-all16-free-")
os.environ.update({
    "LHM_DB_PATH":str(Path(home.name)/"all16.sqlite3"),
    "LHM_LLM_PROVIDER":"openrouter",
    "OPENROUTER_MODEL":"openrouter/free",
    "LHM_OPENROUTER_FREE_ONLY":"1",
    "LHM_OPENROUTER_FREE_AUTO_SELECT":"1",
    "LHM_OPENROUTER_24H_LIMIT":"16",
    "LHM_OPENROUTER_1M_LIMIT":"20",
    "LHM_ALLOW_PRO":"0",
    "LHM_ADMIN_TOKEN":"",
    "DEEPSEEK_API_KEY":"",
    "OPENROUTER_API_KEY":os.environ.get("OPENROUTER_TEST_API_KEY",""),
})
if not os.environ["OPENROUTER_API_KEY"]:
    raise SystemExit("LIVE NOT RUN: disposable OpenRouter GitHub secret missing")

import app
import core
import worker

MAX_REQUESTS=16
attempts=[]
gateway=app.call_free_model

async def real_free_guard(role,ws,instruction,context,model):
    if len(attempts)>=MAX_REQUESTS or model!="openrouter/free":
        raise RuntimeError("Unauthorized provider request blocked")
    record={"role":role,"requested":"openrouter/free",
            "actual_model":None,"reported_cost_usd":None,"verified_zero_cost":False,
            "tokens_in":0,"tokens_out":0,"gateway_response_valid":False}
    attempts.append(record)
    reply=await gateway(role,ws,instruction,context,model)
    model_id=reply.get("model")
    if not isinstance(model_id,str) or not model_id.endswith(":free"):
        raise RuntimeError("Actual provider model not verified free")
    usage=reply.get("usage") or {}
    cost_values=[v for v in (usage.get("cost"),reply.get("cost")) if v is not None]
    if not cost_values or any(float(v)!=0 for v in cost_values):
        raise RuntimeError("Provider did not explicitly confirm cost exactly 0 USD")
    if type(usage.get("prompt_tokens")) is not int or type(usage.get("completion_tokens")) is not int:
        raise RuntimeError("Provider usage metadata unavailable")
    record.update(actual_model=model_id,reported_cost_usd=0,
                  verified_zero_cost=True,tokens_in=usage["prompt_tokens"],
                  tokens_out=usage["completion_tokens"],gateway_response_valid=True)
    return reply

async def main():
    core.setup()
    assert len(core.ROLES)==16 and core.MODEL=="openrouter/free"
    assert not os.environ.get("DEEPSEEK_API_KEY")
    app.call_free_model=real_free_guard
    wsid="ci_all16_live"
    start=time.monotonic()
    with core.db() as c:
        c.execute("""INSERT INTO workspaces
          (id,name,industry,business_context,budget_usd,created_at)
          VALUES(?,?,?,?,?,?)""",
          (wsid,"All 16 Free Agent Test","general","Fictional software quality scenario",1.0,core.now()))
        tasks=[]
        for role in core.ROLES:
            text=("Kiểm tra vai trò "+role+": đề xuất đúng một cải tiến quy trình "
                  "cho dự án phần mềm giả định, tối đa 90 từ bằng tiếng Việt. "
                  "Nêu giới hạn dữ liệu. Không tạo email/điện thoại, không xuất bản.")
            info=app.task_record(c,wsid,role,text,"Synthetic test only",
                                 model="openrouter/free",
                                 idem="real16-"+role)
            tasks.append((role,info["id"]))
    results=[]
    for i,(role,taskid) in enumerate(tasks,1):
        with core.db() as c:
            task=c.execute("SELECT id,workspace_id,role,model FROM tasks WHERE id=?",
                           (taskid,)).fetchone()
            assert task["model"]=="openrouter/free"
        sent=await worker.dispatch_agent_batch([task])
        assert sent==1,"Production worker did not dispatch queued task"
        with core.db() as c:
            state=c.execute("SELECT status,error,result,attempts FROM tasks WHERE id=?",
                            (taskid,)).fetchone()
        success=state["status"]=="completed" and isinstance(state["result"],str) and bool(state["result"].strip())
        result={"role":role,"pass":success,"status":state["status"],
                "attempts":state["attempts"],
                "safe_failure_type":"no_data_or_provider_error" if not success else None,
                "elapsed_sec":round(time.monotonic()-start,1)}
        results.append(result)
        print("ALL16_LIVE_ROLE "+json.dumps(result),flush=True)
        # A 16-call cap plus slight spacing avoids a burst into Free providers.
        if i<MAX_REQUESTS:
            await asyncio.sleep(8)

    valid_calls=[x for x in attempts if x["gateway_response_valid"]]
    report={
        "test":"LHM all 16 roles via real Free model and production task worker",
        "registered_roles":len(core.ROLES),
        "requested_provider_calls":len(attempts),
        "max_requests_allowed":MAX_REQUESTS,
        "completed_roles":sum(x["pass"] for x in results),
        "failed_roles":[x["role"] for x in results if not x["pass"]],
        "all_role_results":results,
        "all_successful_calls_verified_zero_usd":all(x["verified_zero_cost"] for x in valid_calls),
        "all_attempts_cost_verifiable":len(valid_calls)==len(attempts),
        "actual_free_models":sorted(set(x["actual_model"] for x in valid_calls)),
        "total_reported_input_tokens":sum(x["tokens_in"] for x in valid_calls),
        "total_reported_output_tokens":sum(x["tokens_out"] for x in valid_calls),
        "elapsed_seconds":round(time.monotonic()-start,1),
        "scope":"16 individual roles, NOT 16-way concurrency or physical Windows acceptance",
        "api_attempts":[{k:v for k,v in x.items() if k not in ("requested",)} for x in attempts],
    }
    Path("all16-agent-live-free-result.json").write_text(
        json.dumps(report,ensure_ascii=False,indent=2),encoding="utf-8")
    print("ALL16_LIVE_SUMMARY "+json.dumps({k:v for k,v in report.items()
          if k not in ("all_role_results","api_attempts")},ensure_ascii=False),flush=True)
    assert len(attempts)==MAX_REQUESTS,"Not all roles attempted"
    assert report["completed_roles"]==16,"One or more roles failed; no claim of full 16/16"
    assert report["all_attempts_cost_verifiable"],"A provider request was not verified cost zero"
    print("ALL16_LIVE_PASS 16 roles with real OpenRouter Free and real worker",flush=True)

if __name__=="__main__":
    try:
        asyncio.run(main())
    finally:
        os.environ.pop("OPENROUTER_API_KEY",None)
        home.cleanup()
