"""
GRUS — EventBridge and Lambda setup

Deploys the scenario replay: a Lambda that reads a scenario file from S3
and writes one hour of observations into Aurora, and an EventBridge rule
that invokes it on a schedule.

    python grus_eventbridge_setup.py secret     store the DB password
    python grus_eventbridge_setup.py package    build the zip
    python grus_eventbridge_setup.py deploy     create role, Lambda, rule
    python grus_eventbridge_setup.py upload     put a scenario in S3
    python grus_eventbridge_setup.py start      enable the schedule
    python grus_eventbridge_setup.py stop       disable it
    python grus_eventbridge_setup.py invoke     run one tick now
    python grus_eventbridge_setup.py status

The Lambda needs VPC access to reach Aurora, which means it also needs
subnets and a security group. Those are read from the Aurora cluster
itself rather than hardcoded.
"""

import os
import io
import sys
import json
import time
import zipfile
import boto3
from dotenv import load_dotenv



REGION = "ap-south-1"
BUCKET = "grus-mimic-data-etl"
CLUSTER_ID = "grus-db"

FUNCTION = "grus-scenario-replay"
ROLE_NAME = "grus-lambda-role"
RULE_NAME = "grus-scenario-tick"
SECRET_NAME = "grus/db-password"
LAYER_NAME = "grus-psycopg"

SCENARIO_KEY = "scenarios/active.txt"
STATE_KEY = "scenarios/state.json"

SCHEDULE = "rate(1 minute)"     # one simulated hour per real minute

sess = boto3.Session(region_name=REGION)
lam = sess.client("lambda")
events = sess.client("events")
iam = sess.client("iam")
s3 = sess.client("s3")
rds = sess.client("rds")
secrets = sess.client("secretsmanager")
sts = sess.client("sts")

ACCOUNT = sts.get_caller_identity()["Account"]


# ---------------------------------------------------------------
# Secret
# ---------------------------------------------------------------
def secret():
    """
    Aurora's password into Secrets Manager.

    A Lambda has no .env file. Putting the password in an environment
    variable would leave it visible to anyone with console read access;
    Secrets Manager keeps it out of the function configuration.
    """
    pwd = os.getenv("DB_PASSWORD")
    if not pwd:
        print("DB_PASSWORD not set in .env")
        return

    try:
        r = secrets.create_secret(Name=SECRET_NAME, SecretString=pwd,
                                  Description="GRUS Aurora master password")
        arn = r["ARN"]
        print(f"created {SECRET_NAME}")
    except secrets.exceptions.ResourceExistsException:
        secrets.put_secret_value(SecretId=SECRET_NAME, SecretString=pwd)
        arn = secrets.describe_secret(SecretId=SECRET_NAME)["ARN"]
        print(f"updated {SECRET_NAME}")

    print(f"  {arn}")
    return arn


# ---------------------------------------------------------------
# Package
# ---------------------------------------------------------------
def package():
    """
    Zip the handler and the modules it imports.

    psycopg needs binaries built for Amazon Linux, so it goes in a layer
    rather than the function zip. See `layer` below.
    """
    files = ["grus_scenario_lambda.py", "grus_rules.py"]
    missing = [f for f in files if not os.path.exists(f)]
    if missing:
        print(f"missing: {missing}")
        return

    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        for f in files:
            z.write(f, os.path.basename(f))

    with open("lambda_package.zip", "wb") as f:
        f.write(buf.getvalue())

    size = len(buf.getvalue()) / 1024
    print(f"lambda_package.zip  ({size:.0f} KB)")
    print(f"  {', '.join(files)}")
    return buf.getvalue()


def layer():
    """
    Build the psycopg layer.

    Needs Docker or a Linux machine — psycopg ships compiled binaries and
    a Windows build will not run on Lambda. If neither is available, use
    a prebuilt public layer instead; the ARN is printed below.
    """
    print("Build with Docker:\n")
    print("  mkdir -p layer/python")
    print("  docker run --rm -v %cd%/layer:/out "
          "public.ecr.aws/sam/build-python3.11 \\")
    print("    pip install psycopg[binary] boto3 -t /out/python")
    print("  cd layer && zip -r ../psycopg-layer.zip python\n")
    print("Then:")
    print(f"  aws lambda publish-layer-version --layer-name {LAYER_NAME} \\")
    print("    --zip-file fileb://psycopg-layer.zip \\")
    print("    --compatible-runtimes python3.11 --region " + REGION)


# ---------------------------------------------------------------
# Network — read from Aurora rather than guessed
# ---------------------------------------------------------------
def _vpc_config():
    """
    The subnets and security group Aurora sits in.

    A Lambda outside the VPC cannot reach a private database, and one in
    the wrong subnets cannot either. Reading these from the cluster is
    more reliable than copying ids by hand.
    """
    c = rds.describe_db_clusters(DBClusterIdentifier=CLUSTER_ID)["DBClusters"][0]
    sg_ids = [g["VpcSecurityGroupId"] for g in c["VpcSecurityGroups"]]
    subnet_group = c["DBSubnetGroup"]
    sg = rds.describe_db_subnet_groups(
        DBSubnetGroupName=subnet_group)["DBSubnetGroups"][0]
    subnet_ids = [s["SubnetIdentifier"] for s in sg["Subnets"]]

    return {"SubnetIds": subnet_ids, "SecurityGroupIds": sg_ids}, \
           c["Endpoint"]


# ---------------------------------------------------------------
# Role
# ---------------------------------------------------------------
def _role():
    trust = {
        "Version": "2012-10-17",
        "Statement": [{
            "Effect": "Allow",
            "Principal": {"Service": "lambda.amazonaws.com"},
            "Action": "sts:AssumeRole",
        }],
    }

    try:
        r = iam.create_role(
            RoleName=ROLE_NAME,
            AssumeRolePolicyDocument=json.dumps(trust),
            Description="GRUS scenario replay Lambda")
        arn = r["Role"]["Arn"]
        print(f"created role {ROLE_NAME}")
        time.sleep(10)          # IAM propagation
    except iam.exceptions.EntityAlreadyExistsException:
        arn = iam.get_role(RoleName=ROLE_NAME)["Role"]["Arn"]
        print(f"role {ROLE_NAME} exists")

    for policy in [
        "arn:aws:iam::aws:policy/service-role/AWSLambdaBasicExecutionRole",
        "arn:aws:iam::aws:policy/service-role/AWSLambdaVPCAccessExecutionRole",
    ]:
        iam.attach_role_policy(RoleName=ROLE_NAME, PolicyArn=policy)

    inline = {
        "Version": "2012-10-17",
        "Statement": [
            {"Effect": "Allow",
             "Action": ["s3:GetObject", "s3:PutObject"],
             "Resource": f"arn:aws:s3:::{BUCKET}/scenarios/*"},
            {"Effect": "Allow",
             "Action": "secretsmanager:GetSecretValue",
             "Resource": f"arn:aws:secretsmanager:{REGION}:{ACCOUNT}:"
                         f"secret:{SECRET_NAME}*"},
        ],
    }
    iam.put_role_policy(RoleName=ROLE_NAME, PolicyName="grus-scenario",
                        PolicyDocument=json.dumps(inline))
    print("  policies attached")
    return arn


# ---------------------------------------------------------------
# Deploy
# ---------------------------------------------------------------
def deploy():
    code = package()
    if not code:
        return

    role_arn = _role()
    vpc, endpoint = _vpc_config()
    secret_arn = secrets.describe_secret(SecretId=SECRET_NAME)["ARN"]

    print(f"\nvpc: {len(vpc['SubnetIds'])} subnets, "
          f"{len(vpc['SecurityGroupIds'])} security groups")
    print(f"aurora: {endpoint}")

    env = {
        "DB_HOST": endpoint,
        "DB_NAME": "grus",
        "DB_USER": "grusadmin",
        "DB_SECRET_ARN": secret_arn,
        "SCENARIO_BUCKET": BUCKET,
        "SCENARIO_KEY": SCENARIO_KEY,
        "STATE_KEY": STATE_KEY,
    }

    layers = []
    try:
        vs = lam.list_layer_versions(LayerName=LAYER_NAME)["LayerVersions"]
        if vs:
            layers = [vs[0]["LayerVersionArn"]]
            print(f"layer: {layers[0].split(':')[-2]} v{vs[0]['Version']}")
    except Exception:
        print("no psycopg layer found — run 'layer' for instructions")

    try:
        lam.create_function(
            FunctionName=FUNCTION,
            Runtime="python3.11",
            Role=role_arn,
            Handler="grus_scenario_lambda.handler",
            Code={"ZipFile": code},
            Timeout=60,
            MemorySize=512,
            Environment={"Variables": env},
            VpcConfig=vpc,
            Layers=layers,
            Description="Reads a scenario from S3, writes one hour to Aurora",
        )
        print(f"created {FUNCTION}")
    except lam.exceptions.ResourceConflictException:
        lam.update_function_code(FunctionName=FUNCTION, ZipFile=code)
        waiter = lam.get_waiter("function_updated")
        waiter.wait(FunctionName=FUNCTION)
        lam.update_function_configuration(
            FunctionName=FUNCTION,
            Environment={"Variables": env},
            VpcConfig=vpc,
            Layers=layers,
            Timeout=60, MemorySize=512)
        print(f"updated {FUNCTION}")

    _rule()


def _rule():
    """
    The schedule. One tick per minute, each writing one simulated hour.

    Created disabled — a rule that starts firing the moment it exists
    would write hours before anyone uploaded a scenario.
    """
    events.put_rule(
        Name=RULE_NAME,
        ScheduleExpression=SCHEDULE,
        State="DISABLED",
        Description="GRUS scenario replay tick")

    fn = lam.get_function(FunctionName=FUNCTION)["Configuration"]["FunctionArn"]

    try:
        lam.add_permission(
            FunctionName=FUNCTION,
            StatementId="eventbridge-invoke",
            Action="lambda:InvokeFunction",
            Principal="events.amazonaws.com",
            SourceArn=f"arn:aws:events:{REGION}:{ACCOUNT}:rule/{RULE_NAME}")
    except lam.exceptions.ResourceConflictException:
        pass

    events.put_targets(Rule=RULE_NAME,
                       Targets=[{"Id": "lambda", "Arn": fn}])

    print(f"\nrule {RULE_NAME}: {SCHEDULE}, currently DISABLED")
    print("  python grus_eventbridge_setup.py start")


# ---------------------------------------------------------------
# Scenario file
# ---------------------------------------------------------------
SAMPLE = """# patient 90000001
# Post-traumatic haemorrhage. SYNTHETIC — not from MIMIC.
# The pulse rises before the pressure falls. That compensation window
# is where a bleed gets missed.

Hour 0: HR 96, BP 124/78, SpO2 98, RR 18, Temp 36.6
        INR 3.8, Hgb 13.1, Hct 39.2, Plt 244, Creat 0.9, Lactate 1.8

Hour 1: HR 104, BP 122/76, SpO2 97, RR 20

Hour 2: HR 112, BP 118/72, SpO2 97, RR 22
        Hgb 11.4, Hct 34.1, Lactate 2.4

Hour 3: HR 121, BP 112/68, SpO2 96, RR 24

Hour 4: HR 128, BP 104/62, SpO2 95, RR 26
        Hgb 9.8, Hct 29.4, Lactate 3.6, Plt 198

Hour 5: HR 138, BP 92/54, SpO2 93, RR 28
        Hgb 8.4, Hct 25.2, Lactate 5.1

Hour 6: HR 142, BP 84/48, SpO2 92, RR 30
        Hgb 7.6, Hct 22.8, Lactate 6.4, Creat 1.3

Hour 7: HR 128, BP 96/58, SpO2 95, RR 26
        Hgb 9.1, Hct 27.3, INR 1.6, Lactate 4.2

Hour 8: HR 112, BP 108/66, SpO2 96, RR 22
        Hgb 9.6, Lactate 2.8
"""


def upload(path=None):
    text = SAMPLE
    if path:
        with open(path) as f:
            text = f.read()

    s3.put_object(Bucket=BUCKET, Key=SCENARIO_KEY,
                  Body=text.encode(), ContentType="text/plain")

    try:
        s3.delete_object(Bucket=BUCKET, Key=STATE_KEY)
    except Exception:
        pass

    sys.path.insert(0, ".")
    try:
        from grus_scenario_lambda import parse_scenario
        p = parse_scenario(text)
        print(f"uploaded to s3://{BUCKET}/{SCENARIO_KEY}")
        print(f"  patient {p['hadm_id']}, {len(p['hours'])} hours")
        if p["warnings"]:
            print(f"  warnings: {p['warnings'][:3]}")
    except ImportError:
        print(f"uploaded to s3://{BUCKET}/{SCENARIO_KEY}")

    print("\nEdit it in the console: S3 -> bucket -> scenarios -> "
          "active.txt -> Actions -> Edit")
    print("Replacing the file resets the replay to hour 0.")


# ---------------------------------------------------------------
# Control
# ---------------------------------------------------------------
def start():
    events.enable_rule(Name=RULE_NAME)
    print(f"{RULE_NAME} enabled — one hour per minute")
    print("  watch: aws logs tail /aws/lambda/" + FUNCTION + " --follow")


def stop():
    events.disable_rule(Name=RULE_NAME)
    print(f"{RULE_NAME} disabled")


def invoke():
    """One tick, now, without waiting for the schedule."""
    r = lam.invoke(FunctionName=FUNCTION, InvocationType="RequestResponse",
                   Payload=json.dumps({}).encode())
    payload = json.loads(r["Payload"].read())
    body = json.loads(payload.get("body", "{}"))

    if payload.get("statusCode") != 200:
        print(f"error: {body}")
        return

    print(f"hour {body.get('hour')}  ({body.get('progress')})")
    if body.get("scenario_replaced"):
        print("  scenario was replaced — restarted from hour 0")
    print(f"  written: {', '.join(body.get('written', [])[:6])}")
    if body.get("shock_index"):
        print(f"  shock index {body['shock_index']}")
    a = body.get("alerts", {})
    print(f"  alerts: {a.get('critical', 0)} critical, "
          f"{a.get('warning', 0)} warning")
    for t in a.get("titles", []):
        print(f"    {t}")


def status():
    print(f"region {REGION}, account {ACCOUNT}\n")

    try:
        c = lam.get_function(FunctionName=FUNCTION)["Configuration"]
        print(f"lambda {FUNCTION}")
        print(f"  runtime {c['Runtime']}, {c['MemorySize']}MB, "
              f"{c['Timeout']}s timeout")
        print(f"  layers: {len(c.get('Layers', []))}")
        print(f"  vpc: {len(c.get('VpcConfig', {}).get('SubnetIds', []))} subnets")
    except lam.exceptions.ResourceNotFoundException:
        print(f"lambda {FUNCTION}: not deployed")

    try:
        r = events.describe_rule(Name=RULE_NAME)
        print(f"\nrule {RULE_NAME}: {r['State']}, {r['ScheduleExpression']}")
    except events.exceptions.ResourceNotFoundException:
        print(f"\nrule {RULE_NAME}: not created")

    try:
        obj = s3.get_object(Bucket=BUCKET, Key=STATE_KEY)
        st = json.loads(obj["Body"].read())
        print(f"\nprogress: hour {st.get('index', 0)}/{st.get('total', '?')} "
              f"for patient {st.get('hadm_id')}")
    except Exception:
        print("\nno replay in progress")


if __name__ == "__main__":
    cmd = sys.argv[1] if len(sys.argv) > 1 else "status"
    arg = sys.argv[2] if len(sys.argv) > 2 else None

    {"secret": secret, "package": package, "layer": layer,
     "deploy": deploy, "upload": lambda: upload(arg),
     "start": start, "stop": stop, "invoke": invoke,
     "status": status}.get(cmd, status)()