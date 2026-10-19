"""
GRUS — Brief text tidy-up

The brief is written for a clinician reading on a screen. The model
sometimes passes through database shorthand that means nothing to them.
This rewrites it deterministically, after the brief is generated, so it
never depends on the model remembering an instruction:

  RENAL_CONTRAST: ...          -> Kidney function and contrast: ...
  EW EMER.                     -> Emergency
  Creatinine 2.7 at 205.35h    -> Creatinine 2.7 on day 9
  No urine output after 84.85h -> No urine output after day 4
  [labs#1, medications#2]      -> [labs#1] [medications#2]
                                  (each one becomes its own clickable chip)

Only wording changes. No value and no source id is touched, so the
citation check that runs afterwards sees exactly the same ids.
"""

import re

RULE_TITLES = {
    "ANAEMIA": "Anaemia",
    "ANTICOAGULATED": "Anticoagulated",
    "ANTICOAG_TRAUMA": "Anticoagulated with trauma",
    "MED_TIMING_UNRELIABLE": "Medication timing unreliable",
    "NO_PRIOR_RECORDS": "No prior records",
    "NSAID_BLEEDING": "NSAID with bleeding risk",
    "RENAL_CONTRAST": "Kidney function and contrast",
    "SHOCK_INDEX": "Shock index",
    "THROMBOCYTOPENIA": "Low platelets",
}

# MIMIC-IV admission_type codes.
ADMISSION_TYPES = {
    "EW EMER.": "Emergency",
    "DIRECT EMER.": "Direct emergency",
    "EU OBSERVATION": "Emergency observation",
    "OBSERVATION ADMIT": "Observation",
    "DIRECT OBSERVATION": "Direct observation",
    "AMBULATORY OBSERVATION": "Ambulatory observation",
    "SURGICAL SAME DAY ADMISSION": "Same-day surgical",
    "ELECTIVE": "Elective",
    "URGENT": "Urgent",
}

_CODE_AT_LINE_START = re.compile(
    r"(?m)^(\s*(?:[-*•]\s*|\[(?:critical|warning|info|unknown)\]\s*)?)"
    r"(?:\*\*)?([A-Z][A-Z0-9]*(?:_[A-Z0-9]+)+|[A-Z]{5,})(?:\*\*)?(\s*[:—-])")
_GROUPED_TAGS = re.compile(
    r"\[\s*([a-z_]+#\d+(?:\s*,\s*[a-z_]+#\d+)+)\s*\]")
_HOURS = r"(-?\d+(?:\.\d+)?)\s?h(?:rs?|ours?)?\b"


def _point(h):
    """A moment on the admission, for a screen: 'on day 9', 'on arrival'."""
    h = float(h)
    if h < 0:
        return "before arrival"
    if h <= 1:
        return "on arrival"
    if h < 48:
        n = int(round(h))
        return f"{n} hour{'s' if n != 1 else ''} after arrival"
    return f"on day {int(h // 24) + 1}"


def _edge(h):
    """A boundary: 'after day 4', 'after hour 5'."""
    h = float(h)
    if h < 48:
        return f"hour {max(int(round(h)), 0)}"
    return f"day {int(h // 24) + 1}"


def _title(code):
    if code in RULE_TITLES:
        return RULE_TITLES[code]
    if "_" not in code:
        return None                      # an ordinary capitalised word
    t = code.replace("_", " ").lower()
    return t[:1].upper() + t[1:]


def tidy_brief(text):
    if not text:
        return text

    # 1. Rule codes at the start of a line -> plain titles.
    def code(m):
        t = _title(m.group(2))
        return (m.group(1) + t + m.group(3)) if t else m.group(0)
    text = _CODE_AT_LINE_START.sub(code, text)

    # 2. Admission type codes.
    for raw, plain in ADMISSION_TYPES.items():
        text = re.sub(r"(?<![A-Za-z])" + re.escape(raw) + r"(?![A-Za-z])",
                      plain, text)

    # 3. Record hours -> where on the admission they fall.
    text = re.sub(r"\b(after|before|until|since|from)\s+" + _HOURS,
                  lambda m: f"{m.group(1)} {_edge(m.group(2))}", text)
    text = re.sub(r"(?:\bat\s+|@\s*)" + _HOURS,
                  lambda m: _point(m.group(1)), text)
    # Bare decimal hours ("Vitals 121.85h") only; a bare whole number
    # like "24h urine" is left alone.
    text = re.sub(r"(?<![\w.#])(-?\d+\.\d+)\s?h\b",
                  lambda m: _point(m.group(1)), text)

    # 4. One chip per source: [a#1, b#2] -> [a#1] [b#2]
    text = _GROUPED_TAGS.sub(
        lambda m: " ".join(f"[{t.strip()}]" for t in m.group(1).split(",")),
        text)
    return text