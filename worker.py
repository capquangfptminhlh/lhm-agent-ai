"""Actual background worker: consumes queued tasks, retries transient errors.
Usage: python worker.py   (separate terminal; keep running with a process manager).
"""
from __future__ import annotations

import asyncio
import os
import time
from fastapi import HTTPException
from core import db, now, audit
from app import execute_task


async def recover_stuck() -> None:
    # Stale running requests may have generated provider cost; do not retry blindly
    # to avoid duplicate work/billing. Human checks before retry.
    with db() as c:
        c.execute("BEGIN IMMEDIATE")
        blockers=c.execute("""SELECT t.id,t.workspace_id FROM tasks t
            JOIN tasks d ON t.depends_on=d.id WHERE t.status='queued'
            AND d.status IN ('failed','blocked_budget','blocked_dependency','cancelled')""").fetchall()
        for t in blockers:
            c.execute("UPDATE tasks SET status='blocked_dependency',updated_at=? WHERE id=?",(now(),t['id']))
            audit(c,t['workspace_id'],"task.dependency_blocked","Predecessor failed or stopped",t['id'])
        stale = c.execute("SELECT id,workspace_id FROM tasks WHERE status='running' AND updated_at<?",(now()-600,)).fetchall()
        for t in stale:
            c.execute("UPDATE tasks SET status='failed',reserve_usd=0,error='Stale run: manual review required',updated_at=? WHERE id=?",(now(),t["id"]))
            audit(c,t["workspace_id"],"task.stale","Manual review required after interruption",t["id"])


async def main() -> None:
    print("LHM Agent Worker running. Ctrl+C stops. No external posting is implemented.", flush=True)
    last_recover=0
    while True:
        # This timestamp is observed by the office: never fake an online worker.
        with db() as c:
            c.execute("INSERT INTO worker_heartbeats(id,updated_at) VALUES('default',?) "
                      "ON CONFLICT(id) DO UPDATE SET updated_at=excluded.updated_at",(now(),))
        if time.time()-last_recover>60:
            await recover_stuck()
            last_recover=time.time()
        if not os.getenv("DEEPSEEK_API_KEY"):
            await asyncio.sleep(3)
            continue
        with db() as c:
            rows=c.execute("""SELECT t.id FROM tasks t LEFT JOIN tasks dep ON t.depends_on=dep.id
                WHERE t.status='queued' AND t.next_run_at<=?
                AND (t.depends_on IS NULL OR dep.status='completed')
                ORDER BY t.created_at,t.rowid LIMIT 1""",(now(),)).fetchall()
        if not rows:
            await asyncio.sleep(2)
            continue
        for t in rows:
            try:
                await execute_task(t["id"])
            except HTTPException as err:
                print(f"Task {t['id']}: {err.status_code} {err.detail}",flush=True)
            except Exception as err:
                print(f"Unexpected worker error: {type(err).__name__}",flush=True)


if __name__=="__main__":
    asyncio.run(main())