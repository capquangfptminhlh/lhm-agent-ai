"""One-shot real OpenRouter smoke for five formerly failing LHM roles.

No account/contact data; real app.execute_task + verified free text catalog
selection even with auto flag disabled. Exactly one attempt per role, no retry.
Never print raw model responses, errors from providers, prompts or secrets.
"""
import asyncio, json, os, sys, tempfile, time
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
temp=tempfile.TemporaryDirectory(prefix="lhm-five-free-")
os.environ.update({
    "LHM_DB_PATH":str(Path(temp.name)/"test.sqlite3"),
    "LHM_LLM_PROVIDER":"openrouter","OPENROUTER_MODEL":"openrouter/free",
    "LHM_OPENROUTER_FREE_ONLY":"1","LHM_OPENROUTER_FREE_AUTO_SELECT":"0",
    "OPENROUTER_API_KEY":os.environ.get("OPENROUTER_TEST_API_KEY",""),
    "DEEPSEEK_API_KEY":"","LHM_ALLOW_PRO":"0",
    "LHM_OPENROUTER_24H_LIMIT":"5","LHM_OPENROUTER_1M_LIMIT":"10",
})
if not os.environ["OPENROUTER_API_KEY"]:
    raise SystemExit("No disposable GitHub Free test key; LIVE NOT RUN")
import core,app,worker
ROLES=("director","lead","content","ads","finance")

async def run():
    core.setup()
    assert len(core.ROLES)==16 and core.MODEL=="openrouter/free"
    recorded=[]
    gateway=app.call_free_model
    async def checked(role,ws,prompt,ctx,requested):
        assert requested=="openrouter/free" and len(recorded)<5
        row={"role":role,"called":True,"verified_free":False,"verified_zero_cost":False}
        recorded.append(row)
        reply=await gateway(role,ws,prompt,ctx,requested)
        model=reply.get("model")
        usage=reply.get("usage") or {}
        row["verified_free"]=isinstance(model,str) and model.endswith(":free")
        row["model"]=model if row["verified_free"] else "UNVERIFIED"
        costs=[v for v in (usage.get("cost"),reply.get("cost")) if v is not None]
        row["verified_zero_cost"]=bool(costs) and all(str(x) in ("0","0.0","0.00") for x in costs)
        row["input_tokens"]=usage.get("prompt_tokens")
        row["output_tokens"]=usage.get("completion_tokens")
        return reply
    app.call_free_model=checked
    with core.db() as c:
        c.execute("INSERT INTO workspaces (id,name,industry,business_context,budget_usd,created_at) VALUES (?,?,?,?,?,?)",
                  ("fivefree","Live Free Test","general","Synthetic software quality scenario",1.0,core.now()))
        tasks=[]
        for role in ROLES:
            item=app.task_record(c,"fivefree",role,
                "Đề xuất một cải tiến chất lượng cho phần mềm giả định, trả lời tiếng Việt ngắn gọn, "
                "không có dữ liệu khách hàng, tên cá nhân, email, số điện thoại hay quảng cáo thật.",
                "Nhiệm vụ test nội bộ",model="openrouter/free",idem="five-"+role)
            tasks.append((role,item["id"]))
    start=time.monotonic()
    for role,tid in tasks:
        with core.db() as c:
            t=c.execute("SELECT id,role,model,workspace_id FROM tasks WHERE id=?",(tid,)).fetchone()
        await worker.dispatch_agent_batch([t])
        with core.db() as c:
            row=c.execute("SELECT status FROM tasks WHERE id=?",(tid,)).fetchone()
        status=row["status"]
        print("FIVE_ROLE "+json.dumps({"role":role,"status":status}),flush=True)
        await asyncio.sleep(2)
    with core.db() as c:
        rows=c.execute("SELECT role,status FROM tasks WHERE workspace_id='fivefree' ORDER BY rowid").fetchall()
    summary={
        "roles":len(ROLES),"requests":len(recorded),
        "passed":sum(r["status"]=="completed" for r in rows),
        "failed":[r["role"] for r in rows if r["status"]!="completed"],
        "models":[x.get("model","UNVERIFIED") for x in recorded],
        "all_requests_verified_free":all(x["verified_free"] for x in recorded),
        "all_requests_reported_zero_cost":all(x["verified_zero_cost"] for x in recorded),
        "elapsed_seconds":round(time.monotonic()-start,1),
        "scope":"5 formerly failed roles, individual tasks (not team concurrency)"
    }
    Path("five-free-roles-result.json").write_text(json.dumps(summary,ensure_ascii=False,indent=2))
    print("FIVE_ROLE_SUMMARY "+json.dumps(summary,ensure_ascii=False),flush=True)
    assert len(recorded)==5 and summary["passed"]==5 and summary["all_requests_verified_free"] and summary["all_requests_reported_zero_cost"], "5/5 not verified"
    print("FIVE_ROLE_LIVE_PASS",flush=True)

if __name__=="__main__":
    try: asyncio.run(run())
    finally: os.environ.pop("OPENROUTER_API_KEY",None);temp.cleanup()
