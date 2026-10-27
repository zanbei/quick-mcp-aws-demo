#!/usr/bin/env bash
# Returns the PoC to read-only: Lambda env back to default, INSERT revoked.
# The Gateway tool description (tools.json) is not reverted; it is generic and harmless.
set -euo pipefail
NAME="${NAME_PREFIX:-quick-mcp-demo}"   # resource name prefix; change it to run a second, independent copy in the same account
export AWS_PROFILE="${AWS_PROFILE:?set AWS_PROFILE}" AWS_REGION="${AWS_REGION:-us-east-1}" AWS_DEFAULT_REGION="${AWS_REGION:-us-east-1}" AWS_PAGER=""
aws lambda update-function-configuration --function-name ${NAME}-redshift-mcp \
  --environment 'Variables={FASTMCP_LOG_LEVEL=ERROR}' --query LastUpdateStatus --output text
aws lambda wait function-updated-v2 --function-name ${NAME}-redshift-mcp
SID=$(aws redshift-data execute-statement --workgroup-name "${RS_WORKGROUP:-default-workgroup}" --database dev \
  --sql "REVOKE INSERT ON public.demo_sales, public.demo_products FROM \"IAMR:${NAME}-lambda-role\"" \
  --query Id --output text)
sleep 8
aws redshift-data describe-statement --id "$SID" --query Status --output text
echo "Read-only restored. Tables created by the write test (if any) remain owned by the Lambda DB user; drop them as needed."
