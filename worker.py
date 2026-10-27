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
from openrouter_provider import model_configured
from tool_hub import execute_tool_job, configured, TOOL_ACTIONS
from workflow_builder import sync_ready_builder_runs
from computer_agent import process_one_computer_run, recover_computer_runs
from team_collaboration import sync_ready_team_sessions


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
        stale_tools = c.execute("SELECT id,workspace_id FROM tool_jobs WHERE status='executing' AND updated_at<?", (now()-600,)).fetchall()
        for job in stale_tools:
            c.execute("UPDATE tool_jobs SET status='needs_reconciliation',error=?,updated_at=? WHERE id=?",
                      ("Worker interrupted; remote outcome must be verified manually", now(), job['id']))
            audit(c,job['workspace_id'],"tool_job.stale","Reconciliation required after interrupted execution",job['id'])
        stale = c.execute("SELECT id,workspace_id FROM tasks WHERE status='running' AND updated_at<?",(now()-600,)).fetchall()
        for t in stale:
            c.execute("UPDATE tasks SET status='failed',reserve_usd=0,error='Stale run: manual review required',updated_at=? WHERE id=?",(now(),t["id"]))
            audit(c,t["workspace_id"],"task.stale","Manual review required after interruption",t["id"])


async def process_one_approved_tool() -> bool:
    """Execute at most one approved, configured external action per invocation.

    A job is claimed atomically inside execute_tool_job. Multiple workers cannot
    successfully execute the same approved job twice.
    """
    with db() as c:
        jobs = c.execute("SELECT id,action FROM tool_jobs WHERE status='approved' ORDER BY created_at,rowid LIMIT 10").fetchall()
    for job in jobs:
        provider = TOOL_ACTIONS.get(job['action'], {}).get('tool')
        if not provider or not configured(provider):
            continue
        try:
            await execute_tool_job(job['id'])
        except HTTPException as err:
            print(f"Tool job {job['id']}: {err.status_code} {err.detail}", flush=True)
        except Exception as err:
            print(f"Unexpected tool worker error: {type(err).__name__}", flush=True)
        return True
    return False


async def main() -> None:
    print("LHM Agent Worker running. Approved tool jobs execute once, with reconciliation on errors.", flush=True)
    last_recover=0
    while True:
        # This timestamp is observed by the office: never fake an online worker.
        with db() as c:
            c.execute("INSERT INTO worker_heartbeats(id,updated_at) VALUES('default',?) "
                      "ON CONFLICT(id) DO UPDATE SET updated_at=excluded.updated_at",(now(),))
        if time.time()-last_recover>60:
            await recover_stuck()
            recover_computer_runs()
            last_recover=time.time()
        # Finalize completed builder workflows: create review-gated tool requests
        # only. Tool Hub still requires a separate human approval.
        sync_ready_builder_runs()
        # Process completed Agent messages and create bounded coworker handoffs.
        sync_ready_team_sessions()
        # Computer sessions run one LLM/tool step per poll and pause for approvals.
        await process_one_computer_run()
        # Approved external actions run even without an AI API key.
        await process_one_approved_tool()
        with db() as c:
            rows=c.execute("""SELECT t.id FROM tasks t LEFT JOIN tasks dep ON t.depends_on=dep.id
                WHERE t.status='queued' AND t.next_run_at<=?
                AND (t.depends_on IS NULL OR dep.status='completed')
                ORDER BY t.created_at,t.rowid LIMIT 20""",(now(),)).fetchall()
        if not rows:
            await asyncio.sleep(2)
            continue
        for t in rows:
            with db() as c:
                queued = c.execute("SELECT model FROM tasks WHERE id=?", (t["id"],)).fetchone()
            # A missing provider key must not starve other providers' ready work.
            if not queued or not model_configured(queued["model"]):
                continue
            try:
                await execute_task(t["id"])
            except HTTPException as err:
                print(f"Task {t['id']}: {err.status_code} {err.detail}",flush=True)
            except Exception as err:
                print(f"Unexpected worker error: {type(err).__name__}",flush=True)
            # One AI request per loop prevents a burst from consuming free quota.
            break


if __name__=="__main__":
    asyncio.run(main())