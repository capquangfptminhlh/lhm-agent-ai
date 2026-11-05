"""Grounded GitHub tool stack for 16 agents; catalog ≠ an installed service.

The returned statuses deliberately distinguish existing adapter code, an
optional read-only connector, and repositories chosen for later evaluation.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from fastapi import APIRouter
from fastapi.responses import FileResponse

from core import ROLES

router = APIRouter(tags=["Curated GitHub Tool Stack"])
CATALOG_PATH=Path(__file__).parent/"templates"/"github_tool_stack.json"


def _catalog() -> dict[str,Any]:
    data=json.loads(CATALOG_PATH.read_text(encoding="utf-8"))
    expected=set(ROLES)
    ids=set()
    for item in data["tools"]:
        if item["id"] in ids or not set(item["roles"])<=expected:
            raise RuntimeError("Invalid curated tool stack or role")
        ids.add(item["id"])
    return data


@router.get("/agents/github-stack")
def github_stack():
    catalog=_catalog()
    tools=catalog["tools"]
    # Never infer provider availability just because an adapter exists.
    return {
        "version":catalog["version"],
        "selection_principles":catalog["selection_principles"],
        "source_note":catalog["source"],
        "repo_count":len(tools),
        "role_count":len(ROLES),
        "provider_connections_verified":False,
        "auto_install_enabled":False,
        "tools":tools,
        "roles":[{
            "role":role,"name":ROLES[role][0],
            "tools":[{"id":x["id"],"status":x["status"]}
                     for x in tools if role in x["roles"]]
        } for role in ROLES]
    }


@router.get("/agents/github-stack/ui")
def github_stack_ui():
    return FileResponse(Path(__file__).parent/"static"/"github_stack.html",
                        media_type="text/html")
