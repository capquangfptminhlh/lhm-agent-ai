"""One-time real Free-only role retest on verified text-capable model catalog.

Max FIVE provider POSTs, one each for roles that failed the unfiltered Free router:
director, lead, content, ads, finance. Each call goes through production
task_record -> worker.dispatch_agent_batch -> app.execute_task -> Free gateway.
Never prints prompts, API keys, raw upstream responses or customer data.
No automatic retries. Report FAIL if provider error or unverified billing.
"""
import asyncio
import json
import os
import sys
import tempfile
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
home=tempfile.TemporaryDirectory(prefix="lhm-five-free-models-")
os.environ.update({
    "LHM_DB_PATH":str(Path(home.name)/"test.sqlite3"),
    "LHM_LLM_PROVIDER":"openrouter",
    "OPENROUTER_MODEL":"openrouter/free",
    "LHM_OPENROUTER_FREE_ONLY":"1",
    "LHM_OPENROUTER_FREE_AUTO_SELECT":"1",
    "LHM_OPENROUTER_24H_LIMIT":"5",
    "LHM_OPENROUTER_1M_LIMIT":"5",
    "LHM_ALLOW_PRO":"0",
    "DEEPSEEK_API_KEY":"",
    "LHM_ADMIN_TOKEN":"",
    "OPENROUTER_API_KEY":os.environ.get("OPENROUTER_TEST_API_KEY",""),
})
if not os.environ.get("OPENROUTER_API_KEY"):
    raise SystemExit("LIVE NOT RUN: GitHub test secret missing")
import app
import core
import worker
from openrouter_free_catalog import select_free_model

ROLES=("director","lead","content","ads","finance")
attempts=[]
original=app.call_free_model

async def verified_gateway(role,ws,instruction,context,model):
    if len(attempts)>=len(ROLES) or model!="openrouter/free":
        raise RuntimeError("Free-only hard cap exceeded")
    item={"role":role,"request_model":model,"actual_model":None,
          "tokens_in":None,"tokens_out":None,"reported_cost_usd":None}
    attempts.append(item)
    response=await original(role,ws,instruction,context,model)
    mid=response.get("model")
    usage=response.get("usage",{})
    if not isinstance(mid,str) or not mid.endswith(":free"):
        raise RuntimeError("Returned model is not verified :free")
    cost=usage.get("cost",response.get("cost"))
    if cost is None or float(cost)!=0:
        raise RuntimeError("Provider cost missing or nonzero")
    item.update(actual_model=mid,tokens_in=usage.get("prompt_tokens"),
                tokens_out=usage.get("completion_tokens"),reported_cost_usd=0)
    return response

async def run():
    core.setup()
    assert len(core.ROLES)==16 and core.MODEL=="openrouter/free"
    # Catalog is fetched WITHOUT API secret; URLs/paid fallback disallowed.
    candidates={}
    for role in ROLES:
        candidates[role]=await select_free_model(role)
        assert candidates[role].endswith(":free")
    print("LIVE5_TEXT_MODEL_SELECTION "+json.dumps(candidates),flush=True)
    app.call_free_model=verified_gateway
    wsid="ci_retest_five"
    with core.db() as c:
        c.execute("""INSERT INTO workspaces
          (id,name,industry,business_context,budget_usd,created_at)
          VALUES(?,?,?,?,?,?)""",
          (wsid,"CI Free Roles Retest","general","Synthetic test only",1.0,core.now()))
        ids={}
        for role in ROLES:
            task=app.task_record(c,wsid,role,
                "Viết đúng một đề xuất cải tiến quy trình phần mềm giả định, "
                "bằng tiếng Việt ngắn gọn và có tiêu chí kiểm tra. Không liên hệ khách hàng, "
                "không thực hiện thay đổi hệ thống hoặc giao dịch.",
                "Synthetic, no private information.",
                model="openrouter/free",idem="text-five-"+role)
            ids[role]=task["id"]
    started=time.monotonic()
    results=[]
    for n,role in enumerate(ROLES,1):
        with core.db() as c:
            row=c.execute("SELECT id,workspace_id,role,model FROM tasks WHERE id=?",
                          (ids[role],)).fetchone()
        dispatched=await worker.dispatch_agent_batch([row])
        assert dispatched==1
        with core.db() as c:
            r=c.execute("SELECT status,result,error,attempts FROM tasks WHERE id=?",
                        (ids[role],)).fetchone()
        succeeded=r["status"]=="completed" and bool(r["result"])
        meta=attempts[-1] if attempts else {}
        item={"role":role,"selected":candidates[role],
              "actual":meta.get("actual_model"),
              "pass":succeeded,"status":r["status"],
              "attempts":r["attempts"],
              "safe_error":r["error"][:180] if r["error"] else None}
        results.append(item)
        print("LIVE5_ROLE "+json.dumps(item,ensure_ascii=False),flush=True)
        if n<len(ROLES): await asyncio.sleep(4)
    report={"count":len(results),"passed":sum(x["pass"] for x in results),
            "failed_roles":[x["role"] for x in results if not x["pass"]],
            "all_attempts_reported_cost_zero":len(attempts)==len(ROLES) and all(
                a["reported_cost_usd"]==0 for a in attempts),
            "input_tokens":sum(x["tokens_in"] or 0 for x in attempts),
            "output_tokens":sum(x["tokens_out"] or 0 for x in attempts),
            "seconds":round(time.monotonic()-started,1),
            "results":results,
            "scope":"Five earlier failing roles with owner-approved catalog text models. "
                    "Does not prove 16/16 reliability across multiple days."}
    Path("live-five-roles-result.json").write_text(
        json.dumps(report,ensure_ascii=False,indent=2),encoding="utf-8")
    print("LIVE5_RESULT "+json.dumps({k:v for k,v in report.items() if k!="results"},
                                   ensure_ascii=False),flush=True)
    if report["passed"]!=5 or not report["all_attempts_reported_cost_zero"]:
        raise AssertionError("Five-role real Free-only acceptance is not fully met")
    print("LIVE5_PASS: all 5 formerly failed roles completed at reported USD 0",
          flush=True)

if __name__=="__main__":
    try:
        asyncio.run(run())
    finally:
        os.environ.pop("OPENROUTER_API_KEY",None)
        home.cleanup()
