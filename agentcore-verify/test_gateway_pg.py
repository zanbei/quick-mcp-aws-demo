"""Gateway test for the PostgreSQL (RDS) target: Cognito JWT -> AgentCore Gateway -> Lambda (in VPC) -> RDS.

Usage: uv run --no-project --isolated --with 'mcp<2' --with boto3 python test_gateway_pg.py
"""
import asyncio
import json
import os

os.environ.setdefault("TARGET_NAME", "postgres-lambda-target")
import test_gateway as g  # noqa: E402  (reuses token + gateway lookup helpers)
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
            print(f"tools/list -> {len(tools)} tools:", sorted(t.name for t in tools))
            await g.call(s, "list_tables", {}, "list_tables", 500)
            await g.call(s, "get_table_schema", {"table_name": "demo_sales"}, "get_table_schema(demo_sales)", 500)
            await g.call(
                s, "run_query",
                {"sql": "SELECT p.category, COUNT(*) AS txns, SUM(s.revenue) AS revenue FROM public.demo_sales s "
                        "JOIN public.demo_products p USING (product_id) GROUP BY 1 ORDER BY 3 DESC"},
                "run_query (aggregate)", 700)
            await g.call(s, "run_query", {"sql": "DELETE FROM public.demo_sales"}, "run_query (WRITE, must be rejected)", 400)
            await g.call(
                s, "run_query",
                {"sql": "SELECT current_user, current_setting('default_transaction_read_only') AS ro, inet_server_addr() AS server_ip"},
                "run_query (identity + private IP)", 400)


if __name__ == "__main__":
    asyncio.run(main())
