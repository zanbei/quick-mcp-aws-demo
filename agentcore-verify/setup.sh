#!/usr/bin/env bash
# Rebuilds the AgentCore Gateway + Lambda (awslabs Redshift MCP, read-only) + Cognito PoC with plain AWS CLI.
# No CDK / CloudFormation. All resources are named quick-mcp-demo-* and tagged Project=quick-mcp-demo.
#
# Prerequisites: aws cli (v1 or v2), uv, python3. Run from this directory:  AWS_PROFILE=<profile> ./setup.sh
# NOTE: fails fast if a resource already exists; run cleanup.sh first for a clean rebuild.
set -euo pipefail
NAME="${NAME_PREFIX:-quick-mcp-demo}"   # resource name prefix; change it to run a second, independent copy in the same account

export AWS_PROFILE="${AWS_PROFILE:?set AWS_PROFILE}"
export AWS_REGION="${AWS_REGION:-us-east-1}"
export AWS_DEFAULT_REGION="$AWS_REGION"   # aws cli v1 only reads this one
export AWS_PAGER=""

ACCT=$(aws sts get-caller-identity --query Account --output text)
RS_WORKGROUP="${RS_WORKGROUP:-default-workgroup}"; RS_DB="${RS_DB:-dev}"   # Redshift Serverless workgroup name and database
WG_ID="${WG_ID:-$(aws redshift-serverless get-workgroup --workgroup-name "$RS_WORKGROUP" --query workgroup.workgroupId --output text)}"
export WG_ID
WG_ARN="arn:aws:redshift-serverless:${AWS_REGION}:${ACCT}:workgroup/${WG_ID}"
FN=${NAME}-redshift-mcp
BUCKET="${NAME}-artifacts-${ACCT}"
LAMBDA_ROLE=${NAME}-lambda-role
GW_ROLE=${NAME}-gateway-role
GW_NAME=${NAME}-gw
TARGET=redshift-lambda-target
DOMAIN="${NAME}-${ACCT:0:6}"
SECRET=${NAME}/cognito-client
TAG="Key=Project,Value=${NAME}"

cd "$(dirname "$0")"
./render_policies.sh   # policy templates -> .rendered/ with this account / region / workgroup

echo "== 1. Package Lambda (arm64, python3.13)"
# Two isolated dependency sets: the Lambda adapter needs mcp 1.x, the awslabs server needs mcp 2.x.
rm -rf build function.zip && mkdir -p build/server_pkgs
uv pip install --quiet --target build --python-platform aarch64-manylinux2014 --python-version 3.13 \
  --only-binary=:all: -r lambda_src/requirements.txt
uv pip install --quiet --target build/server_pkgs --python-platform aarch64-manylinux2014 --python-version 3.13 \
  --only-binary=:all: -r lambda_src/requirements-server.txt
cp lambda_src/redshift_agentcore_lambda.py build/
(cd build && zip -q -r -X ../function.zip . -x "*.pyc" -x "*/__pycache__/*")

echo "== 2. Artifact bucket (zip is > 50 MB, so it must go through S3)"
aws s3api create-bucket --bucket "$BUCKET" >/dev/null
aws s3api put-public-access-block --bucket "$BUCKET" --public-access-block-configuration \
  BlockPublicAcls=true,IgnorePublicAcls=true,BlockPublicPolicy=true,RestrictPublicBuckets=true
aws s3api put-bucket-tagging --bucket "$BUCKET" --tagging "TagSet=[{Key=Project,Value=${NAME}}]"
aws s3 cp function.zip "s3://${BUCKET}/function.zip" --only-show-errors

echo "== 3. IAM roles (least privilege)"
aws iam create-role --role-name "$LAMBDA_ROLE" --assume-role-policy-document file://.rendered/policies/lambda-trust.json --tags "$TAG" >/dev/null
aws iam attach-role-policy --role-name "$LAMBDA_ROLE" --policy-arn arn:aws:iam::aws:policy/service-role/AWSLambdaBasicExecutionRole
aws iam put-role-policy --role-name "$LAMBDA_ROLE" --policy-name redshift-readonly-access --policy-document file://.rendered/policies/lambda-redshift.json
aws iam create-role --role-name "$GW_ROLE" --assume-role-policy-document file://.rendered/policies/gateway-trust.json --tags "$TAG" >/dev/null
aws iam put-role-policy --role-name "$GW_ROLE" --policy-name invoke-lambda-target --policy-document file://.rendered/policies/gateway-invoke.json
sleep 12   # IAM propagation

echo "== 4. Lambda"
aws lambda create-function --function-name "$FN" --runtime python3.13 --architectures arm64 \
  --handler redshift_agentcore_lambda.handler --role "arn:aws:iam::${ACCT}:role/${LAMBDA_ROLE}" \
  --code "S3Bucket=${BUCKET},S3Key=function.zip" --timeout 900 --memory-size 1024 \
  --environment 'Variables={FASTMCP_LOG_LEVEL=ERROR}' --tags Project=${NAME} >/dev/null
aws lambda wait function-active-v2 --function-name "$FN"

echo "== 5. Cognito (client_credentials)"
POOL=$(aws cognito-idp create-user-pool --pool-name ${NAME}-pool --user-pool-tags Project=${NAME} \
  --query UserPool.Id --output text)
aws cognito-idp create-user-pool-domain --user-pool-id "$POOL" --domain "$DOMAIN" >/dev/null
aws cognito-idp create-resource-server --user-pool-id "$POOL" --identifier ${NAME} --name ${NAME} \
  --scopes ScopeName=invoke,ScopeDescription="Invoke the MCP gateway" >/dev/null
CID=$(aws cognito-idp create-user-pool-client --user-pool-id "$POOL" --client-name ${NAME}-client \
  --generate-secret --allowed-o-auth-flows client_credentials --allowed-o-auth-scopes ${NAME}/invoke \
  --allowed-o-auth-flows-user-pool-client --supported-identity-providers COGNITO \
  --query UserPoolClient.ClientId --output text)
CSEC=$(aws cognito-idp describe-user-pool-client --user-pool-id "$POOL" --client-id "$CID" \
  --query UserPoolClient.ClientSecret --output text)
# Secret goes straight into Secrets Manager; it is never echoed or written to a file.
aws secretsmanager create-secret --name "$SECRET" --tags "$TAG" --description "Quick MCP demo Cognito client credentials" \
  --secret-string "$(python3 -c 'import json,sys;print(json.dumps({"client_id":sys.argv[1],"client_secret":sys.argv[2],"user_pool_id":sys.argv[3],"token_url":"https://%s.auth.%s.amazoncognito.com/oauth2/token"%(sys.argv[4],sys.argv[5]),"scope":sys.argv[6]}))' \
  "$CID" "$CSEC" "$POOL" "$DOMAIN" "$AWS_REGION" "${NAME}/invoke")" >/dev/null
unset CSEC

echo "== 6. AgentCore Gateway + Lambda target"
GW_ID=$(aws bedrock-agentcore-control create-gateway --name "$GW_NAME" --description "Quick MCP demo: Redshift read-only MCP" \
  --role-arn "arn:aws:iam::${ACCT}:role/${GW_ROLE}" --protocol-type MCP --authorizer-type CUSTOM_JWT \
  --authorizer-configuration "customJWTAuthorizer={discoveryUrl=https://cognito-idp.${AWS_REGION}.amazonaws.com/${POOL}/.well-known/openid-configuration,allowedClients=[${CID}]}" \
  --tags Project=${NAME} --query gatewayId --output text)
until [ "$(aws bedrock-agentcore-control get-gateway --gateway-identifier "$GW_ID" --query status --output text)" = "READY" ]; do sleep 5; done
python3 - <<EOF
import json
tools = json.load(open("tools.json"))
cfg = {"mcp": {"lambda": {"lambdaArn": "arn:aws:lambda:${AWS_REGION}:${ACCT}:function:${FN}",
                          "toolSchema": {"inlinePayload": tools}}}}
json.dump(cfg, open("target-config.json", "w"), indent=1)
json.dump([{"credentialProviderType": "GATEWAY_IAM_ROLE"}], open("target-creds.json", "w"))
EOF
aws bedrock-agentcore-control create-gateway-target --gateway-identifier "$GW_ID" --name "$TARGET" \
  --target-configuration file://target-config.json --credential-provider-configurations file://target-creds.json >/dev/null

echo "== 7. Redshift: allow the Lambda role's DB user to read the two demo tables (run once, as the table owner)"
# The IAM-mapped DB user is normally created on first login; create it up front so the GRANT cannot fail on a fresh workgroup.
for STMT in "CREATE USER \"IAMR:${LAMBDA_ROLE}\" PASSWORD DISABLE" "GRANT SELECT ON public.demo_sales, public.demo_products TO \"IAMR:${LAMBDA_ROLE}\""; do
  SID=$(aws redshift-data execute-statement --workgroup-name "$RS_WORKGROUP" --database "$RS_DB" --sql "$STMT" --query Id --output text)
  sleep 6
  echo "${STMT%%\"*}...: $(aws redshift-data describe-statement --id "$SID" --query Status --output text)   (CREATE USER may say FAILED if the user already exists; the GRANT must say FINISHED)"
done

echo
echo "Done. Gateway URL:"
aws bedrock-agentcore-control get-gateway --gateway-identifier "$GW_ID" --query gatewayUrl --output text
echo "Cognito token URL / client id / secret are in Secrets Manager: ${SECRET}"
echo "Test:  uv run --no-project --isolated --with 'mcp<2' --with boto3 python test_gateway.py"
