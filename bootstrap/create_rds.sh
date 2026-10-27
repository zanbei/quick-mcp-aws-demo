#!/usr/bin/env bash
# Creates a small RDS PostgreSQL instance in the DEFAULT VPC for the demo.
# Usage: ./create_rds.sh create | delete
#   create     instance (encrypted, password in Secrets Manager, never public) plus its security group, which has NO inbound rules.
#              Load the demo data with scripts/seed_rds_private.py (runs inside the VPC; nothing is ever opened to the internet).
#   delete     deletes the instance (no final snapshot), its subnet group and security group.
# Env: AWS_PROFILE (required), AWS_REGION (default us-east-1), RDS_ID (default quick-mcp-demo-pg), DB_NAME (default demo)
set -euo pipefail
NAME="${NAME_PREFIX:-quick-mcp-demo}"   # resource name prefix; change it to run a second, independent copy in the same account
: "${AWS_PROFILE:?set AWS_PROFILE}"
export AWS_REGION="${AWS_REGION:-us-east-1}" AWS_DEFAULT_REGION="${AWS_REGION:-us-east-1}" AWS_PAGER=""
RDS_ID="${RDS_ID:-${NAME}-pg}"; DB_NAME="${DB_NAME:-demo}"
SG_NAME=${NAME}-rds-sg; SUBNET_GROUP=${NAME}-subnets
TAG="Key=Project,Value=${NAME}"
VPC=$(aws ec2 describe-vpcs --filters Name=isDefault,Values=true --query 'Vpcs[0].VpcId' --output text)
sg_id() { aws ec2 describe-security-groups --filters Name=group-name,Values=$SG_NAME Name=vpc-id,Values="$VPC" --query 'SecurityGroups[0].GroupId' --output text; }

case "${1:-create}" in
create)
  [ "$VPC" != "None" ] || { echo "no default VPC; create the instance yourself and pass VPC/SUBNET/RDS_SG to setup_pg.sh" >&2; exit 1; }
  SG=$(aws ec2 create-security-group --group-name $SG_NAME --description "${NAME} RDS" --vpc-id "$VPC" \
        --tag-specifications "ResourceType=security-group,Tags=[{$TAG}]" --query GroupId --output text)
  SUBNETS=$(aws ec2 describe-subnets --filters Name=vpc-id,Values="$VPC" Name=default-for-az,Values=true --query 'Subnets[].SubnetId' --output text)
  aws rds create-db-subnet-group --db-subnet-group-name $SUBNET_GROUP --db-subnet-group-description "${NAME}" \
    --subnet-ids $SUBNETS --tags "$TAG" >/dev/null
  aws rds create-db-instance --db-instance-identifier "$RDS_ID" --engine postgres --db-instance-class db.t4g.micro \
    --allocated-storage 20 --storage-type gp3 --storage-encrypted --master-username pgadmin --manage-master-user-password \
    --db-name "$DB_NAME" --db-subnet-group-name $SUBNET_GROUP --vpc-security-group-ids "$SG" \
    --no-publicly-accessible --no-multi-az --backup-retention-period 1 --tags "$TAG" >/dev/null
  echo "waiting for $RDS_ID (about 5-10 minutes) ..."
  aws rds wait db-instance-available --db-instance-identifier "$RDS_ID"
  echo "RDS_ID=$RDS_ID"; echo "RDS_INSTANCE=$RDS_ID"; echo "RDS_DB=$DB_NAME"; echo "RDS_SG=$SG"
  ;;
delete)
  aws rds delete-db-instance --db-instance-identifier "$RDS_ID" --skip-final-snapshot --delete-automated-backups >/dev/null || true
  aws rds wait db-instance-deleted --db-instance-identifier "$RDS_ID" || true
  aws rds delete-db-subnet-group --db-subnet-group-name $SUBNET_GROUP || true
  SG=$(sg_id); [ "$SG" != "None" ] && aws ec2 delete-security-group --group-id "$SG" || true
  echo "deleted $RDS_ID, subnet group and security group (setup_pg.sh resources: run cleanup_pg.sh first)"
  ;;
*) echo "usage: $0 create|delete" >&2; exit 2 ;;
esac
