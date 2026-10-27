"""Launch every server in an mcp.json exactly as Quick Desktop would
(absolute command, only the file's env plus HOME), and list its tools.

Usage: uv run --no-project --isolated --with 'mcp<2' python test/verify_mcp_json.py quick-mcp.json
"""
import asyncio
import json
import os
import sys
import time

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

# A GUI app does not inherit the terminal PATH, so keep it minimal.
BASE_ENV = {"HOME": os.environ["HOME"], "PATH": "/usr/bin:/bin"}


async def check(name, cfg):
    params = StdioServerParameters(
        command=cfg["command"], args=cfg.get("args", []),
        env={**BASE_ENV, **cfg.get("env", {})}, cwd="/tmp",
    )
    t0 = time.time()
    try:
        async with stdio_client(params) as (r, w):
            async with ClientSession(r, w) as s:
                await asyncio.wait_for(s.initialize(), 120)
                tools = (await asyncio.wait_for(s.list_tools(), 60)).tools
                names = [t.name for t in tools]
                print(f"[OK]   {name}: {len(names)} tools in {time.time()-t0:.1f}s -> {', '.join(names)}")
                return True
    except BaseException as e:  # noqa: BLE001
        print(f"[FAIL] {name}: {type(e).__name__}: {e}")
        return False


async def main(path):
    servers = json.load(open(path))["mcpServers"]
    results = []
    for name, cfg in servers.items():  # sequential: mirrors a clean first start
        results.append(await check(name, cfg))
    sys.exit(0 if all(results) else 1)


asyncio.run(main(sys.argv[1]))
