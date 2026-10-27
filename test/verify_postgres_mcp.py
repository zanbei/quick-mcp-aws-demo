"""Verify awslabs.postgres-mcp-server (stdio) against the demo RDS PostgreSQL instance, READ-ONLY role.

Connection: pgwire, database_type RPG (RDS for PostgreSQL), credentials from Secrets Manager
(quick-mcp-demo/pg-readonly, role mcp_readonly). --privilege_check enforce, no --allow_write_query.

Usage: AWS_PROFILE=<profile> uv run --no-project --isolated --with mcp --with boto3 python test/verify_postgres_mcp.py
"""
import asyncio
import json
import os

import boto3
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

PROFILE = os.environ.get("AWS_PROFILE")
REGION = os.environ.get("AWS_REGION", "us-east-1")
NAME = os.environ.get("NAME_PREFIX", "quick-mcp-demo")
INSTANCE = os.environ.get("RDS_INSTANCE", f"{NAME}-pg")
DBNAME = os.environ.get("RDS_DB", "demo")
SECRET = os.environ.get("PG_SECRET", f"{NAME}/pg-readonly")

sess = boto3.Session(profile_name=PROFILE, region_name=REGION)
endpoint = sess.client("rds").describe_db_instances(DBInstanceIdentifier=INSTANCE)["DBInstances"][0]["Endpoint"]["Address"]
secret_arn = sess.client("secretsmanager").describe_secret(SecretId=SECRET)["ARN"]


def show(title, result, limit=1500):
    print(f"\n===== {title} (isError={getattr(result, "isError", getattr(result, "is_error", None))}) =====")
    print("\n".join(getattr(c, "text", str(c)) for c in result.content)[:limit])


def schema_of(t):
    return getattr(t, "inputSchema", None) or t.input_schema


async def main():
    # NOTE: do NOT pass --db_type / --db_endpoint / --connection_method at startup for an RDS *instance*:
    # awslabs.postgres-mcp-server (latest at test time) then does args.db_cluster_arn.split(':') and crashes
    # when no cluster ARN is given. Start without them and let the agent call connect_to_database instead.
    params = StdioServerParameters(
        command="uvx",
        args=[
            "awslabs.postgres-mcp-server@latest",
            "--region", REGION,
            "--secret_arn", secret_arn,
            "--privilege_check", "enforce",
        ],
        env={**os.environ, "AWS_PROFILE": PROFILE, "AWS_REGION": REGION, "AWS_DEFAULT_REGION": REGION,
             "FASTMCP_LOG_LEVEL": "ERROR"},
    )
    # RDS for PostgreSQL (RPG) is addressed by endpoint; cluster_identifier must be empty (it is only for Aurora/APG).
    conn = {"connection_method": "pgwire", "cluster_identifier": "", "db_endpoint": endpoint, "database": DBNAME}
    async with stdio_client(params) as (r, w):
        async with ClientSession(r, w) as s:
            await s.initialize()
            tools = (await s.list_tools()).tools
            print("TOOLS:", [t.name for t in tools])
            defs = schema_of(next(t for t in tools if t.name == "connect_to_database")).get("$defs", {})
            print("enums:", {k: v.get("enum") for k, v in defs.items()})

            show("connect_to_database", await s.call_tool("connect_to_database", {
                "region": REGION, "database_type": "RPG", "connection_method": "pgwire",
                "cluster_identifier": "", "db_endpoint": endpoint, "port": 5432, "database": DBNAME}))
            show("run_query: aggregate", await s.call_tool("run_query", {
                **conn,
                "sql": "SELECT p.category, COUNT(*) AS txns, SUM(s.revenue) AS revenue "
                       "FROM public.demo_sales s JOIN public.demo_products p USING (product_id) "
                       "GROUP BY 1 ORDER BY 3 DESC"}))
            show("get_table_schema(demo_sales)", await s.call_tool("get_table_schema", {**conn, "table_name": "demo_sales"}))
            show("run_query: WRITE (must be rejected)", await s.call_tool("run_query", {
                **conn, "sql": "DELETE FROM public.demo_sales"}))
            show("run_query: current_user / read-only flag", await s.call_tool("run_query", {
                **conn, "sql": "SELECT current_user, current_setting('default_transaction_read_only') AS ro"}))


if __name__ == "__main__":
    asyncio.run(main())
