"""Live 3D office projection over persisted LHM Agent OS state.

Read-only scene API. Every person/action in 3D derives from actual SQLite
task/worker/tool status. No generated demo tasks or synthetic success events.
"""
from __future__ import annotations

import asyncio
import hashlib
import json

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import FileResponse, StreamingResponse

from core import now
from office_api import ROOT, office_snapshot

router = APIRouter(tags=["Live 3D Office"])
SCHEMA_VERSION = 1


def build_world_snapshot(workspace_id: str = "demo") -> dict:
    raw = office_snapshot(workspace_id)
    departments = [
        {"id": f"room-{idx+1}", "name": name, "agent_ids": roles}
        for idx, (name, roles) in enumerate(raw["departments"].items())
    ]
    agents = []
    for room in departments:
        for role in room["agent_ids"]:
            item = raw["agents"][role]
            agents.append({
                "id": role, "name": item["name"], "description": item["description"],
                "department": room["name"], "room_id": room["id"],
                "status": item["status"], "task_id": item["task_id"],
                "task_title": (item["task_title"] or "")[:240],
                "total_tasks": item["total"], "completed_tasks": item["completed"],
                "updated_at": item["updated_at"],
            })
    return {
        "schema_version": SCHEMA_VERSION,
        "mode": "live_backend",
        "source": "LHM Agent OS persisted tasks and worker heartbeats",
        "generated_at": now(),
        "workspace": {
            "id": raw["workspace"]["id"], "name": raw["workspace"]["name"],
            "industry": raw["workspace"]["industry"],
        },
        "departments": departments,
        "agents": agents,
        "tasks": [
            {"id": x["id"], "role": x["role"], "instruction": x["instruction"][:240],
             "status": x["status"], "updated_at": x["updated_at"]}
            for x in raw["tasks"][:60]
        ],
        "events": [
            {"id": x["id"], "event_type": x["event_type"], "summary": x["summary"],
             "task_id": x["task_id"], "timestamp": x["created_at"]}
            for x in raw["events"][:35]
        ],
        "worker": raw["worker"],
        "tool_summary": raw["tool_summary"],
        "financials": raw["financials"],
        "statuses": raw["statuses"],
    }


@router.get("/office3d")
def office3d():
    return FileResponse(ROOT / "static" / "office3d.html", media_type="text/html")


@router.get("/office3d/scene.css")
def office3d_css():
    return FileResponse(ROOT / "static" / "office3d.css", media_type="text/css")


@router.get("/office3d/scene.js")
def office3d_js():
    return FileResponse(ROOT / "static" / "office3d.js", media_type="text/javascript")


@router.get("/api/office3d/snapshot")
def world_snapshot(workspace_id: str = "demo"):
    return build_world_snapshot(workspace_id)


@router.get("/api/office3d/events")
async def world_events(request: Request, workspace_id: str = "demo"):
    """SSE snapshots on *actual state change*, with heartbeat comments.

    Streaming uses same Basic/Bearer authentication middleware as other routes.
    No fake 'task started/completed' event is emitted without a DB state change.
    """
    # Validate before returning a 200 streaming response.
    build_world_snapshot(workspace_id)

    async def stream():
        previous = None
        yield "retry: 4000\n\n"
        while not await request.is_disconnected():
            try:
                frame = await asyncio.to_thread(build_world_snapshot, workspace_id)
                # generated_at is not part of change detection. Idle scenes
                # should not trigger UI changes or fictitious activity.
                identity = {k: v for k, v in frame.items() if k != "generated_at"}
                # The heartbeat timestamp moves every worker tick; only its
                # online/offline transition is a meaningful scene change.
                identity["worker"] = {"online": frame["worker"]["online"]}
                digest = hashlib.sha256(json.dumps(
                    identity, sort_keys=True, ensure_ascii=False, separators=(",", ":")
                ).encode("utf-8")).hexdigest()
                if digest != previous:
                    previous = digest
                    payload = json.dumps(frame, ensure_ascii=False, separators=(",", ":"))
                    yield f"event: snapshot\ndata: {payload}\n\n"
                else:
                    yield ": heartbeat\n\n"
            except (HTTPException, ValueError, OSError):
                # Connectivity errors must be explicit, not synthetic idle.
                yield "event: offline\ndata: {\"reason\":\"Backend snapshot unavailable\"}\n\n"
                break
            await asyncio.sleep(3)

    return StreamingResponse(stream(), media_type="text/event-stream",
                             headers={"Cache-Control": "no-store, no-cache",
                                      "X-Accel-Buffering": "no",
                                      "Connection": "keep-alive"})
