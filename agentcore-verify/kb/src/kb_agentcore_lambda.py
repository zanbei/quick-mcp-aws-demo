"""AgentCore Gateway Lambda target: Amazon Bedrock Knowledge Bases (RAG) through awslabs.bedrock-kb-retrieval-mcp-server.

Same pattern as the Redshift target: the stock stdio adapter (run-mcp-servers-with-aws-lambda, mcp 1.x) starts the
server (mcp 2.x, isolated in server_pkgs) once per invocation. The tools are stateless (the knowledge base id is a
tool argument), so no connect step is needed.

Differences from the Redshift handler:
- The knowledge base can be in another Region than the Lambda: KB_REGION sets the Region the server talks to.
- DEFAULT_KB_ID fills in knowledge_base_id when the caller omits it, so the agent does not have to know the id.
  The IAM policy of the function role is the real boundary (it only allows Retrieve on that knowledge base).
"""
import logging
import os
import sys
import tempfile

import boto3
from mcp.client.stdio import StdioServerParameters
from mcp_lambda import BedrockAgentCoreGatewayTargetHandler, StdioServerAdapterRequestHandler

logger = logging.getLogger()
logger.setLevel(logging.INFO)

KB_REGION = os.environ.get("KB_REGION", os.environ.get("AWS_REGION", "us-east-1"))
DEFAULT_KB_ID = os.environ.get("DEFAULT_KB_ID", "")


def handler(event, context):
    creds = boto3.Session().get_credentials().get_frozen_credentials()
    params = StdioServerParameters(
        command=sys.executable,
        args=["-m", "awslabs.bedrock_kb_retrieval_mcp_server.server"],
        cwd=tempfile.gettempdir(),  # keep /var/task (mcp 1.x) off sys.path of the server process
        env={
            "FASTMCP_LOG_LEVEL": "ERROR",
            "AWS_REGION": KB_REGION,
            "AWS_DEFAULT_REGION": KB_REGION,
            "AWS_ACCESS_KEY_ID": creds.access_key,
            "AWS_SECRET_ACCESS_KEY": creds.secret_key,
            "AWS_SESSION_TOKEN": creds.token or "",
            "TMPDIR": tempfile.gettempdir(),
            "PYTHONPATH": "/var/task/server_pkgs",
        },
    )
    if isinstance(event, dict) and DEFAULT_KB_ID and not event.get("knowledge_base_id"):
        name = ""
        if context.client_context and getattr(context.client_context, "custom", None):
            name = context.client_context.custom.get("bedrockAgentCoreToolName", "")
        if name.endswith("QueryKnowledgeBases") and not name.endswith("AgenticQueryKnowledgeBases"):
            event = {**event, "knowledge_base_id": DEFAULT_KB_ID}
    try:
        return BedrockAgentCoreGatewayTargetHandler(StdioServerAdapterRequestHandler(params)).handle(event, context)
    except Exception:
        logger.exception("kb tool failed")
        raise
