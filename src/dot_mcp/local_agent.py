#!/usr/bin/env python3
"""Local-only dot agent: ChatGPT model (API key) + MCP server on this machine.
No tunnel, no sub needed. Server: http://127.0.0.1:33041/mcp

  export OPENAI_API_KEY=sk-...
  .venv/bin/python local_agent.py            # chat loop
  .venv/bin/python local_agent.py --list     # list MCP tools only (no key needed)
  OPENAI_MODEL=gpt-4o-mini .venv/bin/python local_agent.py
"""
from __future__ import annotations
import asyncio, json, os, sys

MCP_URL = os.environ.get("MCP_URL", "http://127.0.0.1:33041/mcp/")
MODEL = os.environ.get("OPENAI_MODEL", "gpt-4o-mini")

def _owner_secret() -> str:
    if os.environ.get("MCP_PATH_SECRET", "").strip():
        return os.environ["MCP_PATH_SECRET"].strip()
    from dot_mcp.lifecycle import state_dir
    for name in ("owner_secret", "path_secret"):
        try:
            s = (state_dir() / name).read_text(encoding="utf-8").strip()
            if s:
                return s
        except OSError:
            pass
    return ""

def _http_client():
    try:
        import httpx
    except ImportError:
        import httpx2 as httpx  # .venv/LiteLLM-era rename
    sec = _owner_secret()
    if not sec: return None
    # NOTE: no custom User-Agent — httpx2 in .venv duplicates it and the v2 server rejects the request.
    return httpx.AsyncClient(headers={"Authorization": f"Bearer {sec}"})
SYSTEM = ("You are the user's dot on their own machine. "
  "At start call get_state, poll_inbox, recall. "
  "Save important facts with remember, progress with add_update, "
  "visible results with notify_user. "
  "Relative paths resolve from $HOME.")

async def mcp_tools():
    from mcp.client.streamable_http import streamable_http_client
    from mcp.client.session import ClientSession
    async with streamable_http_client(MCP_URL, http_client=_http_client()) as streams:
        rs, ws = streams[0], streams[1]
        async with ClientSession(rs, ws) as s:
            await s.initialize()
            t = await s.list_tools()
            return [{"name": x.name, "desc": x.description or "",
                     "schema": getattr(x, "input_schema", None) or getattr(x, "inputSchema", None)} for x in t.tools]

async def main():
    if "--list" in sys.argv:
        for t in await mcp_tools():
            print(f"- {t['name']}: {t['desc'][:80]}")
        return
    key = os.environ.get("OPENAI_API_KEY", "")
    if not key:
        print("need OPENAI_API_KEY"); raise SystemExit(1)
    from openai import OpenAI
    client = OpenAI(base_url=os.environ.get("OPENAI_BASE_URL") or None)
    defs = await mcp_tools()
    tools = [{"type": "function", "function": {
        "name": d["name"], "description": d["desc"][:500],
        "parameters": d["schema"] or {"type": "object", "properties": {}}}}
        for d in defs]
    from mcp.client.streamable_http import streamable_http_client
    from mcp.client.session import ClientSession
    async with streamable_http_client(MCP_URL, http_client=_http_client()) as streams:
        rs, ws = streams[0], streams[1]
        async with ClientSession(rs, ws) as s:
            await s.initialize()
            msgs = [{"role": "system", "content": SYSTEM}]
            print(f"dot local ready [{MODEL}] ({len(tools)} tools). exit to quit.")
            while True:
                try: q = input("\n> ").strip()
                except (EOFError, KeyboardInterrupt): break
                if q in ("exit", "quit"): break
                if not q: continue
                msgs.append({"role": "user", "content": q})
                for _ in range(10):
                    r = client.chat.completions.create(model=MODEL, messages=msgs, tools=tools or None)
                    m = r.choices[0].message
                    if not m.tool_calls:
                        print(m.content); msgs.append({"role": "assistant", "content": m.content or ""}); break
                    msgs.append({"role": "assistant", "content": m.content or "", "tool_calls": [
                        {"id": c.id, "type": "function",
                         "function": {"name": c.function.name, "arguments": c.function.arguments}}
                        for c in m.tool_calls]})
                    for c in m.tool_calls:
                        name = c.function.name
                        try: args = json.loads(c.function.arguments or "{}")
                        except Exception: args = {}
                        try:
                            res = await s.call_tool(name, args)
                            out = "\n".join(
                                getattr(b, "text", str(b)) for b in (res.content or []))[:6000]
                        except Exception as e:
                            out = f"ERROR: {e}"
                        print(f"[{name}] {out[:500]}")
                        msgs.append({"role": "tool", "tool_call_id": c.id, "content": out})

asyncio.run(main())
