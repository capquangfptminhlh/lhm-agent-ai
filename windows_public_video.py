"""Windows public YouTube search through yt-dlp's library, not sys.executable -m.

Frozen PyInstaller EXEs are not Python interpreters, so Agent-Reach's CLI
yt-dlp backend can't start 'python -m yt_dlp' inside a frozen Windows app.
The same trusted helper process exposes ONLY bounded ytsearch queries.
"""
from __future__ import annotations

import json
import re
from typing import Any

_VIDEO_ID=re.compile(r"^[A-Za-z0-9_-]{11}$")


def search(query: str) -> dict[str,Any]:
    from yt_dlp import YoutubeDL
    from agent_reach_bridge import validate
    validate("research",{"channel":"youtube","query":query})
    # Avoid any external credential files, cookies or arbitrary destinations.
    opts={
        "quiet":True,"no_warnings":True,
        "skip_download":True,"extract_flat":True,
        "ignoreerrors":False,"noplaylist":True,
        "cachedir":False,"youtube_include_dash_manifest":False,
    }
    try:
        with YoutubeDL(opts) as ydl:
            result=ydl.extract_info("ytsearch5:"+query,download=False)
    except Exception:
        return {"schema_version":1,"ok":False,"channel":"youtube",
                "action":"search","backend":None,"items":[],
                "error":{"code":"UPSTREAM_BROKEN",
                         "message":"Public YouTube search failed; sign-in or rate limits may apply."}}
    entries=result.get("entries",[]) if isinstance(result,dict) else []
    items=[]
    for row in entries[:5]:
        if not isinstance(row,dict):
            continue
        vid=row.get("id")
        if not isinstance(vid,str) or not _VIDEO_ID.fullmatch(vid):
            continue
        title=row.get("title")
        author=row.get("channel") or row.get("uploader")
        items.append({"title":str(title)[:180] if title is not None else None,
                      "url":"https://www.youtube.com/watch?v="+vid,
                      "author":str(author)[:80] if author is not None else None,
                      "published_at":None,"text":None})
    return {"schema_version":1,"ok":True,"channel":"youtube",
            "action":"search","backend":"yt-dlp-python",
            "items":items,"error":None}
