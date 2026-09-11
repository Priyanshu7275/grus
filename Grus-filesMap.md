# GRUS — File Map

What each file does and when to run it.

---

## Pipeline scripts (`.py`)

Run these in order. They import from each other, so keep them in the same folder.

| File | What it does | Runtime |
|---|---|---|
| `grus_etl_tier1.py` | Current admission for all 301 patients → Aurora. Patients, admissions, diagnoses, procedures, medications, labs, vitals, outputs. All twelve Phase 1 transforms live here. | ~10 min |
| `grus_notes_pipeline.py` | Discharge summaries for those 301 admissions. Splits each note by clinical section, loads to `notes` and `note_chunks`. | ~3 min |
| `grus_etl_prior.py` | Up to 5 previous admissions per patient. Diagnoses, meds, procedures, key labs, discharge summaries. Marked `cohort='prior'`. No vitals or outputs. | ~8 min |

```
python grus_etl_tier1.py
python grus_notes_pipeline.py
python grus_etl_prior.py
```

**Before running any of them:** close the notebook connection or restart the kernel. An open connection holds a lock and `TRUNCATE` will hang indefinitely.

`grus_etl_prior.py` imports `DRUG_NORM_SQL` and the chunking functions from the other two — it must run last, and all three must be `.py`, not `.ipynb`.

---

## Notebooks (`.ipynb`)

Exploration and one-off setup. Not part of the pipeline.

| File | What it was for |
|---|---|
| `duckdb.ipynb` | Phase 1 exploration. Narrowed 431,088 admissions to the demo patient, found the twelve ETL findings. |
| `cohort.ipynb` | Built `cohort_300.csv` — 301 patients stratified across trauma / cardiac / sepsis / respiratory / other. |
| `grus-db aurora.ipynb` | Aurora connection, pgvector extensions, schema creation. Run once. |
| `training-data.ipynb` | Tier 2 extract — 20,000 patients to Parquet for S3 and Glue. |

---

## Other files

| File | What it is |
|---|---|
| `Grus schema.sql` | The 16-table Aurora schema. Applied via `grus-db aurora.ipynb`. |
| `cohort_300.csv` | The 301 selected patients. Read by all three ETL scripts. **Patient data — never commit.** |
| `.env` | `DB_PASSWORD`. Gitignored. |
| `.gitignore` | Must cover `.env`, `*.csv`, `tier2/`, `grus-env/`, `*.ipynb` if notebooks hold credentials. |
| `GRUS-Project-Reference.md` | Architecture, roadmap, team split. |
| `GRUS-Phase1-Conclusion.md` | Demo patient, twelve ETL findings, table decisions. |
| `GRUS-API-Contract.md` | Endpoint shapes for the frontend. |

---

## Data locations

```
C:/Users/Hp/OneDrive/Desktop/MIMICS ETL/
├── mimic-iv-2.1/hosp/        structured tables
├── mimic-iv-2.1/icu/         vitals, outputs
└── note/discharge.csv.gz     clinical text

C:/.../Grus/tier2/            Parquet for S3 upload
```

---

## AWS

| Service | What's there |
|---|---|
| Aurora PostgreSQL `grus-db` | 16 tables, ap-south-1, pgvector 0.8.0 |
| S3 `grus-mimic-data-etl` | `raw/` (20k Parquet), `processed/` (Glue output) |
| Glue `grus-transform-tier2` | Tier 2 job → 4 training tables |
| Bedrock | Claude + Qwen, global inference profiles |

---

## Known issues

- **Password hardcoded** in `grus-db aurora.ipynb`. Move to `os.getenv("DB_PASSWORD")` and rotate.
- **Project folder is inside OneDrive.** ETL writes patient-derived extracts here; OneDrive syncs them to Microsoft's cloud. DUA concern.
- **`VITAL_MAP` is duplicated** in `grus_etl_tier1.py` and the Glue script. If they drift, training and serving see different numbers. Extract to a shared module before training models.
- **Aurora is publicly accessible** with an IP allowlist. Fine for development; move to private subnet before submission.

---

## Not built yet

Retriever, rule engine, the five Strands agents, embeddings, risk models, API, frontend.