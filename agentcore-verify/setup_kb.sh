#!/usr/bin/env bash
# Adds the Bedrock Knowledge Base (RAG) MCP to the existing AgentCore Gateway as a target.
# Prereq: setup.sh already ran (Gateway, gateway role, artifact bucket) and a knowledge base exists
# (bootstrap/create_kb.py, or your own). Needs KB_ID; KB_REGION defaults to AWS_REGION.
# The Lambda role may call bedrock:Retrieve on that ONE knowledge base only (policy rendered from kb/policies/).
#   KB_ID=<id> [KB_REGION=us-east-1] AWS_PROFILE=<profile> ./setup_kb.sh
set -euo pipefail
NAME="${NAME_PREFIX:-quick-mcp-demo}"   # resource name prefix; change it to run a second, independent copy in the same account
: "${KB_ID:?set KB_ID (see bootstrap/state.env)}"
export AWS_PROFILE="${AWS_PROFILE:?set AWS_PROFILE}" AWS_REGION="${AWS_REGION:-us-east-1}" AWS_DEFAULT_REGION="${AWS_REGION:-us-east-1}" AWS_PAGER=""
export KB_REGION="${KB_REGION:-$AWS_REGION}"
cd "$(dirname "$0")"
WG_ID="${WG_ID:-}" ./render_policies.sh

ACCT=$(aws sts get-caller-identity --query Account --output text)
BUCKET="${NAME}-artifacts-${ACCT}"; FN=${NAME}-bedrock-kb-mcp; ROLE=${NAME}-kb-lambda-role
GW_ID=$(aws bedrock-agentcore-control list-gateways --query "items[?name=='${NAME}-gw'].gatewayId | [0]" --output text)
TAG="Key=Project,Value=${NAME}"

echo "== package (arm64, python3.13; two dependency sets because the adapter needs mcp 1.x and the server mcp 2.x)"
rm -rf kbbuild kb-function.zip && mkdir -p kbbuild/server_pkgs
uv pip install --quiet --target kbbuild --python-platform aarch64-manylinux2014 --python-version 3.13 --only-binary=:all: -r kb/requirements.txt
uv pip install --quiet --target kbbuild/server_pkgs --python-platform aarch64-manylinux2014 --python-version 3.13 --only-binary=:all: -r kb/requirements-server.txt
cp kb/src/kb_agentcore_lambda.py kbbuild/ && (cd kbbuild && zip -q -r -X ../kb-function.zip . -x "*.pyc" -x "*/__pycache__/*")
aws s3 cp kb-function.zip "s3://${BUCKET}/kb-function.zip" --only-show-errors

echo "== IAM role (Retrieve on one knowledge base) + gateway invoke permission"
aws iam create-role --role-name "$ROLE" --assume-role-policy-document file://.rendered/policies/lambda-trust.json --tags "$TAG" >/dev/null
aws iam attach-role-policy --role-name "$ROLE" --policy-arn arn:aws:iam::aws:policy/service-role/AWSLambdaBasicExecutionRole
aws iam put-role-policy --role-name "$ROLE" --policy-name kb-retrieve-access --policy-document file://.rendered/kb/policies/kb-lambda-access.json
aws iam put-role-policy --role-name ${NAME}-gateway-role --policy-name invoke-lambda-target --policy-document file://.rendered/policies/gateway-invoke.json
sleep 12

echo "== Lambda"
python3 - <<PY
import json
json.dump({"Variables": {"KB_REGION": "${KB_REGION}", "DEFAULT_KB_ID": "${KB_ID}", "FASTMCP_LOG_LEVEL": "ERROR"}}, open("/tmp/kb_env.json", "w"))
PY
aws lambda create-function --function-name "$FN" --runtime python3.13 --architectures arm64 \
  --handler kb_agentcore_lambda.handler --role "arn:aws:iam::${ACCT}:role/${ROLE}" \
  --code "S3Bucket=${BUCKET},S3Key=kb-function.zip" --timeout 120 --memory-size 1024 \
  --environment file:///tmp/kb_env.json --tags Project=${NAME} >/dev/null
rm -f /tmp/kb_env.json
aws lambda wait function-active-v2 --function-name "$FN"

echo "== Gateway target"
python3 - <<PY
import json
tools = json.load(open("kb/tools.json"))
json.dump({"mcp": {"lambda": {"lambdaArn": "arn:aws:lambda:${AWS_REGION}:${ACCT}:function:${FN}",
  "toolSchema": {"inlinePayload": tools}}}}, open("kb/target-config.json", "w"), indent=1)
json.dump([{"credentialProviderType": "GATEWAY_IAM_ROLE"}], open("target-creds.json", "w"))
PY
aws bedrock-agentcore-control create-gateway-target --gateway-identifier "$GW_ID" --name bedrock-kb-lambda-target \
  --target-configuration file://kb/target-config.json --credential-provider-configurations file://target-creds.json >/dev/null
echo "Done. Test: uv run --no-project --isolated --with 'mcp<2' --with boto3 python test_gateway_kb.py"
echo "Then click Sync on the Quick connector so the 2 new tools appear."
