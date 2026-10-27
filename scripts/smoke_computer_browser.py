"""Exercise LHM Computer Runner's real Playwright/browser path on CI Chrome.

All web content is served by in-process Playwright route fulfilment; no
production accounts, third-party sites, paid AI or external publishing.
"""
from __future__ import annotations
import asyncio
import base64
import os
import tempfile
from pathlib import Path

from playwright.async_api import async_playwright

import computer_executor as executor

PAGE = """<!doctype html><html><head><title>Local Agent Browser Smoke</title></head>
<body><h1>Computer browser is real</h1>
<input id="search" name="query" type="text" placeholder="Gõ nội dung">
<button id="submit" onclick="document.getElementById('result').textContent =
  'Agent typed: ' + document.getElementById('search').value">Test action</button>
<p id="result">Not yet</p></body></html>"""

async def smoke():
    with tempfile.TemporaryDirectory(prefix="lhm-browser-computer-") as temp:
        executor.ROOT=Path(temp)
        os.environ["LHM_COMPUTER_ALLOWED_HOSTS"]="example.com"
        os.environ["LHM_COMPUTER_BROWSER_CHANNEL"]="chrome"
        ws,role="demo","research"
        # Create the persistent context through the actual production tool path.
        start=await executor.perform(executor.ToolRequest(
           workspace_id=ws,role=role,tool="browser_snapshot",args={}))
        assert "url" in start, start
        key=ws+"/"+role
        context=executor.BROWSER_SESSIONS[key]
        async def fixture(route):
            await route.fulfill(status=200,content_type="text/html",body=PAGE)
        # Fulfil a trusted host locally; never call the public internet.
        await context.route("https://example.com/**",fixture)
        async def action(tool,args):
            return await executor.perform(executor.ToolRequest(
                workspace_id=ws,role=role,tool=tool,args=args))
        screen=await action("browser_open",{"url":"https://example.com/agent-test"})
        assert screen["title"]=="Local Agent Browser Smoke",screen
        assert "Computer browser is real" in screen["text"]
        await action("browser_fill",{"selector":"#search","text":"LHM live computer"})
        await action("browser_click",{"selector":"#submit"})
        await action("browser_scroll",{"direction":"down","pixels":120})
        observed=await action("browser_snapshot",{})
        assert "Agent typed: LHM live computer" in observed["text"],observed
        capture=await action("browser_screenshot",{})
        png=base64.b64decode(capture["png_base64"])
        assert png.startswith(b"\x89PNG\r\n\x1a\n") and len(png)>1000, len(png)
        print("Computer Runner real browser smoke PASS:",
              {"title":screen["title"],"typed_value":True,"png_bytes":len(png)})
        await context.close()
        executor.BROWSER_SESSIONS.pop(key,None)
        if executor._PLAYWRIGHT is not None:
            await executor._PLAYWRIGHT.stop()
            executor._PLAYWRIGHT=None

if __name__=="__main__":
    asyncio.run(smoke())
