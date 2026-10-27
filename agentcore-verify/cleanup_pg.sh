#!/usr/bin/env bash
# Removes only what setup_pg.sh created. Does not touch the Redshift target, Gateway, RDS instance or its data.
set -uo pipefail
NAME="${NAME_PREFIX:-quick-mcp-demo}"   # resource name prefix; change it to run a second, independent copy in the same account
export AWS_PROFILE="${AWS_PROFILE:?set AWS_PROFILE}" AWS_REGION="${AWS_REGION:-us-east-1}" AWS_DEFAULT_REGION="${AWS_REGION:-us-east-1}" AWS_PAGER=""
RDS_ID="${RDS_ID:-${NAME}-pg}"
RDS_SG="${RDS_SG:-$(aws rds describe-db-instances --db-instance-identifier "$RDS_ID" --query 'DBInstances[0].VpcSecurityGroups[0].VpcSecurityGroupId' --output text)}"
GW_ID=$(aws bedrock-agentcore-control list-gateways --query "items[?name=='${NAME}-gw'].gatewayId | [0]" --output text)

echo "== Gateway target"
T=$(aws bedrock-agentcore-control list-gateway-targets --gateway-identifier "$GW_ID" --query "items[?name=='postgres-lambda-target'].targetId | [0]" --output text)
[ -n "$T" ] && [ "$T" != "None" ] && aws bedrock-agentcore-control delete-gateway-target --gateway-identifier "$GW_ID" --target-id "$T" >/dev/null

echo "== Lambda (ENIs are released asynchronously; security-group deletion below may need a retry)"
aws lambda delete-function --function-name ${NAME}-postgres-mcp 2>/dev/null

echo "== VPC endpoints"
EPS=$(aws ec2 describe-vpc-endpoints --filters Name=tag:Project,Values=${NAME} --query "VpcEndpoints[].VpcEndpointId" --output text)
[ -n "$EPS" ] && aws ec2 delete-vpc-endpoints --vpc-endpoint-ids $EPS >/dev/null

echo "== IAM role"
aws iam detach-role-policy --role-name ${NAME}-pg-lambda-role --policy-arn arn:aws:iam::aws:policy/service-role/AWSLambdaVPCAccessExecutionRole 2>/dev/null
aws iam delete-role-policy --role-name ${NAME}-pg-lambda-role --policy-name pg-readonly-access 2>/dev/null
aws iam delete-role --role-name ${NAME}-pg-lambda-role 2>/dev/null

echo "== security groups (wait for ENIs to be released)"
LSG=$(aws ec2 describe-security-groups --filters Name=group-name,Values=${NAME}-pg-lambda-sg --query "SecurityGroups[0].GroupId" --output text)
ESG=$(aws ec2 describe-security-groups --filters Name=group-name,Values=${NAME}-vpce-sg --query "SecurityGroups[0].GroupId" --output text)
[ "$LSG" != "None" ] && aws ec2 revoke-security-group-ingress --group-id "$RDS_SG" --ip-permissions "IpProtocol=tcp,FromPort=5432,ToPort=5432,UserIdGroupPairs=[{GroupId=$LSG}]" >/dev/null 2>&1
for _ in $(seq 1 30); do
  ok=1
  [ "$ESG" != "None" ] && { aws ec2 delete-security-group --group-id "$ESG" >/dev/null 2>&1 || ok=0; }
  [ "$LSG" != "None" ] && { aws ec2 delete-security-group --group-id "$LSG" >/dev/null 2>&1 || ok=0; }
  [ $ok = 1 ] && break; sleep 20
done
echo "Done. The gateway role policy still lists the postgres function ARN (harmless); update policies/gateway-invoke.json if you want it removed."
