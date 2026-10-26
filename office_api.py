"""Office UI integration: all visible Agent states are calculated from persisted tasks.
No simulated progress / activity: idle agents have no active work.
"""
from __future__ import annotations

from pathlib import Path
from fastapi import APIRouter, HTTPException
from fastapi.responses import FileResponse
from core import CATALOG, DEPARTMENTS, ROLES, WORKFLOWS, db, financials, workspace, now

router = APIRouter()
ROOT = Path(__file__).parent


@router.get('/office')
def office():
    return FileResponse(ROOT / 'static' / 'office.html', media_type='text/html')


@router.get('/office/office.css')
def office_styles():
    return FileResponse(ROOT / 'static' / 'office.css', media_type='text/css')


@router.get('/office/office.js')
def office_script():
    return FileResponse(ROOT / 'static' / 'office.js', media_type='text/javascript')


@router.get('/api/office/snapshot')
def office_snapshot(workspace_id: str = 'demo'):
    """Single query boundary so realtime visual view and management share data."""
    with db() as c:
        w = workspace(c, workspace_id)
        if not w:
            raise HTTPException(404, 'Không tìm thấy doanh nghiệp')
        rows = c.execute(
            "SELECT * FROM tasks WHERE workspace_id=? ORDER BY created_at DESC,rowid DESC LIMIT 200",
            (workspace_id,)).fetchall()
        tasks = [dict(row) for row in rows]
        events = [dict(row) for row in c.execute(
            "SELECT * FROM events WHERE workspace_id=? ORDER BY id DESC LIMIT 50", (workspace_id,)
        ).fetchall()]
        leads = [dict(row) for row in c.execute(
            "SELECT * FROM leads WHERE workspace_id=? ORDER BY score DESC,created_at DESC LIMIT 80",
            (workspace_id,)
        ).fetchall()]
        tool_counts = {row['status']: row['amount'] for row in c.execute(
            "SELECT status,COUNT(*) amount FROM tool_jobs WHERE workspace_id=? GROUP BY status", (workspace_id,)
        ).fetchall()}
        tool_recent = [dict(row) for row in c.execute(
            "SELECT id,action,status,created_at,updated_at FROM tool_jobs WHERE workspace_id=? ORDER BY created_at DESC LIMIT 8", (workspace_id,)
        ).fetchall()]
        heartbeats = c.execute("SELECT updated_at FROM worker_heartbeats WHERE id='default'").fetchone()
        agent_states = {}
        for role, (name, desc) in ROLES.items():
            own = [t for t in tasks if t['role'] == role]
            active = next((t for st in ('running','awaiting_approval','queued','blocked_budget',
                                       'blocked_dependency','failed') for t in own if t['status'] == st), None)
            current = active or (own[0] if own else None)
            if not current:
                status = 'idle'
            elif active:
                status = current['status']
            else:
                status = 'completed' if current['status'] == 'completed' else 'idle'
            agent_states[role] = {
                'name': name, 'description': desc, 'status': status,
                'task_id': current['id'] if current else None,
                'task_title': current['instruction'] if current else None,
                'updated_at': current['updated_at'] if current else None,
                'total': len(own),
                'completed': sum(t['status'] == 'completed' for t in own),
                'running': sum(t['status'] == 'running' for t in own),
            }
        statuses={}
        for t in tasks:
            statuses[t['status']] = statuses.get(t['status'],0)+1
        return {
            'workspace': dict(w),
            'financials':financials(c,workspace_id),
            'departments': DEPARTMENTS,
            'agents':agent_states,
            'tasks':tasks,
            'events':events,
            'leads':leads,
            'statuses':statuses,
            'catalog': {key:{'label':value['label']} for key,value in CATALOG.items()},
            'workflows':WORKFLOWS,
            'tool_summary': {'counts':tool_counts,'recent':tool_recent},
            'worker':{
                'online':bool(heartbeats and (now()-int(heartbeats['updated_at']) < 15)),
                'last_heartbeat':int(heartbeats['updated_at']) if heartbeats else None,
            },
        }