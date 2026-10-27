"""AgentCore Gateway Lambda target: read-only Postgres (RDS) access through awslabs.postgres-mcp-server.

Why a custom handler instead of the stock stdio adapter (as used for Redshift):
awslabs.postgres-mcp-server keeps its database connection in process memory, and run_query only works after
connect_to_database was called in the SAME process. A Lambda invocation spawns a fresh server process, so this
handler starts the server, calls connect_to_database itself, then runs the requested tool - all in one session.

The database target (endpoint, database, secret) is fixed by Lambda environment variables, never by the caller,
so the agent cannot point the function at another database or secret.

Exposed tools: list_tables, get_table_schema, run_query. (create_cluster / get_job_status are intentionally not exposed.)
"""
import asyncio
import json
import logging
import os
import sys
import tempfile

import boto3
from mcp import ClientSession
from mcp.client.stdio import StdioServerParameters, stdio_client

logger = logging.getLogger()
logger.setLevel(logging.INFO)

REGION = os.environ.get("AWS_REGION", "us-east-1")
DB_ENDPOINT = os.environ["DB_ENDPOINT"]
DB_NAME = os.environ.get("DB_NAME", "postgres")
DB_PORT = int(os.environ.get("DB_PORT", "5432"))
SECRET_ARN = os.environ["SECRET_ARN"]
ALLOW_WRITE = os.environ.get("ALLOW_WRITE_QUERY", "false").lower() == "true"

LIST_TABLES_SQL = (
    "SELECT table_schema, table_name, table_type FROM information_schema.tables "
    "WHERE table_schema NOT IN ('pg_catalog','information_schema') ORDER BY 1,2"
)


def _server_params() -> StdioServerParameters:
    creds = boto3.Session().get_credentials().get_frozen_credentials()
    args = ["-m", "awslabs.postgres_mcp_server.server", "--region", REGION,
            "--secret_arn", SECRET_ARN, "--privilege_check", "enforce"]
    if ALLOW_WRITE:
        args.append("--allow_write_query")
    env = {
        "FASTMCP_LOG_LEVEL": "ERROR",
        "AWS_DEFAULT_REGION": REGION,
        "AWS_REGION": REGION,
        "AWS_ACCESS_KEY_ID": creds.access_key,
        "AWS_SECRET_ACCESS_KEY": creds.secret_key,
        "AWS_SESSION_TOKEN": creds.token or "",
        "TMPDIR": tempfile.gettempdir(),
        # Server dependencies (mcp 2.x) live in server_pkgs; the handler side uses mcp 1.x from /var/task.
        "PYTHONPATH": "/var/task/server_pkgs",
    }
    # Optional private-endpoint overrides (interface VPC endpoints with private DNS disabled).
    if os.environ.get("PG_SECRETSMANAGER_ENDPOINT"):
        env["AWS_ENDPOINT_URL_SECRETS_MANAGER"] = os.environ["PG_SECRETSMANAGER_ENDPOINT"]
    if os.environ.get("PG_RDS_ENDPOINT"):
        env["AWS_ENDPOINT_URL_RDS"] = os.environ["PG_RDS_ENDPOINT"]
    # python -m puts cwd first on sys.path; run from a neutral dir so /var/task (mcp 1.x) cannot shadow mcp 2.x.
    return StdioServerParameters(command=sys.executable, args=args, env=env, cwd=tempfile.gettempdir())


def _text_result(text: str, is_error: bool = False) -> dict:
    return {"content": [{"type": "text", "text": text}], "isError": is_error}


async def _run(tool: str, args: dict) -> dict:
    conn = {"connection_method": "pgwire", "cluster_identifier": "", "db_endpoint": DB_ENDPOINT, "database": DB_NAME}
    if tool == "list_tables":
        server_tool, server_args = "run_query", {**conn, "sql": LIST_TABLES_SQL}
    elif tool == "get_table_schema":
        if not args.get("table_name"):
            return _text_result("table_name is required", True)
        server_tool, server_args = "get_table_schema", {**conn, "table_name": args["table_name"]}
    elif tool == "run_query":
        if not args.get("sql"):
            return _text_result("sql is required", True)
        server_tool, server_args = "run_query", {**conn, "sql": args["sql"]}
    else:
        return _text_result(f"unknown tool: {tool}", True)

    async with stdio_client(_server_params()) as (r, w):
        async with ClientSession(r, w) as session:
            await session.initialize()
            connected = await session.call_tool("connect_to_database", {
                "region": REGION, "database_type": "RPG", "connection_method": "pgwire",
                "cluster_identifier": "", "db_endpoint": DB_ENDPOINT, "port": DB_PORT, "database": DB_NAME})
            connect_text = "".join(getattr(c, "text", "") for c in connected.content)
            # The server reports some failures inside the payload with isError=false, so check both.
            if connected.isError or '"error"' in connect_text or "Error" in connect_text[:200]:
                return _text_result("connect_to_database failed: " + connect_text[:1500], True)
            result = await session.call_tool(server_tool, server_args)
            return result.model_dump(by_alias=True, exclude_none=True, mode="json")


def handler(event, context):
    name = None
    if context.client_context and getattr(context.client_context, "custom", None):
        name = context.client_context.custom.get("bedrockAgentCoreToolName")
    if not name:
        raise ValueError("Missing bedrockAgentCoreToolName in context")
    tool = name.split("___", 1)[-1]
    try:
        return asyncio.run(_run(tool, event or {}))
    except Exception as exc:  # surface a clean error to the agent instead of a Lambda failure
        logger.exception("tool %s failed", tool)
        return _text_result(f"{type(exc).__name__}: {exc}", True)
