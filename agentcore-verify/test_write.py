"""Write-path test through the Gateway (run only after enable_write.sh).

Creates a scratch table, inserts, reads back, then checks that destructive statements on the demo tables
are still refused by database privileges, and finally drops the scratch table.
Usage: uv run --no-project --isolated --with 'mcp<2' --with boto3 python test_write.py
"""
import asyncio
import json

import test_gateway as g
from mcp import ClientSession
from mcp.client.streamable_http import streamablehttp_client

SQLS = [
    ("CREATE scratch table", "CREATE TABLE public.demo_write_test (id INT, note VARCHAR(50))"),
    ("INSERT into scratch table", "INSERT INTO public.demo_write_test VALUES (1,'inserted via Quick MCP'),(2,'second row')"),
    ("INSERT into demo_products", "INSERT INTO public.demo_products VALUES (99,'Write Test Item','Test',1.00)"),
    ("SELECT scratch table", "SELECT * FROM public.demo_write_test ORDER BY id"),
    ("DELETE on demo_sales (expect permission denied)", "DELETE FROM public.demo_sales WHERE sale_id = 1"),
    ("UPDATE on demo_sales (expect permission denied)", "UPDATE public.demo_sales SET quantity = 0 WHERE sale_id = 1"),
    ("DROP demo_products (expect permission denied)", "DROP TABLE public.demo_products"),
    ("DROP scratch table (cleanup)", "DROP TABLE public.demo_write_test"),
]


async def main():
    url = g.gateway_url()
    cfg = json.loads(g.session.client("secretsmanager").get_secret_value(SecretId=g.SECRET_NAME)["SecretString"])
    headers = {"Authorization": f"Bearer {g.get_token(cfg)}"}
    async with streamablehttp_client(url, headers=headers, timeout=180) as (r, w, _):
        async with ClientSession(r, w) as s:
            await s.initialize()
            for label, sql in SQLS:
                await g.call(
                    s,
                    "execute_query",
                    {**g.CLUSTER, "database_name": g.RS_DB, "sql": sql},
                    label,
                    max_chars=400,
                )


if __name__ == "__main__":
    asyncio.run(main())
