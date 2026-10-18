import psycopg
from psycopg.rows import tuple_row
from grus_config import DB
from grus_rules import evaluate

conn = psycopg.connect(
    host=DB.HOST, port=5432, dbname=DB.NAME, user=DB.USER,
    password=DB.PASSWORD, sslmode="require", row_factory=tuple_row)
conn.execute("SET search_path TO grus, public")

ids = [r[0] for r in conn.execute(
    "SELECT hadm_id FROM admissions WHERE is_current = TRUE").fetchall()]
conn.commit()

print(f"Evaluating rules for {len(ids)} patients...")

failed = 0
for i, hadm_id in enumerate(ids, 1):
    try:
        evaluate(conn, hadm_id, persist=True)
        conn.commit()
    except Exception as e:
        conn.rollback()
        failed += 1
        print(f"  {hadm_id} failed: {e}")
    if i % 25 == 0:
        print(f"{i}/{len(ids)}")

conn.close()
print(f"done: {len(ids) - failed} saved, {failed} failed")