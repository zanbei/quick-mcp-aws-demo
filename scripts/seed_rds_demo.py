"""Seed the demo RDS PostgreSQL instance (quick-mcp-demo-pg2) with the same demo data as Redshift,
and create a read-only role for MCP / Quick.

- Master password is read from the RDS-managed Secrets Manager secret; it is never printed.
- The read-only role's password is generated here and stored in Secrets Manager
  (quick-mcp-demo/pg-readonly); it is never printed.

Usage:
  AWS_PROFILE=<profile> uv run --no-project --isolated --with boto3 --with "psycopg[binary]" python scripts/seed_rds_demo.py seed
  AWS_PROFILE=<profile> uv run ... python scripts/seed_rds_demo.py cleanup
"""
import json
import os
import secrets
import sys

import boto3
import psycopg
import psycopg.sql

PROFILE = os.environ.get("AWS_PROFILE")
REGION = os.environ.get("AWS_REGION", "us-east-1")
NAME = os.environ.get("NAME_PREFIX", "quick-mcp-demo")
INSTANCE = os.environ.get("RDS_INSTANCE", f"{NAME}-pg")
DBNAME = os.environ.get("RDS_DB", "demo")
RO_SECRET = f"{NAME}/pg-readonly"
RO_USER = "mcp_readonly"

sess = boto3.Session(profile_name=PROFILE, region_name=REGION)
rds = sess.client("rds")
sm = sess.client("secretsmanager")

inst = rds.describe_db_instances(DBInstanceIdentifier=INSTANCE)["DBInstances"][0]
host = inst["Endpoint"]["Address"]
port = inst["Endpoint"]["Port"]
master = json.loads(sm.get_secret_value(SecretId=inst["MasterUserSecret"]["SecretArn"])["SecretString"])

conn = psycopg.connect(
    host=host, port=port, dbname=DBNAME, user=master["username"], password=master["password"],
    sslmode="require", connect_timeout=15, autocommit=True,
)
cur = conn.cursor()

mode = sys.argv[1] if len(sys.argv) > 1 else "seed"

if mode == "cleanup":
    cur.execute("DROP TABLE IF EXISTS public.demo_sales")
    cur.execute("DROP TABLE IF EXISTS public.demo_products")
    cur.execute(f"DROP OWNED BY {RO_USER}")
    cur.execute(f"DROP ROLE IF EXISTS {RO_USER}")
    try:
        sm.delete_secret(SecretId=RO_SECRET, ForceDeleteWithoutRecovery=True)
    except sm.exceptions.ResourceNotFoundException:
        pass
    print("cleanup done")
    sys.exit(0)

cur.execute("""
CREATE TABLE IF NOT EXISTS public.demo_products (
  product_id INT PRIMARY KEY, product_name VARCHAR(50), category VARCHAR(30), unit_price NUMERIC(10,2))""")
cur.execute("""
INSERT INTO public.demo_products VALUES
 (1,'Echo Dot','Devices',49.99),(2,'Kindle Paperwhite','Devices',139.99),(3,'Fire TV Stick','Devices',39.99),
 (4,'Ring Doorbell','Smart Home',99.99),(5,'Smart Plug','Smart Home',24.99),(6,'Prime Annual','Subscription',139.00),
 (7,'Music Unlimited','Subscription',99.00),(8,'Kindle Unlimited','Subscription',143.88)
ON CONFLICT DO NOTHING""")
cur.execute("""
CREATE TABLE IF NOT EXISTS public.demo_sales (
  sale_id INT PRIMARY KEY, sale_date DATE, region VARCHAR(20), product_id INT, quantity INT, revenue NUMERIC(12,2))""")
cur.execute("SELECT COUNT(*) FROM public.demo_sales")
if cur.fetchone()[0] == 0:
    # 650 days x 4 regions = 2600 deterministic rows, identical logic to the Redshift seed
    cur.execute("""
    INSERT INTO public.demo_sales
    WITH nums AS (SELECT generate_series(0,649) AS n),
    regions AS (SELECT * FROM (VALUES (0,'North America'),(1,'Europe'),(2,'APAC'),(3,'China')) v(r,region)),
    base AS (
      SELECT n, r, region,
             1 + ((n + r*3) % 8)   AS product_id,
             1 + ((n*7 + r*3) % 9) AS quantity,
             (DATE '2025-01-01' + n) AS sale_date
      FROM nums CROSS JOIN regions)
    SELECT n*4 + r + 1, b.sale_date, b.region, b.product_id, b.quantity, b.quantity * p.unit_price
    FROM base b JOIN public.demo_products p ON p.product_id = b.product_id""")

# read-only role for MCP / Quick
try:
    existing = json.loads(sm.get_secret_value(SecretId=RO_SECRET)["SecretString"])
    ro_password = existing["password"]
except sm.exceptions.ResourceNotFoundException:
    ro_password = secrets.token_urlsafe(24)
    sm.create_secret(
        Name=RO_SECRET,
        Description="Read-only Postgres role for Quick MCP demo",
        Tags=[{"Key": "Project", "Value": NAME}],
        SecretString=json.dumps({"username": RO_USER, "password": ro_password, "engine": "postgres",
                                 "host": host, "port": port, "dbname": DBNAME}),
    )
cur.execute(f"SELECT 1 FROM pg_roles WHERE rolname = '{RO_USER}'")
if cur.fetchone():
    cur.execute(psycopg.sql.SQL("ALTER ROLE {} WITH LOGIN PASSWORD {}").format(
        psycopg.sql.Identifier(RO_USER), psycopg.sql.Literal(ro_password)))
else:
    cur.execute(psycopg.sql.SQL(
        "CREATE ROLE {} WITH LOGIN PASSWORD {} NOSUPERUSER NOCREATEDB NOCREATEROLE NOREPLICATION").format(
        psycopg.sql.Identifier(RO_USER), psycopg.sql.Literal(ro_password)))
cur.execute(f"GRANT CONNECT ON DATABASE {DBNAME} TO {RO_USER}")
cur.execute(f"GRANT USAGE ON SCHEMA public TO {RO_USER}")
cur.execute(f"GRANT SELECT ON ALL TABLES IN SCHEMA public TO {RO_USER}")
cur.execute(f"ALTER ROLE {RO_USER} SET default_transaction_read_only = on")

cur.execute("SELECT COUNT(*), SUM(revenue) FROM public.demo_sales")
print("demo_sales rows / total revenue:", cur.fetchone())
cur.execute("""SELECT p.category, COUNT(*), SUM(s.revenue) FROM public.demo_sales s
               JOIN public.demo_products p USING (product_id) GROUP BY 1 ORDER BY 3 DESC""")
for row in cur.fetchall():
    print(row)
conn.close()
