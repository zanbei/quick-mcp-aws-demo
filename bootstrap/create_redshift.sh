#!/usr/bin/env bash
# Creates a small Redshift Serverless namespace + workgroup for the demo (admin password managed by Secrets Manager).
# Usage: ./create_redshift.sh create | delete
# Env:   AWS_PROFILE (required), AWS_REGION (default us-east-1), RS_NAMESPACE, RS_WORKGROUP, RS_DB, RS_BASE_RPU (default 8)
# Cost:  Serverless bills RPU-seconds only while queries run, plus storage. Delete when finished.
set -euo pipefail
NAME="${NAME_PREFIX:-quick-mcp-demo}"   # resource name prefix; change it to run a second, independent copy in the same account
: "${AWS_PROFILE:?set AWS_PROFILE}"
export AWS_REGION="${AWS_REGION:-us-east-1}" AWS_DEFAULT_REGION="${AWS_REGION:-us-east-1}" AWS_PAGER=""
NS="${RS_NAMESPACE:-${NAME}-ns}"; WG="${RS_WORKGROUP:-${NAME}-wg}"; DB="${RS_DB:-dev}"
RPU="${RS_BASE_RPU:-8}"

case "${1:-create}" in
create)
  aws redshift-serverless create-namespace --namespace-name "$NS" --db-name "$DB" --admin-username admin \
    --manage-admin-password --tags key=Project,value=${NAME} >/dev/null
  # The workgroup lands in the default VPC; it is NOT publicly accessible. The Data API (used by MCP and the seed) does not need network access.
  aws redshift-serverless create-workgroup --workgroup-name "$WG" --namespace-name "$NS" --base-capacity "$RPU" \
    --no-publicly-accessible --tags key=Project,value=${NAME} >/dev/null
  echo "waiting for workgroup $WG ..."
  until [ "$(aws redshift-serverless get-workgroup --workgroup-name "$WG" --query workgroup.status --output text)" = "AVAILABLE" ]; do sleep 15; done
  WG_ID=$(aws redshift-serverless get-workgroup --workgroup-name "$WG" --query workgroup.workgroupId --output text)
  echo "RS_NAMESPACE=$NS"; echo "RS_WORKGROUP=$WG"; echo "RS_DB=$DB"; echo "WG_ID=$WG_ID"
  ;;
delete)
  aws redshift-serverless delete-workgroup --workgroup-name "$WG" >/dev/null || true
  until ! aws redshift-serverless get-workgroup --workgroup-name "$WG" >/dev/null 2>&1; do sleep 15; done
  aws redshift-serverless delete-namespace --namespace-name "$NS" >/dev/null || true
  echo "deleted $WG / $NS (data is gone; no final snapshot was taken)"
  ;;
*) echo "usage: $0 create|delete" >&2; exit 2 ;;
esac
