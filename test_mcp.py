"""
Talks to the GRUS Room MCP server exactly as Alexa+ would: the official
MCP client, Streamable HTTP, bearer token. Read-only: it does not open a
room or log anything, so it leaves no rows in the timeline.

    python test_mcp.py                       # local: http://127.0.0.1:8000/mcp
    python test_mcp.py https://.../mcp       # deployed
"""
import asyncio
import os
import sys

import httpx
from dotenv import load_dotenv
from mcp.client.session import ClientSession
from mcp.client.streamable_http import streamable_http_client

load_dotenv()
URL = sys.argv[1] if len(sys.argv) > 1 else "http://127.0.0.1:8000/mcp"
TOKEN = os.environ.get("GRUS_MCP_TOKENS", "").split(",")[0].split("=")[0]


async def main():
    async with httpx.AsyncClient() as h:
        r = await h.post(URL, json={})
        print(f"Without a token: {r.status_code} (should be 401)")

    async with httpx.AsyncClient(
            headers={"Authorization": f"Bearer {TOKEN}"}, timeout=120) as client, \
            streamable_http_client(URL, http_client=client) as (rd, wr, *_):
        async with ClientSession(rd, wr) as s:
            init = await s.initialize()
            print(f"Protocol {init.protocol_version}, server "
                  f"{init.server_info.name}")
            tools = (await s.list_tools()).tools
            print(f"{len(tools)} tools\n")

            async def call(name, args):
                r = await s.call_tool(name, args)
                print(f"[{name}]{' ERROR' if r.is_error else ''}\n  "
                      f"{r.content[0].text}\n")
                return r

            board = await call("triage_board", {"limit": 3})
            first = board.structured_content["patients"][0]["ending"]
            await call("get_alerts", {"patient": first})
            await call("ask_record", {"patient": first,
                                      "question": "What was the INR on arrival?"})
            await call("verify_record", {"patient": first})


if sys.platform == "win32":
    # Windows' default event loop prints a harmless "Event loop is closed"
    # warning at exit; the selector loop doesn't.
    asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())
asyncio.run(main())