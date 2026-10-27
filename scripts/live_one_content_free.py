"""One authorized live Content Agent request after Gemma Free HTTP 429.

Production task/worker, catalog-verified text model, no personal data,
one POST maximum, fail-closed if unknown cost or non-Free model.
"""
import asyncio, json, os, sys, tempfile
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
folder=tempfile.TemporaryDirectory(prefix="content-one-free-")
os.environ.update({
    "LHM_DB_PATH":str(Path(folder.name)/"test.sqlite3"),
    "LHM_LLM_PROVIDER":"openrouter","OPENROUTER_MODEL":"openrouter/free",
    "LHM_OPENROUTER_FREE_ONLY":"1","LHM_OPENROUTER_FREE_AUTO_SELECT":"1",
    "LHM_OPENROUTER_24H_LIMIT":"1","LHM_OPENROUTER_1M_LIMIT":"1",
    "DEEPSEEK_API_KEY":"","LHM_ALLOW_PRO":"0",
    "OPENROUTER_API_KEY":os.environ.get("OPENROUTER_TEST_API_KEY","")})
if not os.environ.get("OPENROUTER_API_KEY"):
    raise SystemExit("Missing disposable test secret; no live request made")
import app,core,worker
from openrouter_free_catalog import select_free_model
calls=[]
gateway=app.call_free_model
async def guarded(role,ws,instruction,context,model):
    assert role=="content" and model=="openrouter/free" and not calls
    item={"requested":"openrouter/free","verified_cost_zero":False,"actual_model":None}
    calls.append(item)
    payload=await gateway(role,ws,instruction,context,model)
    usage=payload.get("usage") or {}
    actual=payload.get("model")
    assert isinstance(actual,str) and actual.endswith(":free")
    assert usage.get("cost") is not None and float(usage["cost"])==0
    item.update(actual_model=actual,verified_cost_zero=True,
                input_tokens=usage.get("prompt_tokens"),
                output_tokens=usage.get("completion_tokens"))
    return payload

async def main():
    core.setup()
    selected=await select_free_model("content")
    assert selected.startswith("nvidia/") and selected.endswith(":free"),selected
    print("CONTENT_FREE_VERIFIED_SELECTION "+selected,flush=True)
    app.call_free_model=guarded
    with core.db() as db:
        db.execute("""INSERT INTO workspaces
          (id,name,industry,business_context,budget_usd,created_at)
          VALUES(?,?,?,?,?,?)""",
          ("one_content","Synthetic Content Test","general","Synthetic example",1.0,core.now()))
        task=app.task_record(db,"one_content","content",
            "Soạn mô tả 60 từ tiếng Việt cho phần mềm quản lý công việc giả định, "
            "không quảng cáo sai, không có email, không gọi công cụ ngoài.",
            "Only invented test scenario",model="openrouter/free")
    with core.db() as db:
        row=db.execute("SELECT id,workspace_id,role,model FROM tasks WHERE id=?",
                       (task["id"],)).fetchone()
    count=await worker.dispatch_agent_batch([row])
    assert count==1
    with core.db() as db:
        result=db.execute("SELECT status,error,result FROM tasks WHERE id=?",
                          (task["id"],)).fetchone()
    report={"role":"content","pass":result["status"]=="completed" and bool(result["result"]),
            "status":result["status"],"error_category":result["error"],
            "live_provider_requests":len(calls),
            "request":calls,
            "selected_verified_model":selected}
    Path("one-content-free-result.json").write_text(json.dumps(
        report,ensure_ascii=False,indent=2),encoding="utf-8")
    print("CONTENT_FREE_RESULT "+json.dumps({k:v for k,v in report.items() if k!="request"},
                                          ensure_ascii=False),flush=True)
    assert report["pass"] and len(calls)==1 and calls[0]["verified_cost_zero"]
    print("CONTENT_FREE_LIVE_PASS: true model and worker; one request; reported USD 0",flush=True)

if __name__=="__main__":
    try: asyncio.run(main())
    finally:
        os.environ.pop("OPENROUTER_API_KEY",None)
        folder.cleanup()
