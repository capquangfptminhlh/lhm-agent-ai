"""Read-only Agent Office worker-status bridge for a trusted local installation.

Agent Office's /office/workers hook endpoint is *not* a public HTTP API: it
requires the identity and token of a worker running inside that office floor.
This pilot never hires, prompts, sends home or executes external workers.
"""
from __future__ import annotations

import os
import re
from pathlib import Path
from urllib.parse import urlsplit

import httpx
from fastapi import APIRouter
from fastapi.responses import FileResponse

router = APIRouter(tags=["Agent Office Read-only Bridge"])
ROOT = Path(__file__).parent
MAX_RESPONSE_BYTES = 256_000
MAX_WORKERS = 64
ALLOWED_STATUSES = {"starting", "idle", "working", "done", "needs_input", "offline", "stopping", "error"}


def _config() -> tuple[str | None, str | None, str | None, str]:
    """Fail closed. Do not accept hostnames, arbitrary paths, redirects or public URLs."""
    base = (os.getenv("LHM_AGENT_OFFICE_HOOK_URL") or os.getenv("AGENT_OFFICE_HOOK_URL") or "").strip()
    worker = (os.getenv("LHM_AGENT_OFFICE_WORKER_ID") or os.getenv("AGENT_OFFICE_WORKER_ID") or "").strip()
    token = (os.getenv("LHM_AGENT_OFFICE_HOOK_TOKEN") or os.getenv("AGENT_OFFICE_HOOK_TOKEN") or "").strip()
    if not any((base, worker, token)):
        return None, None, None, "not_configured"
    if not all((base, worker, token)):
        return None, None, None, "incomplete_configuration"
    try:
        parsed = urlsplit(base)
        valid = (parsed.scheme == "http" and parsed.hostname == "127.0.0.1"
                 and parsed.port is not None and 1024 <= parsed.port <= 65535
                 and parsed.path in ("", "/") and not parsed.query and not parsed.fragment
                 and parsed.username is None and parsed.password is None
                 and parsed.netloc == f"127.0.0.1:{parsed.port}")
    except ValueError:
        valid = False
    if not valid or not re.fullmatch(r"[A-Za-z0-9_-]{1,128}", worker) or len(token) > 512:
        return None, None, None, "invalid_configuration"
    return f"http://127.0.0.1:{parsed.port}", worker, token, "configured_not_verified"


def _short(value: object, maximum: int = 160) -> str:
    return value[:maximum] if isinstance(value, str) else ""


def _present(data: object) -> dict:
    if not isinstance(data, dict) or not isinstance(data.get("workers"), list):
        raise ValueError("Unsupported Agent Office response")
    floor = data.get("floor")
    if not isinstance(floor, dict):
        raise ValueError("Missing floor")
    workers = []
    for raw in data["workers"][:MAX_WORKERS]:
        if not isinstance(raw, dict):
            continue
        status = raw.get("status")
        # Future upstream statuses are not guessed to be idle or successful.
        workers.append({
            "id": _short(raw.get("id"), 128),
            "name": _short(raw.get("name"), 80),
            "kind": raw.get("kind") if raw.get("kind") in ("agent", "shell") else "unknown",
            "provider": _short(raw.get("provider"), 48),
            "model": _short(raw.get("model"), 80),
            "desk": _short(raw.get("desk"), 80),
            "status": status if isinstance(status, str) and status in ALLOWED_STATUSES else "unknown",
            "task": _short(raw.get("task"), 160),
            "activity": _short(raw.get("activity"), 160),
            "pull_request": raw.get("pr", {}).get("number")
                if isinstance(raw.get("pr"), dict) and isinstance(raw["pr"].get("number"), int)
                else None,
        })
    return {
        "state": "connected",
        "source": "Agent Office loopback hook /office/workers",
        "scope": "one_authorized_floor",
        "mode": "read_only",
        "floor": {
            "name": _short(floor.get("name"), 100),
            "repo": _short(floor.get("repo"), 160),
            "branch": _short(floor.get("branch"), 120),
        },
        "workers": workers,
        "truncated": len(data["workers"]) > MAX_WORKERS,
        "external_execution_enabled": False,
        "note": "Agent Office status only; it does not prove LHM tasks were executed or business work verified.",
    }


@router.get("/agent-office")
def agent_office_page():
    return FileResponse(ROOT / "static" / "agent_office_bridge.html", media_type="text/html")


@router.get("/api/agent-office/workers")
async def agent_office_workers():
    base, worker, token, state = _config()
    if state != "configured_not_verified":
        return {"state": state, "mode": "read_only", "workers": [], "external_execution_enabled": False}
    try:
        async with httpx.AsyncClient(timeout=3.0, trust_env=False, follow_redirects=False) as client:
            response = await client.get(
                base + "/office/workers", params={"worker": worker},
                headers={"Authorization": "Bearer " + token, "Accept": "application/json"},
            )
        if response.status_code != 200:
            return {"state": "authorization_failed" if response.status_code in (401, 403)
                    else "unavailable", "mode": "read_only", "workers": [],
                    "external_execution_enabled": False}
        if len(response.content) > MAX_RESPONSE_BYTES:
            return {"state": "invalid_response", "mode": "read_only",
                    "workers": [], "external_execution_enabled": False}
        return _present(response.json())
    except (httpx.HTTPError, ValueError, TypeError, UnicodeError):
        return {"state": "unavailable", "mode": "read_only",
                "workers": [], "external_execution_enabled": False}
