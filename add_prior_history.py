import psycopg
from grus_config import DB
from psycopg.rows import tuple_row

conn = psycopg.connect(
    host=DB.HOST, port=5432, dbname=DB.NAME, user=DB.USER,
    password=DB.PASSWORD, sslmode="require", row_factory=tuple_row)
conn.execute("SET search_path TO grus, public")
conn.commit()

subject_id = 90000001

# A prior admission — mild chest pain investigated and discharged,
# consistent with the current cardiac presentation
prior_hadm_id = 90000101

conn.execute("""
    INSERT INTO admissions
        (hadm_id, subject_id, admittime, admission_type, arrival_unit,
         cohort, is_current)
    VALUES (%s, %s, now() - interval '8 months', 'URGENT',
            'Emergency Department', 'cardiac', FALSE)
""", (prior_hadm_id, subject_id))

# A relevant coded diagnosis for that prior visit
conn.execute("""
    INSERT INTO diagnoses (hadm_id, subject_id, icd_code, icd_version,
                           long_title, seq_num)
    VALUES (%s, %s, 'R07.9', 10, 'Chest pain, unspecified', 1)
""", (prior_hadm_id, subject_id))

conn.commit()
print(f"Prior admission {prior_hadm_id} created for subject {subject_id}")
conn.close()