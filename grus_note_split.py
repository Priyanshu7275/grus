"""
GRUS — Note section splitting

Splits a pasted clinical note into chunks by clinical section.

This exists because fixed-size splitting destroys exactly the content
that matters most. A medication list cut at 500 characters leaves
"Warfarin 5mg" in one chunk and "daily" in another, and neither
retrieves when someone searches for the anticoagulant. The section
heading is the natural boundary — it is where the writer changed
subject.

Used by POST /admissions/{id}/notes so a whole discharge summary can be
pasted in one call and still chunk properly.
"""

import re

# Headings as they appear in MIMIC discharge summaries, plus the
# variations clinicians actually type. Order matters only for display;
# matching is by position in the text.
SECTION_PATTERNS = [
    (r"chief\s+complaint", "Chief Complaint"),
    (r"history\s+of\s+present\s+illness|hpi\b", "History of Present Illness"),
    (r"past\s+medical\s+history|pmh\b", "Past Medical History"),
    (r"past\s+surgical\s+history|psh\b", "Past Surgical History"),
    (r"medications?\s+on\s+admission|home\s+medications?|"
     r"admission\s+medications?", "Medications on Admission"),
    (r"discharge\s+medications?", "Discharge Medications"),
    (r"allerg(y|ies)", "Allergies"),
    (r"social\s+history", "Social History"),
    (r"family\s+history", "Family History"),
    (r"physical\s+exam(ination)?|examination\s+on\s+admission",
     "Physical Exam"),
    (r"vital\s+signs?", "Vital Signs"),
    (r"pertinent\s+results?|laboratory\s+(data|results?)|labs?\b",
     "Pertinent Results"),
    (r"imaging|radiology", "Imaging"),
    (r"(brief\s+)?hospital\s+course", "Hospital Course"),
    (r"assessment\s+(and|&)\s+plan|impression", "Assessment and Plan"),
    (r"discharge\s+diagnos[ei]s", "Discharge Diagnosis"),
    (r"discharge\s+instructions?", "Discharge Instructions"),
    (r"discharge\s+disposition", "Discharge Disposition"),
    (r"follow\s*-?\s*up", "Follow-up"),
    (r"procedures?", "Procedures"),
    (r"consultations?", "Consultations"),
]

# A heading is a short line that ends in a colon, or sits alone in caps.
# Requiring one of those shapes stops "the patient has a history of
# diabetes" being read as a Past Medical History heading.
HEADING_SHAPE = re.compile(
    r"^\s*(?:#+\s*)?([A-Za-z][A-Za-z /&'-]{2,60}?)\s*:\s*(.*)$")
CAPS_HEADING = re.compile(r"^\s*([A-Z][A-Z /&'-]{2,60})\s*:?\s*$")

MAX_CHUNK_CHARS = 4000


def _canonical(heading):
    """A heading to its canonical section name, or None."""
    h = heading.strip().lower()
    for pattern, name in SECTION_PATTERNS:
        if re.fullmatch(rf"\s*{pattern}\s*", h):
            return name
    for pattern, name in SECTION_PATTERNS:
        if re.search(pattern, h):
            return name
    return None


def split_note(text, default_section="Clinical Note"):
    """
    Split a note into (section, text) pairs.

    Returns one chunk per recognised section. Text before the first
    heading is kept under default_section rather than discarded — a
    pasted note often opens with the complaint and no label.

    A section longer than MAX_CHUNK_CHARS is split on paragraph breaks,
    never mid-line, and the parts share the section name.
    """
    if not text or not text.strip():
        return []

    lines = text.splitlines()
    marks = []          # (line index, section name, inline remainder)

    for i, line in enumerate(lines):
        stripped = line.strip()
        if not stripped or len(stripped) > 120:
            continue

        m = HEADING_SHAPE.match(line)
        if m:
            name = _canonical(m.group(1))
            if name:
                marks.append((i, name, m.group(2).strip()))
                continue

        m = CAPS_HEADING.match(line)
        if m:
            name = _canonical(m.group(1))
            if name:
                marks.append((i, name, ""))

    chunks = []

    # Anything before the first heading.
    first = marks[0][0] if marks else len(lines)
    preamble = "\n".join(lines[:first]).strip()
    if preamble:
        chunks.append((default_section, preamble))

    for idx, (line_no, name, inline) in enumerate(marks):
        end = marks[idx + 1][0] if idx + 1 < len(marks) else len(lines)
        body = "\n".join(lines[line_no + 1:end]).strip()
        if inline:
            body = f"{inline}\n{body}".strip()
        if body:
            chunks.append((name, body))

    # Split anything oversized on paragraph breaks. A medication list
    # cut mid-line is the failure this whole module exists to avoid, so
    # the split happens between paragraphs or not at all.
    out = []
    for name, body in chunks:
        if len(body) <= MAX_CHUNK_CHARS:
            out.append((name, body))
            continue

        parts, current = [], ""
        for para in body.split("\n\n"):
            if len(current) + len(para) + 2 > MAX_CHUNK_CHARS and current:
                parts.append(current.strip())
                current = para
            else:
                current = f"{current}\n\n{para}" if current else para
        if current.strip():
            parts.append(current.strip())

        for n, part in enumerate(parts):
            label = name if len(parts) == 1 else f"{name} ({n + 1}/{len(parts)})"
            out.append((label, part))

    return out


def summarise_split(chunks):
    """A one-line description of what the split produced."""
    if not chunks:
        return "no content"
    names = [c[0] for c in chunks]
    return f"{len(chunks)} sections: {', '.join(names[:6])}" + \
           (f" and {len(names) - 6} more" if len(names) > 6 else "")


if __name__ == "__main__":
    sample = """
Chief Complaint:
Chest pain and shortness of breath for two hours.

History of Present Illness:
71F with known atrial fibrillation on apixaban presents with central
chest pain since this morning, worse on exertion. Increasing
breathlessness over two days.

Past Medical History:
Atrial fibrillation
Congestive heart failure, EF 35%
Type 2 diabetes
Chronic kidney disease stage 3

Medications on Admission:
Apixaban 5mg twice daily
Furosemide 40mg daily
Ramipril 5mg daily
Bisoprolol 5mg daily
Metformin 1g twice daily

Allergies:
Penicillin - rash

Physical Exam:
BP 138/82, HR 88, RR 20, SpO2 94% on air.
Bibasal crackles. JVP raised 4cm.

Hospital Course:
Troponin rose from 0.08 to 2.86 over three hours. Treated as NSTEMI.
Furosemide increased. Cardiology review arranged.
"""

    chunks = split_note(sample)
    print(summarise_split(chunks))
    print()
    for name, body in chunks:
        preview = body.replace("\n", " ")[:70]
        print(f"  {name:32} {len(body):>5} chars   {preview}")