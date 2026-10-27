"""End-to-end test of: MCP client -> (Cognito JWT) -> AgentCore Gateway -> Lambda -> awslabs Redshift MCP.

Reads Cognito client credentials from Secrets Manager (quick-mcp-demo/cognito-client) using
AWS_PROFILE (set AWS_PROFILE). Secrets are never printed.

Usage: uv run --with mcp --with boto3 python test_gateway.py
"""
import asyncio
import base64
import json
import os
import sys
import time
import urllib.error
import urllib.parse
import urllib.request

import boto3
from mcp import ClientSession
from mcp.client.streamable_http import streamablehttp_client

PROFILE = os.environ.get("AWS_PROFILE")
REGION = os.environ.get("AWS_REGION", "us-east-1")
NAME = os.environ.get("NAME_PREFIX", "quick-mcp-demo")
GATEWAY_NAME = os.environ.get("GATEWAY_NAME", f"{NAME}-gw")
SECRET_NAME = os.environ.get("SECRET_NAME", f"{NAME}/cognito-client")
TARGET = os.environ.get("TARGET_NAME", "redshift-lambda-target")
RS_DB = os.environ.get("RS_DB", "dev")
CLUSTER = {"cluster_identifier": os.environ.get("RS_WORKGROUP", "default-workgroup"), "cluster_type": "serverless"}

session = boto3.Session(profile_name=PROFILE, region_name=REGION)


def gateway_url() -> str:
    c = session.client("bedrock-agentcore-control")
    for g in c.list_gateways()["items"]:
        if g["name"] == GATEWAY_NAME:
            return c.get_gateway(gatewayIdentifier=g["gatewayId"])["gatewayUrl"]
    sys.exit(f"gateway {GATEWAY_NAME} not found")


def get_token(cfg: dict) -> str:
    body = urllib.parse.urlencode({"grant_type": "client_credentials", "scope": cfg["scope"]}).encode()
    basic = base64.b64encode(f"{cfg['client_id']}:{cfg['client_secret']}".encode()).decode()
    req = urllib.request.Request(
        cfg["token_url"],
        data=body,
        headers={"Authorization": f"Basic {basic}", "Content-Type": "application/x-www-form-urlencoded"},
    )
    return json.loads(urllib.request.urlopen(req, timeout=30).read())["access_token"]


def text_of(result) -> str:
    return "\n".join(getattr(c, "text", str(c)) for c in result.content)


async def call(s: ClientSession, name: str, args: dict, label: str, max_chars: int = 700):
    t0 = time.time()
    r = await s.call_tool(f"{TARGET}___{name}", args)
    print(f"\n--- {label} ({time.time() - t0:.1f}s, isError={r.isError}) ---")
    print(text_of(r)[:max_chars])
    return r


async def main():
    url = gateway_url()
    print("gateway:", url)

    # 1. unauthenticated / bad token must be rejected
    for label, hdr in [("no token", {}), ("bad token", {"Authorization": "Bearer not-a-real-token"})]:
        req = urllib.request.Request(
            url,
            data=json.dumps({"jsonrpc": "2.0", "id": 1, "method": "tools/list"}).encode(),
            headers={"Content-Type": "application/json", "Accept": "application/json, text/event-stream", **hdr},
        )
        try:
            urllib.request.urlopen(req, timeout=30)
            print(f"[{label}] UNEXPECTED success")
        except urllib.error.HTTPError as e:
            print(f"[{label}] HTTP {e.code} (expected 401)")

    # 2. authenticated session
    cfg = json.loads(session.client("secretsmanager").get_secret_value(SecretId=SECRET_NAME)["SecretString"])
    token = get_token(cfg)
    headers = {"Authorization": f"Bearer {token}"}
    async with streamablehttp_client(url, headers=headers, timeout=120) as (r, w, _):
        async with ClientSession(r, w) as s:
            await s.initialize()
            tools = (await s.list_tools()).tools
            print(f"\ntools/list -> {len(tools)} tools:", [t.name for t in tools])
            for t in tools:
                req = t.inputSchema.get("required", [])
                bad = [k for k, v in t.inputSchema.get("properties", {}).items() if isinstance(v.get("required"), bool)]
                print(f"  {t.name}: required={req} draft3-style-required-in-property={bad}")

            await call(s, "list_clusters", {}, "list_clusters")
            await call(s, "list_databases", {**CLUSTER, "database_name": RS_DB}, "list_databases")
            await call(
                s,
                "list_tables",
                {**CLUSTER, "table_database_name": RS_DB, "table_schema_name": "public"},
                "list_tables(public)",
            )
            await call(
                s,
                "execute_query",
                {
                    **CLUSTER,
                    "database_name": RS_DB,
                    "sql": "SELECT p.category, COUNT(*) AS txns, SUM(s.revenue) AS revenue "
                    "FROM public.demo_sales s JOIN public.demo_products p USING (product_id) "
                    "GROUP BY 1 ORDER BY 3 DESC",
                },
                "execute_query (SELECT)",
            )
            await call(
                s,
                "execute_query",
                {**CLUSTER, "database_name": RS_DB, "sql": "DELETE FROM public.demo_sales"},
                "execute_query (WRITE, must be rejected)",
            )


if __name__ == "__main__":
    asyncio.run(main())
