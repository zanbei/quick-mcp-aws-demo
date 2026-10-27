#!/usr/bin/env bash
# Removes only what setup_kb.sh created (Gateway target, Lambda, Lambda role). The knowledge base itself is removed by
# bootstrap/create_kb.py cleanup.
set -uo pipefail
NAME="${NAME_PREFIX:-quick-mcp-demo}"   # resource name prefix; change it to run a second, independent copy in the same account
export AWS_PROFILE="${AWS_PROFILE:?set AWS_PROFILE}" AWS_REGION="${AWS_REGION:-us-east-1}" AWS_DEFAULT_REGION="${AWS_REGION:-us-east-1}" AWS_PAGER=""
GW_ID=$(aws bedrock-agentcore-control list-gateways --query "items[?name=='${NAME}-gw'].gatewayId | [0]" --output text)
T=$(aws bedrock-agentcore-control list-gateway-targets --gateway-identifier "$GW_ID" --query "items[?name=='bedrock-kb-lambda-target'].targetId | [0]" --output text)
[ -n "$T" ] && [ "$T" != "None" ] && aws bedrock-agentcore-control delete-gateway-target --gateway-identifier "$GW_ID" --target-id "$T" >/dev/null
aws lambda delete-function --function-name ${NAME}-bedrock-kb-mcp 2>/dev/null
aws iam detach-role-policy --role-name ${NAME}-kb-lambda-role --policy-arn arn:aws:iam::aws:policy/service-role/AWSLambdaBasicExecutionRole 2>/dev/null
aws iam delete-role-policy --role-name ${NAME}-kb-lambda-role --policy-name kb-retrieve-access 2>/dev/null
aws iam delete-role --role-name ${NAME}-kb-lambda-role 2>/dev/null
echo "kb target, Lambda and role removed"
