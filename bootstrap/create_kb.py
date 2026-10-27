"""Creates a demo Bedrock Knowledge Base from scratch and loads the sample documents in kb_docs/.

Stack: S3 bucket (documents) -> Titan Text Embeddings v2 -> S3 Vectors index -> Bedrock Knowledge Base.
S3 Vectors has no always-on cost (no OpenSearch Serverless OCUs), which is why it is used here.

Usage (needs a recent boto3, so run it through uv; the AWS CLI v1 is too old for S3 Vectors):
  AWS_PROFILE=<profile> AWS_REGION=us-east-1 uv run --no-project --isolated --with boto3 python bootstrap/create_kb.py create
  ... create_kb.py verify    # runs two Retrieve calls and prints only scores and source file names
  ... create_kb.py cleanup   # removes everything this script created

On success it prints KB_ID=... and KB_REGION=... and writes them to bootstrap/state.env (git-ignored).
Names: all resources are called quick-mcp-demo-* and tagged Project=quick-mcp-demo.
"""
import json
import os
import pathlib
import sys
import time

import boto3
from botocore.exceptions import ClientError

PROFILE = os.environ.get("AWS_PROFILE")
REGION = os.environ.get("AWS_REGION", "us-east-1")
if not PROFILE:
    sys.exit("set AWS_PROFILE")
sess = boto3.Session(profile_name=PROFILE, region_name=REGION)
ACCT = sess.client("sts").get_caller_identity()["Account"]

NAME = os.environ.get("NAME_PREFIX", "quick-mcp-demo")
KB_NAME = f"{NAME}-kb"
DOCS_BUCKET = f"{NAME}-kb-docs-{ACCT}-{REGION}"
VEC_BUCKET = f"{NAME}-vec-{ACCT}-{REGION}"[:63]
VEC_INDEX = "kb-index"
ROLE = f"{NAME}-kb-role"
MODEL_ARN = f"arn:aws:bedrock:{REGION}::foundation-model/amazon.titan-embed-text-v2:0"
DOCS_DIR = pathlib.Path(__file__).parent / "kb_docs"
STATE = pathlib.Path(__file__).parent / "state.env"
TAGS_DICT = {"Project": NAME}

s3 = sess.client("s3")
iam = sess.client("iam")
vec = sess.client("s3vectors")
agent = sess.client("bedrock-agent")
runtime = sess.client("bedrock-agent-runtime")


def find_kb():
    for page in agent.get_paginator("list_knowledge_bases").paginate():
        for kb in page["knowledgeBaseSummaries"]:
            if kb["name"] == KB_NAME:
                return kb["knowledgeBaseId"]
    return None


def create():
    print("== documents bucket")
    try:
        kwargs = {"Bucket": DOCS_BUCKET}
        if REGION != "us-east-1":
            kwargs["CreateBucketConfiguration"] = {"LocationConstraint": REGION}
        s3.create_bucket(**kwargs)
    except ClientError as e:
        if e.response["Error"]["Code"] not in ("BucketAlreadyOwnedByYou",):
            raise
    s3.put_public_access_block(Bucket=DOCS_BUCKET, PublicAccessBlockConfiguration={
        "BlockPublicAcls": True, "IgnorePublicAcls": True, "BlockPublicPolicy": True, "RestrictPublicBuckets": True})
    s3.put_bucket_tagging(Bucket=DOCS_BUCKET, Tagging={"TagSet": [{"Key": k, "Value": v} for k, v in TAGS_DICT.items()]})
    files = sorted(DOCS_DIR.glob("*.md"))
    for f in files:
        s3.upload_file(str(f), DOCS_BUCKET, f"docs/{f.name}")
    print(f"   uploaded {len(files)} documents to s3://{DOCS_BUCKET}/docs/")

    print("== S3 Vectors bucket + index (Titan v2 = 1024 dimensions, cosine)")
    try:
        vec.create_vector_bucket(vectorBucketName=VEC_BUCKET)
    except ClientError as e:
        if e.response["Error"]["Code"] != "ConflictException":
            raise
    try:
        vec.create_index(
            vectorBucketName=VEC_BUCKET, indexName=VEC_INDEX, dataType="float32", dimension=1024,
            distanceMetric="cosine",
            # Bedrock stores each chunk's text in this metadata key; it is too large to be filterable.
            metadataConfiguration={"nonFilterableMetadataKeys": ["AMAZON_BEDROCK_TEXT"]})
    except ClientError as e:
        if e.response["Error"]["Code"] != "ConflictException":
            raise
    index_arn = vec.get_index(vectorBucketName=VEC_BUCKET, indexName=VEC_INDEX)["index"]["indexArn"]
    vec_bucket_arn = index_arn.rsplit("/index/", 1)[0]

    print("== IAM role for Bedrock")
    trust = {"Version": "2012-10-17", "Statement": [{
        "Effect": "Allow", "Principal": {"Service": "bedrock.amazonaws.com"}, "Action": "sts:AssumeRole",
        "Condition": {"StringEquals": {"aws:SourceAccount": ACCT},
                      "ArnLike": {"aws:SourceArn": f"arn:aws:bedrock:{REGION}:{ACCT}:knowledge-base/*"}}}]}
    try:
        iam.create_role(RoleName=ROLE, AssumeRolePolicyDocument=json.dumps(trust),
                        Tags=[{"Key": "Project", "Value": NAME}])
    except ClientError as e:
        if e.response["Error"]["Code"] != "EntityAlreadyExists":
            raise
    iam.put_role_policy(RoleName=ROLE, PolicyName="kb-access", PolicyDocument=json.dumps({
        "Version": "2012-10-17", "Statement": [
            {"Sid": "Embed", "Effect": "Allow", "Action": "bedrock:InvokeModel", "Resource": MODEL_ARN},
            {"Sid": "ReadDocs", "Effect": "Allow", "Action": ["s3:ListBucket"], "Resource": f"arn:aws:s3:::{DOCS_BUCKET}"},
            {"Sid": "ReadDocObjects", "Effect": "Allow", "Action": ["s3:GetObject"], "Resource": f"arn:aws:s3:::{DOCS_BUCKET}/*"},
            {"Sid": "VectorIndex", "Effect": "Allow",
             "Action": ["s3vectors:GetIndex", "s3vectors:QueryVectors", "s3vectors:PutVectors",
                        "s3vectors:GetVectors", "s3vectors:DeleteVectors", "s3vectors:ListVectors"],
             "Resource": [index_arn, f"{index_arn}/*"]}]}))
    role_arn = f"arn:aws:iam::{ACCT}:role/{ROLE}"

    print("== knowledge base")
    kb_id = find_kb()
    if not kb_id:
        for attempt in range(12):  # IAM is eventually consistent; Bedrock validates the role at creation
            try:
                kb = agent.create_knowledge_base(
                    name=KB_NAME, description="Demo Store FAQ, return policy, shipping and warranty documents",
                    roleArn=role_arn,
                    knowledgeBaseConfiguration={"type": "VECTOR", "vectorKnowledgeBaseConfiguration": {
                        "embeddingModelArn": MODEL_ARN,
                        "embeddingModelConfiguration": {"bedrockEmbeddingModelConfiguration": {
                            "dimensions": 1024, "embeddingDataType": "FLOAT32"}}}},
                    storageConfiguration={"type": "S3_VECTORS", "s3VectorsConfiguration": {"indexArn": index_arn}},
                    # awslabs bedrock-kb-retrieval-mcp-server ListKnowledgeBases only returns KBs carrying this tag
                    tags={**TAGS_DICT, "mcp-multirag-kb": "true"})
                kb_id = kb["knowledgeBase"]["knowledgeBaseId"]
                break
            except ClientError as e:
                if e.response["Error"]["Code"] in ("ValidationException", "AccessDeniedException") and attempt < 11:
                    time.sleep(10)
                    continue
                raise
    while agent.get_knowledge_base(knowledgeBaseId=kb_id)["knowledgeBase"]["status"] != "ACTIVE":
        time.sleep(5)

    print("== data source + ingestion")
    ds_id = next((d["dataSourceId"] for d in agent.list_data_sources(knowledgeBaseId=kb_id)["dataSourceSummaries"]
                  if d["name"] == "demo-docs"), None)
    if not ds_id:
        ds_id = agent.create_data_source(
            knowledgeBaseId=kb_id, name="demo-docs",
            dataSourceConfiguration={"type": "S3", "s3Configuration": {
                "bucketArn": f"arn:aws:s3:::{DOCS_BUCKET}", "inclusionPrefixes": ["docs/"]}},
            vectorIngestionConfiguration={"chunkingConfiguration": {"chunkingStrategy": "FIXED_SIZE",
                "fixedSizeChunkingConfiguration": {"maxTokens": 300, "overlapPercentage": 10}}},
        )["dataSource"]["dataSourceId"]
    job = agent.start_ingestion_job(knowledgeBaseId=kb_id, dataSourceId=ds_id)["ingestionJob"]["ingestionJobId"]
    while True:
        j = agent.get_ingestion_job(knowledgeBaseId=kb_id, dataSourceId=ds_id, ingestionJobId=job)["ingestionJob"]
        if j["status"] in ("COMPLETE", "FAILED", "STOPPED"):
            break
        time.sleep(5)
    print("   ingestion:", j["status"], j.get("statistics", {}))
    if j["status"] != "COMPLETE":
        sys.exit(f"ingestion did not complete: {j.get('failureReasons')}")

    STATE.write_text(f"KB_ID={kb_id}\nKB_REGION={REGION}\n")
    print(f"KB_ID={kb_id}\nKB_REGION={REGION}")


def verify():
    kb_id = find_kb()
    if not kb_id:
        sys.exit("knowledge base not found; run create first")
    ok = True
    for q, expect in (("How many days do I have to return a product?", "return-policy.md"),
                      ("Does the Kindle Paperwhite have accessibility features for low vision?", "product-faq.md")):
        res = runtime.retrieve(knowledgeBaseId=kb_id, retrievalQuery={"text": q},
                               retrievalConfiguration={"vectorSearchConfiguration": {"numberOfResults": 3}})["retrievalResults"]
        top = [(round(r.get("score", 0), 3), r["location"]["s3Location"]["uri"].rsplit("/", 1)[-1]) for r in res]
        hit = bool(top) and top[0][1] == expect
        ok &= hit
        print(f"{'OK  ' if hit else 'FAIL'} {q!r} -> {top}")
    sys.exit(0 if ok else 1)


def cleanup():
    kb_id = find_kb()
    if kb_id:
        for d in agent.list_data_sources(knowledgeBaseId=kb_id)["dataSourceSummaries"]:
            agent.delete_data_source(knowledgeBaseId=kb_id, dataSourceId=d["dataSourceId"])
        time.sleep(10)
        for _ in range(30):
            try:
                agent.delete_knowledge_base(knowledgeBaseId=kb_id)
                break
            except ClientError as e:
                if e.response["Error"]["Code"] in ("ConflictException", "ValidationException"):
                    time.sleep(10)
                    continue
                raise
        print("knowledge base deleted")
    for fn in (lambda: vec.delete_index(vectorBucketName=VEC_BUCKET, indexName=VEC_INDEX),
               lambda: vec.delete_vector_bucket(vectorBucketName=VEC_BUCKET)):
        try:
            fn()
        except ClientError as e:
            print("   skip:", e.response["Error"]["Code"])
    try:
        b = sess.resource("s3").Bucket(DOCS_BUCKET)
        b.objects.all().delete()
        b.delete()
    except ClientError as e:
        print("   skip bucket:", e.response["Error"]["Code"])
    try:
        iam.delete_role_policy(RoleName=ROLE, PolicyName="kb-access")
        iam.delete_role(RoleName=ROLE)
    except ClientError as e:
        print("   skip role:", e.response["Error"]["Code"])
    STATE.unlink(missing_ok=True)
    print("cleanup done")


{"create": create, "verify": verify, "cleanup": cleanup}[sys.argv[1] if len(sys.argv) > 1 else "create"]()
