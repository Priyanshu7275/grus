"""
GRUS — Verifier

Checks a generated brief against the data it was given, and strips
anything that cannot be traced.

This exists because prompting is not a guarantee. Early Composer runs
produced vital signs that were never supplied, printed beside genuine
source ids. A fabricated value next to a real citation looks verified
and is not.

The Verifier is deterministic. No model, no judgement. It walks the JSON
field by field and checks every value and every source id against what
the Composer actually had.

Checking fields is far more exact than checking prose. "value": "121" is
either in the source data or it is not; extracting 121 from a sentence
and guessing whether it was clinical was always approximate.
"""

import os
import re
import json
import psycopg
from dataclasses import dataclass, field
from grus_config import DB

from grus_composer import Composer, Brief, render_terminal



HOST = DB.HOST
PWD = DB.PASSWORD

SOURCE_TAG = re.compile(r"^[a-z_]+#\d+$")
NUMBER = re.compile(r"-?\d+(?:\.\d+)?")


@dataclass
class Problem:
    where: str          # "red_flags[0].detail"
    kind: str           # invented_value | invented_source | bad_source_format
    detail: str


@dataclass
class VerificationResult:
    data: dict = field(default_factory=dict)
    problems: list = field(default_factory=list)
    claims_made: int = 0
    claims_sourced: int = 0
    claims_removed: int = 0

    @property
    def traceable_pct(self):
        if not self.claims_made:
            return 100
        return round(100 * self.claims_sourced / self.claims_made)

    @property
    def passed(self):
        return not self.problems


class Verifier:
    """
    Builds the set of values and source ids the Composer was given, then
    walks the returned JSON checking each field against it.
    """

    def __init__(self, brief: Brief):
        self.brief = brief
        self.allowed_sources = set(brief.sources_available)
        self.allowed_values = self._values_from(brief.data_block)

    @staticmethod
    def _values_from(data_block):
        """
        Every number in the DATA block, in the forms a model might
        reasonably write it. 36.47 may sensibly become 36.5; 2.0 may
        become 2.
        """
        allowed = set()
        nums = []
        for raw in NUMBER.findall(data_block):
            allowed.add(raw)
            try:
                f = float(raw)
            except ValueError:
                continue
            nums.append(f)
            allowed.add(str(f))
            allowed.add(f"{f:.1f}")
            allowed.add(str(round(f, 1)))
            allowed.add(str(round(f)))
            if f == int(f):
                allowed.add(str(int(f)))
            if f < 0:
                allowed.add(str(abs(f)))          # pre-arrival shown positive
                allowed.add(f"{abs(f):.1f}")

        # GCS is reported as a total. The DATA carries the three
        # components separately (eye, verbal, motor), so the sum a
        # clinician actually reads appears nowhere in the source. Allow
        # sums of small integers, which is all a GCS total can be.
        smalls = sorted({int(f) for f in nums if 0 < f <= 6 and f == int(f)})
        for a in smalls:
            for b in smalls:
                for c in smalls:
                    if 3 <= a + b + c <= 15:
                        allowed.add(str(a + b + c))
        return allowed

    def _check_value(self, value, where, problems):
        """
        A field's numbers must all come from the source data. Composite
        values like '121/65' are split first — a clinician writes blood
        pressure that way and both halves are real readings.
        """
        if value is None:
            return True
        text = str(value)
        ok = True
        for n in NUMBER.findall(text):
            if n not in self.allowed_values:
                problems.append(Problem(where, "invented_value",
                                        f"'{n}' is not in the source data"))
                ok = False
        return ok

    def _check_sources(self, sources, where, problems):
        ok = True
        for s in (sources or []):
            if not SOURCE_TAG.match(str(s)):
                problems.append(Problem(where, "bad_source_format",
                                        f"'{s}' is not a source id"))
                ok = False
            elif s not in self.allowed_sources:
                problems.append(Problem(where, "invented_source",
                                        f"'{s}' was never supplied"))
                ok = False
        return ok

    def verify(self, strip=True):
        res = VerificationResult()
        d = json.loads(json.dumps(self.brief.data))   # work on a copy
        p = res.problems

        if self.brief.parse_error:
            p.append(Problem("(root)", "parse_error", self.brief.parse_error))
            res.data = d
            return res

        # --- summary: prose, values must still be real ---
        if d.get("summary"):
            res.claims_made += 1
            if self._check_value(d["summary"], "summary", p):
                res.claims_sourced += 1
            elif strip:
                d["summary"] = "[summary removed - contained untraceable values]"
                res.claims_removed += 1

        # --- list sections ---
        for section, value_fields in [
            ("red_flags",   ["detail", "title", "action"]),
            ("vitals",      ["value"]),
            ("labs",        ["value"]),
            ("medications", ["note"]),
        ]:
            kept = []
            for i, item in enumerate(d.get(section) or []):
                res.claims_made += 1
                where = f"{section}[{i}]"
                good = True
                for f in value_fields:
                    if not self._check_value(item.get(f), f"{where}.{f}", p):
                        good = False
                if not self._check_sources(item.get("sources"), where, p):
                    good = False

                if good:
                    res.claims_sourced += 1
                    kept.append(item)
                elif strip:
                    res.claims_removed += 1
                else:
                    kept.append(item)
            if section in d:
                d[section] = kept

        # --- objects ---
        for section in ["home_medications", "history"]:
            obj = d.get(section)
            if not isinstance(obj, dict):
                continue
            res.claims_made += 1
            good = self._check_value(obj.get("detail"), f"{section}.detail", p)
            if not self._check_sources(obj.get("sources"), section, p):
                good = False
            if good:
                res.claims_sourced += 1
            elif strip:
                d[section] = {"found": False,
                              "detail": "[removed - could not be traced]"}
                res.claims_removed += 1

        # --- critical unknowns: about absence, so values are rare ---
        kept = []
        for i, u in enumerate(d.get("critical_unknowns") or []):
            res.claims_made += 1
            where = f"critical_unknowns[{i}]"
            good = self._check_value(u.get("why_it_matters"),
                                     f"{where}.why_it_matters", p)
            if not self._check_sources(u.get("sources"), where, p):
                good = False
            if good:
                res.claims_sourced += 1
                kept.append(u)
            elif strip:
                res.claims_removed += 1
        if "critical_unknowns" in d:
            d["critical_unknowns"] = kept

        res.data = d
        return res


def compose_and_verify(conn, hadm_id, as_of_hours=None, retries=1):
    """
    Generate, verify, regenerate once if anything was fabricated. A second
    attempt at temperature 0 usually sticks to the data.
    """
    for attempt in range(retries + 1):
        brief = Composer(conn, hadm_id, as_of_hours).compose(
            temperature=0.2 if attempt == 0 else 0.0)
        res = Verifier(brief).verify()
        if res.passed or attempt == retries:
            brief.data = res.data          # verified version is the one shown
            return brief, res, attempt
    return brief, res, retries


def report(brief: Brief, res: VerificationResult):
    print(render_terminal(brief))
    print()
    print(f"  claims {res.claims_made} | sourced {res.claims_sourced} | "
          f"removed {res.claims_removed} | traceable {res.traceable_pct}%")
    if res.problems:
        for pr in res.problems[:8]:
            print(f"    {pr.kind:18} {pr.where:28} {pr.detail}")
    else:
        print("  PASSED - nothing fabricated")


if __name__ == "__main__":
    import sys

    conn = psycopg.connect(
        host=HOST, port=5432, dbname="grus", user="grusadmin",
        password=PWD, sslmode="require", keepalives=1, keepalives_idle=30)
    conn.execute("SET search_path TO grus, public")

    if "--data" in sys.argv:
        block, srcs = Composer(conn, 28173870, None).build_data_block()
        print(block)
        print(f"\n({len(srcs)} source tags available)")
        conn.close()
        sys.exit()

    if "--json" in sys.argv:
        b, r, _ = compose_and_verify(conn, 28173870, 1)
        print(json.dumps(b.data, indent=2))
        conn.close()
        sys.exit()

    for label, hadm, as_of in [
        ("28173870 - arrival (hour 1)", 28173870, 1),
        ("28173870 - full record",      28173870, None),
        ("27180495 - full record",      27180495, None),
    ]:
        print("\n\n" + "=" * 76)
        print(label)
        print("=" * 76)
        brief, res, attempts = compose_and_verify(conn, hadm, as_of)
        report(brief, res)
        print(f"  {brief.latency_ms}ms, {attempts + 1} generation(s)")

    conn.close()