"""
GRUS — Cohort relabelling

The original cohort query classified patients with ICD prefix matching that
ignored icd_version. ICD-9 and ICD-10 reuse letters for different things,
so the trauma demo patient (hemopericardium after a crash) landed in
'other' while his valve-replacement code looked like a transport accident.

This reclassifies all current admissions using the same version-aware
logic the rule engine uses. Run once.
"""

import os
import psycopg
from collections import Counter
from dotenv import load_dotenv



from grus_config import DB
HOST = DB.HOST
PWD = DB.PASSWORD


def is_trauma(code, ver):
    """
    True injuries only.

    The first pass used ICD-9 800-959, which swept in poisonings (960-989)
    and complications of care (996-999) and labelled 43% of the cohort as
    trauma. Narrowed to 800-904 — fractures, dislocations, sprains,
    intracranial and internal injury, open wounds, and vascular injury.

    External-cause codes only count as a transport, fall, or assault
    mechanism. Adverse drug effects and surgical misadventure are not
    injuries; they are complications the hospital recorded.
    """
    if ver == 9:
        if code[:3].isdigit():
            n = int(code[:3])
            # 800-904: fractures through injury to blood vessels
            # 905-909 are late effects, 910-959 superficial/unspecified
            if 800 <= n <= 904:
                return True
        if code.startswith("E") and code[1:4].isdigit():
            n = int(code[1:4])
            # E800-E848 transport, E880-E888 falls, E960-E969 assault
            return (800 <= n <= 848) or (880 <= n <= 888) or (960 <= n <= 969)
    elif ver == 10:
        if code[0] == "S":
            return True
        # V00-V99 transport, W00-W19 falls, X92-Y09 assault
        if code[0] == "V":
            return True
        if code[0] == "W" and code[1:3].isdigit():
            return int(code[1:3]) <= 19
        if code[0] == "X" and code[1:3].isdigit():
            return int(code[1:3]) >= 92
        if code[0] == "Y" and code[1:3].isdigit():
            return int(code[1:3]) <= 9
    return False


def is_bleeding(code, ver):
    if ver == 9:
        return code[:3] in ("423", "578", "285", "431", "432", "459", "998")
    return code[:3] in ("K92", "I31", "D62", "I60", "I61", "I62", "R58")


def is_cardiac(code, ver):
    if ver == 9:
        return code[:3] in ("410", "411", "412", "413", "414", "427",
                            "428", "429", "423", "424", "425")
    return code[:3] in ("I21", "I22", "I25", "I48", "I50", "I31", "I34", "I35")


def is_sepsis(code, ver):
    if ver == 9:
        return code[:3] in ("038", "995", "790") or code[:5] == "99592"
    return code[:3] in ("A41", "R65", "A40")


def is_respiratory(code, ver):
    if ver == 9:
        return code[:3] in ("486", "518", "480", "481", "482", "485", "491",
                            "492", "493", "496")
    return code[:3] in ("J18", "J96", "J44", "J45", "J12", "J13", "J15")


def classify(dx_rows):
    """
    dx_rows: [(icd_code, icd_version, seq_num), ...] ordered by seq_num.

    Trauma requires an injury in the first five coded positions. A fracture
    listed twentieth is usually incidental history, not what brought them
    in — matching on any mention put 43% of the cohort in trauma.

    Otherwise the earliest-ranked matching category wins, since seq_num 1
    is the principal diagnosis.
    """
    top = dx_rows[:5]
    if any(is_trauma(c, v) for c, v, _ in top):
        return "trauma"

    for code, ver, _ in dx_rows:
        if is_sepsis(code, ver):
            return "sepsis"
        if is_cardiac(code, ver) or is_bleeding(code, ver):
            return "cardiac"
        if is_respiratory(code, ver):
            return "respiratory"

    # An injury outside the top five still beats no category at all
    if any(is_trauma(c, v) for c, v, _ in dx_rows):
        return "trauma"
    return "other"


def run():
    conn = psycopg.connect(
        host=HOST, port=5432, dbname="grus", user="grusadmin",
        password=PWD, sslmode="require", keepalives=1, keepalives_idle=30)
    conn.execute("SET search_path TO grus, public")

    hadms = [r[0] for r in conn.execute(
        "SELECT hadm_id FROM admissions WHERE is_current = TRUE").fetchall()]

    before = Counter(r[0] or "null" for r in conn.execute(
        "SELECT cohort FROM admissions WHERE is_current = TRUE").fetchall())

    changed = 0
    for h in hadms:
        dx = conn.execute("""
            SELECT icd_code, icd_version, seq_num FROM diagnoses
            WHERE hadm_id = %s ORDER BY seq_num NULLS LAST
        """, (h,)).fetchall()
        if not dx:
            continue
        new = classify(dx)
        old = conn.execute(
            "SELECT cohort FROM admissions WHERE hadm_id = %s", (h,)).fetchone()[0]
        if new != old:
            conn.execute("UPDATE admissions SET cohort = %s WHERE hadm_id = %s",
                         (new, h))
            changed += 1
    conn.commit()

    after = Counter(r[0] or "null" for r in conn.execute(
        "SELECT cohort FROM admissions WHERE is_current = TRUE").fetchall())

    print(f"reclassified {changed} of {len(hadms)} admissions\n")
    print(f"{'cohort':14} {'before':>8} {'after':>8}")
    for k in sorted(set(before) | set(after)):
        print(f"{k:14} {before.get(k,0):>8} {after.get(k,0):>8}")

    print("\ndemo patients:")
    for h, name in [(28173870, "trauma case"), (27180495, "cardiac case")]:
        c = conn.execute(
            "SELECT cohort FROM admissions WHERE hadm_id = %s", (h,)).fetchone()
        print(f"  {name:14} {h}  ->  {c[0]}")

    conn.close()


if __name__ == "__main__":
    run()