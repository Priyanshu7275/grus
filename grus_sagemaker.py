"""
GRUS — SageMaker training, registry, and deployment

Retrains the risk models on SageMaker rather than deploying the local
ones, so that every service the architecture claims is real: Training
jobs, Model Registry, Clarify bias reports, and an inference endpoint.

Four stages, runnable separately:

    python grus_sagemaker.py upload    training data -> S3
    python grus_sagemaker.py train     Training jobs, one per label
    python grus_sagemaker.py register  Model Registry, pending approval
    python grus_sagemaker.py deploy    approved models -> endpoint
    python grus_sagemaker.py clarify   bias report on the shipped models

Registration deliberately does NOT auto-approve. A model reaches
production only when a human marks it approved — that is the
champion/challenger gate, and it is the difference between 'it learns
every day' being governance and being a magic claim.

Cost note: the endpoint bills per hour whether or not anyone calls it.
    python grus_sagemaker.py teardown
"""

import os
import sys
import json
import re
import time
import tarfile
import boto3
import sagemaker
import sagemaker.estimator
from sagemaker.inputs import TrainingInput
from sagemaker.model import Model
from sagemaker.serverless import ServerlessInferenceConfig
from sagemaker.clarify import (SageMakerClarifyProcessor, DataConfig,
                               BiasConfig, ModelConfig, ModelPredictedLabelConfig)
from grus_config import AWS, Paths

REGION = AWS.REGION
BUCKET = AWS.BUCKET
MODEL_DIR = str(Paths.MODEL_DIR)
MODEL_PACKAGE_GROUP = AWS.MODEL_PACKAGE_GROUP
ENDPOINT_NAME = "grus-risk"

TRAIN_INSTANCE = "ml.m5.xlarge"      # ~₹20/hour, minutes of use

# Serverless inference: billed per request, nothing while idle.
#
# Three always-on ml.t2.medium endpoints run about ₹36,000 over the two
# months between submission and judging. Serverless costs under ₹100 for
# the same period at demo traffic. The trade is a 10-30 second cold start
# on the first request after idle — warm it once before recording.
SERVERLESS_MEMORY_MB = 2048
SERVERLESS_MAX_CONCURRENCY = 5

boto_sess = boto3.Session(region_name=REGION)
sm = boto_sess.client("sagemaker")
s3 = boto_sess.client("s3")
sess = sagemaker.Session(boto_session=boto_sess, default_bucket=BUCKET)


def role_arn():
    """
    The execution role SageMaker assumes. Created once in the console:
    IAM -> Roles -> Create -> SageMaker -> AmazonSageMakerFullAccess,
    plus S3 access to this bucket.
    """
    arn = os.getenv("SAGEMAKER_ROLE_ARN")
    if arn:
        return arn
    try:
        return sagemaker.get_execution_role(sagemaker_session=sess)
    except Exception:
        acct = boto_sess.client("sts").get_caller_identity()["Account"]
        guess = f"arn:aws:iam::{acct}:role/service-role/AmazonSageMaker-ExecutionRole"
        print(f"  no SAGEMAKER_ROLE_ARN set, trying {guess}")
        return guess


def shipped_labels():
    with open(f"{MODEL_DIR}/models/shipped.txt") as f:
        return [l.strip() for l in f if l.strip()]


def feature_cols():
    with open(f"{MODEL_DIR}/models/features.json") as f:
        return json.load(f)


# ---------------------------------------------------------------
# 1. Upload
#
# The SageMaker XGBoost container expects CSV with the label in the
# FIRST column and no header. Anything else fails at training time with
# an unhelpful message.
# ---------------------------------------------------------------
def upload():
    import pandas as pd

    labels = shipped_labels()
    feats = feature_cols()
    print(f"preparing {len(labels)} labels, {len(feats)} features")

    for split in ["train", "val"]:
        df = pd.read_parquet(f"{MODEL_DIR}/{split}.parquet")
        for label in labels:
            out = df[[label] + feats].copy()
            out = out.fillna(-999)          # XGBoost treats this as missing
            path = f"{MODEL_DIR}/{split}_{label}.csv"
            out.to_csv(path, index=False, header=False)

            key = f"{PREFIX}/{label}/{split}/data.csv"
            s3.upload_file(path, BUCKET, key)
            print(f"  s3://{BUCKET}/{key}  ({len(out):,} rows)")
            os.remove(path)

    # The feature names travel with the data — the container sees only
    # column positions, and a report about 'f17' helps nobody.
    s3.put_object(Bucket=BUCKET, Key=f"{PREFIX}/features.json",
                  Body=json.dumps(feats).encode())
    print(f"  s3://{BUCKET}/{PREFIX}/features.json")


# ---------------------------------------------------------------
# 2. Train
# ---------------------------------------------------------------
def train():
    labels = shipped_labels()
    role = role_arn()

    with open(f"{MODEL_DIR}/models/report.json") as f:
        report = json.load(f)

    jobs = {}
    for label in labels:
        # scale_pos_weight from the local run — the imbalance is a
        # property of the data, not of where training happens.
        rate = report["labels"][label]["metrics"]["positive_rate"]
        spw = round((1 - rate) / rate, 2)

        print(f"\n{label}  (positive rate {rate:.3f}, scale_pos_weight {spw})")

        # The XGBoost estimator wrapper JSON-encodes string
        # hyperparameters, so 'binary:logistic' reaches the container as
        # '"binary:logistic"' and fails its literal range check. The
        # generic Estimator passes strings through unchanged.
        image = sagemaker.image_uris.retrieve("xgboost", REGION, "1.7-1")

        est = sagemaker.estimator.Estimator(
            image_uri=image,
            role=role,
            instance_count=1,
            instance_type=TRAIN_INSTANCE,
            output_path=f"s3://{BUCKET}/{PREFIX}/{label}/output",
            sagemaker_session=sess,
            base_job_name=f"grus-{label.replace('_','-')}"[:32],
        )
        est.set_hyperparameters(
            objective="binary:logistic",
            eval_metric="aucpr",
            num_round=400,
            max_depth=5,
            eta=0.05,
            subsample=0.8,
            colsample_bytree=0.8,
            min_child_weight=20,
            **{"lambda": 2.0},
            scale_pos_weight=spw,
        )

        est.fit(
            {
                "train": TrainingInput(
                    f"s3://{BUCKET}/{PREFIX}/{label}/train/data.csv",
                    content_type="text/csv"),
                "validation": TrainingInput(
                    f"s3://{BUCKET}/{PREFIX}/{label}/val/data.csv",
                    content_type="text/csv"),
            },
            wait=True, logs="None",
        )

        jobs[label] = {
            "job_name": est.latest_training_job.name,
            "model_data": est.model_data,
        }
        print(f"  done: {est.model_data}")

    with open(f"{MODEL_DIR}/models/sagemaker_jobs.json", "w") as f:
        json.dump(jobs, f, indent=2)
    print(f"\n{len(jobs)} training jobs complete")


# ---------------------------------------------------------------
# 3. Register
#
# Registered as PendingManualApproval, never Approved. Deployment reads
# only approved packages, so a model reaches a clinician when a person
# says so — not when a script finishes.
# ---------------------------------------------------------------
def register():
    with open(f"{MODEL_DIR}/models/sagemaker_jobs.json") as f:
        jobs = json.load(f)
    with open(f"{MODEL_DIR}/models/report.json") as f:
        report = json.load(f)
    with open(f"{MODEL_DIR}/models/thresholds.json") as f:
        thresholds = json.load(f)

    try:
        sm.create_model_package_group(
            ModelPackageGroupName=MODEL_PACKAGE_GROUP,
            ModelPackageGroupDescription="GRUS clinical risk models. "
            "Each package carries its evaluation metrics, chosen operating "
            "point, and subgroup fairness results.",
        )
        print(f"created group {MODEL_PACKAGE_GROUP}")
    except Exception as e:
        # SageMaker raises ValidationException rather than ResourceInUse
        # for an existing package group.
        if "already exists" in str(e):
            print(f"group {MODEL_PACKAGE_GROUP} exists")
        else:
            raise

    image = sagemaker.image_uris.retrieve("xgboost", REGION, "1.7-1")

    def clean(v):
        """
        Model package metadata allows only letters, digits, spaces, and
        _.:/=+-@ — no percent signs. 'recall floor 50%' is rejected.
        """
        s = str(v)
        return re.sub(r"[^\w\s.:/=+\-@]", "", s)[:255] or "-"

    for label, job in jobs.items():
        m = report["labels"][label]["metrics"]
        fair = report["labels"][label]["fairness"]
        t = thresholds.get(label, {})

        resp = sm.create_model_package(
            ModelPackageGroupName=MODEL_PACKAGE_GROUP,
            ModelPackageDescription=(
                f"{label}: AUC {m['auc']:.3f}, AP {m['average_precision']:.3f}, "
                f"precision {m['precision']:.2f}, recall {m['recall']:.2f} at "
                f"threshold {m['threshold']:.3f} ({m.get('threshold_policy','')}). "
                f"Calibration error {m.get('calibration_error', 0):.3f}. "
                f"Max subgroup AUC gap {fair['max_gap']:.3f}. "
                f"Alerts on {m['alert_rate']:.1%} of patient-hours."
            ),
            InferenceSpecification={
                "Containers": [{"Image": image, "ModelDataUrl": job["model_data"]}],
                "SupportedContentTypes": ["text/csv"],
                "SupportedResponseMIMETypes": ["text/csv"],
                "SupportedRealtimeInferenceInstanceTypes": ["ml.t2.medium", "ml.m5.large"],
                "SupportedTransformInstanceTypes": ["ml.m5.large"],
            },
            # A human decides. This is the champion/challenger gate.
            ModelApprovalStatus="PendingManualApproval",
            CustomerMetadataProperties={
                k: clean(v) for k, v in {
                    "label": label,
                    "auc": round(m["auc"], 4),
                    "average_precision": round(m["average_precision"], 4),
                    "precision": round(m["precision"], 3),
                    "recall": round(m["recall"], 3),
                    "threshold": round(m["threshold"], 4),
                    "threshold_policy": m.get("threshold_policy", ""),
                    "calibration_error": round(m.get("calibration_error") or 0, 4),
                    "subgroup_auc_gap": fair["max_gap"],
                    "alert_rate": round(m["alert_rate"], 4),
                    "positive_rate": round(m["positive_rate"], 4),
                    "training_job": job["job_name"],
                }.items()
            },
        )
        print(f"  {label}: {resp['ModelPackageArn'].split('/')[-1]}  "
              f"PendingManualApproval")

    print("\nRegistered, not approved. Approve in the console:")
    print("  SageMaker -> Model registry -> grus-risk-models")
    print("or with:")
    print("  python grus_sagemaker.py approve <version>")


def approve(version=None):
    """Mark a package approved. The gate a human passes."""
    pkgs = sm.list_model_packages(
        ModelPackageGroupName=MODEL_PACKAGE_GROUP,
        SortBy="CreationTime", SortOrder="Descending")["ModelPackageSummaryList"]

    for p in pkgs:
        if version and str(p["ModelPackageVersion"]) != str(version):
            continue
        if p["ModelApprovalStatus"] == "Approved":
            continue
        sm.update_model_package(ModelPackageArn=p["ModelPackageArn"],
                                ModelApprovalStatus="Approved")
        print(f"  approved v{p['ModelPackageVersion']}")


# ---------------------------------------------------------------
# 4. Deploy
# ---------------------------------------------------------------
def deploy():
    pkgs = sm.list_model_packages(
        ModelPackageGroupName=MODEL_PACKAGE_GROUP,
        ModelApprovalStatus="Approved",
        SortBy="CreationTime", SortOrder="Descending")["ModelPackageSummaryList"]

    if not pkgs:
        print("No approved model packages. Nothing is deployed until a")
        print("human approves it:  python grus_sagemaker.py approve")
        return

    role = role_arn()
    deployed = {}

    for p in pkgs:
        desc = sm.describe_model_package(ModelPackageName=p["ModelPackageArn"])
        label = desc["CustomerMetadataProperties"]["label"]
        if label in deployed:
            continue                        # newest approved version wins

        name = f"grus-{label.replace('_','-')}"[:60]
        # ModelPackage, not Model — the base class builds from an image
        # and artifact url, while a registered package already carries
        # both and needs its own wrapper.
        model = sagemaker.ModelPackage(
            model_package_arn=p["ModelPackageArn"], role=role,
            sagemaker_session=sess, name=name)
        ep = f"{ENDPOINT_NAME}-{label.replace('_','-')}"[:62]

        print(f"  deploying {label} -> {ep}")
        model.deploy(
            serverless_inference_config=ServerlessInferenceConfig(
                memory_size_in_mb=SERVERLESS_MEMORY_MB,
                max_concurrency=SERVERLESS_MAX_CONCURRENCY),
            endpoint_name=ep, wait=False)

        deployed[label] = {
            "endpoint": ep,
            "version": p["ModelPackageVersion"],
            "threshold": float(desc["CustomerMetadataProperties"]["threshold"]),
            "serverless": True,
        }

    with open(f"{MODEL_DIR}/models/endpoints.json", "w") as f:
        json.dump(deployed, f, indent=2)

    print(f"\n{len(deployed)} serverless endpoints creating")
    print("  the Risk agent reads endpoints.json to find them")
    print("\nbilled per request, nothing while idle. First call after a")
    print("quiet period takes 10-30s to warm — call each endpoint once")
    print("before recording the demo.")


# ---------------------------------------------------------------
# 5. Clarify — bias report
#
# The local run printed subgroup AUCs. Clarify produces the same finding
# as a signed artifact tied to the model package, which is what an
# auditor would ask for.
# ---------------------------------------------------------------
def clarify():
    import pandas as pd

    labels = shipped_labels()
    feats = feature_cols()
    role = role_arn()

    df = pd.read_parquet(f"{MODEL_DIR}/test.parquet")
    df = df.sample(n=min(20000, len(df)), random_state=42)

    proc = SageMakerClarifyProcessor(
        role=role, instance_count=1, instance_type=TRAIN_INSTANCE,
        sagemaker_session=sess)

    with open(f"{MODEL_DIR}/models/endpoints.json") as f:
        endpoints = json.load(f)

    for label in labels:
        if label not in endpoints:
            print(f"  {label}: not deployed, skipping")
            continue

        sub = df[[label] + feats].fillna(-999)
        local = f"{MODEL_DIR}/clarify_{label}.csv"
        sub.to_csv(local, index=False)
        key = f"{PREFIX}/clarify/{label}/input.csv"
        s3.upload_file(local, BUCKET, key)
        os.remove(local)

        data_cfg = DataConfig(
            s3_data_input_path=f"s3://{BUCKET}/{key}",
            s3_output_path=f"s3://{BUCKET}/{PREFIX}/clarify/{label}/output",
            label=label, headers=[label] + feats, dataset_type="text/csv")

        # Age is the facet that matters here: ICU patients skew old, and a
        # model that works on the young and fails on the old is failing
        # exactly the people it will be used on.
        bias_cfg = BiasConfig(
            label_values_or_threshold=[1],
            facet_name="anchor_age",
            facet_values_or_threshold=[65],
            group_name="is_male")

        model_cfg = ModelConfig(
            endpoint_name=endpoints[label]["endpoint"],
            instance_count=1, instance_type="ml.m5.large",
            content_type="text/csv", accept_type="text/csv")

        pred_cfg = ModelPredictedLabelConfig(
            probability_threshold=endpoints[label]["threshold"])

        print(f"  {label}: running bias analysis")
        proc.run_bias(data_config=data_cfg, bias_config=bias_cfg,
                      model_config=model_cfg,
                      model_predicted_label_config=pred_cfg,
                      pre_training_methods="all",
                      post_training_methods="all",
                      wait=True, logs=False)
        print(f"    report: s3://{BUCKET}/{PREFIX}/clarify/{label}/output")


# ---------------------------------------------------------------
# Teardown
# ---------------------------------------------------------------
def teardown():
    try:
        with open(f"{MODEL_DIR}/models/endpoints.json") as f:
            endpoints = json.load(f)
    except FileNotFoundError:
        print("no endpoints.json")
        return

    for label, info in endpoints.items():
        try:
            sm.delete_endpoint(EndpointName=info["endpoint"])
            sm.delete_endpoint_config(EndpointConfigName=info["endpoint"])
            print(f"  deleted {info['endpoint']}")
        except Exception as e:
            print(f"  {info['endpoint']}: {e}")


def status():
    print(f"region {REGION}, bucket {BUCKET}\n")

    try:
        pkgs = sm.list_model_packages(
            ModelPackageGroupName=MODEL_PACKAGE_GROUP,
            SortBy="CreationTime", SortOrder="Descending")["ModelPackageSummaryList"]
        print(f"model registry: {len(pkgs)} packages")
        for p in pkgs[:10]:
            print(f"  v{p['ModelPackageVersion']:<3} {p['ModelApprovalStatus']}")
    except Exception as e:
        print(f"model registry: {e}")

    print()
    for ep in sm.list_endpoints(NameContains="grus")["Endpoints"]:
        print(f"  {ep['EndpointName']:<40} {ep['EndpointStatus']}")


if __name__ == "__main__":
    cmd = sys.argv[1] if len(sys.argv) > 1 else "status"
    arg = sys.argv[2] if len(sys.argv) > 2 else None

    {"upload": upload, "train": train, "register": register,
     "approve": lambda: approve(arg), "deploy": deploy,
     "clarify": clarify, "teardown": teardown, "status": status,
     }.get(cmd, status)()