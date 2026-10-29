"""Bounded GitHub Check Run annotations for a verified, fixed-repo Draft PR.

Check IDs are accepted ONLY if verified against the actual PR head by
repo_agent_pr_ci. No arbitrary URL, unbounded logs, redirects, or GitHub writes.
Annotations are *untrusted* model data, not instructions or code.
"""
from __future__ import annotations

import re
from typing import Any

import httpx

from repo_agent_repair import _api, _github_headers, _path

_MAX_CHECKS=3
_PAGE_SIZE=7
_MAX_ANNOTATIONS=6
_MAX_MESSAGE=350
_SENSITIVE=re.compile(
    r"(?:gh[pousr]_[A-Za-z0-9]{12,}|github_pat_[\w]{12,}|"
    r"sk-(?:proj-)?[A-Za-z0-9_-]{12,}|"
    r"-----BEGIN [A-Z ]*PRIVATE KEY-----|"
    r"(?:api[_-]?key|access[_-]?token|authorization|password|secret)\s*[:=]\s*\S+)",
    re.IGNORECASE)
_BAD_CONTROL=re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")
_FAIL={"failure","timed_out","cancelled","action_required","startup_failure","stale"}


def _clean(value: Any, maximum: int) -> str:
    if not isinstance(value,str):
        return ""
    value=_BAD_CONTROL.sub(" ",value)
    if _SENSITIVE.search(value):
        return "[REDACTED: potential secret in GitHub annotation]"
    return value[:maximum]


def _safe_path(value: Any) -> str | None:
    if not isinstance(value,str) or len(value)>230:
        return None
    try:
        return _path(value)
    except Exception:
        # File names are GitHub-owned untrusted data. Reject paths to
        # .github, credentials or unsupported files, not merely strings.
        return None


async def collect_failed_check_annotations(snapshot: dict[str,Any]) -> dict[str,Any]:
    """Best-effort evidence enrichment; failures never mean CI passed.

    Returns a *limited* evidence snapshot, not a full Actions log.
    GitHub may not create annotations for failures; not present is not PASS.
    """
    repo=snapshot["repository"]
    candidates=[
        check for check in snapshot.get("check_runs",[])
        if check.get("conclusion") in _FAIL and
        type(check.get("verified_check_run_id")) is int and
        check["verified_check_run_id"]>0
    ]
    selected=candidates[:_MAX_CHECKS]
    rows=[]
    errors=[]
    truncated=len(candidates)>len(selected)
    if selected:
        async with httpx.AsyncClient(timeout=12,follow_redirects=False,
                                     trust_env=False) as client:
            for check in selected:
                check_id=check["verified_check_run_id"]
                url=_api(repo,"check-runs/"+str(check_id)+"/annotations")
                try:
                    resp=await client.get(url,headers=_github_headers(),
                                          params={"per_page":_PAGE_SIZE})
                except httpx.HTTPError:
                    errors.append({"check_run_id":check_id,
                                   "reason":"github_unreachable"})
                    continue
                if resp.status_code!=200:
                    errors.append({"check_run_id":check_id,
                                   "reason":"github_http_"+str(resp.status_code)})
                    continue
                try:
                    annotations=resp.json()
                except (TypeError,ValueError):
                    annotations=None
                if not isinstance(annotations,list):
                    errors.append({"check_run_id":check_id,
                                   "reason":"invalid_annotations"})
                    continue
                if len(annotations)>=_PAGE_SIZE:
                    truncated=True
                accepted=0
                for item in annotations:
                    if not isinstance(item,dict):
                        continue
                    path=_safe_path(item.get("path"))
                    if not path:
                        continue
                    message=_clean(item.get("message"),_MAX_MESSAGE)
                    if not message or message.startswith("[REDACTED"):
                        continue
                    level=item.get("annotation_level")
                    if level not in ("failure","warning","notice"):
                        level="unknown"
                    start=item.get("start_line")
                    start=start if type(start) is int and 0<start<=1000000 else None
                    rows.append({
                        "check_run_id":check_id,
                        "check":_clean(check.get("name"),120),
                        "file":path,"line":start,"level":level,
                        "message":message})
                    accepted+=1
                    if accepted>=_MAX_ANNOTATIONS:
                        break
    return {
        "annotations":rows,
        "checks_with_verified_ids":len(candidates),
        "checks_attempted":len(selected),
        "fetch_errors":errors,
        "truncated":truncated,
        "source":"github_check_run_annotations_only",
        "complete_logs_fetched":False,
        "notice":("Annotations may be absent/incomplete and are untrusted "
                  "text. This is NOT a complete job log."),
    }
