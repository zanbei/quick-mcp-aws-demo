"""Seed the demo RDS PostgreSQL instance from INSIDE the VPC (no public access, no open port, works behind any VPN/proxy).

A short-lived Lambda (psycopg, in the instance's VPC) runs the SQL. Nothing is opened to the internet.
It creates the same demo data as the Redshift seed (public.demo_products 8 rows, public.demo_sales 2600 rows) and a read-only role
mcp_readonly. The master password is read from the RDS-managed secret and the read-only password is generated here;
both travel only inside the (TLS) Lambda invocation payload. Neither is printed, logged or written to disk.

Leaves behind for setup_pg.sh to reuse: security group <NAME>-pg-lambda-sg and its 5432 rule on the RDS security group,
and the secret <NAME>/pg-readonly. The temporary Lambda and its role are deleted at the end.

Usage:
  AWS_PROFILE=<p> AWS_REGION=us-east-1 [NAME_PREFIX=quick-mcp-demo] RDS_INSTANCE=<id> RDS_DB=demo \
    uv run --no-project --isolated --with boto3 python scripts/seed_rds_private.py seed
  ... seed_rds_private.py cleanup     # removes the temporary Lambda and role if a run was interrupted
"""
import io
import json
import os
import secrets
import subprocess
import sys
import tempfile
import time
import zipfile

import boto3
from botocore.exceptions import ClientError

PROFILE = os.environ.get("AWS_PROFILE")
REGION = os.environ.get("AWS_REGION", "us-east-1")
NAME = os.environ.get("NAME_PREFIX", "quick-mcp-demo")
INSTANCE = os.environ.get("RDS_INSTANCE", f"{NAME}-pg")
DBNAME = os.environ.get("RDS_DB", "demo")
if not PROFILE:
    sys.exit("set AWS_PROFILE")

FN = f"{NAME}-rds-seed"
ROLE = f"{NAME}-seed-role"
SG_NAME = f"{NAME}-pg-lambda-sg"
RO_SECRET = f"{NAME}/pg-readonly"
RO_USER = "mcp_readonly"
TAGS = [{"Key": "Project", "Value": NAME}]

sess = boto3.Session(profile_name=PROFILE, region_name=REGION)
rds, ec2, iam, lam, sm = (sess.client(s) for s in ("rds", "ec2", "iam", "lambda", "secretsmanager"))

HANDLER = '''
import psycopg
from psycopg import sql

def handler(event, context):
    c = psycopg.connect(host=event["host"], port=event["port"], dbname=event["dbname"], user=event["user"],
                        password=event["password"], sslmode="require", connect_timeout=15, autocommit=True)
    cur = c.cursor()
    cur.execute("CREATE TABLE IF NOT EXISTS public.demo_products (product_id INT PRIMARY KEY, product_name VARCHAR(50), category VARCHAR(30), unit_price NUMERIC(10,2))")
    cur.execute("""INSERT INTO public.demo_products VALUES
 (1,'Echo Dot','Devices',49.99),(2,'Kindle Paperwhite','Devices',139.99),(3,'Fire TV Stick','Devices',39.99),
 (4,'Ring Doorbell','Smart Home',99.99),(5,'Smart Plug','Smart Home',24.99),(6,'Prime Annual','Subscription',139.00),
 (7,'Music Unlimited','Subscription',99.00),(8,'Kindle Unlimited','Subscription',143.88) ON CONFLICT DO NOTHING""")
    cur.execute("CREATE TABLE IF NOT EXISTS public.demo_sales (sale_id INT PRIMARY KEY, sale_date DATE, region VARCHAR(20), product_id INT, quantity INT, revenue NUMERIC(12,2))")
    cur.execute("SELECT COUNT(*) FROM public.demo_sales")
    if cur.fetchone()[0] == 0:
        cur.execute("""INSERT INTO public.demo_sales
WITH nums AS (SELECT generate_series(0,649) AS n),
regions AS (SELECT * FROM (VALUES (0,'North America'),(1,'Europe'),(2,'APAC'),(3,'China')) v(r,region)),
base AS (SELECT n, r, region, 1 + ((n + r*3) % 8) AS product_id, 1 + ((n*7 + r*3) % 9) AS quantity,
                (DATE '2025-01-01' + n) AS sale_date FROM nums CROSS JOIN regions)
SELECT n*4 + r + 1, b.sale_date, b.region, b.product_id, b.quantity, b.quantity * p.unit_price
FROM base b JOIN public.demo_products p ON p.product_id = b.product_id""")
    ro, pw, db = event["ro_user"], event["ro_password"], event["dbname"]
    cur.execute("SELECT 1 FROM pg_roles WHERE rolname = %s", (ro,))
    if cur.fetchone():
        cur.execute(sql.SQL("ALTER ROLE {} WITH LOGIN PASSWORD {}").format(sql.Identifier(ro), sql.Literal(pw)))
    else:
        cur.execute(sql.SQL("CREATE ROLE {} WITH LOGIN PASSWORD {} NOSUPERUSER NOCREATEDB NOCREATEROLE NOREPLICATION").format(sql.Identifier(ro), sql.Literal(pw)))
    cur.execute(sql.SQL("GRANT CONNECT ON DATABASE {} TO {}").format(sql.Identifier(db), sql.Identifier(ro)))
    cur.execute(sql.SQL("GRANT USAGE ON SCHEMA public TO {}").format(sql.Identifier(ro)))
    cur.execute(sql.SQL("GRANT SELECT ON ALL TABLES IN SCHEMA public TO {}").format(sql.Identifier(ro)))
    cur.execute(sql.SQL("ALTER ROLE {} SET default_transaction_read_only = on").format(sql.Identifier(ro)))
    cur.execute("SELECT COUNT(*), SUM(revenue) FROM public.demo_sales")
    n, total = cur.fetchone()
    cur.execute("SELECT p.category, COUNT(*), SUM(s.revenue) FROM public.demo_sales s JOIN public.demo_products p USING (product_id) GROUP BY 1 ORDER BY 3 DESC")
    cats = [[r[0], r[1], str(r[2])] for r in cur.fetchall()]
    c.close()
    return {"rows": n, "total_revenue": str(total), "by_category": cats}
'''


def build_zip():
    with tempfile.TemporaryDirectory() as d:
        subprocess.run(["uv", "pip", "install", "--quiet", "--target", d, "--python-platform", "aarch64-manylinux2014",
                        "--python-version", "3.13", "--only-binary=:all:", "psycopg[binary]"], check=True)
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
            for root, _, files in os.walk(d):
                for f in files:
                    p = os.path.join(root, f)
                    if "__pycache__" not in p:
                        z.write(p, os.path.relpath(p, d))
            z.writestr("seed_handler.py", HANDLER)
        return buf.getvalue()


def instance_info():
    i = rds.describe_db_instances(DBInstanceIdentifier=INSTANCE)["DBInstances"][0]
    az = i["AvailabilityZone"]
    subnet = next(s["SubnetIdentifier"] for s in i["DBSubnetGroup"]["Subnets"] if s["SubnetAvailabilityZone"]["Name"] == az)
    return {"host": i["Endpoint"]["Address"], "port": i["Endpoint"]["Port"], "vpc": i["DBSubnetGroup"]["VpcId"],
            "rds_sg": i["VpcSecurityGroups"][0]["VpcSecurityGroupId"], "subnet": subnet,
            "master_secret": i["MasterUserSecret"]["SecretArn"]}


def ensure_lambda_sg(info):
    found = ec2.describe_security_groups(Filters=[{"Name": "group-name", "Values": [SG_NAME]},
                                                  {"Name": "vpc-id", "Values": [info["vpc"]]}])["SecurityGroups"]
    if found:
        sg = found[0]["GroupId"]
    else:
        sg = ec2.create_security_group(GroupName=SG_NAME, Description=f"{NAME} postgres Lambdas", VpcId=info["vpc"],
                                       TagSpecifications=[{"ResourceType": "security-group", "Tags": TAGS}])["GroupId"]
    try:
        ec2.authorize_security_group_ingress(GroupId=info["rds_sg"], IpPermissions=[{
            "IpProtocol": "tcp", "FromPort": 5432, "ToPort": 5432, "UserIdGroupPairs": [{"GroupId": sg}]}])
    except ClientError as e:
        if e.response["Error"]["Code"] != "InvalidPermission.Duplicate":
            raise
    return sg


def cleanup_temp():
    for fn in (lambda: lam.delete_function(FunctionName=FN),
               lambda: iam.detach_role_policy(RoleName=ROLE, PolicyArn="arn:aws:iam::aws:policy/service-role/AWSLambdaVPCAccessExecutionRole"),
               lambda: iam.delete_role(RoleName=ROLE)):
        try:
            fn()
        except ClientError as e:
            if e.response["Error"]["Code"] not in ("ResourceNotFoundException", "NoSuchEntity"):
                print("   skip:", e.response["Error"]["Code"])
    print("temporary Lambda and role removed")


def seed():
    acct = sess.client("sts").get_caller_identity()["Account"]
    info = instance_info()
    sg = ensure_lambda_sg(info)
    print("== temporary role + Lambda in the VPC")
    trust = {"Version": "2012-10-17", "Statement": [{"Effect": "Allow", "Principal": {"Service": "lambda.amazonaws.com"}, "Action": "sts:AssumeRole"}]}
    try:
        iam.create_role(RoleName=ROLE, AssumeRolePolicyDocument=json.dumps(trust), Tags=TAGS)
    except ClientError as e:
        if e.response["Error"]["Code"] != "EntityAlreadyExists":
            raise
    iam.attach_role_policy(RoleName=ROLE, PolicyArn="arn:aws:iam::aws:policy/service-role/AWSLambdaVPCAccessExecutionRole")
    time.sleep(12)  # IAM propagation
    try:
        for attempt in range(6):
            try:
                lam.create_function(FunctionName=FN, Runtime="python3.13", Architectures=["arm64"], Handler="seed_handler.handler",
                                    Role=f"arn:aws:iam::{acct}:role/{ROLE}", Code={"ZipFile": build_zip()}, Timeout=120, MemorySize=512,
                                    VpcConfig={"SubnetIds": [info["subnet"]], "SecurityGroupIds": [sg]}, Tags={"Project": NAME})
                break
            except ClientError as e:
                if e.response["Error"]["Code"] == "InvalidParameterValueException" and attempt < 5:
                    time.sleep(8)
                    continue
                raise
        lam.get_waiter("function_active_v2").wait(FunctionName=FN)

        master = json.loads(sm.get_secret_value(SecretId=info["master_secret"])["SecretString"])
        try:
            ro_pw = json.loads(sm.get_secret_value(SecretId=RO_SECRET)["SecretString"])["password"]
        except sm.exceptions.ResourceNotFoundException:
            ro_pw = secrets.token_urlsafe(24)
            sm.create_secret(Name=RO_SECRET, Description="Read-only Postgres role for the Quick MCP demo", Tags=TAGS,
                             SecretString=json.dumps({"username": RO_USER, "password": ro_pw, "engine": "postgres",
                                                      "host": info["host"], "port": info["port"], "dbname": DBNAME}))
        payload = {"host": info["host"], "port": info["port"], "dbname": DBNAME, "user": master["username"],
                   "password": master["password"], "ro_user": RO_USER, "ro_password": ro_pw}
        print("== run the seed")
        result = None
        for attempt in range(5):
            r = lam.invoke(FunctionName=FN, Payload=json.dumps(payload).encode())
            body = json.loads(r["Payload"].read())
            if "errorMessage" not in body:
                result = body
                break
            print("   attempt", attempt + 1, "failed:", body.get("errorType"), "-", str(body.get("errorMessage"))[:120].replace(payload["password"], "***"))
            time.sleep(15)
        if not result:
            sys.exit("seed failed; see above")
        print(f"demo_sales rows / total revenue: ({result['rows']}, Decimal('{result['total_revenue']}'))")
        for c in result["by_category"]:
            print(tuple(c))
    finally:
        cleanup_temp()


{"seed": seed, "cleanup": cleanup_temp}[sys.argv[1] if len(sys.argv) > 1 else "seed"]()
