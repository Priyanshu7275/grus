"""
GRUS — configuration

Every setting in one place, read from the environment with sensible
defaults.

This exists because the alternative was a hardcoded Windows path and a
hardcoded database endpoint in every module:

    
    HOST = "grus-db.cluster-ch02wk02ky83.ap-south-1.rds.amazonaws.com"

That runs on exactly one machine. A container, a Lambda, or anyone
cloning the repository gets nothing.

Usage:

    from grus_config import DB, AWS, connect
    conn = connect()
"""

import os
from pathlib import Path

# Find a .env by walking up from this file, rather than naming a
# directory that only exists on one laptop.
try:
    from dotenv import load_dotenv
    _here = Path(__file__).resolve().parent
    for _candidate in (_here, *_here.parents):
        _env = _candidate / ".env"
        if _env.exists():
            load_dotenv(_env)
            break
except ImportError:
    # python-dotenv is not installed in the Lambda runtime, which gets
    # its configuration from the function environment instead.
    pass


class DB:
    HOST = os.environ.get(
        "GRUS_DB_HOST",
        "grus-db.cluster-ch02wk02ky83.ap-south-1.rds.amazonaws.com")
    PORT = int(os.environ.get("GRUS_DB_PORT", 5432))
    NAME = os.environ.get("GRUS_DB_NAME", "grus")
    USER = os.environ.get("GRUS_DB_USER", "grusadmin")
    PASSWORD = os.environ.get("DB_PASSWORD") or os.environ.get("GRUS_DB_PASSWORD")
    SCHEMA = os.environ.get("GRUS_DB_SCHEMA", "grus")
    SSLMODE = os.environ.get("GRUS_DB_SSLMODE", "require")


class AWS:
    REGION = os.environ.get("AWS_REGION", "ap-south-1")
    BUCKET = os.environ.get("GRUS_BUCKET", "grus-mimic-data-etl")

    # Bedrock. Qwen does the structured work; the reasoning slot is
    # Claude when the account can reach it. Anthropic models on Bedrock
    # bill through AWS Marketplace, which does not accept every payment
    # method — REASONING_MODEL falls back to Qwen so the graph still
    # completes, and REASONING_PREFERRED records what it should be.
    FAST_MODEL = os.environ.get("GRUS_FAST_MODEL", "qwen.qwen3-32b-v1:0")
    REASONING_MODEL = os.environ.get("GRUS_REASONING_MODEL",
                                     "qwen.qwen3-32b-v1:0")
    REASONING_PREFERRED = "global.anthropic.claude-haiku-4-5-20251001-v1:0"
    EMBED_MODEL = os.environ.get("GRUS_EMBED_MODEL",
                                 "amazon.titan-embed-text-v2:0")

    SAGEMAKER_ROLE = os.environ.get("SAGEMAKER_ROLE_ARN")
    MODEL_PACKAGE_GROUP = os.environ.get("GRUS_MODEL_GROUP",
                                         "grus-risk-models")


class Paths:
    """Local artefacts. Relative to this file, not to a home directory."""
    ROOT = Path(__file__).resolve().parent
    MODEL_DIR = Path(os.environ.get("GRUS_MODEL_DIR", ROOT / "model"))
    TIER2_DIR = Path(os.environ.get("GRUS_TIER2_DIR", ROOT / "tier2processed"))


def connect(row_factory=None):
    """
    An Aurora connection with the search path set.

    row_factory defaults to tuple rows, which is what the retriever and
    the rules engine index into. The API passes dict_row explicitly.
    """
    import psycopg

    if not DB.PASSWORD:
        raise RuntimeError(
            "No database password. Set DB_PASSWORD in a .env file beside "
            "the code, or in the environment.")

    kwargs = dict(
        host=DB.HOST, port=DB.PORT, dbname=DB.NAME, user=DB.USER,
        password=DB.PASSWORD, sslmode=DB.SSLMODE,
        connect_timeout=15, keepalives=1, keepalives_idle=30,
    )
    if row_factory is not None:
        kwargs["row_factory"] = row_factory

    conn = psycopg.connect(**kwargs)
    conn.execute(f"SET search_path TO {DB.SCHEMA}, public")
    conn.commit()
    return conn


def describe():
    """What the configuration resolved to. Never prints the password."""
    return {
        "db_host": DB.HOST,
        "db_name": DB.NAME,
        "db_user": DB.USER,
        "password_set": bool(DB.PASSWORD),
        "region": AWS.REGION,
        "bucket": AWS.BUCKET,
        "fast_model": AWS.FAST_MODEL,
        "reasoning_model": AWS.REASONING_MODEL,
        "model_dir": str(Paths.MODEL_DIR),
        "model_dir_exists": Paths.MODEL_DIR.exists(),
    }


if __name__ == "__main__":
    for k, v in describe().items():
        print(f"  {k:20} {v}")