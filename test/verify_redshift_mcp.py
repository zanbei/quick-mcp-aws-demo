"""Verify awslabs.redshift-mcp-server against the given AWS profile (READ-ONLY).

Spawns the MCP server over stdio exactly as an MCP client (Kiro / Claude / etc.) would,
then calls: tools/list -> list_clusters -> list_databases -> list_schemas -> list_tables -> execute_query.
"""
import asyncio
import json
import os
import sys

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

PROFILE = os.environ.get("AWS_PROFILE")
REGION = os.environ.get("AWS_REGION", "us-east-1")
CLUSTER = os.environ.get("RS_CLUSTER", "default-workgroup")
CTYPE = os.environ.get("RS_TYPE", "serverless")
DB = os.environ.get("RS_DB", "sample_data_dev")


def show(title, result):
    print(f"\n===== {title} =====")
    for c in result.content:
        text = getattr(c, "text", str(c))
        print(text[:3000])


async def main():
    params = StdioServerParameters(
        command="uvx",
        args=["awslabs.redshift-mcp-server@latest"],
        env={
            **os.environ,
            "AWS_PROFILE": PROFILE,
            "AWS_DEFAULT_REGION": REGION,
            "AWS_REGION": REGION,
            "LOG_LEVEL": "ERROR",
        },
    )
    async with stdio_client(params) as (r, w):
        async with ClientSession(r, w) as s:
            await s.initialize()
            tools = await s.list_tools()
            print("TOOLS:", [t.name for t in tools.tools])

            show("list_clusters", await s.call_tool("list_clusters", {}))
            show(
                "list_databases",
                await s.call_tool(
                    "list_databases",
                    {"cluster_identifier": CLUSTER, "cluster_type": CTYPE, "database_name": "dev"},
                ),
            )
            show(
                "list_schemas",
                await s.call_tool(
                    "list_schemas",
                    {"cluster_identifier": CLUSTER, "cluster_type": CTYPE, "schema_database_name": DB},
                ),
            )
            show(
                "list_tables(public)",
                await s.call_tool(
                    "list_tables",
                    {
                        "cluster_identifier": CLUSTER,
                        "cluster_type": CTYPE,
                        "table_database_name": DB,
                        "table_schema_name": "public",
                    },
                ),
            )
            sql = (
                "SELECT p.category, COUNT(*) AS txns, SUM(s.revenue) AS revenue "
                "FROM public.demo_sales s JOIN public.demo_products p USING (product_id) "
                "GROUP BY 1 ORDER BY 3 DESC"
            )
            show(
                "execute_query",
                await s.call_tool(
                    "execute_query",
                    {"cluster_identifier": CLUSTER, "cluster_type": CTYPE, "database_name": DB, "sql": sql},
                ),
            )


if __name__ == "__main__":
    asyncio.run(main())
