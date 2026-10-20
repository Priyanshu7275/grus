"""
GRUS — Value check for chat answers

The chat already strips any citation the tools never returned. That
catches an invented id. It does not catch a real id beside a wrong
number:

    INR 8.1 [labs#301694]        <- the row says 6.3

The id is genuine, so it survives, and a fabricated value next to a real
citation looks verified and is not. The brief's Verifier prevents this by
checking values field by field. This does the same for chat, where the
answer is prose rather than JSON.

Rule: every number in the clause that ends at a citation must appear in
one of the rows that citation points to, and a sentence that states a
number without any citation is removed. Time expressions (@36.47h,
"2 hours ago") and the patient's age ("82M") are set aside first — they
are not values of the cited row, and some times are computed, not stored.

Deterministic. No model. Fails closed: a clause whose value cannot be
matched is removed and reported, never silently kept.
"""

import re

# A minus sign only when not preceded by a digit: "0.9-1.1" is a range.
# Digits inside a name are not values: SpO2, PaO2, FiO2, HCO3, B12.
NUMBER = re.compile(r"(?<![\w.])-?\d+(?:\.\d+)?")
# Citations with or without square brackets: "[labs#1]" or "labs#1".
TAGS = re.compile(r"\[?\s*\b([a-z_]+#\d+(?:\s*,\s*[a-z_]+#\d+)*)\s*\]?")
ONE_TAG = re.compile(r"[a-z_]+#\d+")
# A clause starts after the previous citation, a sentence end, or a line break.
BOUNDARY = re.compile(r"(?:[.!?;](?=\s)|\n)")
# Set aside before values are checked: timeline positions (some are
# computed, not stored) and the patient's age/sex ("82M", "65-year-old"),
# which belong to the patient, not to the cited row.
SET_ASIDE = re.compile(
    # The patient's own identifier ("patient ending 5843", "admission
    # 22865843") is not a value of the cited row. Voice questions always
    # name the patient, and the model often repeats it in the answer.
    r"\b(?:patient|admission|hadm)(?:\s+(?:id|number|no\.?))?\s*"
    r"(?:ending(?:\s+in)?\s*|#\s*)?\d{3,}\b"
    r"|@\s*-?\d+(?:\.\d+)?\s*h\b"
    r"|\b-?\d+(?:\.\d+)?\s*(?:h|hrs?|hours?|mins?|minutes?|days?)\b(?:\s+ago)?"
    r"|\b\d{1,3}\s*(?:[MF]\b|yo\b|y/o\b|-?years?[- ]old\b)",
    re.I)

REMOVED = "[removed: value did not match the record]"


def numbers_in(obj):
    """Every number anywhere in a tool row (dict, list, str or number)."""
    out = []

    def walk(x):
        if x is None or isinstance(x, bool):
            return
        if isinstance(x, (int, float)):
            out.append(float(x))
        elif isinstance(x, str):
            out.extend(float(n) for n in NUMBER.findall(x))
        elif isinstance(x, dict):
            for v in x.values():
                walk(v)
        elif isinstance(x, (list, tuple, set)):
            for v in x:
                walk(v)

    walk(obj)
    return out


def index_result(values_by_source, res):
    """
    Record which numbers each source id actually carries.

    Tool results put rows[i] beside sources[i] (the Composer relies on the
    same pairing). When a tool returns one row for many sources — a score,
    a summary — every source is given all of that result's numbers: looser,
    but it never rejects a true value.
    """
    rows, sources = res.rows or [], res.sources or []
    if rows and len(rows) == len(sources):
        pairs = zip(sources, rows)
    else:
        pairs = ((s, rows) for s in sources)
    for s, row in pairs:
        tag = f"{s['table']}#{s['id']}"
        values_by_source.setdefault(tag, []).extend(numbers_in(row))


def _matches(claimed: str, true_values) -> bool:
    """
    A stated value matches if a stored value rounds to it at the precision
    it was stated with: '6.3' accepts 6.25..6.35, '142' accepts 141.5..142.5.
    """
    c = float(claimed)
    decimals = len(claimed.split(".")[1]) if "." in claimed else 0
    tol = 0.5 * 10 ** (-decimals) + 1e-9
    return any(abs(v - c) <= tol for v in true_values)


def check_values(text: str, values_by_source: dict):
    """
    Returns (checked_text, rejected).

    rejected: [{"claim": clause, "value": n, "sources": [tags]}] — each
    clause removed because a number in it was not in the rows it cites.
    Citations not in values_by_source are left alone here; the existing
    invented-citation strip handles those.
    """
    rejected = []
    out, pos = [], 0

    for m in TAGS.finditer(text):
        tags = ONE_TAG.findall(m.group(1))
        known = [t for t in tags if t in values_by_source]

        before = text[pos:m.start()]
        bounds = [0] + [b.end() for b in BOUNDARY.finditer(before)]
        cut = bounds[-1]
        # A citation placed after the sentence it supports
        # ("...above range. labs#1") checks that sentence, not empty space.
        if not before[cut:].strip() and len(bounds) > 1:
            cut = bounds[-2]
        lead, clause = before[:cut], before[cut:]

        if not known:
            out.append(before + m.group(0))
            pos = m.end()
            continue

        pool = [v for t in known for v in values_by_source[t]]
        to_check = NUMBER.findall(SET_ASIDE.sub(" ", clause))
        bad = next((n for n in to_check if not _matches(n, pool)), None)

        if bad is None:
            out.append(before + m.group(0))
        else:
            rejected.append({"claim": clause.strip(), "value": bad,
                             "sources": known})
            spacer = " " if lead and not lead.endswith((" ", "\n")) else ""
            out.append(lead + spacer + REMOVED)
        pos = m.end()

    out.append(text[pos:])
    text = "".join(out)
    text, uncited = _remove_uncited(text)
    return text, rejected + uncited


# A sentence ends at . ! ? followed by space, or at a line break.
_SENTENCES = re.compile(r"[^\n]*?(?:[.!?](?=\s|$)|\n|$)")
# "1." / "2)" at the start of a list line is numbering, not a value.
_LIST_MARK = re.compile(r"^\s*(?:\d+[.)]|[-*•])\s+")


def _remove_uncited(text):
    """
    The general rule, independent of wording: a sentence that states a
    number must cite where it came from. The cited clauses were checked
    above; this catches a number with no citation at all
    ("Potassium is 6.2."), which nothing else would.

    A citation placed at the start of the next sentence counts
    ("...above range. labs#1"). Times and age are set aside as above.
    Fails closed: the sentence is removed and reported.
    """
    parts = [m.group(0) for m in _SENTENCES.finditer(text) if m.group(0)]
    rejected = []
    for i, sent in enumerate(parts):
        if TAGS.search(sent) or REMOVED in sent:
            continue
        if re.fullmatch(r"\s*\d+[.)]", sent):      # "1." list number
            continue
        nxt = parts[i + 1].lstrip() if i + 1 < len(parts) else ""
        if TAGS.match(nxt):
            continue
        body = SET_ASIDE.sub(" ", _LIST_MARK.sub("", sent))
        nums = NUMBER.findall(body)
        if nums:
            rejected.append({"claim": sent.strip(), "value": nums[0],
                             "sources": [], "reason": "no citation"})
            lead = sent[:len(sent) - len(sent.lstrip())]
            end = "\n" if sent.endswith("\n") else ""
            parts[i] = lead + REMOVED + ("." if not end else "") + end
    return "".join(parts), rejected

# ----------------------------------------------------------------------
# Speculation
# ----------------------------------------------------------------------
# A model asked "why" will reach for a plausible cause: "likely due to
# cirrhosis", "often associated with anticoagulation". The record rarely
# states the indication, and a confident guess is the most dangerous
# thing a clinical assistant can say. Instructions alone did not stop it
# (tested twice), so, like numbers, it is checked in code: a sentence
# that speculates about cause or meaning is removed and reported.

SPECULATION = re.compile(
    r"\b(?:likely|probably|possibly|presumably|perhaps)\b"
    r"|\bmay\s+(?:suggest|indicate|reflect|explain|imply|be\s+(?:due|related|"
    r"managed|because|a\s+sign))"
    r"|\bmight\s+(?:be|suggest|indicate|explain)\b"
    r"|\bcould\s+(?:be|suggest|indicate|explain)\b"
    r"|\b(?:often|commonly|typically|usually)\s+(?:associated|seen|linked)\b"
    r"|\bit\s+is\s+possible\b"
    r"|\bdue\s+to\s+the\s+risk\b"
    r"|\bwhich\s+(?:suggests|indicates|implies)\b"
    r"|\bconsistent\s+with\b"
    r"|\bsuggestive\s+of\b",
    re.I)


def remove_speculation(text):
    """
    Returns (text, removed). Each removed item is the sentence dropped.
    Citations that only belonged to a dropped sentence go with it.
    """
    parts = [m.group(0) for m in _SENTENCES.finditer(text or "") if m.group(0)]
    kept, removed = [], []
    for sent in parts:
        if SPECULATION.search(sent):
            removed.append(sent.strip())
            if sent.endswith("\n"):
                kept.append("\n")
            continue
        kept.append(sent)
    out = "".join(kept)
    out = re.sub(r"[ \t]{2,}", " ", out).strip()
    return out, removed