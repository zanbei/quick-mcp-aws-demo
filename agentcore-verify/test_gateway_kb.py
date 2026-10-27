"""Gateway test for the Bedrock Knowledge Base (RAG) target: Cognito JWT -> AgentCore Gateway -> Lambda -> Bedrock.

Prints only sizes and short previews so knowledge base content is not copied around.
Usage: uv run --no-project --isolated --with 'mcp<2' --with boto3 python test_gateway_kb.py
"""
import asyncio
import json
import os
import time

os.environ.setdefault("TARGET_NAME", "bedrock-kb-lambda-target")
import test_gateway as g  # noqa: E402
from mcp import ClientSession  # noqa: E402
from mcp.client.streamable_http import streamablehttp_client  # noqa: E402


async def main():
    url = g.gateway_url()
    cfg = json.loads(g.session.client("secretsmanager").get_secret_value(SecretId=g.SECRET_NAME)["SecretString"])
    headers = {"Authorization": f"Bearer {g.get_token(cfg)}"}
    async with streamablehttp_client(url, headers=headers, timeout=180) as (r, w, _):
        async with ClientSession(r, w) as s:
            await s.initialize()
            tools = (await s.list_tools()).tools
            names = sorted(t.name for t in tools)
            print(f"tools/list -> {len(tools)} tools")
            print("  kb tools:", [n for n in names if n.startswith("bedrock-kb")])
            for t in tools:
                if t.name.startswith("bedrock-kb"):
                    sch = getattr(t, "inputSchema", None) or t.input_schema
                    bad = [k for k, v in sch.get("properties", {}).items() if isinstance(v.get("required"), bool)]
                    print(f"  {t.name}: required={sch.get('required', [])} draft3-style-required={bad}")

            for label, name, args in (
                ("QueryKnowledgeBases (default KB, no id passed)", "QueryKnowledgeBases",
                 {"query": "how can I get help", "number_of_results": 3}),
                ("QueryKnowledgeBases (different question)", "QueryKnowledgeBases",
                 {"query": "accessibility features for vision", "number_of_results": 2}),
            ):
                t0 = time.time()
                res = await s.call_tool(f"{g.TARGET}___{name}", args)
                text = "".join(getattr(c, "text", "") for c in res.content)
                err = getattr(res, "isError", getattr(res, "is_error", None))
                print(f"\n--- {label} ({time.time() - t0:.1f}s, isError={err}) ---")
                print(f"response chars: {len(text)} | preview: {text[:110]!r}")


if __name__ == "__main__":
    asyncio.run(main())
