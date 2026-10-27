#!/usr/bin/env bash
# Adds the RDS PostgreSQL (read-only) MCP to the existing AgentCore Gateway as a second target.
# Prereq: setup.sh already ran (Gateway, gateway role, artifact bucket), and the demo RDS + read-only secret exist
# (scripts/seed_rds_demo.py). Private path: Lambda runs in the VPC; Secrets Manager and RDS API are reached through
# interface VPC endpoints with PRIVATE DNS DISABLED (so other workloads in the shared VPC are not affected), by
# pointing the AWS SDK at the endpoint DNS names via AWS_ENDPOINT_URL_* variables.
set -euo pipefail
NAME="${NAME_PREFIX:-quick-mcp-demo}"   # resource name prefix; change it to run a second, independent copy in the same account
export AWS_PROFILE="${AWS_PROFILE:?set AWS_PROFILE}" AWS_REGION="${AWS_REGION:-us-east-1}" AWS_DEFAULT_REGION="${AWS_REGION:-us-east-1}" AWS_PAGER=""
cd "$(dirname "$0")"
WG_ID="${WG_ID:-}" ./render_policies.sh

ACCT=$(aws sts get-caller-identity --query Account --output text)
RDS_ID="${RDS_ID:-${NAME}-pg}"
# VPC, RDS security group and a subnet in the RDS instance's AZ are read from the instance unless you set VPC / SUBNET / RDS_SG.
INFO=$(aws rds describe-db-instances --db-instance-identifier "$RDS_ID" --query 'DBInstances[0].[DBSubnetGroup.VpcId,VpcSecurityGroups[0].VpcSecurityGroupId,AvailabilityZone]' --output text)
VPC="${VPC:-$(echo "$INFO" | cut -f1)}"; RDS_SG="${RDS_SG:-$(echo "$INFO" | cut -f2)}"; RDS_AZ=$(echo "$INFO" | cut -f3)
SUBNET="${SUBNET:-$(aws rds describe-db-instances --db-instance-identifier "$RDS_ID" --query "DBInstances[0].DBSubnetGroup.Subnets[?SubnetAvailabilityZone.Name=='${RDS_AZ}'].SubnetIdentifier | [0]" --output text)}"
BUCKET="${NAME}-artifacts-${ACCT}"; FN=${NAME}-postgres-mcp; ROLE=${NAME}-pg-lambda-role
GW_ID=$(aws bedrock-agentcore-control list-gateways --query "items[?name=='${NAME}-gw'].gatewayId | [0]" --output text)
TAG="Key=Project,Value=${NAME}"

echo "== package"
rm -rf pgbuild pg-function.zip && mkdir -p pgbuild/server_pkgs
uv pip install --quiet --target pgbuild --python-platform aarch64-manylinux2014 --python-version 3.13 --only-binary=:all: -r pg/requirements.txt
uv pip install --quiet --target pgbuild/server_pkgs --python-platform aarch64-manylinux2014 --python-version 3.13 --only-binary=:all: -r pg/requirements-server.txt
cp pg/src/postgres_agentcore_lambda.py pgbuild/ && (cd pgbuild && zip -q -r -X ../pg-function.zip . -x "*.pyc" -x "*/__pycache__/*")
aws s3 cp pg-function.zip "s3://${BUCKET}/pg-function.zip" --only-show-errors

echo "== security groups (Lambda: egress only; endpoints: 443 from Lambda; RDS: 5432 from Lambda)"
LSG=$(aws ec2 describe-security-groups --filters Name=group-name,Values=${NAME}-pg-lambda-sg Name=vpc-id,Values="$VPC" --query 'SecurityGroups[0].GroupId' --output text)   # scripts/seed_rds_private.py may have created it already
[ "$LSG" != "None" ] || LSG=$(aws ec2 create-security-group --group-name ${NAME}-pg-lambda-sg --description "${NAME} postgres MCP Lambda" --vpc-id "$VPC" --tag-specifications "ResourceType=security-group,Tags=[{$TAG}]" --query GroupId --output text)
ESG=$(aws ec2 describe-security-groups --filters Name=group-name,Values=${NAME}-vpce-sg Name=vpc-id,Values="$VPC" --query 'SecurityGroups[0].GroupId' --output text)
[ "$ESG" != "None" ] || ESG=$(aws ec2 create-security-group --group-name ${NAME}-vpce-sg --description "${NAME} interface endpoints" --vpc-id "$VPC" --tag-specifications "ResourceType=security-group,Tags=[{$TAG}]" --query GroupId --output text)
aws ec2 authorize-security-group-ingress --group-id "$ESG" --ip-permissions "IpProtocol=tcp,FromPort=443,ToPort=443,UserIdGroupPairs=[{GroupId=$LSG}]" >/dev/null 2>&1 || true
aws ec2 authorize-security-group-ingress --group-id "$RDS_SG" --ip-permissions "IpProtocol=tcp,FromPort=5432,ToPort=5432,UserIdGroupPairs=[{GroupId=$LSG}]" >/dev/null 2>/dev/null || true   # rule already exists after seed_rds_private.py

echo "== interface endpoints (private DNS off)"
# (macOS ships bash 3.2, which has no associative arrays, so this is a function instead of declare -A)
make_endpoint() {   # $1 = service name; prints the endpoint DNS name
  local svc="$1" EP
  EP=$(aws ec2 create-vpc-endpoint --vpc-endpoint-type Interface --vpc-id "$VPC" --service-name "com.amazonaws.${AWS_REGION}.${svc}" --subnet-ids "$SUBNET" --security-group-ids "$ESG" --no-private-dns-enabled --tag-specifications "ResourceType=vpc-endpoint,Tags=[{$TAG},{Key=Name,Value=${NAME}-${svc}}]" --query VpcEndpoint.VpcEndpointId --output text)
  until [ "$(aws ec2 describe-vpc-endpoints --vpc-endpoint-ids "$EP" --query 'VpcEndpoints[0].State' --output text)" = "available" ]; do sleep 8; done
  aws ec2 describe-vpc-endpoints --vpc-endpoint-ids "$EP" --query 'VpcEndpoints[0].DnsEntries[0].DnsName' --output text
}
DNS_SM=$(make_endpoint secretsmanager)
DNS_RDS=$(make_endpoint rds)
[ -n "$DNS_SM" ] && [ -n "$DNS_RDS" ] || { echo "interface endpoint creation failed" >&2; exit 1; }
echo "== IAM role"
aws iam create-role --role-name "$ROLE" --assume-role-policy-document file://.rendered/policies/lambda-trust.json --tags "$TAG" >/dev/null
aws iam attach-role-policy --role-name "$ROLE" --policy-arn arn:aws:iam::aws:policy/service-role/AWSLambdaVPCAccessExecutionRole
aws iam put-role-policy --role-name "$ROLE" --policy-name pg-readonly-access --policy-document file://.rendered/pg/policies/pg-lambda-access.json
aws iam put-role-policy --role-name ${NAME}-gateway-role --policy-name invoke-lambda-target --policy-document file://.rendered/policies/gateway-invoke.json
sleep 12

echo "== Lambda (in VPC)"
SEC=$(aws secretsmanager describe-secret --secret-id ${NAME}/pg-readonly --query ARN --output text)
ENDPOINT=$(aws rds describe-db-instances --db-instance-identifier "$RDS_ID" --query 'DBInstances[0].Endpoint.Address' --output text)
python3 - "$SEC" "$ENDPOINT" "$DNS_SM" "$DNS_RDS" <<'PY'
import json, sys
json.dump({"Variables": {"DB_ENDPOINT": sys.argv[2], "DB_NAME": "demo", "DB_PORT": "5432", "SECRET_ARN": sys.argv[1],
  "PG_SECRETSMANAGER_ENDPOINT": "https://" + sys.argv[3], "PG_RDS_ENDPOINT": "https://" + sys.argv[4],
  "ALLOW_WRITE_QUERY": "false"}}, open("/tmp/pg_env.json", "w"))
PY
aws lambda create-function --function-name "$FN" --runtime python3.13 --architectures arm64 \
  --handler postgres_agentcore_lambda.handler --role "arn:aws:iam::${ACCT}:role/${ROLE}" \
  --code "S3Bucket=${BUCKET},S3Key=pg-function.zip" --timeout 300 --memory-size 1024 \
  --vpc-config "SubnetIds=${SUBNET},SecurityGroupIds=${LSG}" --environment file:///tmp/pg_env.json \
  --tags Project=${NAME} >/dev/null
rm -f /tmp/pg_env.json
aws lambda wait function-active-v2 --function-name "$FN"

echo "== Gateway target"
python3 - <<PY
import json
tools = json.load(open("pg/tools.json"))
json.dump({"mcp": {"lambda": {"lambdaArn": "arn:aws:lambda:${AWS_REGION}:${ACCT}:function:${FN}",
  "toolSchema": {"inlinePayload": tools}}}}, open("pg/target-config.json", "w"), indent=1)
PY
aws bedrock-agentcore-control create-gateway-target --gateway-identifier "$GW_ID" --name postgres-lambda-target \
  --target-configuration file://pg/target-config.json --credential-provider-configurations file://target-creds.json >/dev/null
echo "Done. Test: uv run --no-project --isolated --with 'mcp<2' --with boto3 python test_gateway_pg.py"
echo "Then click Sync on the Quick connector so the 3 new tools appear."
