"""
GRUS — Embeddings

Reads note_chunks from Aurora, generates vectors with Titan Text
Embeddings v2 on Bedrock, writes them back.

Bedrock is in-VPC, so PHI never leaves AWS.

Run AFTER grus_notes_pipeline.py and grus_etl_prior.py.
Idempotent: only embeds rows where embedding IS NULL, so a failed run
resumes rather than starting over.
"""

import os
import json
import time
import boto3
import psycopg
from grus_config import DB, AWS



HOST = DB.HOST
PWD = DB.PASSWORD

REGION = AWS.REGION
MODEL_ID = AWS.EMBED_MODEL
DIM = 1024
BATCH = 50           # rows fetched per loop; Titan embeds one at a time
MAX_CHARS = 8000     # Titan's input cap, roughly 8k tokens

bedrock = boto3.client("bedrock-runtime", region_name=REGION)


def embed(text):
    """One chunk -> one vector. Retries on throttling."""
    body = json.dumps({
        "inputText": text[:MAX_CHARS],
        "dimensions": DIM,
        "normalize": True,
    })
    for attempt in range(5):
        try:
            r = bedrock.invoke_model(modelId=MODEL_ID, body=body)
            return json.loads(r["body"].read())["embedding"]
        except bedrock.exceptions.ThrottlingException:
            wait = 2 ** attempt
            print(f"    throttled, waiting {wait}s")
            time.sleep(wait)
    raise RuntimeError("throttled 5 times, giving up")


def connect():
    conn = psycopg.connect(
        host=HOST, port=5432, dbname="grus",
        user="grusadmin", password=PWD, sslmode="require",
        keepalives=1, keepalives_idle=30, connect_timeout=30
    )
    conn.execute("SET search_path TO grus, public")
    return conn


def ensure_schema(conn):
    """Titan v2 is 1024-dim; the schema was written for 768."""
    current = conn.execute("""
        SELECT atttypmod FROM pg_attribute
        WHERE attrelid = 'grus.note_chunks'::regclass AND attname = 'embedding'
    """).fetchone()

    if current and current[0] != DIM:
        print(f"  resizing embedding column to VECTOR({DIM})")
        conn.execute("DROP INDEX IF EXISTS idx_chunk_vec")
        conn.execute(f"ALTER TABLE note_chunks ALTER COLUMN embedding TYPE VECTOR({DIM})")
        conn.commit()


def run():
    conn = connect()
    ensure_schema(conn)

    total = conn.execute("SELECT COUNT(*) FROM note_chunks").fetchone()[0]
    todo = conn.execute(
        "SELECT COUNT(*) FROM note_chunks WHERE embedding IS NULL"
    ).fetchone()[0]
    print(f"chunks: {total} total, {todo} to embed")

    done = 0
    t0 = time.time()

    while True:
        rows = conn.execute(f"""
            SELECT chunk_id, text FROM note_chunks
            WHERE embedding IS NULL
            ORDER BY chunk_id LIMIT {BATCH}
        """).fetchall()

        if not rows:
            break

        for chunk_id, text in rows:
            vec = embed(text)
            conn.execute(
                "UPDATE note_chunks SET embedding = %s WHERE chunk_id = %s",
                (str(vec), chunk_id),
            )
            done += 1

        conn.commit()
        rate = done / max(time.time() - t0, 1)
        left = (todo - done) / max(rate, 0.01)
        print(f"  {done}/{todo}  ({rate:.1f}/s, ~{left/60:.1f} min left)")

    print(f"\nembedded {done} chunks in {(time.time()-t0)/60:.1f} min")
    build_index(conn)
    verify(conn)
    conn.close()


def build_index(conn):
    """
    HNSW index. Build it AFTER loading — building on an empty table
    then inserting is much slower than the reverse.
    """
    print("\nbuilding HNSW index...")
    t0 = time.time()
    conn.execute("""
        CREATE INDEX IF NOT EXISTS idx_chunk_vec ON note_chunks
        USING hnsw (embedding vector_cosine_ops)
    """)
    conn.commit()
    print(f"  built in {time.time()-t0:.0f}s")


def verify(conn):
    """
    Semantic search should find things keyword search would miss.
    The test: ask about bleeding history for the trauma patient and see
    whether it surfaces the anticoagulation passage.
    """
    print("\nverification — semantic search")

    queries = [
        (19272232, "history of bleeding or anticoagulation"),
        (19272232, "what reversal agent was given"),
        (10245890, "heart failure and atrial fibrillation history"),
    ]

    for subject_id, q in queries:
        qvec = str(embed(q))
        rows = conn.execute("""
            SELECT section, LEFT(text, 160), 1 - (embedding <=> %s) AS similarity
            FROM note_chunks
            WHERE subject_id = %s AND embedding IS NOT NULL
            ORDER BY embedding <=> %s
            LIMIT 3
        """, (qvec, subject_id, qvec)).fetchall()

        print(f"\n  [{subject_id}] {q}")
        for section, snippet, sim in rows:
            print(f"    {sim:.3f}  {section}")
            print(f"           {snippet.strip()[:120]}...")


if __name__ == "__main__":
    run()