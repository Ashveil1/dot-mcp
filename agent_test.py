#!/usr/bin/env python3
"""Quick client test: list tools + call list_dir, get_state, remember/recall."""
import asyncio, json, os, sys
from mcp.client.streamable_http import streamable_http_client
from mcp.client.session import ClientSession

URL = sys.argv[1] if len(sys.argv) > 1 else "http://127.0.0.1:33041/mcp/"

def _secret() -> str:
    if os.environ.get("MCP_PATH_SECRET", "").strip():
        return os.environ["MCP_PATH_SECRET"].strip()
    base = os.path.join(os.environ.get("XDG_CONFIG_HOME", os.path.expanduser("~/.config")))
    for name in ("owner_secret", "path_secret"):  # latter is legacy
        try:
            return open(os.path.join(base, "dot-mcp", name), encoding="utf-8").read().strip()
        except Exception:
            pass
    return ""

async def main():
    try:
        import httpx
    except ImportError:
        import httpx2 as httpx  # .venv/LiteLLM-era rename
    sec = _secret()
    client = httpx.AsyncClient(headers={"Authorization": f"Bearer {sec}"}) if sec else None
    async with streamable_http_client(URL, http_client=client) as streams:
        rs, ws = streams[0], streams[1]
        async with ClientSession(rs, ws) as s:
            await s.initialize()
            tools = await s.list_tools()
            names = [t.name for t in tools.tools]
            print("TOOLS:", names)
            r = await s.call_tool("get_state", {})
            print("STATE:", r.content[0].text[:500] if r.content else r)
            r = await s.call_tool("list_dir", {"path": "~", "limit": 5})
            print("LS:", r.content[0].text[:800] if r.content else r)
            r = await s.call_tool("remember", {"note": "agent test ok", "tag": "test"})
            print("REMEMBER:", r.content[0].text[:300] if r.content else r)
            r = await s.call_tool("recall", {"query": "agent test", "limit": 3})
            print("RECALL:", r.content[0].text[:800] if r.content else r)

asyncio.run(main())
