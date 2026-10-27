#!/usr/bin/env bash
# Opens limited write access for the PoC. Reversible with disable_write.sh.
#  - Lambda runs the Redshift MCP server with ACCESS_MODE=read-write and UNSAFE_SKIP_WRITE_CONFIRMATION=true
#    (the Lambda/Gateway path cannot show awslabs' per-statement confirmation prompt).
#  - Database side stays the real boundary: the Lambda DB user gets INSERT on the two demo tables only,
#    plus the CREATE-in-public that every user already has. No UPDATE / DELETE / DROP on existing tables.
# Take a snapshot first:  aws redshift-serverless create-snapshot --namespace-name default-namespace --snapshot-name <name> --retention-period 7
set -euo pipefail
NAME="${NAME_PREFIX:-quick-mcp-demo}"   # resource name prefix; change it to run a second, independent copy in the same account
export AWS_PROFILE="${AWS_PROFILE:?set AWS_PROFILE}" AWS_REGION="${AWS_REGION:-us-east-1}" AWS_DEFAULT_REGION="${AWS_REGION:-us-east-1}" AWS_PAGER=""
SID=$(aws redshift-data execute-statement --workgroup-name "${RS_WORKGROUP:-default-workgroup}" --database dev \
  --sql "GRANT INSERT ON public.demo_sales, public.demo_products TO \"IAMR:${NAME}-lambda-role\"" \
  --query Id --output text)
sleep 8
aws redshift-data describe-statement --id "$SID" --query Status --output text
aws lambda update-function-configuration --function-name ${NAME}-redshift-mcp \
  --environment 'Variables={FASTMCP_LOG_LEVEL=ERROR,ACCESS_MODE=read-write,UNSAFE_SKIP_WRITE_CONFIRMATION=true}' \
  --query LastUpdateStatus --output text
aws lambda wait function-updated-v2 --function-name ${NAME}-redshift-mcp
echo "Write access enabled. Verify with: uv run --no-project --isolated --with 'mcp<2' --with boto3 python test_write.py"
