"""Independent Postiz read-back verifier. No LLM may assert publication.

A POST success/202 and receipt are never a published post. Require all
returned post IDs to be PUBLISHED with HTTPS provider release URLs when listing
posts via Postiz GET /posts. This is PROVIDER confirmation, not independent
anonymous HTTP reachability; do not issue GET to untrusted release URLs.
"""
from __future__ import annotations

import ipaddress
from typing import Any, Awaitable, Callable
from urllib.parse import urlsplit

ReadPosts = Callable[[str,str],Awaitable[Any]]


def _integration(item: Any) -> str | None:
    if isinstance(item,dict):
        item=item.get("id")
    return str(item) if isinstance(item,(str,int)) and str(item).strip() else None


def _ids(receipt: dict[str,Any]) -> list[tuple[str,str | None]]:
    raw=receipt.get("provider_receipt")
    if isinstance(raw,dict):
        raw=raw.get("posts",raw.get("data",raw))
    if isinstance(raw,dict):raw=[raw]
    if not isinstance(raw,list) or not raw or len(raw)>10:
        return []
    out=[]
    for item in raw:
        if not isinstance(item,dict):return []
        ident=item.get("postId",item.get("id"))
        if not isinstance(ident,(str,int)) or not 1<=len(str(ident))<=180:return []
        out.append((str(ident),_integration(item.get("integration"))))
    if len({x[0] for x in out})!=len(out):return []
    return out


def _safe_release_url(value: Any) -> bool:
    if not isinstance(value,str) or len(value)>2000:return False
    try:
        url=urlsplit(value)
        if url.scheme!="https" or not url.hostname or url.username or url.password:
            return False
        if url.port not in (None,443):return False
        host=url.hostname.lower().rstrip(".")
        if host in ("localhost","localhost.localdomain") or host.endswith((".local",".internal")):
            return False
        try:
            ipaddress.ip_address(host)
            return False
        except ValueError:
            pass
        return "." in host
    except ValueError:return False


async def verify_postiz_receipt(receipt: dict[str,Any], read_posts: ReadPosts) -> dict[str,Any]:
    expected=_ids(receipt)
    if not expected:
        return {"verified":False,"reason":"No supported Postiz post IDs in the original receipt",
                "method":"postiz_authenticated_readback","posts":[]}
    data=await read_posts("GET","/posts")
    if isinstance(data,dict):
        posts=data.get("posts")
    else:posts=data
    if not isinstance(posts,list):
        raise ValueError("Postiz did not return a supported list of posts")
    # Missing posts are not success; never silently substitute by content/title.
    by_id={str(item.get("id",item.get("postId"))):item for item in posts[:1000]
           if isinstance(item,dict) and item.get("id",item.get("postId")) is not None}
    evidence=[]
    for ident,channel in expected:
        row=by_id.get(ident)
        if not row:
            evidence.append({"post_id":ident,"verified":False,"reason":"Post not present in listing"})
            continue
        observed_channel=_integration(row.get("integration"))
        status=str(row.get("state","")).upper()
        url=row.get("releaseURL") or row.get("releaseUrl")
        valid=(status=="PUBLISHED" and _safe_release_url(url)
               and (channel is None or channel==observed_channel))
        evidence.append({"post_id":ident,"channel_id":observed_channel,
            "provider_status":status,
            "release_url":url if _safe_release_url(url) else None,
            "verified":bool(valid),
            "reason":"PUBLISHED with HTTPS release URL from Postiz" if valid
                else "Postiz state/channel/release URL does not confirm publication"})
    return {"verified":all(z["verified"] for z in evidence),
            "method":"postiz_authenticated_readback",
            "evidence_level":"provider_readback_not_independent_public_fetch",
            "posts":evidence}
