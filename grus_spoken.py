"""
GRUS — Spoken sources

On screen a claim carries its row id: "INR 2.8 [labs#361092]". Read aloud,
"labs hash three six one zero nine two" helps nobody. A clinician wants to
know what kind of record it came from and how recent it is.

This turns ids into words — AFTER the value check, never before, so the
spoken answer is exactly as verified as the written one. The written
answer keeps its ids for the screen and the record.

Every word comes from the row itself, never from the model:
  - time from hours_since_admit (MIMIC-IV shifts calendar dates for
    privacy, so no clock time or date is ever spoken for these records)
  - "N hours ago" only when a point in time (as_of) is set
  - medications with time_valid = false say their timing is unreliable
  - diagnoses carry no time, so none is claimed
A row that does not belong to this patient is not described at all.
"""

import re
from grus_valuecheck import TAGS, ONE_TAG, REMOVED

# table -> (id column, how it is spoken)
_TIMED = {
    "labs":       ("lab_id",       "the labs"),
    "vitals":     ("vital_id",     "the vitals"),
    "outputs":    ("output_id",    "the output chart"),
    "procedures": ("procedure_id", "the procedure record"),
    "transfers":  ("transfer_id",  "the transfer record"),
}

_NOTE_TYPES = {"ds": "discharge summary", "discharge": "discharge summary",
               "rr": "radiology report", "radiology": "radiology report"}


# How units are read aloud. Concentration units are dropped, as clinicians
# do ("haemoglobin six point eight"); a few are spelled out.
_UNITS_SPOKEN = [
    (r"\s*(?:g/dL|mg/dL|mEq/L|mmol/L|µmol/L|umol/L|K/uL|K/µL|IU/L|U/L|ng/mL|pg/mL|mmHg|bpm|insp/min)\b", ""),
    (r"\s*%", " percent"),
    (r"\s*°F", " degrees Fahrenheit"),
    (r"\s*°C", " degrees Celsius"),
    (r"\s*mL\b", " millilitres"),
]


def speak_units(text):
    for pat, rep in _UNITS_SPOKEN:
        text = re.sub(pat, rep, text)
    return _speak_hours(text)


def _speak_hours(text):
    """
    "stopped about 499 hours after arrival" -> "stopped on day 21 of the
    admission". Nobody counts in hundreds of hours aloud. Under 48 hours
    stays as hours, which is how clinicians say it.
    """
    def rep(m):
        h = float(m.group(1))
        if h < 48:
            return m.group(0)
        return f"on day {int(h // 24) + 1} of the admission"
    return re.sub(
        r"\b(?:at\s+|about\s+|around\s+|approximately\s+)?"
        r"(\d+(?:\.\d+)?)\s*(?:hours?|hrs?|h)\s+"
        r"(?:after|since|into|post)\s+(?:the\s+)?(?:arrival|admission)\b",
        rep, text, flags=re.I)


def _hours(n):
    n = int(round(n))
    return f"{n} hour" if n == 1 else f"{n} hours"


def when(h, as_of=None):
    """A position on the admission timeline, in words. None if unknown."""
    if h is None:
        return None
    h = float(h)
    if h < 0:
        return "before arrival"
    if h <= 1:
        return "on arrival"
    if as_of is not None and float(as_of) >= h:
        age = float(as_of) - h
        if age < 1:
            return "within the last hour"
        if age < 48:
            return f"{_hours(age)} ago"
        return f"{int(age // 24)} days ago"
    if h < 48:
        return f"{_hours(h)} into the admission"
    return f"on day {int(h // 24) + 1} of the admission"


def lookup(conn, hadm_id, tags, as_of=None):
    """
    tag -> (what, when). Rows outside this admission are skipped, so a
    stray id can never be read out as if it were this patient's.
    """
    by_table = {}
    for t in tags:
        table, _, rid = t.partition("#")
        if rid.isdigit():
            by_table.setdefault(table, []).append(int(rid))

    out = {}
    for table, ids in by_table.items():
        if table in _TIMED:
            col, what = _TIMED[table]
            rows = conn.execute(
                f"SELECT {col}, hours_since_admit FROM {table} "
                f"WHERE {col} = ANY(%s) AND hadm_id = %s",
                (ids, hadm_id)).fetchall()
            for rid, h in rows:
                out[f"{table}#{rid}"] = (what, when(h, as_of))

        elif table == "medications":
            rows = conn.execute(
                "SELECT medication_id, start_hours, time_valid FROM medications "
                "WHERE medication_id = ANY(%s) AND hadm_id = %s",
                (ids, hadm_id)).fetchall()
            for rid, start, valid in rows:
                if valid is False:
                    out[f"medications#{rid}"] = (
                        "the pharmacy record", "timing unreliable")
                else:
                    w = when(start, as_of)
                    out[f"medications#{rid}"] = (
                        "the pharmacy record", f"started {w}" if w else None)

        elif table == "note_chunks":
            rows = conn.execute(
                "SELECT c.chunk_id, c.note_type, c.section, n.hours_since_admit "
                "FROM note_chunks c LEFT JOIN notes n ON n.note_id = c.note_id "
                "WHERE c.chunk_id = ANY(%s) AND c.hadm_id = %s",
                (ids, hadm_id)).fetchall()
            for rid, ntype, section, h in rows:
                name = _NOTE_TYPES.get((ntype or "").lower(), (ntype or "notes").lower())
                what = f"the {name}" + (f", {section.lower()} section" if section else "")
                out[f"note_chunks#{rid}"] = (what, when(h, as_of))

        elif table == "notes":
            rows = conn.execute(
                "SELECT note_id, note_type, hours_since_admit FROM notes "
                "WHERE note_id = ANY(%s) AND hadm_id = %s",
                (ids, hadm_id)).fetchall()
            for rid, ntype, h in rows:
                name = _NOTE_TYPES.get((ntype or "").lower(), (ntype or "notes").lower())
                out[f"notes#{rid}"] = (f"the {name}", when(h, as_of))

        elif table == "diagnoses":
            rows = conn.execute(
                "SELECT diagnosis_id FROM diagnoses "
                "WHERE diagnosis_id = ANY(%s) AND hadm_id = %s",
                (ids, hadm_id)).fetchall()
            for (rid,) in rows:
                out[f"diagnoses#{rid}"] = ("the coded diagnoses", None)

    return out


def _phrase(items, sentence):
    """
    One spoken source for a group of citations. Duplicates collapse, and a
    time already said in the sentence ("INR on arrival was…") isn't repeated.
    """
    parts = []
    for what, w in items:
        low = sentence.lower()
        if w and (w.lower() in low or
                  ("unreliable" in w and "unreliable" in low)):
            w = None
        # "INR on arrival ... from the labs 2 hours into the admission"
        # sounds like a contradiction; arrival labs are drawn over the
        # first few hours, so the sentence's "on arrival" already says it.
        if w and re.search(r"\b(?:arrival|arrived|presentation|presented|"
                           r"on admission|admitted|initial(?:ly)?|first)\b",
                           low) and re.fullmatch(
                r"(?:[2-6] hours into the admission|on arrival)", w):
            w = None
        p = f"{what}, {w}" if (w and what.endswith("record")) else \
            (f"{what} {w}" if w else what)
        if p not in parts:
            parts.append(p)
    if not parts:
        return ""
    return "from " + (parts[0] if len(parts) == 1 else
                      ", ".join(parts[:-1]) + " and " + parts[-1])


def to_speech(text, info):
    """
    The verified answer, rewritten for the ear: ids become source phrases,
    a removed value becomes one plain sentence. Unknown ids are dropped
    silently — they were stripped from the written answer already.
    """
    text = text.replace(REMOVED, "I couldn't verify one value, so I've left it out")
    def last_sentence(s):
        cut = max(s.rfind(". "), s.rfind("\n"))
        return s[cut + 1:] if cut >= 0 else s

    out, pos = [], 0
    for m in TAGS.finditer(text):
        before = text[pos:m.start()].rstrip()
        items = [info[t] for t in ONE_TAG.findall(m.group(1)) if t in info]
        if before.endswith((".", "!", "?")):
            # Citation placed after its sentence ("...range. labs#1"):
            # speak the source inside it ("...range, from the labs.").
            body, punct = before[:-1], before[-1]
            phrase = _phrase(items, last_sentence(body))
            out.append(body + (f", {phrase}" if phrase else "") + punct + " ")
        else:
            phrase = _phrase(items, last_sentence(before))
            rest = text[m.end():].lstrip()
            more = ", " if phrase and rest[:1].isalnum() else " "
            out.append(before + (f", {phrase}{more}" if phrase else " "))
        pos = m.end()
    out.append(text[pos:])
    speech = speak_units("".join(out))
    speech = re.sub(r"\s+([,.!?])", r"\1", speech)
    speech = re.sub(r",\s*,", ",", speech)
    return re.sub(r"\s{2,}", " ", speech).strip()


# ================================================================
# Nova writes the spoken version; GRUS checks it
# ================================================================
#
# The model writes speech far more naturally than rules can. But a
# second, uncited answer could drift from the checked one ("INR 4.8"
# where the checked answer says 4.0). So the model's spoken draft is
# used only if every number in it appears in the checked written
# answer or in the rule-built speech (which carries the verified
# times). Otherwise the rule-built speech is used. Nothing unchecked is
# spoken either way.

SPOKEN_INSTRUCTION = """
After your answer, add one final line that starts with SPOKEN: and gives
the same answer the way you would say it aloud to a doctor in a busy room.
- One to three short sentences. Most important fact first.
- No citation tags, no markdown, no lists.
- Numbers as digits, exactly as in your answer (write 4.0, not "four").
- Say units only if needed for meaning; never say g/dL, mmHg or mEq/L.
- Do not add any fact, number or time that is not in your answer.
- If your answer says something is not on file, say that plainly.
"""

_SPOKEN_LINE = re.compile(r"(?:^|\n)\s*\**\s*SPOKEN\s*:\s*\**\s*(.*)\Z",
                          re.S | re.I)
_NUMBER_WORDS = re.compile(
    r"\b(?:zero|one|two|three|four|five|six|seven|eight|nine|ten|eleven|"
    r"twelve|thirteen|fourteen|fifteen|sixteen|seventeen|eighteen|"
    r"nineteen|twenty|thirty|forty|fifty|sixty|seventy|eighty|ninety|"
    r"hundred|thousand|point|half|dozen)\b", re.I)
_DIGITS = re.compile(r"(?<![\w.])\d+(?:\.\d+)?")
MAX_SPOKEN_WORDS = 70


def split_spoken(text):
    """Model output -> (written answer for the screen, spoken draft or None)."""
    m = _SPOKEN_LINE.search(text or "")
    if not m:
        return text, None
    return text[:m.start()].rstrip(), m.group(1).strip() or None


def _nums(s):
    return {float(n) for n in _DIGITS.findall(s or "")}


def choose_speech(draft, checked_text, rule_speech):
    """
    Returns (speech, source, reason). source is "model" or "rules".

    The model's draft is accepted only if:
      - it exists and is short enough to listen to,
      - it writes every number as digits (spelled-out numbers can't be
        checked, so they aren't accepted),
      - every number in it is in the checked answer or the rule speech,
      - nothing was removed from the written answer (a removed value
        might still be in the draft in another form).
    """
    def rules(reason):
        return rule_speech, "rules", reason

    if not draft:
        return rules("no spoken draft")
    if "[removed:" in (checked_text or ""):
        return rules("a value was removed from the answer")
    if TAGS.search(draft):
        return rules("draft contains citation tags")
    if len(draft.split()) > MAX_SPOKEN_WORDS:
        return rules("draft too long")
    if _NUMBER_WORDS.search(draft):
        return rules("draft spells out a number")
    allowed = _nums(checked_text) | _nums(rule_speech)
    extra = [n for n in _nums(draft)
             if not any(abs(n - a) < 1e-9 for a in allowed)]
    if extra:
        return rules(f"draft has a number not in the checked answer: "
                     f"{extra[0]:g}")
    return speak_units(draft), "model", None