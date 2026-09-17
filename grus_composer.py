"""
GRUS — Composer

Turns rule traces and retrieved data into a structured brief.

The model does NOT decide anything clinical. Rules already decided what
fires and at what severity; the retriever already decided what the values
are. The model's job is to phrase those decisions in the register a
clinician reads, and to say plainly what is missing.

Output is JSON, not prose. Three reasons:

  - The frontend gets real fields to render, not paragraphs to parse.
  - Verification checks a value in a field, which is exact; extracting
    numbers from prose is guesswork.
  - Layout belongs to the renderer. Terminal, web, PDF — same JSON.

The model's job shrinks to writing short phrases inside known fields,
which is much harder to get wrong than composing a whole document.
"""

import os
import json
import re
import time
import boto3
import psycopg
from dataclasses import dataclass, field
from grus_config import DB, AWS

from grus_tools import GrusTools
from grus_rules import evaluate



HOST = DB.HOST
PWD = DB.PASSWORD

REGION = AWS.REGION
COMPOSER_MODEL = AWS.FAST_MODEL

bedrock = boto3.client("bedrock-runtime", region_name=REGION)


SYSTEM = """You write emergency-department briefs for clinicians.

You are not diagnosing. Rules and retrieval have already decided what is
true. You are phrasing it.

OUTPUT: a single JSON object. No markdown fence, no commentary, nothing
before or after the JSON.

THE RULE THAT MATTERS MOST

Every value you write must be COPIED from the DATA block. Not rounded,
not converted, not inferred. If a value is not in the DATA, it does not
exist. A plausible invented number beside a real source id looks verified
and is not.

SCHEMA

{
  "summary": "one line: age, sex, presentation, single most important finding",

  "red_flags": [
    {
      "severity": "critical" | "warning",
      "title": "short, from the alert title",
      "detail": "one or two sentences with the values",
      "action": "what the DATA says to do",
      "sources": ["labs#301694", "medications#30851"]
    }
  ],

  "vitals": [
    {"label": "HR", "value": "89", "unit": "bpm",
     "hours": 36.5, "stale": false, "sources": ["vitals#492675"]}
  ],

  "labs": [
    {"label": "INR", "value": "6.3", "unit": null, "hours": -3.9,
     "abnormal": true, "pre_arrival": true, "sources": ["labs#301694"]}
  ],

  "medications": [
    {"drug": "heparin", "class": "anticoagulant",
     "status": "active" | "stopped" | "unknown",
     "note": "why the status is unknown, or null",
     "sources": ["medications#30851"]}
  ],

  "home_medications": {
    "found": true,
    "detail": "what the reconciled list says, verbatim where possible",
    "sources": ["note_chunks#975"]
  },

  "history": {
    "prior_admissions": 5,
    "recurring": ["congestive heart failure (5 visits)"],
    "detail": "one line, or null when there are none",
    "sources": []
  },

  "critical_unknowns": [
    {
      "what": "short label",
      "why_it_matters": "one line",
      "sources": []
    }
  ]
}

FIELD RULES

summary        One line. No citation needed.
red_flags      One per critical or warning alert in the DATA. Copy the
               severity and title. Do not invent alerts.
vitals         Only what the DATA lists. Short labels: HR, BP, RR, SpO2,
               Temp, GCS, Weight. Combine systolic and diastolic into one
               BP entry with both source ids.
labs           Priority first: INR, PT, PTT, Hgb, Hct, Platelets,
               Creatinine. Ten at most. Keep the DATA's units.
               "pre_arrival" is true ONLY when the DATA writes
               "pre-arrival" for that lab. A lab at 3.9h was drawn 3.9
               hours AFTER arrival — set pre_arrival false and put 3.9 in
               "hours". Copy the DATA's timing; do not assume.
medications    Anticoagulants, antiplatelets and NSAIDs first, then the
               rest. Eight at most.
critical_unknowns
               ONLY what the DATA explicitly marks absent, unknown or
               unreliable. Copy them.
                 DATA "sources: none - about data that does not exist" -> include
                 DATA "TIMING UNRELIABLE"                              -> include
                 DATA "NONE. First presentation here"                  -> include
                 DATA silent about head CT                             -> DO NOT MENTION
               Do not speculate about what a chart might lack.
               "Whether the patient has a bleeding disorder is unknown"
               is not a finding, it is you imagining a gap. Everything
               not in the DATA is unknown; listing those is infinite.

SOURCES

Every sources array holds ids copied exactly from the DATA:
"labs#301694". When something has no source in the DATA, use an empty
array. Never invent an id, and never put a word in there - not
"unknown", not "prior_admissions", not "no source".

STYLE

Terse. Clinical register. A registrar reads this in twenty seconds.
Times as hours since arrival, never dates.
Do not recommend a diagnosis or a drug dose. Repeat only the action the
DATA already states.
"""


def _src(s):
    return f"{s['table']}#{s['id']}"


@dataclass
class Brief:
    hadm_id: int
    as_of_hours: object
    data: dict = field(default_factory=dict)
    raw: str = ""
    sources_cited: list = field(default_factory=list)
    sources_available: list = field(default_factory=list)
    data_block: str = ""
    model: str = COMPOSER_MODEL
    tokens_in: int = 0
    tokens_out: int = 0
    latency_ms: int = 0
    parse_error: str = ""


class Composer:
    def __init__(self, conn, hadm_id, as_of_hours=None):
        self.conn = conn
        self.hadm_id = hadm_id
        self.as_of = as_of_hours
        self.tools = GrusTools(conn, hadm_id, as_of_hours)

    # -----------------------------------------------------------
    # Assemble the facts
    # -----------------------------------------------------------
    def build_data_block(self):
        lines, sources = [], []

        def add_sources(res):
            for s in res.sources:
                tag = _src(s)
                if tag not in sources:
                    sources.append(tag)

        summ = self.tools.get_patient_summary()
        p = summ.rows[0]
        lines.append("PATIENT")
        lines.append(f"  {p['age']}{p['sex']}, {p['admission_type']}, "
                     f"arrived via {p['arrival_unit']}")
        lines.append(f"  category: {p['category']}, "
                     f"prior admissions: {p['prior_admissions']}")
        view = 'full record' if self.as_of is None else f'first {self.as_of}h only'
        lines.append(f"  view: {view}")

        alerts = evaluate(self.conn, self.hadm_id, self.as_of, persist=False)
        lines.append("\nALERTS (from the rule engine - already evaluated)")
        if not alerts:
            lines.append("  none triggered")
        for a in alerts:
            lines.append(f"  [{a.severity}] {a.title}")
            lines.append(f"      detail: {a.body}")
            lines.append(f"      action: {a.action}")
            if a.sources:
                tags = ", ".join(_src(s) for s in a.sources)
                lines.append(f"      sources: [{tags}]")
                for s in a.sources:
                    if _src(s) not in sources:
                        sources.append(_src(s))
            else:
                lines.append("    (no source - this alert reports absent data)")

        dx = self.tools.get_diagnoses()
        if dx.found:
            lines.append("\nDIAGNOSES (coded, principal first)")
            for d, s in zip(dx.rows[:8], dx.sources[:8]):
                lines.append(f"  {d['title']} [{_src(s)}]")
            add_sources(dx)

        labs = self.tools.get_arrival_labs()
        if labs.found:
            lines.append("\nARRIVAL LABS")
            for l, s in zip(labs.rows, labs.sources):
                flag = " ABNORMAL" if l["abnormal"] else ""
                lines.append(f"  {l['label']}: {l['value']} "
                             f"{l['unit'] or ''} ({l['when']}){flag} [{_src(s)}]")
            add_sources(labs)
        else:
            lines.append("\nARRIVAL LABS\n  none available in this window")

        vit = self.tools.get_current_vitals()
        lines.append("\nCURRENT VITALS")
        if vit.found:
            for v, s in zip(vit.rows, vit.sources):
                stale = (f" STALE {v['stale_hours']}h old"
                         if v.get("stale_hours") else "")
                lines.append(f"  {v['label']}: {v['value']} "
                             f" at {v['hours']}h{stale} [{_src(s)}]")
            add_sources(vit)
        else:
            lines.append(f"  NONE. {vit.note}")

        meds = self.tools.get_medications()
        lines.append("\nMEDICATIONS THIS ADMISSION")
        if meds.found:
            for m, s in zip(meds.rows[:15], meds.sources[:15]):
                unreliable = "" if m["timing_reliable"] else " TIMING UNRELIABLE"
                cls = f" ({m['class']})" if m["class"] else ""
                lines.append(f"  {m['drug']}{cls}: {m['status']}"
                             f" from {m['start_hours']}h{unreliable} [{_src(s)}]")
            if meds.note:
                lines.append(f"  note: {meds.note}")
            add_sources(meds)
        else:
            lines.append(f"  NONE FOUND. {meds.note}")

        home = self.tools.get_home_medications()
        lines.append("\nHOME MEDICATIONS (before arrival)")
        if home.found:
            lines.append(f"  {home.summary}")
            for h, s in zip(home.rows[:2], home.sources[:2]):
                txt = h["text"][:400].replace("\n", " ")
                lines.append(f"  from {h['source']}: {txt} [{_src(s)}]")
            if home.note:
                lines.append(f"  note: {home.note}")
            add_sources(home)
        else:
            lines.append(f"  NOT DOCUMENTED. {home.note}")

        hist = self.tools.get_admission_history()
        lines.append("\nPRIOR ADMISSIONS")
        if hist.found:
            for h in hist.rows[:5]:
                dxs = "; ".join(h["principal_diagnoses"][:2])
                lines.append(f"  {h['admission_type']}, "
                             f"{h['length_of_stay_days']}d: {dxs}")
            rec = self.tools.get_recurring_diagnoses()
            if rec.found:
                lines.append("  recurring across visits:")
                for r, s in zip(rec.rows[:5], rec.sources[:5]):
                    lines.append(f"    {r['condition']} ({r['visits']}) [{_src(s)}]")
                add_sources(rec)
        else:
            lines.append(f"  NONE. {hist.note}")

        out = self.tools.get_outputs()
        if out.found:
            pairs = [(o, s) for o, s in zip(out.rows, out.sources)
                     if "void" not in o["label"].lower()]
            if pairs:
                lines.append("\nDRAIN OUTPUT")
                for o, s in pairs[:6]:
                    lines.append(f"  {o['label']}: {o['value']}{o['unit']} "
                                 f" at {o['hours']}h [{_src(s)}]")
                add_sources(out)

        return "\n".join(lines), sources

    # -----------------------------------------------------------
    # Generate
    # -----------------------------------------------------------
    @staticmethod
    def _extract_json(text):
        """
        Qwen3 emits reasoning in <think> tags and sometimes wraps JSON in a
        markdown fence. Strip both, then take the outermost object.
        """
        text = re.sub(r"<think>.*?</think>", "", text, flags=re.DOTALL)
        text = re.sub(r"^```(?:json)?|```$", "", text.strip(),
                      flags=re.MULTILINE).strip()
        start, end = text.find("{"), text.rfind("}")
        if start == -1 or end == -1:
            raise ValueError("no JSON object in model output")
        return json.loads(text[start:end + 1])

    def compose(self, temperature=0.2):
        data_block, available = self.build_data_block()

        prompt = (f"DATA\n{'=' * 60}\n{data_block}\n{'=' * 60}\n\n"
                  f"Return the JSON object. Nothing else.")

        t0 = time.time()
        r = bedrock.converse(
            modelId=COMPOSER_MODEL,
            system=[{"text": SYSTEM}],
            messages=[{"role": "user", "content": [{"text": prompt}]}],
            inferenceConfig={"maxTokens": 2500, "temperature": temperature},
        )
        ms = int((time.time() - t0) * 1000)
        raw = r["output"]["message"]["content"][0]["text"]

        brief = Brief(
            hadm_id=self.hadm_id, as_of_hours=self.as_of, raw=raw,
            sources_available=available, data_block=data_block,
            tokens_in=r["usage"]["inputTokens"],
            tokens_out=r["usage"]["outputTokens"], latency_ms=ms,
        )

        try:
            brief.data = self._extract_json(raw)
        except Exception as e:
            brief.parse_error = str(e)
            return brief

        brief.sources_cited = sorted(set(
            re.findall(r"([a-z_]+#\d+)", json.dumps(brief.data))))
        return brief


# ---------------------------------------------------------------
# Rendering - the layout lives here, not in the model
# ---------------------------------------------------------------
SEV_MARK = {"critical": "!!", "warning": "! ", "info": "i ", "unknown": "? "}


def _wrap(text, width):
    words, lines, cur = (text or "").split(), [], ""
    for w in words:
        if len(cur) + len(w) + 1 > width:
            lines.append(cur)
            cur = w
        else:
            cur = f"{cur} {w}".strip()
    if cur:
        lines.append(cur)
    return lines or [""]


def _columns(cells, width, n):
    col = width // n
    rows = []
    for i in range(0, len(cells), n):
        rows.append("  " + "".join(c.ljust(col) for c in cells[i:i + n]))
    return rows


def render_terminal(b: Brief, width=76):
    """A doctor's-eye view. The web renderer reads the same JSON."""
    if b.parse_error:
        return f"[brief could not be parsed: {b.parse_error}]\n\n{b.raw[:600]}"

    d = b.data
    L = []
    rule = "-" * width

    L.append(rule)
    L.append((d.get("summary") or "").upper())
    view = "FULL RECORD" if b.as_of_hours is None else f"AS OF HOUR {b.as_of_hours}"
    L.append(f"{view}   |   admission {b.hadm_id}")
    L.append(rule)

    for f in (d.get("red_flags") or []):
        mark = SEV_MARK.get(f.get("severity", ""), "  ")
        L.append("")
        L.append(f"[{mark}] {f.get('title', '')}")
        for line in _wrap(f.get("detail", ""), width - 7):
            L.append(f"       {line}")
        if f.get("action"):
            L.append(f"       -> {f['action']}")
        if f.get("sources"):
            L.append(f"       {' '.join(f['sources'])}")

    vitals = d.get("vitals") or []
    if vitals:
        L.append("")
        L.append("VITALS")
        cells = []
        for v in vitals:
            val = f"{v.get('value','')}{v.get('unit') or ''}"
            stale = " (stale)" if v.get("stale") else ""
            cells.append(f"{v.get('label',''):<7}{val:>9}{stale}")
        L += _columns(cells, width, 3)
        first_h = next((v.get("hours") for v in vitals
                        if v.get("hours") is not None), None)
        if first_h is not None:
            L.append(f"  recorded at {first_h}h")

    labs = d.get("labs") or []
    if labs:
        L.append("")
        L.append("LABS")
        L.append(f"   {'':<22}{'value':>12}   when")
        for l in labs:
            val = f"{l.get('value','')} {l.get('unit') or ''}".strip()
            mark = "*" if l.get("abnormal") else " "
            hrs = l.get("hours")
            # Trust the number over the flag. A lab at 3.9h was drawn
            # after arrival whatever the model tagged it.
            if hrs is not None and hrs >= 0:
                when = f"{hrs}h"
            elif hrs is not None:
                when = f"pre-arrival ({abs(hrs)}h before)"
            elif l.get("pre_arrival"):
                when = "pre-arrival"
            else:
                when = ""
            L.append(f" {mark} {l.get('label',''):<22}{val:>12}   {when}")
        L.append("   * abnormal")

    meds = d.get("medications") or []
    if meds:
        L.append("")
        L.append("MEDICATIONS")
        for m in meds:
            box = {"active": "[on ]", "stopped": "[off]"}.get(
                m.get("status", ""), "[ ? ]")
            cls = f"  ({m['class']})" if m.get("class") else ""
            L.append(f"  {box} {m.get('drug','')}{cls}")
            if m.get("note"):
                for line in _wrap(m["note"], width - 9):
                    L.append(f"        {line}")

    home = d.get("home_medications") or {}
    if home.get("found"):
        L.append("")
        L.append("HOME MEDICATIONS")
        for line in _wrap(home.get("detail", ""), width - 2):
            L.append(f"  {line}")

    hist = d.get("history") or {}
    if hist.get("prior_admissions"):
        L.append("")
        L.append(f"HISTORY - {hist['prior_admissions']} prior admissions")
        for r in (hist.get("recurring") or [])[:6]:
            L.append(f"  - {r}")
        if hist.get("detail"):
            for line in _wrap(hist["detail"], width - 2):
                L.append(f"  {line}")

    L.append("")
    L.append("CRITICAL UNKNOWNS")
    unknowns = d.get("critical_unknowns") or []
    if unknowns:
        for u in unknowns:
            L.append(f"  ? {u.get('what','')}")
            for line in _wrap(u.get("why_it_matters", ""), width - 6):
                L.append(f"      {line}")
    else:
        L.append("  None identified - absence of a record is not absence")
        L.append("  of a condition.")

    L.append("")
    L.append(rule)
    L.append("decision support, not diagnosis")
    return "\n".join(L)


if __name__ == "__main__":
    conn = psycopg.connect(
        host=HOST, port=5432, dbname="grus", user="grusadmin",
        password=PWD, sslmode="require", keepalives=1, keepalives_idle=30)
    conn.execute("SET search_path TO grus, public")

    for label, hadm, as_of in [
        ("28173870 - arrival (hour 1)", 28173870, 1),
        ("28173870 - full record",      28173870, None),
        ("27180495 - full record",      27180495, None),
    ]:
        print("\n\n" + "=" * 76)
        print(label)
        print("=" * 76)
        b = Composer(conn, hadm, as_of).compose()
        print(render_terminal(b))
        print(f"\n{b.latency_ms}ms  |  {b.tokens_in} in / {b.tokens_out} out"
              f"  |  {len(b.sources_cited)} sources cited")

    conn.close()
