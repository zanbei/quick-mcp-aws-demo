#!/usr/bin/env bash
# Seed a small, self-contained demo dataset into Redshift Serverless (tables: public.demo_products, public.demo_sales).
# Additive only: creates two NEW tables in the public schema (the IAM user has no CREATE on database dev).
# Usage: AWS_PROFILE=<profile> ./seed_redshift_demo.sh [seed|cleanup]
set -euo pipefail

PROFILE="${AWS_PROFILE:?set AWS_PROFILE}"
REGION="${AWS_REGION:-us-east-1}"
WG="${RS_WORKGROUP:-default-workgroup}"
DB="${RS_DB:-dev}"
MODE="${1:-seed}"

run() {
  local sql="$1"
  local id status
  id=$(aws redshift-data execute-statement --profile "$PROFILE" --region "$REGION" \
        --workgroup-name "$WG" --database "$DB" --sql "$sql" --query Id --output text)
  for _ in $(seq 1 60); do
    status=$(aws redshift-data describe-statement --profile "$PROFILE" --region "$REGION" \
              --id "$id" --query Status --output text)
    case "$status" in
      FINISHED) echo "OK   : ${sql:0:80}..."; return 0 ;;
      FAILED|ABORTED)
        aws redshift-data describe-statement --profile "$PROFILE" --region "$REGION" --id "$id" \
          --query Error --output text >&2
        return 1 ;;
    esac
    sleep 2
  done
  echo "TIMEOUT: $sql" >&2; return 1
}

if [[ "$MODE" == "cleanup" ]]; then
  run "DROP TABLE IF EXISTS public.demo_sales; DROP TABLE IF EXISTS public.demo_products"
  exit 0
fi


run "CREATE TABLE IF NOT EXISTS public.demo_products (
       product_id   INT,
       product_name VARCHAR(50),
       category     VARCHAR(30),
       unit_price   DECIMAL(10,2))"

run "INSERT INTO public.demo_products VALUES
       (1,'Echo Dot','Devices',49.99),
       (2,'Kindle Paperwhite','Devices',139.99),
       (3,'Fire TV Stick','Devices',39.99),
       (4,'Ring Doorbell','Smart Home',99.99),
       (5,'Smart Plug','Smart Home',24.99),
       (6,'Prime Annual','Subscription',139.00),
       (7,'Music Unlimited','Subscription',99.00),
       (8,'Kindle Unlimited','Subscription',143.88)"

run "CREATE TABLE IF NOT EXISTS public.demo_sales (
       sale_id    INT,
       sale_date  DATE,
       region     VARCHAR(20),
       product_id INT,
       quantity   INT,
       revenue    DECIMAL(12,2))"

# 650 days x 4 regions = 2600 deterministic rows (no randomness, repeatable demo numbers)
run "INSERT INTO public.demo_sales
     WITH digits AS (
       SELECT 0 AS d UNION ALL SELECT 1 UNION ALL SELECT 2 UNION ALL SELECT 3 UNION ALL SELECT 4
       UNION ALL SELECT 5 UNION ALL SELECT 6 UNION ALL SELECT 7 UNION ALL SELECT 8 UNION ALL SELECT 9
     ),
     nums AS (
       SELECT a.d*100 + b.d*10 + c.d AS n
       FROM digits a CROSS JOIN digits b CROSS JOIN digits c
       WHERE a.d*100 + b.d*10 + c.d < 650
     ),
     regions AS (
       SELECT 0 AS r, 'North America' AS region UNION ALL SELECT 1, 'Europe'
       UNION ALL SELECT 2, 'APAC' UNION ALL SELECT 3, 'China'
     ),
     base AS (
       SELECT n, r, region,
              1 + ((n + r*3) % 8)         AS product_id,
              1 + ((n*7 + r*3) % 9)       AS quantity,
              DATEADD(day, n, DATE '2025-01-01') AS sale_date
       FROM nums CROSS JOIN regions
     )
     SELECT n*4 + r + 1, b.sale_date, b.region, b.product_id, b.quantity,
            b.quantity * p.unit_price
     FROM base b JOIN public.demo_products p ON p.product_id = b.product_id"

run "SELECT COUNT(*) AS rows_loaded, SUM(revenue) AS total_revenue FROM public.demo_sales"
echo "Seed complete."
