#!/usr/bin/env bash
# Deletes everything setup.sh created (all named quick-mcp-demo-*). Review before running.
# Does NOT touch: Redshift workgroup/data, other Lambdas/roles, Quick datasets, security groups, RDS instance.
# Usage: AWS_PROFILE=<profile> ./cleanup.sh
set -uo pipefail   # no -e: keep going if a resource is already gone
NAME="${NAME_PREFIX:-quick-mcp-demo}"   # resource name prefix; change it to run a second, independent copy in the same account

export AWS_PROFILE="${AWS_PROFILE:?set AWS_PROFILE}"
export AWS_REGION="${AWS_REGION:-us-east-1}"
export AWS_DEFAULT_REGION="$AWS_REGION"
export AWS_PAGER=""

ACCT=$(aws sts get-caller-identity --query Account --output text)
FN=${NAME}-redshift-mcp
BUCKET="${NAME}-artifacts-${ACCT}"
LAMBDA_ROLE=${NAME}-lambda-role
GW_ROLE=${NAME}-gateway-role
GW_NAME=${NAME}-gw
DOMAIN="${NAME}-${ACCT:0:6}"
SECRET=${NAME}/cognito-client

echo "== Redshift: revoke the demo grant (optional, run as table owner)"
SID=$(aws redshift-data execute-statement --workgroup-name "${RS_WORKGROUP:-default-workgroup}" --database dev \
  --sql "REVOKE SELECT ON public.demo_sales, public.demo_products FROM \"IAMR:${LAMBDA_ROLE}\"" --query Id --output text 2>/dev/null) \
  && { sleep 5; aws redshift-data describe-statement --id "$SID" --query Status --output text; }

echo "== Gateway target + gateway"
GW_ID=$(aws bedrock-agentcore-control list-gateways --query "items[?name=='${GW_NAME}'].gatewayId | [0]" --output text)
if [ -n "$GW_ID" ] && [ "$GW_ID" != "None" ]; then
  for T in $(aws bedrock-agentcore-control list-gateway-targets --gateway-identifier "$GW_ID" --query "items[].targetId" --output text); do
    aws bedrock-agentcore-control delete-gateway-target --gateway-identifier "$GW_ID" --target-id "$T" >/dev/null
  done
  # targets delete asynchronously; wait until none remain
  for _ in $(seq 1 30); do
    N=$(aws bedrock-agentcore-control list-gateway-targets --gateway-identifier "$GW_ID" --query "length(items)" --output text)
    [ "$N" = "0" ] && break; sleep 5
  done
  aws bedrock-agentcore-control delete-gateway --gateway-identifier "$GW_ID" >/dev/null
fi

echo "== Lambda"
aws lambda delete-function --function-name "$FN" 2>/dev/null

echo "== IAM roles"
aws iam detach-role-policy --role-name "$LAMBDA_ROLE" --policy-arn arn:aws:iam::aws:policy/service-role/AWSLambdaBasicExecutionRole 2>/dev/null
aws iam delete-role-policy --role-name "$LAMBDA_ROLE" --policy-name redshift-readonly-access 2>/dev/null
aws iam delete-role --role-name "$LAMBDA_ROLE" 2>/dev/null
aws iam delete-role-policy --role-name "$GW_ROLE" --policy-name invoke-lambda-target 2>/dev/null
aws iam delete-role --role-name "$GW_ROLE" 2>/dev/null

echo "== Cognito"
POOL=$(aws cognito-idp list-user-pools --max-results 60 --query "UserPools[?Name=='${NAME}-pool'].Id | [0]" --output text)
if [ -n "$POOL" ] && [ "$POOL" != "None" ]; then
  aws cognito-idp delete-user-pool-domain --user-pool-id "$POOL" --domain "$DOMAIN" 2>/dev/null
  aws cognito-idp delete-user-pool --user-pool-id "$POOL"
fi

echo "== Secrets Manager (demo secret, deleted immediately)"
aws secretsmanager delete-secret --secret-id "$SECRET" --force-delete-without-recovery >/dev/null 2>&1

echo "== S3 artifact bucket"
aws s3 rm "s3://${BUCKET}" --recursive --only-show-errors 2>/dev/null
aws s3api delete-bucket --bucket "$BUCKET" 2>/dev/null

echo "Cleanup finished. Local files (build/, function.zip) can be deleted manually."
echo "Optional: in Redshift, DROP USER \"IAMR:${LAMBDA_ROLE}\"; (leftover DB user, harmless)."
