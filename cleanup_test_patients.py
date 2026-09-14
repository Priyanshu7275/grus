import psycopg
from grus_config import DB
from psycopg.rows import tuple_row

conn = psycopg.connect(
    host=DB.HOST, port=5432, dbname=DB.NAME, user=DB.USER,
    password=DB.PASSWORD, sslmode="require", row_factory=tuple_row)
conn.execute("SET search_path TO grus, public")
conn.commit()

# Delete all simulated test patients (hadm_id >= 90000000)
tables = ["note_chunks", "notes", "alert_traces", "vitals", "labs",
          "diagnoses", "medications", "icu_stays", "transfers",
          "admissions"]

for t in tables:
    conn.execute(f"DELETE FROM {t} WHERE hadm_id >= 90000000")

conn.execute("DELETE FROM patients WHERE subject_id >= 90000000")
conn.commit()
print("Deleted all test/simulated patients")
conn.close()