"""
GRUS — AgentCore deployment

Builds the container, pushes it to ECR, and creates the AgentCore
runtime.

    python grus_agentcore_deploy.py role      IAM role for the runtime
    python grus_agentcore_deploy.py ecr       create the repository
    python grus_agentcore_deploy.py build     build and push the image
    python grus_agentcore_deploy.py create    create the runtime
    python grus_agentcore_deploy.py invoke    test it
    python grus_agentcore_deploy.py status
    python grus_agentcore_deploy.py delete

Two things that catch people out:

  AgentCore runs ARM64. An image built on an x86 laptop without
  --platform linux/arm64 will push happily and fail at runtime with an
  exec format error.

  The runtime needs VPC access to reach Aurora, which means the same
  subnets and security group the database sits in. Those are read from
  the cluster rather than hardcoded.
"""

import os
import sys
import json
import time
import base64
import subprocess
import boto3
from dotenv import load_dotenv



REGION = "ap-south-1"
REPO = "grus-agent"
RUNTIME_NAME = "grus_agent"          # underscores only — hyphens are rejected
ROLE_NAME = "grus-agentcore-role"
CLUSTER_ID = "grus-db"

sess = boto3.Session(region_name=REGION)
ecr = sess.client("ecr")
iam = sess.client("iam")
rds = sess.client("rds")
sts = sess.client("sts")
agentcore = sess.client("bedrock-agentcore-control")

ACCOUNT = sts.get_caller_identity()["Account"]
IMAGE_URI = f"{ACCOUNT}.dkr.ecr.{REGION}.amazonaws.com/{REPO}:latest"


def _run(cmd, **kw):
    print(f"  $ {cmd}")
    r = subprocess.run(cmd, shell=True, **kw)
    if r.returncode != 0:
        raise SystemExit(f"failed: {cmd}")
    return r


# ---------------------------------------------------------------
# Role
# ---------------------------------------------------------------
def role():
    """
    The identity AgentCore assumes.

    It needs Bedrock to invoke models, ECR to pull the image, CloudWatch
    to log, and EC2 network permissions to attach itself to the VPC.
    """
    trust = {
        "Version": "2012-10-17",
        "Statement": [{
            "Effect": "Allow",
            "Principal": {"Service": "bedrock-agentcore.amazonaws.com"},
            "Action": "sts:AssumeRole",
            "Condition": {
                "StringEquals": {"aws:SourceAccount": ACCOUNT},
            },
        }],
    }

    try:
        r = iam.create_role(RoleName=ROLE_NAME,
                            AssumeRolePolicyDocument=json.dumps(trust),
                            Description="GRUS AgentCore runtime")
        arn = r["Role"]["Arn"]
        print(f"created {ROLE_NAME}")
        time.sleep(10)                  # IAM propagation
    except iam.exceptions.EntityAlreadyExistsException:
        arn = iam.get_role(RoleName=ROLE_NAME)["Role"]["Arn"]
        print(f"{ROLE_NAME} exists")

    policy = {
        "Version": "2012-10-17",
        "Statement": [
            {"Effect": "Allow",
             "Action": ["bedrock:InvokeModel",
                        "bedrock:InvokeModelWithResponseStream",
                        "bedrock:Converse", "bedrock:ConverseStream"],
             "Resource": "*"},
            {"Effect": "Allow",
             "Action": ["ecr:GetAuthorizationToken",
                        "ecr:BatchCheckLayerAvailability",
                        "ecr:GetDownloadUrlForLayer",
                        "ecr:BatchGetImage"],
             "Resource": "*"},
            {"Effect": "Allow",
             "Action": ["logs:CreateLogGroup", "logs:CreateLogStream",
                        "logs:PutLogEvents", "logs:DescribeLogStreams"],
             "Resource": "*"},
            {"Effect": "Allow",
             "Action": ["ec2:CreateNetworkInterface",
                        "ec2:DescribeNetworkInterfaces",
                        "ec2:DeleteNetworkInterface",
                        "ec2:DescribeSubnets", "ec2:DescribeVpcs",
                        "ec2:DescribeSecurityGroups"],
             "Resource": "*"},
            {"Effect": "Allow",
             "Action": "sagemaker:InvokeEndpoint",
             "Resource": "*"},
            {"Effect": "Allow",
             "Action": ["xray:PutTraceSegments",
                        "xray:PutTelemetryRecords"],
             "Resource": "*"},
        ],
    }
    iam.put_role_policy(RoleName=ROLE_NAME, PolicyName="grus-agentcore",
                        PolicyDocument=json.dumps(policy))
    print(f"  {arn}")
    return arn


# ---------------------------------------------------------------
# ECR
# ---------------------------------------------------------------
def ecr_repo():
    try:
        ecr.create_repository(repositoryName=REPO,
                              imageScanningConfiguration={"scanOnPush": False})
        print(f"created {REPO}")
    except ecr.exceptions.RepositoryAlreadyExistsException:
        print(f"{REPO} exists")
    print(f"  {IMAGE_URI}")


def build():
    """
    Build for ARM64 and push.

    AgentCore runs ARM. An x86 image pushes without complaint and then
    fails at startup with 'exec format error', which is an unhelpful way
    to discover the problem.
    """
    token = ecr.get_authorization_token()["authorizationData"][0]
    user, pwd = base64.b64decode(token["authorizationToken"]).decode().split(":")
    registry = token["proxyEndpoint"]

    print("logging in to ECR")
    _run(f"docker login -u {user} -p {pwd} {registry}",
         stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

    print("\nbuilding arm64 image (several minutes on an x86 host)")
    _run("docker buildx create --use --name grus-builder 2>nul || "
         "docker buildx use grus-builder")
    _run(f"docker buildx build --platform linux/arm64 "
         f"-t {IMAGE_URI} --push .")

    print(f"\npushed {IMAGE_URI}")


# ---------------------------------------------------------------
# Runtime
# ---------------------------------------------------------------
def _vpc():
    """Aurora's subnets and security group."""
    c = rds.describe_db_clusters(
        DBClusterIdentifier=CLUSTER_ID)["DBClusters"][0]
    sgs = [g["VpcSecurityGroupId"] for g in c["VpcSecurityGroups"]]
    sg = rds.describe_db_subnet_groups(
        DBSubnetGroupName=c["DBSubnetGroup"])["DBSubnetGroups"][0]
    subnets = [s["SubnetIdentifier"] for s in sg["Subnets"]]
    return subnets, sgs, c["Endpoint"]


def create():
    role_arn = iam.get_role(RoleName=ROLE_NAME)["Role"]["Arn"]
    subnets, sgs, endpoint = _vpc()

    print(f"aurora  {endpoint}")
    print(f"vpc     {len(subnets)} subnets, {len(sgs)} security groups")

    env = {
        "DB_HOST": endpoint,
        "DB_NAME": "grus",
        "DB_USER": "grusadmin",
        "DB_PASSWORD": os.getenv("DB_PASSWORD", ""),
        "AWS_REGION": REGION,
    }

    args = {
        "agentRuntimeName": RUNTIME_NAME,
        "description": "GRUS emergency-medicine agent graph. Five Strands "
                       "agents over MIMIC-IV; every claim carries its "
                       "source row.",
        "agentRuntimeArtifact": {
            "containerConfiguration": {"containerUri": IMAGE_URI}},
        "roleArn": role_arn,
        "networkConfiguration": {
            "networkMode": "VPC",
            "networkModeConfig": {
                "subnets": subnets,
                "securityGroups": sgs,
            },
        },
        "environmentVariables": env,
    }

    try:
        r = agentcore.create_agent_runtime(**args)
        print(f"\ncreated {RUNTIME_NAME}")
    except Exception as e:
        if "already exists" not in str(e).lower():
            # VPC mode is not available in every region yet. Public
            # networking works if Aurora is publicly accessible, which
            # yours is.
            if "networkMode" in str(e) or "VPC" in str(e):
                print(f"  VPC mode rejected ({str(e)[:100]})")
                print("  retrying with public networking")
                args["networkConfiguration"] = {"networkMode": "PUBLIC"}
                r = agentcore.create_agent_runtime(**args)
                print(f"\ncreated {RUNTIME_NAME} (public networking)")
            else:
                raise
        else:
            rts = agentcore.list_agent_runtimes()["agentRuntimes"]
            rt = next(x for x in rts if x["agentRuntimeName"] == RUNTIME_NAME)
            r = agentcore.update_agent_runtime(
                agentRuntimeId=rt["agentRuntimeId"],
                agentRuntimeArtifact=args["agentRuntimeArtifact"],
                roleArn=role_arn,
                networkConfiguration=args["networkConfiguration"],
                environmentVariables=env)
            print(f"\nupdated {RUNTIME_NAME}")

    arn = r.get("agentRuntimeArn")
    print(f"  {arn}")
    with open("agentcore_runtime.json", "w") as f:
        json.dump({"arn": arn, "name": RUNTIME_NAME,
                   "id": r.get("agentRuntimeId")}, f, indent=2)

    print("\n  a few minutes to become READY")
    print("  python grus_agentcore_deploy.py status")


def invoke():
    with open("agentcore_runtime.json") as f:
        rt = json.load(f)

    client = sess.client("bedrock-agentcore")
    payload = json.dumps({"hadm_id": 28173870, "as_of_hours": 1})

    print(f"invoking {rt['name']}")
    t0 = time.time()
    r = client.invoke_agent_runtime(
        agentRuntimeArn=rt["arn"],
        qualifier="DEFAULT",
        payload=payload.encode())

    body = r["response"].read()
    ms = int((time.time() - t0) * 1000)

    try:
        out = json.loads(body)
    except json.JSONDecodeError:
        print(body.decode()[:600])
        return

    if "brief" in out:
        print(f"\n{out['brief'][:1200]}")
        t = out.get("trust", {})
        print(f"\n  agents: {out.get('agents_run')}")
        print(f"  citations: {t.get('citations_valid')} valid, "
              f"{t.get('traceable_pct')}% traceable")
    else:
        print(json.dumps(out, indent=2)[:900])

    print(f"  {ms}ms round trip")


def status():
    print(f"region {REGION}, account {ACCOUNT}\n")

    try:
        imgs = ecr.describe_images(repositoryName=REPO)["imageDetails"]
        print(f"ecr: {len(imgs)} images")
        for i in imgs[:2]:
            mb = i["imageSizeInBytes"] / 1024 / 1024
            print(f"  {i.get('imageTags', ['<untagged>'])[0]}  {mb:.0f} MB  "
                  f"{i['imagePushedAt']:%Y-%m-%d %H:%M}")
    except Exception as e:
        print(f"ecr: {str(e)[:80]}")

    try:
        rts = agentcore.list_agent_runtimes()["agentRuntimes"]
        print(f"\nruntimes: {len(rts)}")
        for rt in rts:
            print(f"  {rt['agentRuntimeName']:20} {rt.get('status')}")
    except Exception as e:
        print(f"\nruntimes: {str(e)[:120]}")


def delete():
    try:
        with open("agentcore_runtime.json") as f:
            rt = json.load(f)
        agentcore.delete_agent_runtime(agentRuntimeId=rt["id"])
        print(f"deleted {rt['name']}")
    except Exception as e:
        print(str(e)[:200])


if __name__ == "__main__":
    cmd = sys.argv[1] if len(sys.argv) > 1 else "status"
    {"role": role, "ecr": ecr_repo, "build": build, "create": create,
     "invoke": invoke, "status": status, "delete": delete,
     }.get(cmd, status)()