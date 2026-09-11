import duckdb
T = "C:/Users/Hp/OneDrive/Desktop/Grus/tier2processed"

d = duckdb.connect()
d.execute(f"""
  CREATE VIEW vh AS SELECT * FROM read_parquet('{T}/vitals_hourly/*.parquet');
  CREATE VIEW lc AS SELECT * FROM read_parquet('{T}/labs_clean/*.parquet');
""")

print(d.execute("""
  SELECT stay_id, hadm_id,
         COUNT(*) AS hours,
         ROUND(MIN(shock_index),2) AS si_min,
         ROUND(MAX(shock_index),2) AS si_max,
         ROUND(MIN(sbp)) AS sbp_min,
         ROUND(MAX(hr)) AS hr_max
  FROM vh
  WHERE shock_index IS NOT NULL AND hour_bin BETWEEN 0 AND 24
  GROUP BY 1,2
  HAVING MIN(shock_index) < 0.85
     AND MAX(shock_index) > 1.3
     AND COUNT(*) >= 8
  ORDER BY si_max DESC
  LIMIT 15
""").df().to_string())

print(d.execute("""
  SELECT stay_id, hadm_id, COUNT(*) AS hours,
         ROUND(MIN(shock_index),2) AS si_min,
         ROUND(MAX(shock_index),2) AS si_max,
         ROUND(MIN(sbp)) AS sbp_min, ROUND(MAX(hr)) AS hr_max
  FROM vh
  WHERE shock_index IS NOT NULL AND hour_bin BETWEEN 0 AND 24
    AND hr BETWEEN 30 AND 200
    AND sbp BETWEEN 50 AND 220
  GROUP BY 1,2
  HAVING MIN(shock_index) < 0.85 AND MAX(shock_index) BETWEEN 1.2 AND 2.0
     AND COUNT(*) >= 10
  ORDER BY si_max DESC LIMIT 15
""").df().to_string())

print(d.execute("""
  SELECT hour_bin, ROUND(hr) AS hr, ROUND(sbp) AS sbp, ROUND(dbp) AS dbp,
         ROUND(spo2) AS spo2, ROUND(rr) AS rr,
         ROUND(shock_index,2) AS si, gcs_total
  FROM vh WHERE stay_id = 36448499 AND hour_bin BETWEEN 0 AND 24
  ORDER BY hour_bin
""").df().to_string())

print(d.execute("""
  SELECT CAST(FLOOR(hours_since_admit) AS INT) AS hour,
         lab_label, ROUND(valuenum,2) AS value
  FROM lc WHERE hadm_id = 21976128
    AND lab_label IN ('Hemoglobin','Hematocrit','INR(PT)','Lactate',
                      'Platelet Count','Creatinine')
    AND hours_since_admit BETWEEN 0 AND 24
  ORDER BY hour, lab_label
""").df().to_string())

print(d.execute("""
WITH hgb AS (
  SELECT hadm_id,
         MAX(valuenum) FILTER (WHERE hours_since_admit <= 6) AS early,
         MIN(valuenum) FILTER (WHERE hours_since_admit BETWEEN 6 AND 24) AS late
  FROM lc
  WHERE lab_label = 'Hemoglobin' AND valuenum BETWEEN 4 AND 20
    AND hours_since_admit BETWEEN 0 AND 24
  GROUP BY 1
),
vit AS (
  SELECT hadm_id, stay_id, COUNT(*) AS hours,
         ROUND(MIN(shock_index),2) AS si_min,
         ROUND(MAX(shock_index),2) AS si_max
  FROM vh
  WHERE hr BETWEEN 30 AND 200 AND sbp BETWEEN 50 AND 220
    AND hour_bin BETWEEN 0 AND 24
  GROUP BY 1,2 HAVING COUNT(*) >= 10
)
SELECT v.stay_id, v.hadm_id, v.hours, v.si_min, v.si_max,
       ROUND(h.early,1) AS hgb_start, ROUND(h.late,1) AS hgb_low,
       ROUND(h.early - h.late,1) AS drop
FROM vit v JOIN hgb h ON h.hadm_id = v.hadm_id
WHERE h.early - h.late >= 2.5 AND v.si_max > 1.0
ORDER BY drop DESC LIMIT 15
""").df().to_string())

print(d.execute("""
  SELECT hour_bin, ROUND(hr) AS hr, ROUND(sbp) AS sbp, ROUND(dbp) AS dbp,
         ROUND(spo2) AS spo2, ROUND(rr) AS rr,
         ROUND(shock_index,2) AS si, gcs_total
  FROM vh WHERE stay_id = 36064998 AND hour_bin BETWEEN 0 AND 20
  ORDER BY hour_bin
""").df().to_string())

print(d.execute("""
  SELECT CAST(FLOOR(hours_since_admit) AS INT) AS hour,
         lab_label, ROUND(valuenum,2) AS value
  FROM lc WHERE hadm_id = 25502924
    AND lab_label IN ('Hemoglobin','Hematocrit','INR(PT)','Lactate',
                      'Platelet Count','Creatinine','Base Excess')
    AND hours_since_admit BETWEEN 0 AND 20
  ORDER BY hour, lab_label
""").df().to_string())

print(d.execute("""
  SELECT hour_bin, ROUND(hr) AS hr, ROUND(sbp) AS sbp, ROUND(dbp) AS dbp,
         ROUND(spo2) AS spo2, ROUND(rr) AS rr,
         ROUND(shock_index,2) AS si, gcs_total
  FROM vh WHERE stay_id = 36064998 AND hour_bin BETWEEN 0 AND 9
  ORDER BY hour_bin
""").df().to_string())

print(d.execute("""
  SELECT hour_bin, ROUND(hr) AS hr, ROUND(sbp) AS sbp, ROUND(spo2) AS spo2,
         ROUND(rr) AS rr, ROUND(shock_index,2) AS si
  FROM vh WHERE stay_id = 37314726 AND hour_bin BETWEEN 0 AND 20
  ORDER BY hour_bin
""").df().to_string())